from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

META_DIR = ".notion-mount"
CONFIG_FILE = "config.toml"
STATE_FILE = "state.db"


@dataclass(frozen=True, slots=True)
class Config:
    workspace: Path
    root_page_id: str
    token_env: str = "NOTION_TOKEN"

    @property
    def metadata_dir(self) -> Path:
        return self.workspace / META_DIR

    @property
    def state_path(self) -> Path:
        return self.metadata_dir / STATE_FILE

    @property
    def token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise ValueError(f"Environment variable {self.token_env} is not set")
        return token

    @classmethod
    def load(cls, workspace: Path) -> Config:
        workspace = workspace.resolve()
        path = workspace / META_DIR / CONFIG_FILE
        if not path.exists():
            raise FileNotFoundError(f"Configuration not found: {path}; run 'notion-mount init'")
        with path.open("rb") as file:
            data = tomllib.load(file)
        notion = data.get("notion", {})
        root_page_id = str(notion.get("root_page_id", "")).strip()
        if not root_page_id:
            raise ValueError(f"notion.root_page_id is required in {path}")
        return cls(
            workspace=workspace,
            root_page_id=root_page_id,
            token_env=str(notion.get("token_env", "NOTION_TOKEN")),
        )


def initialize(workspace: Path, root_page_id: str, token_env: str = "NOTION_TOKEN") -> Path:
    workspace = workspace.resolve()
    metadata = workspace / META_DIR
    metadata.mkdir(parents=True, exist_ok=True)
    path = metadata / CONFIG_FILE
    if path.exists():
        raise FileExistsError(f"Configuration already exists: {path}")
    path.write_text(
        "[notion]\n"
        f'root_page_id = "{root_page_id}"\n'
        f'token_env = "{token_env}"\n',
        encoding="utf-8",
    )
    return path
