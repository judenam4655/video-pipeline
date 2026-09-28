"""
Thin wrapper around the official MCP Python client, so the orchestrator can
call tools on the Premiere bridge server (Node, stdio) without repeating
boilerplate at each call site.

The server is launched as a subprocess and talked to over stdio, which is
the standard local MCP transport — no ports, no networking config needed for
this leg (the Node server's own WebSocket-to-UXP leg is separate and
internal to that process).

NOTE: this used to also launch a transcriber-mcp-server subprocess, back
when transcription required a manually-exported audio file passed to a
separate Transcriber MCP tool. That's gone now that get_transcript reads
directly from Premiere's Project panel — removed here rather than left
unwired, since keeping a whole extra subprocess alive per job for no
caller is real overhead, not just dead code sitting still.
"""

import json
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).parent.parent


class McpServerHandle:
    """One connected MCP server + a convenience call_tool() method."""

    def __init__(self, session: ClientSession):
        self.session = session

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.session.call_tool(name, arguments=arguments)
        # MCP tool results are a list of content blocks; we're only using
        # plain text blocks (JSON-encoded strings) in this project.
        text_parts = [block.text for block in result.content if hasattr(block, "text")]
        text = "\n".join(text_parts)
        # Field name differs across mcp SDK versions (isError vs is_error).
        if getattr(result, "is_error", None) or getattr(result, "isError", None):
            raise RuntimeError(f"MCP tool '{name}' failed: {text}")
        return text

    async def call_tool_json(self, name: str, arguments: dict) -> dict:
        raw = await self.call_tool(name, arguments)
        return json.loads(raw)


class McpServers:
    """
    Usage:
        async with McpServers() as servers:
            info = await servers.premiere.call_tool_json("get_active_sequence_info", {})
            transcript = await servers.premiere.call_tool_json("get_transcript", {})
    """

    def __init__(self):
        self._stack = AsyncExitStack()
        self.premiere: McpServerHandle | None = None

    async def __aenter__(self):
        await self._stack.__aenter__()

        premiere_params = StdioServerParameters(
            command="node",
            args=[str(PROJECT_ROOT / "premiere-mcp-server" / "index.js")],
        )
        premiere_read, premiere_write = await self._stack.enter_async_context(
            stdio_client(premiere_params)
        )
        premiere_session = await self._stack.enter_async_context(
            ClientSession(premiere_read, premiere_write)
        )
        await premiere_session.initialize()
        self.premiere = McpServerHandle(premiere_session)

        return self

    async def __aexit__(self, *exc):
        await self._stack.__aexit__(*exc)