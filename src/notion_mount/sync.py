from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .backend import NotionBackend
from .models import ChangeType, DocumentChange, DocumentState, SyncResult
from .state import StateStore
from .storage import LocalStorage, projected_path, render_markdown


class SyncEngine:
    def __init__(self, backend: NotionBackend, state: StateStore, storage: LocalStorage) -> None:
        self.backend = backend
        self.state = state
        self.storage = storage

    def sync(self, root_page_id: str) -> SyncResult:
        documents = self.backend.scan(root_page_id)
        ids = [document.notion_id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Backend returned duplicate Notion page IDs")

        previous = self.state.all()
        result = SyncResult()
        seen: set[str] = set()
        used_paths: dict[str, str] = {}
        now = datetime.now(UTC).isoformat()

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

            content = render_markdown(
                notion_id=document.notion_id,
                last_edited_time=document.last_edited_time,
                name=document.name,
                properties=document.properties,
                body=document.markdown,
            )
            digest = hashlib.sha256(content.encode()).hexdigest()
            old = previous.get(document.notion_id)
            unchanged = bool(
                old
                and old.local_path == path_string
                and old.last_edited_time == document.last_edited_time
                and old.content_hash == digest
            )
            if unchanged:
                result.unchanged += 1
                continue

            # Write first, then remove an obsolete projection after successful replacement.
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
