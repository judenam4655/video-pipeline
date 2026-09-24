/**
 * UXP panel main script.
 *
 * IMPORTANT — read before building on this:
 * The exact Premiere UXP scripting API (module names, method names, object
 * shapes) has been actively evolving in Adobe's rollout of UXP scripting for
 * Premiere Pro (replacing the older ExtendScript/CEP API). The calls marked
 * with "// VERIFY:" below are my best understanding of the shape of that API
 * but you MUST check them against Adobe's current Premiere Pro UXP scripting
 * reference (inside Premiere: Help > UXP Scripting Guide, or Adobe's
 * developer docs site) before trusting them. Treat this file as a structural
 * skeleton, not a verified-working implementation.
 *
 * Architecture: this panel does NOT host a server. It connects OUT to the
 * Node MCP server (premiere-mcp-server) over WebSocket, since UXP's sandbox
 * is much more reliable for outbound connections than for hosting an inbound
 * listener. The Node server sends command messages; this panel executes them
 * against Premiere and sends back a result.
 */

const BRIDGE_URL = "ws://localhost:39281";
const RECONNECT_DELAY_MS = 2000;

const logEl = document.getElementById("log");
const statusEl = document.getElementById("status");

function log(msg) {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.textContent += line + "\n";
  logEl.scrollTop = logEl.scrollHeight;
  console.log(line);
}

function setStatus(text, ok) {
  statusEl.textContent = `Bridge server: ${text}`;
  statusEl.style.color = ok ? "#7CFC00" : "#ff6b6b";
}

let ws = null;

function connect() {
  ws = new WebSocket(BRIDGE_URL);

  ws.onopen = () => {
    setStatus("connected", true);
    log("Connected to MCP bridge server.");
  };

  ws.onclose = () => {
    setStatus("disconnected — retrying…", false);
    log("Disconnected from bridge. Retrying in " + RECONNECT_DELAY_MS + "ms");
    setTimeout(connect, RECONNECT_DELAY_MS);
  };

  ws.onerror = (err) => {
    log("WebSocket error: " + JSON.stringify(err));
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      log("Received non-JSON message, ignoring.");
      return;
    }

    const { requestId, action, params } = msg;
    log(`Received command: ${action} (id=${requestId})`);

    try {
      const result = await handleAction(action, params || {});
      ws.send(JSON.stringify({ requestId, ok: true, result }));
      log(`Completed: ${action}`);
    } catch (err) {
      log(`Error handling ${action}: ${err.message || err}`);
      ws.send(JSON.stringify({ requestId, ok: false, error: String(err.message || err) }));
    }
  };
}

/**
 * Dispatches a command from the orchestrator to the correct Premiere action.
 */
async function handleAction(action, params) {
  switch (action) {
    case "ping":
      return { pong: true };

    case "sync_clips":
      return await syncClips(params.clipPaths);

    case "remove_fillers":
      return await removeFillers(params.sequenceId);

    case "place_marker":
      return await placeMarker(params);

    case "get_active_sequence_info":
      return await getActiveSequenceInfo();

    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

/* ---------------------------------------------------------------------- */
/* Premiere-specific implementations below. These are the parts you'll    */
/* need to fill in / correct against the real API while testing inside    */
/* Premiere with the UXP Developer Tool's console open for debugging.     */
/* ---------------------------------------------------------------------- */

async function syncClips(clipPaths) {
  // VERIFY: module import path/shape for the Premiere UXP scripting API.
  // As of Adobe's UXP rollout this is typically accessed via:
  //   const premierepro = require("premierepro");
  //   const app = await premierepro.Application.getActiveObject? or similar
  // The exact multi-clip "synchronize by audio" action may only be exposed
  // via a project-panel command / menu command ID rather than a first-class
  // API method — if so, you may need Application's `executeMenuCommand` /
  // equivalent, importing the clips into a bin first, selecting them, then
  // invoking the sync command programmatically. Confirm this against docs;
  // it's the least certain part of this whole bridge.
  const premierepro = require("premierepro");

  // 1. Import the 3 raw clips into the active project (if not already).
  // 2. Select them in the Project panel.
  // 3. Trigger "Synchronize > Audio" — this may require executeMenuCommand
  //    with the correct command id, since it's a legacy UI action.
  // 4. Return info about the resulting synced sequence/clip.

  throw new Error(
    "syncClips: not yet implemented — fill in against verified Premiere UXP API. See comments above."
  );
}

async function removeFillers(sequenceId) {
  // VERIFY: Premiere's native filler-word/silence detection ("Text-Based
  // Editing" panel) may not currently be exposed as a scriptable UXP action
  // at all — parts of Premiere's newer AI features have historically lagged
  // behind in scripting API coverage. If there's no scriptable hook, the
  // fallback is: this step stays a manual click for now (mother triggers it
  // in the UI), and this MCP tool becomes a no-op / status-check instead of
  // an automated trigger. Confirm feasibility before assuming this can be
  // fully automated end-to-end.
  throw new Error(
    "removeFillers: verify whether Premiere's filler-word detection is UXP-scriptable before implementing."
  );
}

async function placeMarker(params) {
  // This one is the most likely to have solid API support — markers are a
  // long-standing, well-documented part of Premiere's scripting surface.
  const premierepro = require("premierepro");

  const { startSeconds, endSeconds, ruleType, reasoning, colorLabel } = params;

  // VERIFY exact call shape, roughly:
  // const project = await premierepro.Project.getActiveProject();
  // const sequence = await project.getActiveSequence();
  // const markers = sequence.getMarkers();
  // const newMarker = await markers.createMarker(startSeconds); // in ticks/seconds per API
  // newMarker.name = ruleType;
  // newMarker.comments = reasoning;
  // newMarker.duration = endSeconds - startSeconds;
  // newMarker.setColorByIndex(colorForRule(ruleType));

  throw new Error(
    "placeMarker: not yet implemented — this is the highest-priority piece to verify first, since markers are your core deliverable."
  );
}

async function getActiveSequenceInfo() {
  const premierepro = require("premierepro");
  // VERIFY: return basic info (name, duration, frame rate) — useful as your
  // first working end-to-end test before tackling sync/fillers/markers.
  throw new Error("getActiveSequenceInfo: not yet implemented — good first call to get working.");
}

/* ---------------------------------------------------------------------- */

connect();
