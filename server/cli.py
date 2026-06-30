"""Test harness. Run: python -m server.cli "your message"

Wires Agent + shell + memory tools, runs the loop, prints response."""

import sys
from functools import partial

from server.agent import Agent, Runner
from server.tools.shell import run as shell_run, TOOL_DEF as SHELL_TOOL
from server.memory.service import (
    memory_list,
    memory_read,
    memory_write,
    ALL_MEMORY_TOOLS,
)

SAGE_INSTRUCTIONS = """You are Sage, the Empyralis assistant.

You act on your own reasoning — there are no approval gates. You have access to
shell and memory tools. Use them when they help answer the user's request.

When running shell commands:
- Prefer listing and reading over destructive actions
- Explain what you're doing before running commands that modify state
- The working directory is the user's home directory

When using memory:
- Check memory_list() before answering preference questions
- Write important facts the user shares to memory_write()
- Use memory_read() to recall specific stored facts

Be concise. Don't ask permission — just act."""

SAGE_MANIFEST = Agent(
    name="Sage",
    instructions=SAGE_INSTRUCTIONS,
    tools=[SHELL_TOOL] + ALL_MEMORY_TOOLS,
    model="claude-sonnet-4-6",
)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m server.cli \"your message\"")
        sys.exit(1)

    message = sys.argv[1]
    agent = SAGE_MANIFEST
    runner = Runner(agent)

    # Register tools — bind infrastructure params so LLM doesn't see them
    runner.register("shell", shell_run)
    runner.register("memory_list", partial(memory_list, workspace="default", agent="sage"))
    runner.register("memory_read", partial(memory_read, workspace="default", agent="sage"))
    runner.register("memory_write", partial(memory_write, workspace="default", agent="sage"))

    response = runner.run(message)
    print(response)


if __name__ == "__main__":
    main()
