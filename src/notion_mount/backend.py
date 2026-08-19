from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import SyncProgress, TraversalBatch, TraversalTask


class NotionBackend(Protocol):
    """Notion access boundary for checkpointable hierarchy traversal."""

    def set_progress(self, progress: Callable[[SyncProgress], None] | None) -> None: ...

    def initial_task(self, root_page_id: str) -> TraversalTask: ...

    def process_task(self, task: TraversalTask) -> TraversalBatch: ...

    def fetch_markdown(self, notion_id: str) -> str: ...
