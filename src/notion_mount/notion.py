from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
from notion_client.errors import RequestTimeoutError

from .models import (
    RemoteDocumentMetadata,
    SyncProgress,
    TraversalBatch,
    TraversalTask,
)


class NotionClientBackend:
    """Notion backend using notion-sdk-py's synchronous client."""

    def __init__(
        self,
        token: str,
        *,
        requests_per_second: float = 2.5,
        max_retries: int = 8,
        retry_forever: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        try:
            from notion_client import Client
            from notion_to_md import NotionToMarkdown
        except ImportError as error:
            raise RuntimeError("Reinstall notion-mount to restore its Notion dependencies") from error
        self.client = Client(auth=token)
        self.converter = NotionToMarkdown(
            self.client,
            config={"parse_child_pages": False, "convert_images_to_base64": False},
        )
        self.requests_per_second = requests_per_second
        self.max_retries = max_retries
        self.retry_forever = retry_forever
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self._progress: Callable[[SyncProgress], None] | None = None
        self._scan_count = 0

    def set_progress(
        self, progress: Callable[[SyncProgress], None] | None
    ) -> None:
        self._progress = progress

    def initial_task(self, root_page_id: str) -> TraversalTask:
        return TraversalTask("root", root_page_id)

    def process_task(self, task: TraversalTask) -> TraversalBatch:
        """Process one checkpointable API traversal unit."""
        if task.kind == "root":
            root = self._retrieve(task.object_id)
            if root["object"] == "page":
                name = self._title(root)
                return TraversalBatch(
                    documents=(self._page(root, ()),),
                    tasks=(TraversalTask("blocks", root["id"], (name,)),),
                )
            return TraversalBatch(
                tasks=(TraversalTask("database", root["id"], (self._title(root),)),)
            )
        if task.kind == "blocks":
            return self._process_blocks_task(task)
        if task.kind == "database":
            return self._process_database_task(task)
        if task.kind == "data_source":
            return self._process_data_source_task(task)
        raise ValueError(f"Unknown traversal task kind: {task.kind}")

    def _process_blocks_task(self, task: TraversalTask) -> TraversalBatch:
        response = self._request(
            self.client.blocks.children.list,
            block_id=task.object_id,
            **({"start_cursor": task.cursor} if task.cursor else {}),
        )
        documents: list[RemoteDocumentMetadata] = []
        tasks: list[TraversalTask] = []
        for block in response.get("results", []):
            kind = block.get("type")
            if kind == "child_page":
                page = self._request(self.client.pages.retrieve, page_id=block["id"])
                name = self._title(page)
                documents.append(self._page(page, task.ancestors))
                tasks.append(TraversalTask("blocks", page["id"], (*task.ancestors, name)))
            elif kind == "child_database":
                database = self._request(
                    self.client.databases.retrieve, database_id=block["id"]
                )
                tasks.append(
                    TraversalTask(
                        "database", database["id"],
                        (*task.ancestors, self._title(database)),
                    )
                )
            elif block.get("has_children"):
                tasks.append(TraversalTask("blocks", block["id"], task.ancestors))
        if response.get("has_more"):
            tasks.append(
                TraversalTask("blocks", task.object_id, task.ancestors, response["next_cursor"])
            )
        return TraversalBatch(tuple(documents), tuple(tasks))

    def _process_database_task(self, task: TraversalTask) -> TraversalBatch:
        query = getattr(self.client.databases, "query", None)
        if query is not None:
            return self._query_pages(query, "database_id", task)
        database = self._request(
            self.client.databases.retrieve, database_id=task.object_id
        )
        return TraversalBatch(
            tasks=tuple(
                TraversalTask("data_source", source["id"], task.ancestors)
                for source in database.get("data_sources", [])
            )
        )

    def _process_data_source_task(self, task: TraversalTask) -> TraversalBatch:
        return self._query_pages(self.client.data_sources.query, "data_source_id", task)

    def _query_pages(self, method: Any, id_parameter: str, task: TraversalTask) -> TraversalBatch:
        kwargs = {id_parameter: task.object_id}
        if task.cursor:
            kwargs["start_cursor"] = task.cursor
        response = self._request(method, **kwargs)
        documents: list[RemoteDocumentMetadata] = []
        tasks: list[TraversalTask] = []
        for page in response.get("results", []):
            name = self._title(page)
            documents.append(self._page(page, task.ancestors))
            tasks.append(TraversalTask("blocks", page["id"], (*task.ancestors, name)))
        if response.get("has_more"):
            tasks.append(
                TraversalTask(task.kind, task.object_id, task.ancestors, response["next_cursor"])
            )
        return TraversalBatch(tuple(documents), tuple(tasks))

    def scan(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> Iterator[RemoteDocumentMetadata]:
        """Yield metadata as the hierarchy is discovered."""
        self._progress = progress
        self._scan_count = 0
        root = self._retrieve(root_page_id)
        if root["object"] == "page":
            root_name = self._title(root)
            yield self._page(root, ())
            yield from self._scan_blocks(root["id"], (root_name,))
        else:
            yield from self._scan_database(root, (self._title(root),))

    def _report(self, event: SyncProgress) -> None:
        if self._progress:
            self._progress(event)

    def _report_scan(self, name: str) -> None:
        self._scan_count += 1
        self._report(SyncProgress("scan", self._scan_count, name=name))

    def _retrieve(self, object_id: str) -> dict[str, Any]:
        try:
            return self._request(self.client.pages.retrieve, page_id=object_id)
        except Exception as page_error:
            # A root database is not retrievable through the page endpoint. Do
            # not fall back for transient failures such as an exhausted 429,
            # because that would duplicate load and obscure the real error.
            code = getattr(page_error, "code", None)
            if code not in {"object_not_found", "validation_error"}:
                raise
            try:
                return self._request(self.client.databases.retrieve, database_id=object_id)
            except Exception:
                raise page_error

    def _scan_blocks(
        self, parent_id: str, ancestors: tuple[str, ...]
    ) -> Iterator[RemoteDocumentMetadata]:
        for block in self._paginate(self.client.blocks.children.list, block_id=parent_id):
            block_type = block.get("type")
            if block_type == "child_page":
                page = self._request(self.client.pages.retrieve, page_id=block["id"])
                yield self._page(page, ancestors)
                yield from self._scan_blocks(page["id"], (*ancestors, self._title(page)))
            elif block_type == "child_database":
                database = self._request(
                    self.client.databases.retrieve, database_id=block["id"]
                )
                yield from self._scan_database(
                    database, (*ancestors, self._title(database))
                )
            elif block.get("has_children"):
                # Layout blocks do not create a filesystem level, but pages and
                # databases nested inside them remain structural descendants.
                yield from self._scan_blocks(block["id"], ancestors)

    def _scan_database(
        self, database: dict[str, Any], ancestors: tuple[str, ...]
    ) -> Iterator[RemoteDocumentMetadata]:
        query = getattr(self.client.databases, "query", None)
        if query is not None:
            pages = self._paginate(query, database_id=database["id"])
        else:
            sources = database.get("data_sources", [])
            pages = (
                page
                for source in sources
                for page in self._paginate(
                    self.client.data_sources.query, data_source_id=source["id"]
                )
            )
        for page in pages:
            yield self._page(page, ancestors)
            yield from self._scan_blocks(page["id"], (*ancestors, self._title(page)))

    def _page(self, page: dict[str, Any], ancestors: tuple[str, ...]) -> RemoteDocumentMetadata:
        name = self._title(page)
        return RemoteDocumentMetadata(
            notion_id=page["id"],
            parent_id=self._parent_id(page.get("parent", {})),
            name=name,
            last_edited_time=page["last_edited_time"],
            properties=self._properties(page.get("properties", {})),
            ancestors=ancestors,
        )

    def fetch_markdown(self, notion_id: str) -> str:
        """Fetch and convert one changed page body."""
        # notion-to-md-py calls the SDK client directly. Temporarily wrap the
        # endpoint so its paginated block requests use the same limiter/retries.
        endpoint = self.client.blocks.children
        original = endpoint.list

        def limited_list(**kwargs: Any) -> dict[str, Any]:
            return self._request(original, **kwargs)

        endpoint.list = limited_list
        try:
            blocks = self.converter.page_to_markdown(notion_id)
        finally:
            endpoint.list = original
        return self.converter.to_markdown_string(blocks).get("parent", "").strip()

    def _request(self, method: Any, **kwargs: Any) -> Any:
        """Apply request pacing and retry only transient API failures."""
        attempt = 0
        while True:
            self._throttle()
            try:
                return method(**kwargs)
            except Exception as error:
                retry_after = self._retry_delay(error, attempt)
                exhausted = not self.retry_forever and attempt >= self.max_retries
                if retry_after is None or exhausted:
                    raise
                response = getattr(error, "response", None)
                status = getattr(response, "status_code", None)
                reason = "rate limited" if status == 429 else "temporarily unavailable"
                attempt += 1
                self._report(
                    SyncProgress(
                        "retry",
                        attempt,
                        None if self.retry_forever else self.max_retries,
                        f"{reason}; retrying in {retry_after:.1f}s (attempt {attempt})",
                    )
                )
                self._sleep(retry_after)

    def _throttle(self) -> None:
        if self.requests_per_second <= 0:
            return
        interval = 1.0 / self.requests_per_second
        now = self._clock()
        if self._last_request_at is not None:
            remaining = interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    @staticmethod
    def _retry_delay(error: Exception, attempt: int) -> float | None:
        # notion-client wraps its own timeout, while all native httpx timeout,
        # network, proxy, protocol, read/write, and close failures derive from
        # TransportError. Validation and authentication errors do not.
        if isinstance(error, (RequestTimeoutError, httpx.TransportError)):
            return NotionClientBackend._backoff(attempt)
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        code = getattr(error, "code", None)
        if status not in {429, 500, 502, 503, 504} and code != "rate_limited":
            return None
        retry_after = response.headers.get("retry-after") if response is not None else None
        try:
            return (
                max(float(retry_after), 0.1)
                if retry_after
                else NotionClientBackend._backoff(attempt)
            )
        except (TypeError, ValueError):
            return NotionClientBackend._backoff(attempt)

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Cap the exponent as well as the result so an unbounded retry loop
        # never constructs enormous integers after running for a long time.
        return min(2 ** min(attempt, 5), 30) + random.random()

    @staticmethod
    def _rich_text(items: list[dict[str, Any]]) -> str:
        return "".join(item.get("plain_text", "") for item in items)

    def _title(self, obj: dict[str, Any]) -> str:
        if obj.get("object") == "database":
            return self._rich_text(obj.get("title", [])) or "Untitled"
        for value in obj.get("properties", {}).values():
            if value.get("type") == "title":
                return self._rich_text(value.get("title", [])) or "Untitled"
        return "Untitled"

    def _properties(self, properties: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, prop in properties.items():
            kind = prop.get("type")
            if kind == "title":
                continue
            value = prop.get(kind)
            if kind == "rich_text":
                value = self._rich_text(value or [])
            elif kind in {"select", "status"}:
                value = value.get("name") if value else None
            elif kind == "multi_select":
                value = ", ".join(item["name"] for item in value or [])
            elif kind in {"people", "files", "relation", "rollup", "formula"}:
                value = str(value)
            elif kind == "date" and value:
                value = value.get("start")
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[name] = value
        return result

    @staticmethod
    def _parent_id(parent: dict[str, Any]) -> str | None:
        kind = parent.get("type")
        return parent.get(kind) if kind else None

    def _paginate(self, method: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            response = self._request(
                method, **kwargs, **({"start_cursor": cursor} if cursor else {})
            )
            yield from response.get("results", [])
            if not response.get("has_more"):
                return
            cursor = response["next_cursor"]
