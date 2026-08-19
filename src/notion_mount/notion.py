from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterator

from .models import RemoteDocumentMetadata, SyncProgress


class NotionClientBackend:
    """Notion backend using notion-sdk-py's synchronous client.

    The integration must be shared with the configured root page/database. The
    scanner recursively follows child pages and databases visible to it.
    """

    def __init__(self, token: str) -> None:
        try:
            from notion_client import Client
            from notion_to_md import NotionToMarkdown
        except ImportError as error:
            raise RuntimeError("Reinstall notion-mount to restore its Notion dependencies") from error
        self.client = Client(auth=token)
        # Child pages are synchronized as independent filesystem documents, so
        # they must not also be embedded into their parent's Markdown body.
        self.converter = NotionToMarkdown(
            self.client,
            config={"parse_child_pages": False, "convert_images_to_base64": False},
        )

    def scan(
        self,
        root_page_id: str,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> list[RemoteDocumentMetadata]:
        """Collect the remote inventory without converting page bodies."""
        root = self._retrieve(root_page_id)
        documents: list[RemoteDocumentMetadata] = []
        self._progress = progress
        self._scan_count = 0
        if root["object"] == "page":
            root_name = self._title(root)
            documents.append(self._page(root, ()))
            self._scan_blocks(root["id"], (root_name,), documents)
        else:
            self._scan_database(root, (self._title(root),), documents)
        return documents

    def _report_scan(self, name: str) -> None:
        progress = getattr(self, "_progress", None)
        if progress:
            self._scan_count = getattr(self, "_scan_count", 0) + 1
            progress(SyncProgress("scan", self._scan_count, name=name))

    def _retrieve(self, object_id: str) -> dict[str, Any]:
        try:
            return self.client.pages.retrieve(page_id=object_id)
        except Exception as page_error:
            try:
                return self.client.databases.retrieve(database_id=object_id)
            except Exception:
                raise page_error

    def _scan_blocks(
        self, parent_id: str, ancestors: tuple[str, ...], output: list[RemoteDocumentMetadata]
    ) -> None:
        for block in self._paginate(self.client.blocks.children.list, block_id=parent_id):
            block_type = block.get("type")
            if block_type == "child_page":
                page = self.client.pages.retrieve(page_id=block["id"])
                output.append(self._page(page, ancestors))
                self._scan_blocks(page["id"], (*ancestors, self._title(page)), output)
            elif block_type == "child_database":
                database = self.client.databases.retrieve(database_id=block["id"])
                self._scan_database(database, (*ancestors, self._title(database)), output)
            elif block.get("has_children"):
                # Structural blocks such as columns, toggles, and synced blocks
                # can contain child pages/databases. They do not create a
                # filesystem level, but their descendants must still be found.
                self._scan_blocks(block["id"], ancestors, output)

    def _scan_database(
        self, database: dict[str, Any], ancestors: tuple[str, ...], output: list[RemoteDocumentMetadata]
    ) -> None:
        # Notion API 2025-09-03 moved database queries to data sources. Keep
        # compatibility with both notion-client generations.
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
            output.append(self._page(page, ancestors))
            self._scan_blocks(page["id"], (*ancestors, self._title(page)), output)

    def _page(self, page: dict[str, Any], ancestors: tuple[str, ...]) -> RemoteDocumentMetadata:
        name = self._title(page)
        self._report_scan(name)
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
        return self._blocks_to_markdown(notion_id)

    def _blocks_to_markdown(self, page_id: str) -> str:
        blocks = self.converter.page_to_markdown(page_id)
        return self.converter.to_markdown_string(blocks).get("parent", "").strip()

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
            if kind in {"rich_text"}:
                value = self._rich_text(value or [])
            elif kind in {"select", "status"}:
                value = value.get("name") if value else None
            elif kind == "multi_select":
                value = ", ".join(item["name"] for item in value or [])
            elif kind in {"people", "files", "relation", "rollup", "formula"}:
                # Complex values remain deterministic and human-readable.
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

    @staticmethod
    def _paginate(method: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            response = method(**kwargs, **({"start_cursor": cursor} if cursor else {}))
            yield from response.get("results", [])
            if not response.get("has_more"):
                return
            cursor = response["next_cursor"]
