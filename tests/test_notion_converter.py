from notion_mount.notion import NotionClientBackend


class FakeConverter:
    def __init__(self) -> None:
        self.page_id = None
        self.blocks = [{"type": "paragraph", "parent": "converted"}]

    def page_to_markdown(self, page_id: str) -> list[dict[str, str]]:
        self.page_id = page_id
        return self.blocks

    def to_markdown_string(self, blocks: list[dict[str, str]]) -> dict[str, str]:
        assert blocks is self.blocks
        return {"parent": "\n**converted by notion-to-md-py**\n"}


def test_page_body_uses_injected_notion_to_md_converter() -> None:
    backend = object.__new__(NotionClientBackend)
    backend.converter = FakeConverter()

    markdown = backend._blocks_to_markdown("page-id")

    assert backend.converter.page_id == "page-id"
    assert markdown == "**converted by notion-to-md-py**"
