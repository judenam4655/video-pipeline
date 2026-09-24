"""
Thin wrapper around the official MCP Python client, so the orchestrator can
call tools on the Premiere bridge server (Node, stdio) and the transcriber
server (Python, stdio) without repeating boilerplate at each call site.

Both servers are launched as subprocesses and talked to over stdio, which is
the standard local MCP transport — no ports, no networking config needed for
this leg (the Node server's own WebSocket-to-UXP leg is separate and
internal to that process).
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
        return "\n".join(text_parts)

    async def call_tool_json(self, name: str, arguments: dict) -> dict:
        raw = await self.call_tool(name, arguments)
        return json.loads(raw)


class McpServers:
    """
    Usage:
        async with McpServers() as servers:
            info = await servers.premiere.call_tool_json("get_active_sequence_info", {})
            transcript = await servers.transcriber.call_tool_json("transcribe", {...})
    """

    def __init__(self):
        self._stack = AsyncExitStack()
        self.premiere: McpServerHandle | None = None
        self.transcriber: McpServerHandle | None = None

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

        transcriber_params = StdioServerParameters(
            command="python3",
            args=[str(PROJECT_ROOT / "transcriber-mcp-server" / "server.py")],
        )
        transcriber_read, transcriber_write = await self._stack.enter_async_context(
            stdio_client(transcriber_params)
        )
        transcriber_session = await self._stack.enter_async_context(
            ClientSession(transcriber_read, transcriber_write)
        )
        await transcriber_session.initialize()
        self.transcriber = McpServerHandle(transcriber_session)

        return self

    async def __aexit__(self, *exc):
        await self._stack.__aexit__(*exc)
