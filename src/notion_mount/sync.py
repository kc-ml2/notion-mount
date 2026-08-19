from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .backend import NotionBackend
from .models import (
    ChangeType,
    DocumentChange,
    DocumentState,
    RemoteDocumentMetadata,
    SyncProgress,
    SyncResult,
)
from .state import StateStore
from .storage import LocalStorage, projected_path, render_markdown


class SyncEngine:
    def __init__(self, backend: NotionBackend, state: StateStore, storage: LocalStorage) -> None:
        self.backend = backend
        self.state = state
        self.storage = storage

    def sync(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncResult:
        """Stream the remote hierarchy and durably apply each discovered page.

        Deletions are intentionally deferred until the complete traversal succeeds.
        An interruption or API failure can therefore never turn an incomplete
        inventory into destructive local deletions.
        """
        previous = self.state.all()
        result = SyncResult()
        seen: set[str] = set()
        used_paths: dict[str, str] = {}
        reserved_paths = {state.local_path: notion_id for notion_id, state in previous.items()}
        fetched = 0

        for document in self.backend.scan(root_page_id, progress=progress):
            if document.notion_id in seen:
                raise ValueError(f"Backend returned duplicate Notion page ID: {document.notion_id}")
            seen.add(document.notion_id)
            old = previous.get(document.notion_id)
            path, path_string = self._project(
                document, old, used_paths, reserved_paths
            )
            if (
                old
                and old.local_path == path_string
                and old.last_edited_time == document.last_edited_time
                and self.storage.matches_hash(old.local_path, old.content_hash)
            ):
                result.unchanged += 1
                continue

            self._apply(document, path, path_string, old)
            fetched += 1
            if progress:
                progress(SyncProgress("fetch", fetched, name=document.name))
            change_type = ChangeType.MODIFIED if old else ChangeType.ADDED
            change = DocumentChange(document.notion_id, f"/{path_string}", change_type)
            (result.modified if old else result.added).append(change)

        # Reaching this point proves that the inventory is complete. Only now is
        # absence from `seen` safe to interpret as a remote deletion.
        for notion_id, old in previous.items():
            if notion_id in seen:
                continue
            self.storage.delete(old.local_path)
            self.state.delete(notion_id)
            self.state.commit()
            result.deleted.append(
                DocumentChange(notion_id, f"/{old.local_path}", ChangeType.DELETED)
            )
        return result

    @staticmethod
    def _project(
        document: RemoteDocumentMetadata,
        old: DocumentState | None,
        used_paths: dict[str, str],
        reserved_paths: dict[str, str],
    ) -> tuple[PurePosixPath, str]:
        base = projected_path(document.ancestors, document.name)
        candidates = [base]
        # Prefix lengths keep common paths readable while still handling the
        # extremely unlikely case of two IDs sharing the same first 8 chars.
        for length in (8, 12, len(document.notion_id)):
            candidate = base.with_stem(f"{base.stem} ({document.notion_id[:length]})")
            if candidate not in candidates:
                candidates.append(candidate)

        allowed = {candidate.as_posix() for candidate in candidates}
        if old and old.local_path in allowed and old.local_path not in used_paths:
            # Preserve prior ownership regardless of remote traversal order.
            chosen = PurePosixPath(old.local_path)
        else:
            chosen = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.as_posix() not in used_paths
                    and reserved_paths.get(candidate.as_posix()) in {None, document.notion_id}
                ),
                None,
            )
            if chosen is None:
                raise ValueError(f"Could not allocate a unique path for {document.notion_id}")

        path_string = chosen.as_posix()
        used_paths[path_string] = document.notion_id
        return chosen, path_string

    def _apply(
        self,
        document: RemoteDocumentMetadata,
        path: PurePosixPath,
        path_string: str,
        old: DocumentState | None,
    ) -> None:
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
        self.state.upsert(
            DocumentState(
                notion_id=document.notion_id,
                parent_id=document.parent_id,
                name=document.name,
                local_path=path_string,
                last_edited_time=document.last_edited_time,
                content_hash=digest,
                sync_time=datetime.now(UTC).isoformat(),
            )
        )
        self.state.commit()
