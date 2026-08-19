from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import DocumentState

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

    @staticmethod
    def _state(row: sqlite3.Row) -> DocumentState:
        return DocumentState(**dict(row))
