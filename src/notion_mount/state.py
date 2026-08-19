from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import DocumentState, TraversalTask

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    notion_id TEXT PRIMARY KEY,
    parent_id TEXT,
    name TEXT NOT NULL,
    local_path TEXT NOT NULL UNIQUE,
    last_edited_time TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sync_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_parent_id ON documents(parent_id);

CREATE TABLE IF NOT EXISTS sync_sessions (
    root_page_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    resumable INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS traversal_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_page_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    object_id TEXT NOT NULL,
    ancestors TEXT NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    UNIQUE(root_page_id, kind, object_id, ancestors, cursor)
);
CREATE INDEX IF NOT EXISTS idx_traversal_queue_root ON traversal_queue(root_page_id, id);

CREATE TABLE IF NOT EXISTS sync_seen (
    root_page_id TEXT NOT NULL,
    notion_id TEXT NOT NULL,
    PRIMARY KEY(root_page_id, notion_id)
);
"""


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def all(self) -> dict[str, DocumentState]:
        rows = self.connection.execute("SELECT * FROM documents").fetchall()
        return {row["notion_id"]: self._state(row) for row in rows}

    def upsert(self, state: DocumentState) -> None:
        self.connection.execute(
            """INSERT INTO documents
               (notion_id, parent_id, name, local_path, last_edited_time, content_hash, sync_time)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(notion_id) DO UPDATE SET
                 parent_id=excluded.parent_id, name=excluded.name,
                 local_path=excluded.local_path, last_edited_time=excluded.last_edited_time,
                 content_hash=excluded.content_hash, sync_time=excluded.sync_time""",
            (
                state.notion_id, state.parent_id, state.name, state.local_path,
                state.last_edited_time, state.content_hash, state.sync_time,
            ),
        )

    def delete(self, notion_id: str) -> None:
        self.connection.execute("DELETE FROM documents WHERE notion_id = ?", (notion_id,))

    def commit(self) -> None:
        self.connection.commit()

    def has_session(self, root_page_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sync_sessions WHERE root_page_id = ?", (root_page_id,)
        ).fetchone()
        return row is not None

    def session_resumable(self, root_page_id: str) -> bool:
        row = self.connection.execute(
            "SELECT resumable FROM sync_sessions WHERE root_page_id = ?", (root_page_id,)
        ).fetchone()
        return bool(row and row["resumable"])

    def start_session(self, root_page_id: str, task: TraversalTask) -> None:
        self.clear_session(root_page_id)
        self.connection.execute(
            "INSERT INTO sync_sessions (root_page_id, started_at, resumable) VALUES (?, ?, 0)",
            (root_page_id, datetime.now(UTC).isoformat()),
        )
        self.enqueue(root_page_id, task)
        self.connection.commit()

    def mark_session_resumable(self, root_page_id: str) -> None:
        self.connection.execute(
            "UPDATE sync_sessions SET resumable = 1 WHERE root_page_id = ?",
            (root_page_id,),
        )
        self.connection.commit()

    def clear_session(self, root_page_id: str) -> None:
        self.connection.execute("DELETE FROM sync_seen WHERE root_page_id = ?", (root_page_id,))
        self.connection.execute(
            "DELETE FROM traversal_queue WHERE root_page_id = ?", (root_page_id,)
        )
        self.connection.execute(
            "DELETE FROM sync_sessions WHERE root_page_id = ?", (root_page_id,)
        )
        self.connection.commit()

    def next_task(self, root_page_id: str) -> tuple[int, TraversalTask] | None:
        row = self.connection.execute(
            "SELECT * FROM traversal_queue WHERE root_page_id = ? ORDER BY id LIMIT 1",
            (root_page_id,),
        ).fetchone()
        if row is None:
            return None
        return row["id"], TraversalTask(
            kind=row["kind"],
            object_id=row["object_id"],
            ancestors=tuple(json.loads(row["ancestors"])),
            cursor=row["cursor"] or None,
        )

    def enqueue(self, root_page_id: str, task: TraversalTask) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO traversal_queue
               (root_page_id, kind, object_id, ancestors, cursor)
               VALUES (?, ?, ?, ?, ?)""",
            (
                root_page_id,
                task.kind,
                task.object_id,
                json.dumps(task.ancestors),
                task.cursor or "",
            ),
        )

    def complete_task(
        self, root_page_id: str, task_id: int, new_tasks: tuple[TraversalTask, ...]
    ) -> None:
        for task in new_tasks:
            self.enqueue(root_page_id, task)
        self.connection.execute("DELETE FROM traversal_queue WHERE id = ?", (task_id,))
        self.connection.commit()

    def pending_task_count(self, root_page_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM traversal_queue WHERE root_page_id = ?",
            (root_page_id,),
        ).fetchone()
        return int(row["count"])

    def mark_seen(self, root_page_id: str, notion_id: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO sync_seen (root_page_id, notion_id) VALUES (?, ?)",
            (root_page_id, notion_id),
        )

    def seen(self, root_page_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT notion_id FROM sync_seen WHERE root_page_id = ?", (root_page_id,)
        ).fetchall()
        return {row["notion_id"] for row in rows}

    @staticmethod
    def _state(row: sqlite3.Row) -> DocumentState:
        return DocumentState(**dict(row))
