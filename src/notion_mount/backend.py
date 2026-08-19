from __future__ import annotations

from typing import Protocol

from .models import RemoteDocumentMetadata


class NotionBackend(Protocol):
    """Notion access boundary with separate inventory and content phases."""

    def scan(self, root_page_id: str) -> list[RemoteDocumentMetadata]: ...

    def fetch_markdown(self, notion_id: str) -> str: ...
