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

  // Watchdog: if neither onopen nor onclose/onerror fires within a few
  // seconds, the connection is likely being silently blocked by a UXP
  // manifest network-permission mismatch (e.g. a declared domain scheme
  // that doesn't match ws:// exactly) rather than a real network failure —
  // that failure mode does not reliably fire onerror/onclose on all UXP
  // builds, so without this the panel just sits on "starting…" forever
  // with no diagnostic at all.
  const socketAtStart = ws;
  const watchdog = setTimeout(() => {
    if (ws === socketAtStart && ws.readyState === WebSocket.CONNECTING) {
      log(
        "Still stuck connecting after 5s with no open/error/close event — " +
        "this usually means UXP is silently blocking the socket at the " +
        "manifest network-permission layer (check requiredPermissions." +
        "network.domains matches the ws:// scheme exactly), not that the " +
        "bridge server is unreachable."
      );
      setStatus("stuck — check manifest network permissions", false);
    }
  }, 5000);

  ws.onopen = () => {
    clearTimeout(watchdog);
    setStatus("connected", true);
    log("Connected to MCP bridge server.");
  };

  ws.onclose = () => {
    clearTimeout(watchdog);
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
  // Verified against developer.adobe.com/premiere-pro/uxp/ppro-reference
  // (Markers, Marker, TickTime, CompoundAction class pages) plus the
  // lockedAccess()/executeTransaction() pattern confirmed working in an
  // Adobe community thread for the sibling createInsertProjectItemAction API
  // (same execution pattern applies to all create*Action calls, markers
  // included): https://community.adobe.com/t5/premiere-pro-discussions/
  // help-with-the-new-uxp-createinsertprojectitemaction-method
  const ppro = require("premierepro");
  const { startSeconds, endSeconds, ruleType, reasoning } = params;

  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("No active Premiere project.");

  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("No active sequence. Open a sequence in the timeline first.");

  // Markers.getMarkers is a STATIC method here — confusingly, Markers also
  // has an instance method of the same name that lists existing markers.
  // Static form: ppro.Markers.getMarkers(sequenceOrClipProjectItem) -> Markers
  const markers = await ppro.Markers.getMarkers(sequence);

  const startTime = ppro.TickTime.createWithSeconds(startSeconds);
  const duration = ppro.TickTime.createWithSeconds(Math.max(0, endSeconds - startSeconds));
  const name = ruleTypeLabel(ruleType);

  let addedOk = false;
  project.lockedAccess(() => {
    addedOk = project.executeTransaction((compoundAction) => {
      const addAction = markers.createAddMarkerAction(
        name,
        "Comment", // VERIFY: accepted marker-type strings aren't enumerated
                   // in Adobe's docs as of this writing. "Comment" is the
                   // conventional default marker type; if Premiere rejects
                   // it, try omitting this param (it's optional) first.
        startTime,
        duration,
        reasoning
      );
      compoundAction.addAction(addAction);
    }, `AI flag: ${name}`);
  });

  if (!addedOk) {
    throw new Error("Premiere rejected the marker-add transaction (executeTransaction returned false).");
  }

  // Best-effort color coding. UNCONFIRMED: Adobe's docs don't state whether
  // the newly created marker is reliably the last entry in
  // markers.getMarkers() (the instance method, plural markers back) — this
  // is a known open question in Adobe's own developer community as of early
  // 2026. Wrapped so a color failure never blocks the marker itself, since
  // the marker (name + comments, already placed above) is the part your
  // human reviewer actually needs.
  try {
    const existing = markers.getMarkers(); // instance method: Marker[]
    const created = existing[existing.length - 1];
    if (created) {
      const colorIndex = colorIndexForRule(ruleType);
      project.lockedAccess(() => {
        project.executeTransaction((compoundAction) => {
          compoundAction.addAction(created.createSetColorByIndexAction(colorIndex));
        }, `Set color for: ${name}`);
      });
    }
  } catch (colorErr) {
    log(`placeMarker: marker placed, but color-coding failed (non-fatal): ${colorErr.message || colorErr}`);
  }

  return { placed: true, name, startSeconds, endSeconds, ruleType };
}

function ruleTypeLabel(ruleType) {
  switch (ruleType) {
    case "repetition": return "AI: Repetition";
    case "context_drift": return "AI: Context drift";
    case "explicit_request": return "AI: Delete requested";
    case "ppt_typo_reexplain": return "AI: Slide-typo re-explain";
    default: return `AI: ${ruleType}`;
  }
}

function colorIndexForRule(ruleType) {
  // UNVERIFIED: Adobe does not publicly document which colorIndex number
  // maps to which displayed marker color. Before trusting this mapping,
  // create one marker of each color manually in Premiere's UI, then read
  // it back (markers.getMarkers()[i]) in the UDT debugger console to see
  // what index each color actually reports.
  switch (ruleType) {
    case "repetition": return 0;
    case "context_drift": return 1;
    case "explicit_request": return 2;
    case "ppt_typo_reexplain": return 3;
    default: return 0;
  }
}

async function getActiveSequenceInfo() {
  // Verified against developer.adobe.com/premiere-pro/uxp/ppro-reference
  // (Sequence, SequenceSettings, TickTime, FrameRate class pages).
  const ppro = require("premierepro");

  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("No active Premiere project.");

  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("No active sequence. Open a sequence in the timeline first.");

  const [endTime, settings, videoTrackCount, audioTrackCount] = await Promise.all([
    sequence.getEndTime(),          // Promise<TickTime>
    sequence.getSettings(),         // Promise<SequenceSettings>
    sequence.getVideoTrackCount(),  // Promise<number>
    sequence.getAudioTrackCount(),  // Promise<number>
  ]);

  // getVideoFrameRate() returns a FrameRate object; .value is frames/sec.
  const frameRate = await settings.getVideoFrameRate();

  return {
    name: sequence.name, // plain property, no await needed
    durationSeconds: endTime.seconds, // TickTime.seconds is a plain property
    frameRate: frameRate.value,
    videoTrackCount,
    audioTrackCount,
  };
}

/* ---------------------------------------------------------------------- */

connect();