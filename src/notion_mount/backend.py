from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import RemoteDocumentMetadata, SyncProgress


class NotionBackend(Protocol):
    """Notion access boundary with separate inventory and content phases."""

    def scan(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> list[RemoteDocumentMetadata]: ...

    def fetch_markdown(self, notion_id: str) -> str: ...
