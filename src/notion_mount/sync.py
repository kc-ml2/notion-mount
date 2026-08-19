from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .backend import NotionBackend
from .models import (
    ChangeType,
    DocumentChange,
    DocumentState,
    RemoteDocumentMetadata,
    SyncResult,
)
from .state import StateStore
from .storage import LocalStorage, projected_path, render_markdown


class SyncEngine:
    def __init__(self, backend: NotionBackend, state: StateStore, storage: LocalStorage) -> None:
        self.backend = backend
        self.state = state
        self.storage = storage

    def sync(self, root_page_id: str) -> SyncResult:
        # Phase 1: scan only hierarchy and metadata. Page bodies are deliberately
        # excluded so unchanged pages do not incur block API calls or conversion.
        documents = self.backend.scan(root_page_id)
        ids = [document.notion_id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Backend returned duplicate Notion page IDs")

        # Phase 2: compare the inventory with local state and build the work set.
        previous = self.state.all()
        result = SyncResult()
        seen: set[str] = set()
        used_paths: dict[str, str] = {}
        planned: list[
            tuple[RemoteDocumentMetadata, PurePosixPath, str, DocumentState | None]
        ] = []

        # Parents normally precede children, but identity rather than traversal order drives sync.
        for document in documents:
            seen.add(document.notion_id)
            path = projected_path(document.ancestors, document.name)
            path_string = path.as_posix()
            owner = used_paths.get(path_string)
            if owner and owner != document.notion_id:
                path = path.with_stem(f"{path.stem} ({document.notion_id[:8]})")
                path_string = path.as_posix()
            used_paths[path_string] = document.notion_id

            old = previous.get(document.notion_id)
            if (
                old
                and old.local_path == path_string
                and old.last_edited_time == document.last_edited_time
            ):
                result.unchanged += 1
                continue
            planned.append((document, path, path_string, old))

        # Phase 3: fetch and convert only pages selected by the plan, then apply.
        now = datetime.now(UTC).isoformat()
        for document, path, path_string, old in planned:
            body = self.backend.fetch_markdown(document.notion_id)
            content = render_markdown(
                notion_id=document.notion_id,
                last_edited_time=document.last_edited_time,
                name=document.name,
                properties=document.properties,
                body=body,
            )
            digest = hashlib.sha256(content.encode()).hexdigest()
            self.storage.write(path, content)
            if old and old.local_path != path_string:
                self.storage.delete(old.local_path)
            state = DocumentState(
                notion_id=document.notion_id,
                parent_id=document.parent_id,
                name=document.name,
                local_path=path_string,
                last_edited_time=document.last_edited_time,
                content_hash=digest,
                sync_time=now,
            )
            self.state.upsert(state)
            change_type = ChangeType.MODIFIED if old else ChangeType.ADDED
            change = DocumentChange(document.notion_id, f"/{path_string}", change_type)
            (result.modified if old else result.added).append(change)

        for notion_id, old in previous.items():
            if notion_id in seen:
                continue
            self.storage.delete(old.local_path)
            self.state.delete(notion_id)
            result.deleted.append(DocumentChange(notion_id, f"/{old.local_path}", ChangeType.DELETED))

        self.state.commit()
        return result
