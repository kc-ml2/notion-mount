from __future__ import annotations

from typing import Protocol

from .models import RemoteDocument


class NotionBackend(Protocol):
    """Notion access boundary; implementations return the complete current hierarchy."""

    def scan(self, root_page_id: str) -> list[RemoteDocument]: ...
