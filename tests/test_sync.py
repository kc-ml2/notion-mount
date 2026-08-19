from pathlib import Path

from notion_mount.models import ChangeType, RemoteDocument
from notion_mount.state import StateStore
from notion_mount.storage import LocalStorage
from notion_mount.sync import SyncEngine


class FakeBackend:
    def __init__(self, documents: list[RemoteDocument]) -> None:
        self.documents = documents

    def scan(self, root_page_id: str) -> list[RemoteDocument]:
        assert root_page_id == "root"
        return self.documents


def document(name: str = "Arbiter", edited: str = "2026-01-01T00:00:00Z", body: str = "Hello") -> RemoteDocument:
    return RemoteDocument(
        notion_id="page-1", parent_id="db-1", name=name,
        last_edited_time=edited, markdown=body,
        properties={"status": "In Progress"}, ancestors=("Projects",),
    )


def test_incremental_sync_and_delete(tmp_path: Path) -> None:
    backend = FakeBackend([document()])
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        engine = SyncEngine(backend, state, LocalStorage(tmp_path))
        first = engine.sync("root")
        assert [change.change_type for change in first.added] == [ChangeType.ADDED]
        path = tmp_path / "Projects/Arbiter.md"
        assert 'notion_id: "page-1"' in path.read_text()
        assert '# Arbiter\n\nHello' in path.read_text()

        second = engine.sync("root")
        assert second.unchanged == 1
        assert not second.changed

        backend.documents = [document(name="Arbiter Project")]
        renamed = engine.sync("root")
        assert len(renamed.modified) == 1
        assert not path.exists()
        assert (tmp_path / "Projects/Arbiter Project.md").exists()

        backend.documents = []
        deleted = engine.sync("root")
        assert len(deleted.deleted) == 1
        assert not (tmp_path / "Projects/Arbiter Project.md").exists()
        assert state.all() == {}


def test_duplicate_paths_are_disambiguated(tmp_path: Path) -> None:
    docs = [document(), RemoteDocument(
        notion_id="different-page", parent_id="db-1", name="Arbiter",
        last_edited_time="2026-01-01T00:00:00Z", markdown="Other", ancestors=("Projects",),
    )]
    with StateStore(tmp_path / ".notion-mount/state.db") as state:
        result = SyncEngine(FakeBackend(docs), state, LocalStorage(tmp_path)).sync("root")
    assert len(result.added) == 2
    assert (tmp_path / "Projects/Arbiter.md").exists()
    assert (tmp_path / "Projects/Arbiter (differen).md").exists()
