"""File-based memory service. No vector DB. No embeddings.

Layout: ~/.empyralis/v2/memory/<workspace>/<agent>/<topic>.md
Each file: frontmatter (name, description, type) + body.
Index: index.md with one-line per memory.

Exposed as 3 tool-callable functions: list, read, write."""

import os
from pathlib import Path

MEMORY_ROOT = Path.home() / ".empyralis" / "v2" / "memory"

# ── tool definitions ──────────────────────────────────────────────────────────

MEMORY_LIST_TOOL = {
    "name": "memory_list",
    "description": "List all memories. Returns the index with one-line descriptions.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

MEMORY_READ_TOOL = {
    "name": "memory_read",
    "description": "Read the full content of a memory by name (slug).",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name (kebab-case slug, e.g. 'favorite-color')",
            },
        },
        "required": ["name"],
    },
}

MEMORY_WRITE_TOOL = {
    "name": "memory_write",
    "description": "Create or update a memory. Writes a file with frontmatter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name (kebab-case slug, e.g. 'favorite-color')",
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what this memory contains",
            },
            "content": {
                "type": "string",
                "description": "The memory body (Markdown). Include a **Why:** line.",
            },
        },
        "required": ["name", "description", "content"],
    },
}

ALL_MEMORY_TOOLS = [MEMORY_LIST_TOOL, MEMORY_READ_TOOL, MEMORY_WRITE_TOOL]

# ── implementation ────────────────────────────────────────────────────────────


def _resolve(workspace: str, agent: str, name: str = "") -> Path:
    return MEMORY_ROOT / workspace / agent / (f"{name}.md" if name else "")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def memory_list(workspace: str = "default", agent: str = "sage") -> str:
    """Return index of all memories for workspace/agent."""
    index_path = _resolve(workspace, agent, "index")
    if not index_path.exists():
        return "(no memories yet)"
    return index_path.read_text()


def memory_read(name: str, workspace: str = "default", agent: str = "sage") -> str:
    """Return full content of one memory."""
    path = _resolve(workspace, agent, name)
    if not path.exists():
        return f"(no memory named '{name}')"
    return path.read_text()


def memory_write(
    name: str,
    description: str,
    content: str,
    workspace: str = "default",
    agent: str = "sage",
) -> str:
    """Create or update a memory file and update the index."""
    agent_dir = _resolve(workspace, agent)
    _ensure_dir(agent_dir)

    # --- write the memory file ---
    fm = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n"
    mem_path = agent_dir / f"{name}.md"
    mem_path.write_text(fm)

    # --- update the index ---
    index_path = agent_dir / "index.md"
    lines: list[str] = []
    seen: set[str] = set()
    if index_path.exists():
        for line in index_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("- ["):
                # Extract name from markdown link: "- [Title](file.md) — desc"
                try:
                    ln = stripped.split("](")[1].split(".md")[0]
                    seen.add(ln)
                except IndexError:
                    pass
                lines.append(line)

    if name not in seen:
        lines.append(f"- [{name}]({name}.md) — {description}")

    index_path.write_text("\n".join(lines) + "\n")

    return f"Memory '{name}' saved."
