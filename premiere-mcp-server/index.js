/**
 * premiere-bridge-mcp
 *
 * Two jobs in one process:
 *  1. Hosts a local WebSocket server that the Premiere UXP panel connects to
 *     as a client. Commands get sent to the panel; results come back.
 *  2. Exposes MCP tools (sync_clips, remove_fillers, place_marker, ...) over
 *     stdio, so your Python orchestrator (or Claude Desktop, for testing)
 *     can call them like any other MCP tool.
 *
 * Run standalone for testing:   npm start
 * Then in Claude Desktop's config, point an MCP server entry at this file
 * to test tool calls manually before wiring up the Python orchestrator.
 */

import { WebSocketServer } from "ws";
import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const WS_PORT = 39281;
const COMMAND_TIMEOUT_MS = 30_000;

/* ------------------------------------------------------------------ */
/* WebSocket side: talks to the UXP panel                              */
/* ------------------------------------------------------------------ */

let panelSocket = null;
const pending = new Map(); // requestId -> { resolve, reject, timeout }

const wss = new WebSocketServer({ port: WS_PORT });

wss.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    console.error(
      `[bridge] Port ${WS_PORT} is already in use — likely a previous ` +
      `instance of this server is still running. Find and stop it (e.g. ` +
      `'lsof -i :${WS_PORT}' on macOS/Linux, 'netstat -ano | findstr ${WS_PORT}' ` +
      `on Windows) before starting a new one.`
    );
  } else {
    console.error(`[bridge] WebSocket server error: ${err.message || err}`);
  }
  process.exit(1);
});

wss.on("connection", (socket) => {
  console.error(`[bridge] UXP panel connected on port ${WS_PORT}`);
  panelSocket = socket;

  socket.on("message", (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      console.error("[bridge] received malformed message from panel, ignoring");
      return;
    }
    const entry = pending.get(msg.requestId);
    if (!entry) return; // stale or unknown response, ignore
    clearTimeout(entry.timeout);
    pending.delete(msg.requestId);
    if (msg.ok) entry.resolve(msg.result);
    else entry.reject(new Error(msg.error || "Unknown panel error"));
  });

  socket.on("close", () => {
    console.error("[bridge] UXP panel disconnected");
    if (panelSocket === socket) panelSocket = null;
  });
});

/**
 * Sends a command to the connected UXP panel and waits for its response.
 * Rejects if no panel is connected, or if the panel doesn't respond in time.
 */
function sendToPanel(action, params = {}) {
  return new Promise((resolve, reject) => {
    if (!panelSocket || panelSocket.readyState !== panelSocket.OPEN) {
      reject(new Error("No Premiere UXP panel connected. Is Premiere open with the MCP Bridge panel loaded?"));
      return;
    }

    const requestId = randomUUID();
    const timeout = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error(`Timed out waiting for Premiere to respond to '${action}'`));
    }, COMMAND_TIMEOUT_MS);

    pending.set(requestId, { resolve, reject, timeout });
    panelSocket.send(JSON.stringify({ requestId, action, params }));
  });
}

/* ------------------------------------------------------------------ */
/* MCP side: talks to the orchestrator                                 */
/* ------------------------------------------------------------------ */

const server = new McpServer({ name: "premiere-bridge", version: "0.1.0" });

server.tool(
  "ping_premiere",
  "Checks whether the UXP panel is connected and responsive.",
  {},
  async () => {
    const result = await sendToPanel("ping");
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  }
);

server.tool(
  "get_active_sequence_info",
  "Returns basic info about the currently active Premiere sequence (name, duration, frame rate). Good smoke test before running real operations.",
  {},
  async () => {
    const result = await sendToPanel("get_active_sequence_info");
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  }
);

server.tool(
  "sync_clips",
  "Synchronizes 3 raw clips by audio waveform into one multi-cam or merged sequence.",
  { clipPaths: z.array(z.string()).length(3, "Expects exactly 3 clip file paths") },
  async ({ clipPaths }) => {
    const result = await sendToPanel("sync_clips", { clipPaths });
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  }
);

server.tool(
  "remove_fillers",
  "Runs Premiere's native filler-word/silence detection and cut on the given sequence.",
  { sequenceId: z.string() },
  async ({ sequenceId }) => {
    const result = await sendToPanel("remove_fillers", { sequenceId });
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  }
);

server.tool(
  "place_marker",
  "Places a non-destructive, color-coded timeline marker for a flagged deletion candidate, without cutting anything.",
  {
    startSeconds: z.number(),
    endSeconds: z.number(),
    ruleType: z.enum(["repetition", "context_drift", "explicit_request", "ppt_typo_reexplain"]),
    reasoning: z.string(),
  },
  async (params) => {
    const result = await sendToPanel("place_marker", params);
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  }
);

/* ------------------------------------------------------------------ */

console.error(`[bridge] WebSocket server listening on ws://localhost:${WS_PORT} for the UXP panel`);
console.error("[bridge] MCP server starting on stdio for the orchestrator/Claude...");

const transport = new StdioServerTransport();
await server.connect(transport);