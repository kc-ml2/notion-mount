from collections.abc import Callable
from pathlib import Path

from notion_mount.models import ChangeType, RemoteDocumentMetadata, SyncProgress
from notion_mount.state import StateStore
from notion_mount.storage import LocalStorage
from notion_mount.sync import SyncEngine


class FakeBackend:
    def __init__(
        self, documents: list[RemoteDocumentMetadata], bodies: dict[str, str] | None = None
    ) -> None:
        self.documents = documents
        self.bodies = bodies or {document.notion_id: "Hello" for document in documents}
        self.fetches: list[str] = []

    def scan(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ):
        assert root_page_id == "root"
        yield from self.documents

    def fetch_markdown(self, notion_id: str) -> str:
        self.fetches.append(notion_id)
        return self.bodies[notion_id]


def document(
    name: str = "Arbiter", edited: str = "2026-01-01T00:00:00Z"
) -> RemoteDocumentMetadata:
    return RemoteDocumentMetadata(
        notion_id="page-1",
        parent_id="db-1",
        name=name,
        last_edited_time=edited,
        properties={"status": "In Progress"},
        ancestors=("Projects",),
    )


def test_incremental_sync_fetches_only_changed_bodies_and_deletes(tmp_path: Path) -> None:
    backend = FakeBackend([document()])
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        first = engine.sync("root")
        assert [change.change_type for change in first.added] == [ChangeType.ADDED]
        assert backend.fetches == ["page-1"]
        path = tmp_path / "Projects/Arbiter.md"
        assert 'notion_id: "page-1"' in path.read_text()
        assert "# Arbiter\n\nHello" in path.read_text()

        backend.fetches.clear()
        second = engine.sync("root")
        assert second.unchanged == 1
        assert not second.changed
        assert backend.fetches == []

        backend.documents = [document(name="Arbiter Project")]
        renamed = engine.sync("root")
        assert len(renamed.modified) == 1
        assert backend.fetches == ["page-1"]
        assert not path.exists()
        assert (tmp_path / "Projects/Arbiter Project.md").exists()

        backend.documents = []
        deleted = engine.sync("root")
        assert len(deleted.deleted) == 1
        assert not (tmp_path / "Projects/Arbiter Project.md").exists()
        assert state.all() == {}


def test_missing_or_locally_modified_file_is_rematerialized(tmp_path: Path) -> None:
    backend = FakeBackend([document()])
    path = tmp_path / "Projects/Arbiter.md"
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        engine.sync("root")

        path.write_text("locally modified", encoding="utf-8")
        backend.fetches.clear()
        modified = engine.sync("root")
        assert backend.fetches == ["page-1"]
        assert len(modified.modified) == 1
        assert "locally modified" not in path.read_text()

        path.unlink()
        backend.fetches.clear()
        missing = engine.sync("root")
        assert backend.fetches == ["page-1"]
        assert len(missing.modified) == 1
        assert path.exists()


def test_last_edited_time_change_fetches_body(tmp_path: Path) -> None:
    backend = FakeBackend([document()])
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        engine.sync("root")
        backend.fetches.clear()
        backend.bodies["page-1"] = "Updated"
        backend.documents = [document(edited="2026-01-02T00:00:00Z")]

        result = engine.sync("root")

        assert len(result.modified) == 1
        assert backend.fetches == ["page-1"]
        assert "Updated" in (tmp_path / "Projects/Arbiter.md").read_text()


def test_interrupted_sync_commits_completed_pages_and_resumes(tmp_path: Path) -> None:
    second = RemoteDocumentMetadata(
        notion_id="page-2",
        parent_id="db-1",
        name="Chatbot",
        last_edited_time="2026-01-01T00:00:00Z",
        ancestors=("Projects",),
    )
    backend = FakeBackend(
        [document(), second], {"page-1": "First", "page-2": "Second"}
    )
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))

        def interrupt(progress: SyncProgress) -> None:
            if progress.phase == "fetch" and progress.current == 2:
                raise KeyboardInterrupt

        try:
            engine.sync("root", progress=interrupt)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("sync was not interrupted")

        assert set(state.all()) == {"page-1"}
        assert (tmp_path / "Projects/Arbiter.md").exists()
        backend.fetches.clear()

        result = engine.sync("root")

        assert backend.fetches == ["page-2"]
        assert len(result.added) == 1
        assert result.unchanged == 1
        assert set(state.all()) == {"page-1", "page-2"}


def test_interrupted_scan_never_deletes_unseen_documents(tmp_path: Path) -> None:
    unseen = RemoteDocumentMetadata(
        notion_id="page-2",
        parent_id="db-1",
        name="Chatbot",
        last_edited_time="2026-01-01T00:00:00Z",
        ancestors=("Projects",),
    )
    backend = FakeBackend([unseen], {"page-2": "Second"})
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        engine.sync("root")

        class FailingBackend(FakeBackend):
            def scan(self, root_page_id: str, progress=None):
                yield document(name="Restored Arbiter")
                raise RuntimeError("remote traversal failed")

        try:
            SyncEngine(
                FailingBackend([document()], {"page-1": "First"}),
                state,
                LocalStorage(tmp_path),
            ).sync("root")
        except RuntimeError:
            pass
        else:
            raise AssertionError("sync did not fail")

        # The discovered page is durable, while page-2 was not falsely deleted
        # merely because traversal failed before it could be seen.
        assert set(state.all()) == {"page-1", "page-2"}
        assert (tmp_path / "Projects/Restored Arbiter.md").exists()
        assert (tmp_path / "Projects/Chatbot.md").exists()


def test_duplicate_paths_are_disambiguated(tmp_path: Path) -> None:
    docs = [
        document(),
        RemoteDocumentMetadata(
            notion_id="different-page",
            parent_id="db-1",
            name="Arbiter",
            last_edited_time="2026-01-01T00:00:00Z",
            ancestors=("Projects",),
        ),
    ]
    backend = FakeBackend(docs, {"page-1": "First", "different-page": "Other"})
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        result = SyncEngine(backend, state, LocalStorage(tmp_path)).sync("root")
    assert len(result.added) == 2
    assert (tmp_path / "Projects/Arbiter.md").exists()
    assert (tmp_path / "Projects/Arbiter (differen).md").exists()
