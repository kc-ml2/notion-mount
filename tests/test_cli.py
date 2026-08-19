import notion_mount.cli as cli
from notion_mount.cli import _print_progress
from notion_mount.models import SyncProgress


def test_fetch_progress_reports_completed_count_without_unknown_total(capsys) -> None:
    _print_progress(SyncProgress("fetch", 11, name="2026-08-10"))

    output = capsys.readouterr().out
    assert "Synced 11 changed pages | 2026-08-10" in output
    assert "/?" not in output


def test_progress_clears_titles_longer_than_fixed_terminal_width(capsys) -> None:
    cli._progress_width = 0
    long_title = "x" * 200
    _print_progress(SyncProgress("scan", 1, name=long_title))
    _print_progress(SyncProgress("fetch", 1, name="Short"))

    lines = capsys.readouterr().out.split("\r")
    assert len(lines[-1]) >= len(lines[-2])
    assert lines[-1].rstrip().endswith("Short")


def test_fetch_progress_uses_singular_page(capsys) -> None:
    _print_progress(SyncProgress("fetch", 1, name="First page"))

    assert "Synced 1 changed page | First page" in capsys.readouterr().out
