from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol

from .models import RemoteDocumentMetadata, SyncProgress


class NotionBackend(Protocol):
    """Notion access boundary with streaming inventory and content fetching."""

    def scan(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> Iterator[RemoteDocumentMetadata]: ...

    def fetch_markdown(self, notion_id: str) -> str: ...
