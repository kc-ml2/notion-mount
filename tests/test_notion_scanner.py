from notion_mount.notion import NotionClientBackend


class ChildrenAPI:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = responses

    def list(self, block_id: str, **kwargs: object) -> dict:
        return {
            "results": self.responses.get(block_id, []),
            "has_more": False,
            "next_cursor": None,
        }


class BlocksAPI:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.children = ChildrenAPI(responses)


class PagesAPI:
    def retrieve(self, page_id: str) -> dict:
        return {
            "object": "page",
            "id": page_id,
            "parent": {"type": "page_id", "page_id": "root"},
            "last_edited_time": "2026-01-01T00:00:00Z",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Nested page"}],
                }
            },
        }


class FakeClient:
    def __init__(self) -> None:
        self.blocks = BlocksAPI({
            "root": [{
                "id": "columns",
                "type": "column_list",
                "column_list": {},
                "has_children": True,
            }],
            "columns": [{
                "id": "column",
                "type": "column",
                "column": {},
                "has_children": True,
            }],
            "column": [{
                "id": "nested-page",
                "type": "child_page",
                "child_page": {"title": "Nested page"},
                "has_children": False,
            }],
        })
        self.pages = PagesAPI()


class FakeConverter:
    def page_to_markdown(self, page_id: str) -> list[dict]:
        return []

    def to_markdown_string(self, blocks: list[dict]) -> dict[str, str]:
        return {}


def test_scanner_finds_pages_nested_inside_layout_blocks() -> None:
    backend = object.__new__(NotionClientBackend)
    backend.client = FakeClient()
    backend.converter = FakeConverter()
    backend.requests_per_second = 0
    backend.max_retries = 0
    backend._sleep = lambda _: None
    backend._clock = lambda: 0.0
    backend._last_request_at = None
    backend._progress = None
    backend._scan_count = 0
    documents = []

    documents.extend(backend._scan_blocks("root", ("Root",)))

    assert [document.notion_id for document in documents] == ["nested-page"]
    assert documents[0].ancestors == ("Root",)
