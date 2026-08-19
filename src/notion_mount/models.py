from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class RemoteDocumentMetadata:
    """Backend-neutral page metadata collected without Markdown conversion."""

    notion_id: str
    parent_id: str | None
    name: str
    last_edited_time: str
    properties: dict[str, Any] = field(default_factory=dict)
    ancestors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentState:
    notion_id: str
    parent_id: str | None
    name: str
    local_path: str
    last_edited_time: str
    content_hash: str
    sync_time: str


class ChangeType(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class DocumentChange:
    notion_id: str
    path: str
    change_type: ChangeType


@dataclass(slots=True)
class SyncResult:
    added: list[DocumentChange] = field(default_factory=list)
    modified: list[DocumentChange] = field(default_factory=list)
    deleted: list[DocumentChange] = field(default_factory=list)
    unchanged: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.deleted)
