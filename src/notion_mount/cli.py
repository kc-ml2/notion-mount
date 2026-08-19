from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, initialize
from .models import SyncProgress
from .mount import mount_readonly
from .notion import NotionClientBackend
from .state import StateStore
from .storage import LocalStorage
from .sync import SyncEngine


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="notion-mount", description="Read-only Notion Markdown mirror")
    root.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace directory")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="initialize a workspace")
    init.add_argument("--root-page-id", required=True, help="root Notion page or database ID")
    init.add_argument("--token-env", default="NOTION_TOKEN", help="environment variable containing API token")
    sync = commands.add_parser("sync", help="synchronize from Notion")
    sync.add_argument(
        "--restart", action="store_true",
        help="discard an interrupted traversal checkpoint and start from the root",
    )
    commands.add_parser("status", help="show local synchronization state")
    mount = commands.add_parser("mount", help="mount the mirror read-only")
    mount.add_argument("mountpoint", type=Path)
    mount.add_argument("--background", action="store_true")
    return root


_progress_width = 0


def _progress_line(message: str) -> None:
    # Pad to the actual previous width so even very long titles are fully
    # overwritten by a later short title. ANSI is avoided for redirected logs.
    global _progress_width
    width = max(len(message), _progress_width)
    print(f"\r{message:<{width}}", end="", flush=True)
    _progress_width = len(message)


def _print_progress(progress: SyncProgress) -> None:
    if progress.phase == "retry":
        _progress_line(f"Notion API {progress.name or 'temporarily unavailable'}")
    elif progress.phase == "scan":
        _progress_line(
            f"Scanning and syncing... {progress.current} pages discovered | {progress.name or ''}"
        )
    else:
        # Streaming traversal does not know the final total in advance. Report
        # the durable completed count instead of displaying a misleading `?`.
        noun = "page" if progress.current == 1 else "pages"
        _progress_line(
            f"Synced {progress.current} changed {noun} | {progress.name or ''}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            path = initialize(args.workspace, args.root_page_id, args.token_env)
            print(f"Initialized {path}")
            return 0

        config = Config.load(args.workspace)
        if args.command == "sync":
            backend = NotionClientBackend(config.token)
            print("Scanning and synchronizing the Notion hierarchy...", flush=True)
            try:
                with StateStore(config.state_path) as state:
                    resuming = state.has_session(config.root_page_id) and not args.restart
                    if resuming:
                        print("Resuming the interrupted hierarchy traversal...", flush=True)
                    result = SyncEngine(backend, state, LocalStorage(config.workspace)).sync(
                        config.root_page_id,
                        progress=_print_progress,
                        restart=args.restart,
                    )
            except KeyboardInterrupt:
                print("\nSync interrupted. Completed pages were saved; run sync again to resume.")
                return 130
            print("\n", end="")
            print(
                f"Sync complete: {len(result.added)} added, {len(result.modified)} modified, "
                f"{len(result.deleted)} deleted, {result.unchanged} unchanged"
            )
            for change in [*result.added, *result.modified, *result.deleted]:
                print(f"{change.change_type.value:8} {change.path}")
            if result.reconciliation_required:
                print(
                    "Resume completed safely. Run sync once more for full deletion reconciliation."
                )
            return 0

        if args.command == "status":
            with StateStore(config.state_path) as state:
                documents = state.all().values()
                latest = max((item.sync_time for item in documents), default="never")
                count = sum(1 for _ in documents)
                pending = state.pending_task_count(config.root_page_id)
            print(f"Documents: {count}\nLast sync: {latest}\nPending traversal tasks: {pending}")
            return 0

        if args.command == "mount":
            mount_readonly(config.workspace, args.mountpoint, foreground=not args.background)
            return 0
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # notion-client exceptions are intentionally not imported here so the
        # CLI remains decoupled from a specific SDK exception hierarchy.
        if error.__class__.__module__.startswith(("notion_client", "httpx")):
            print(f"\nerror: Notion API request failed: {error}", file=sys.stderr)
            print("Run sync again to resume from completed pages.", file=sys.stderr)
            return 1
        raise
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
