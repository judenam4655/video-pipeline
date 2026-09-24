# 영상 자동 편집 파이프라인 — Beta Skeleton

## What this is

A working *skeleton* of the full pipeline: sync → filler-cut → transcribe →
AI-detected deletion candidates → non-destructive Premiere markers. The
plumbing (state checkpointing, MCP wiring, webpage, prompt for the removal
agent) is real and runnable. The parts that touch Premiere's actual
scripting API are **stubbed with clear TODOs**, because that API surface
needs verification against Adobe's current docs before it can be trusted —
see `premiere-uxp-panel/main.js` for exactly what's uncertain and why.

## What's real vs. stub, honestly

| Piece | Status |
|---|---|
| Orchestrator (state checkpointing, pipeline sequencing) | ✅ Functional |
| Control webpage (upload/start/status) | ✅ Functional |
| Removal agent (Claude API call, prompt, JSON parsing) | ✅ Functional — test independently against a real transcript first |
| Node MCP server (WebSocket bridge + MCP tool definitions) | ✅ Functional structure, tools just forward to the panel |
| UXP panel (WebSocket client, command dispatch) | ✅ Functional structure |
| UXP panel's actual Premiere API calls (sync/filler/marker) | ⚠️ **Stubbed — throws `NotImplementedError`.** Needs real API calls filled in against verified Adobe UXP docs |
| Transcriber MCP server | ⚠️ **Stubbed — needs to know what your actual local transcriber is** (CLI? Python lib? Something else?) |

## Setup order (do this, don't skip ahead)

1. **Node MCP server first, standalone.**
   ```
   cd premiere-mcp-server
   npm install
   npm start
   ```
   You should see `[bridge] WebSocket server listening on ws://localhost:39281`.

2. **Load the UXP panel in Premiere.**
   Open Premiere Pro, use the UXP Developer Tool to load
   `premiere-uxp-panel/manifest.json` as a plugin, open its panel from
   Window > Extensions. You should see the panel log "Connected to MCP
   bridge server" — that confirms the WebSocket leg works end-to-end
   *before* you touch any real Premiere API calls.

3. **Fill in `main.js`'s Premiere calls one at a time**, starting with
   `getActiveSequenceInfo` (simplest, good smoke test), then `placeMarker`
   (your actual deliverable, and markers are well-documented in Premiere's
   scripting API), then `syncClips` and `removeFillers` last (most
   uncertain — may need `executeMenuCommand`-style calls, or may turn out
   `remove_fillers` isn't scriptable at all, in which case that step reverts
   to a manual click and the orchestrator just skips/confirms it).

4. **Test each Premiere MCP tool from Claude Desktop directly** before
   wiring the Python orchestrator in — add `premiere-mcp-server/index.js`
   as an MCP server in Claude Desktop's config, and call
   `get_active_sequence_info` / `place_marker` manually. This isolates
   Premiere-API bugs from orchestrator bugs.

5. **Wire up the transcriber** — tell me what your existing Mac transcriber
   actually is (CLI tool? Python library? App with only a GUI?) and I'll
   fill in `transcriber-mcp-server/server.py` for real instead of leaving
   the OPTION A / OPTION B placeholder.

6. **Test the removal agent independently**, before the full pipeline —
   feed `removal_agent.detect_deletions()` a real transcript (even a
   hand-typed fake one) and check the flagged segments make sense, before
   trusting it inside the full run.

7. **Only then run the full orchestrator:**
   ```
   cd orchestrator
   pip install -r requirements.txt
   export ANTHROPIC_API_KEY=sk-...
   uvicorn main:app --reload --port 8000
   ```
   Open `http://localhost:8000`.

## On the sketchy npm packages

Do not install anything named `premiere-pro-mcp` from npm — see the earlier
conversation for why (many near-identical repos across different accounts,
a strong sign of package-squatting). Everything in this project is written
from scratch using only the official `@modelcontextprotocol/sdk` and
`anthropic` packages.
