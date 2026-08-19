from notion_mount.cli import _print_progress
from notion_mount.models import SyncProgress


def test_fetch_progress_reports_completed_count_without_unknown_total(capsys) -> None:
    _print_progress(SyncProgress("fetch", 11, name="2026-08-10"))

    output = capsys.readouterr().out
    assert "Synced 11 changed pages | 2026-08-10" in output
    assert "/?" not in output


def test_fetch_progress_uses_singular_page(capsys) -> None:
    _print_progress(SyncProgress("fetch", 1, name="First page"))

    assert "Synced 1 changed page | First page" in capsys.readouterr().out
