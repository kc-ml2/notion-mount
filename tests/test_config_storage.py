from pathlib import Path, PurePosixPath

import pytest

from notion_mount.config import Config, initialize
from notion_mount.storage import LocalStorage, projected_path, render_markdown, safe_name


def test_config_round_trip(tmp_path: Path) -> None:
    initialize(tmp_path, "root-id", "CUSTOM_TOKEN")
    config = Config.load(tmp_path)
    assert config.root_page_id == "root-id"
    assert config.token_env == "CUSTOM_TOKEN"


def test_internal_frontmatter_metadata_cannot_be_overridden() -> None:
    markdown = render_markdown(
        notion_id="authoritative-id",
        last_edited_time="2026-01-01T00:00:00Z",
        name="Page",
        properties={"notion_id": "property-id", "status": "Active"},
        body="Body",
    )
    assert 'notion_id: "authoritative-id"' in markdown
    assert "property-id" not in markdown
    assert 'status: "Active"' in markdown


def test_safe_projection_and_traversal_rejection(tmp_path: Path) -> None:
    assert safe_name('bad/name:*') == "bad_name__"
    assert projected_path(("Projects",), "A/B").as_posix() == "Projects/A_B.md"
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).write(PurePosixPath("../secret.md"), "x")
