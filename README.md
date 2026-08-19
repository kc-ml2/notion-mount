# notion-mount

**A read-only filesystem that uses Notion as the source of truth and materializes pages as local Markdown.**

`notion-mount` synchronizes a Notion page or database hierarchy to a local directory and optionally exposes that directory through a read-only FUSE mount. Synchronization is strictly one-way:

```text
Notion (source of truth) → local Markdown → read-only filesystem → applications
```

Local files are a materialized view. Editing, renaming, or deleting files does not update Notion and is not supported.

## Features

- Recursively scans Notion page and database hierarchies
- Converts page blocks with [`notion-to-md-py`](https://pypi.org/project/notion-to-md-py/)
- Stores page properties in YAML frontmatter
- Tracks document identity and synchronization state in SQLite
- Incrementally handles added, modified, renamed, moved, and deleted pages
- Uses safe path projection and atomic file replacement
- Provides a read-only FUSE mount
- Includes `init`, `sync`, `status`, and `mount` commands

## Notion-to-filesystem mapping

```text
Notion workspace/root  → workspace directory
Notion database        → directory
Notion page            → Markdown file
Page properties        → YAML frontmatter
Page blocks            → Markdown body
```

For example:

```text
Projects (database)
├── Arbiter (page)
└── Chatbot (page)
```

becomes:

```text
Projects/
├── Arbiter.md
└── Chatbot.md
```

## Metadata

Metadata is stored in two places, depending on its purpose.

### Document metadata: YAML frontmatter

Notion page properties are included at the top of each generated Markdown file. `notion_id` and `last_edited_time` are always added by `notion-mount` and cannot be overridden by page properties.

```markdown
---
notion_id: "abc123"
last_edited_time: "2026-08-18T12:00:00Z"
status: "In Progress"
priority: "High"
---
# Arbiter

Distributed task queue framework.
```

This metadata travels with the materialized document and is directly available to Markdown consumers.

### Synchronization metadata: SQLite

Internal synchronization state is stored in:

```text
<workspace>/.notion-mount/state.db
```

The `documents` table records:

- `notion_id` — stable document identity
- `parent_id` — Notion parent identity
- `name` — current Notion page title
- `local_path` — projected filesystem path
- `last_edited_time` — Notion edit timestamp
- `content_hash` — hash of the generated Markdown
- `sync_time` — most recent successful synchronization time

Filesystem paths are projections, not identities. A page remains the same document when it is renamed or moved because synchronization is keyed by `notion_id`.

### Configuration

Workspace configuration is stored in:

```text
<workspace>/.notion-mount/config.toml
```

It contains the root page/database ID and the name of the environment variable used for the token. The Notion token itself is **not** written to disk.

## Installation

```bash
python -m pip install -e .
```

To enable FUSE mounting:

```bash
python -m pip install -e '.[fuse]'
```

An operating-system FUSE implementation, such as macFUSE on macOS or libfuse on Linux, is also required.

## Setup and usage

Create a Notion integration, share the target root page or database with it, and initialize a workspace:

```bash
notion-mount init --root-page-id YOUR_ROOT_ID
export NOTION_TOKEN=secret_...
notion-mount sync
notion-mount status
```

Mount the synchronized workspace read-only:

```bash
notion-mount mount /mnt/notion
```

Use `--workspace PATH` before the subcommand to operate on another workspace:

```bash
notion-mount --workspace ./notion-workspace sync
```

## Read-only behavior

The mount supports read operations such as `getattr`, `readdir`, `open`, and `read`. Write access is rejected. The following operations are intentionally outside the project scope:

- Local-to-Notion synchronization
- Bidirectional synchronization
- Filesystem writes, renames, or deletes
- Conflict resolution
- Git-based change detection

If Notion write support is added in the future, it should use an explicit API rather than filesystem writes.

## Design

`SyncEngine` accepts a backend implementing the `NotionBackend` protocol, keeping synchronization, state, and local storage independent from the Notion SDK and FUSE.

Markdown body conversion is delegated to `notion-to-md-py`. Child pages are synchronized as separate files, so the converter uses `parse_child_pages=False` to avoid embedding duplicate child-page content in a parent document.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
```
