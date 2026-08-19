from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

_INVALID_PATH = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(value: str, fallback: str = "Untitled") -> str:
    value = _INVALID_PATH.sub("_", value).strip().strip(".")
    return value or fallback


def projected_path(ancestors: tuple[str, ...], name: str) -> PurePosixPath:
    parts = [safe_name(part) for part in ancestors]
    return PurePosixPath(*parts, f"{safe_name(name)}.md")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_markdown(
    *, notion_id: str, last_edited_time: str, name: str,
    properties: dict[str, Any], body: str,
) -> str:
    # Internal identity fields are authoritative and cannot be overridden by a
    # Notion property with the same name.
    metadata = {**properties, "notion_id": notion_id, "last_edited_time": last_edited_time}
    frontmatter = "\n".join(f"{safe_name(str(key))}: {_yaml_scalar(value)}" for key, value in metadata.items())
    normalized_body = body.strip()
    title = f"# {name}"
    if not normalized_body.startswith(title):
        normalized_body = f"{title}\n\n{normalized_body}".rstrip()
    return f"---\n{frontmatter}\n---\n{normalized_body}\n"


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write(self, relative_path: PurePosixPath, content: str) -> None:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def matches_hash(
        self, relative_path: str | PurePosixPath, expected_hash: str
    ) -> bool:
        """Return whether the materialized file exists and matches stored state."""
        path = self._resolve(PurePosixPath(relative_path))
        try:
            content = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return False
        return hashlib.sha256(content).hexdigest() == expected_hash

    def delete(self, relative_path: str | PurePosixPath) -> None:
        path = self._resolve(PurePosixPath(relative_path))
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != self.root and parent.name != ".notion-mount":
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _resolve(self, relative_path: PurePosixPath) -> Path:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Unsafe local path: {relative_path}")
        path = self.root.joinpath(*relative_path.parts).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path escapes workspace: {relative_path}")
        return path
