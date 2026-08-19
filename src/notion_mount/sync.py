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
        *,
        restart: bool = False,
    ) -> SyncResult:
        """Run or resume a durable hierarchy traversal."""
        if restart:
            self.state.clear_session(root_page_id)
        resumed = self.state.has_session(root_page_id)
        if not resumed:
            self.state.start_session(root_page_id, self.backend.initial_task(root_page_id))
        self.backend.set_progress(progress)

        previous = self.state.all()
        reserved_paths = {item.local_path: notion_id for notion_id, item in previous.items()}
        used_paths = {
            previous[notion_id].local_path: notion_id
            for notion_id in self.state.seen(root_page_id)
            if notion_id in previous
        }
        result = SyncResult(resumed=resumed)
        fetched = 0
        discovered = len(self.state.seen(root_page_id))

        try:
            while queued := self.state.next_task(root_page_id):
                task_id, task = queued
                batch = self.backend.process_task(task)
                for document in batch.documents:
                    discovered += 1
                    if progress:
                        progress(SyncProgress("scan", discovered, name=document.name))
                    self._sync_document(
                        root_page_id,
                        document,
                        previous,
                        reserved_paths,
                        used_paths,
                        result,
                    )
                    if result.added or result.modified:
                        changed = len(result.added) + len(result.modified)
                        if changed > fetched:
                            fetched = changed
                            if progress:
                                progress(SyncProgress("fetch", fetched, name=document.name))
                self.state.complete_task(root_page_id, task_id, batch.tasks)
        except BaseException:
            self.state.mark_session_resumable(root_page_id)
            raise

        seen = self.state.seen(root_page_id)
        was_resumed = resumed or self.state.session_resumable(root_page_id)
        if was_resumed:
            # Cursor-based continuation materializes content safely, but remote
            # ordering may have changed while interrupted. Defer deletion until
            # a subsequent uninterrupted traversal from the root reconciles it.
            result.reconciliation_required = True
        else:
            for notion_id, old in previous.items():
                if notion_id in seen:
                    continue
                self.storage.delete(old.local_path)
                self.state.delete(notion_id)
                self.state.commit()
                result.deleted.append(
                    DocumentChange(notion_id, f"/{old.local_path}", ChangeType.DELETED)
                )
        self.state.clear_session(root_page_id)
        return result

    def _sync_document(
        self,
        root_page_id: str,
        document: RemoteDocumentMetadata,
        previous: dict[str, DocumentState],
        reserved_paths: dict[str, str],
        used_paths: dict[str, str],
        result: SyncResult,
    ) -> None:
        old = previous.get(document.notion_id)
        path, path_string = self._project(document, old, used_paths, reserved_paths)
        if (
            old
            and old.local_path == path_string
            and old.last_edited_time == document.last_edited_time
            and self.storage.matches_hash(old.local_path, old.content_hash)
        ):
            result.unchanged += 1
        else:
            state = self._apply(document, path, path_string, old)
            previous[document.notion_id] = state
            change_type = ChangeType.MODIFIED if old else ChangeType.ADDED
            change = DocumentChange(document.notion_id, f"/{path_string}", change_type)
            (result.modified if old else result.added).append(change)
        self.state.mark_seen(root_page_id, document.notion_id)
        self.state.commit()

    @staticmethod
    def _project(
        document: RemoteDocumentMetadata,
        old: DocumentState | None,
        used_paths: dict[str, str],
        reserved_paths: dict[str, str],
    ) -> tuple[PurePosixPath, str]:
        base = projected_path(document.ancestors, document.name)
        candidates = [base]
        for length in (8, 12, len(document.notion_id)):
            candidate = base.with_stem(f"{base.stem} ({document.notion_id[:length]})")
            if candidate not in candidates:
                candidates.append(candidate)
        allowed = {candidate.as_posix() for candidate in candidates}
        if (
            old
            and old.local_path in allowed
            and used_paths.get(old.local_path) in {None, document.notion_id}
        ):
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
    ) -> DocumentState:
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
            sync_time=datetime.now(UTC).isoformat(),
        )
        self.state.upsert(state)
        self.state.commit()
        return state
