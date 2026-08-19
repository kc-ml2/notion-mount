from pathlib import Path

from notion_mount.models import RemoteDocumentMetadata, TraversalBatch, TraversalTask
from notion_mount.state import StateStore
from notion_mount.storage import LocalStorage
from notion_mount.sync import SyncEngine


def page(notion_id: str, name: str) -> RemoteDocumentMetadata:
    return RemoteDocumentMetadata(
        notion_id=notion_id,
        parent_id="root",
        name=name,
        last_edited_time="2026-01-01T00:00:00Z",
        ancestors=("Root",),
    )


class CheckpointBackend:
    def __init__(self) -> None:
        self.fail_second = True
        self.processed: list[str] = []
        self.fetches: list[str] = []

    def set_progress(self, progress) -> None:
        pass

    def initial_task(self, root_page_id: str) -> TraversalTask:
        return TraversalTask("batch", "first")

    def process_task(self, task: TraversalTask) -> TraversalBatch:
        self.processed.append(task.object_id)
        if task.object_id == "first":
            return TraversalBatch(
                (page("page-1", "First"),),
                (TraversalTask("batch", "second", cursor="cursor-2"),),
            )
        if self.fail_second:
            raise TimeoutError("timed out")
        return TraversalBatch((page("page-2", "Second"),))

    def fetch_markdown(self, notion_id: str) -> str:
        self.fetches.append(notion_id)
        return notion_id


def test_cursorless_tasks_are_deduplicated(tmp_path: Path) -> None:
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        task = TraversalTask("blocks", "same")
        state.start_session("root", task)
        state.enqueue("root", task)
        state.commit()

        assert state.pending_task_count("root") == 1


def test_changed_documents_do_not_reload_the_full_state_table(tmp_path: Path) -> None:
    backend = CheckpointBackend()
    backend.fail_second = False
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        calls = 0
        original_all = state.all

        def counted_all():
            nonlocal calls
            calls += 1
            return original_all()

        state.all = counted_all  # type: ignore[method-assign]
        SyncEngine(backend, state, LocalStorage(tmp_path)).sync("root")

        assert calls == 1
        assert len(state.all()) == 2


def test_timeout_resumes_from_persisted_task_instead_of_root(tmp_path: Path) -> None:
    backend = CheckpointBackend()
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        try:
            engine.sync("root")
        except TimeoutError:
            pass
        else:
            raise AssertionError("sync did not fail")

        queued = state.next_task("root")
        assert queued is not None
        assert queued[1] == TraversalTask("batch", "second", cursor="cursor-2")
        assert set(state.all()) == {"page-1"}

        backend.fail_second = False
        backend.processed.clear()
        result = engine.sync("root")

        assert result.resumed
        assert result.reconciliation_required
        assert backend.processed == ["second"]
        assert backend.fetches == ["page-1", "page-2"]
        assert set(state.all()) == {"page-1", "page-2"}
        assert not state.has_session("root")


def test_existing_session_is_treated_as_resumed_after_hard_process_death(tmp_path: Path) -> None:
    backend = CheckpointBackend()
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        # Simulate a process dying before it can mark the session resumable.
        state.start_session("root", TraversalTask("batch", "second"))
        backend.fail_second = False

        result = SyncEngine(backend, state, LocalStorage(tmp_path)).sync("root")

        assert result.resumed
        assert result.reconciliation_required


def test_restart_discards_checkpoint_and_starts_from_root(tmp_path: Path) -> None:
    backend = CheckpointBackend()
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        try:
            engine.sync("root")
        except TimeoutError:
            pass
        backend.fail_second = False
        backend.processed.clear()

        engine.sync("root", restart=True)

        assert backend.processed == ["first", "second"]
