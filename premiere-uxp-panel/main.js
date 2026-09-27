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

        case "remove_fillers":
        return await removeFillers(params.sequenceId);

        case "place_marker":
        return await placeMarker(params);

        case "get_active_sequence_info":
        return await getActiveSequenceInfo();

        // case "debug_commands":
        // const ppro = require("premierepro");
        // return { message: "Check UXP console for command lists if supported by the API." };

        case "get_transcript":
        return await getTranscript();

        case "debug_commands":
        return await debugCommands();

        case "debug_text_segments":
        return await debugTextSegments();

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
    const ppro = require("premierepro");
    
    const utilsKeys = ppro.Utils ? Object.getOwnPropertyNames(ppro.Utils) : [];
    const projUtilsKeys = ppro.ProjectUtils ? Object.getOwnPropertyNames(ppro.ProjectUtils) : [];

    return { 
        message: "Utils Dump",
        utils: utilsKeys,
        project_utils: projUtilsKeys
    };
}

async function removeFillers(sequenceId) {
    // FALLBACK: Since UXP API hooks for Text-Based Editing are unverified, 
    // this currently acts as a status-check/prompt for the human editor.
    
    log("removeFillers requested. Prompting manual action since UXP TBE hooks are unverified.");
    
    // You can optionally trigger the menu command to open the Text panel so the user can click it
    // const ppro = require("premierepro");
    // ppro.app.executeMenuCommand("Window > Text"); // (Requires finding the exact menu ID for the Text panel)

    return { 
        removed: false, 
        manual_intervention_required: true, 
        message: "Please run 'Delete Filler Words' manually via the Text-Based Editing panel in Premiere." 
    };
}

async function debugTextSegments() {
    const ppro = require("premierepro");

    const project = await ppro.Project.getActiveProject();
    if (!project) {
        throw new Error("No active Premiere project.");
    }

    const sequence = await project.getActiveSequence();
    if (!sequence) {
        throw new Error("No active sequence.");
    }

    const result = {
        sequenceName: sequence.name,
        textSegments: null,
    };

    try {
        const json = await ppro.TextSegments.exportToJSON(sequence);

        result.textSegments = json;
        log("=== TEXT SEGMENTS EXPORT ===");
        log(typeof json === "string" ? json : JSON.stringify(json, null, 2));

    } catch (err) {
        result.textSegmentsError = err.message || String(err);
        log(`TextSegments.exportToJSON failed: ${result.textSegmentsError}`);
    }

    return result;
}

async function debugCommands() {
    const ppro = require("premierepro");

    function inspectObject(name, obj) {
        if (!obj) {
            return {
                exists: false,
                type: typeof obj,
                keys: [],
                prototypeKeys: [],
            };
        }

        let prototypeKeys = [];

        try {
            const proto = Object.getPrototypeOf(obj);
            if (proto) {
                prototypeKeys = Object.getOwnPropertyNames(proto).sort();
            }
        } catch (e) {
            prototypeKeys = [`ERROR: ${e.message || e}`];
        }

        return {
            exists: true,
            type: typeof obj,
            keys: Object.getOwnPropertyNames(obj).sort(),
            prototypeKeys,
        };
    }

    const result = {
        Application: inspectObject("Application", ppro.Application),
        Transcript: inspectObject("Transcript", ppro.Transcript),
        TextSegments: inspectObject("TextSegments", ppro.TextSegments),
        SequenceEditor: inspectObject("SequenceEditor", ppro.SequenceEditor),
        Action: inspectObject("Action", ppro.Action),
        Utils: inspectObject("Utils", ppro.Utils),
    };

    log("=== PREMIERE UXP API DEEP DEBUG ===");

    for (const [name, info] of Object.entries(result)) {
        log(`--- ${name} ---`);
        log(`type: ${info.type}`);
        log(`keys: ${info.keys.join(", ")}`);
        log(`prototype: ${info.prototypeKeys.join(", ")}`);
    }

    return result;
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

async function getTranscript() {
    const ppro = require("premierepro");
    const project = await ppro.Project.getActiveProject();

    const selectionObj = await ppro.ProjectUtils.getSelection(project);
    const selection = await selectionObj.getItems();
    
    if (!selection || selection.length === 0) {
        throw new Error("No clip selected in Project panel. Please select the transcribed raw clip.");
    }

    const clipProjectItem = await ppro.ClipProjectItem.cast(selection[0]);
    if (!clipProjectItem) {
        throw new Error("Selected item is not a valid ClipProjectItem.");
    }

    // 1. Check if transcript exists (Premiere 26.3+ helper check fallback)
    if (ppro.Transcript.hasTranscript && !ppro.Transcript.hasTranscript(clipProjectItem)) {
        throw new Error(`Clip '${selection[0].name}' does not have a native transcript generated yet.`);
    }

    // 2. Export raw JSON string
    const jsonString = await ppro.Transcript.exportToJSON(clipProjectItem);
    const rawData = typeof jsonString === 'string' ? JSON.parse(jsonString) : jsonString;

    // 3. Process segments into normalized format for Python orchestrator
    const processedSegments = (rawData.segments || []).map(seg => {
        const fullText = (seg.words || []).map(w => w.text).join(" ");
        return {
            start: seg.start,
            end: seg.start + seg.duration,
            speaker: seg.speaker,
            text: fullText,
            words: seg.words || []
        };
    });

    return {
        clip_name: selection[0].name,
        language: rawData.language || "unknown",
        segments: processedSegments
    };
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

// Frame rate lookup is intentionally defensive: Adobe's docs list
// getVideoFrameRate() as available since Premiere 25.0, but it's been
// reported missing ("not a function") on at least Premiere 25.6.2 in
// Adobe's own developer community as of Feb 2026, with no fix confirmed
// yet. Rather than let one uncertain field fail sequence info entirely,
// try a couple of known shapes and fall back to null.
let frameRate = null;
let frameRateWarning = null;
try {
    if (typeof settings.getVideoFrameRate === "function") {
    const fr = await settings.getVideoFrameRate();
    frameRate = fr && typeof fr.value === "number" ? fr.value : fr;
    } else if (typeof settings.videoFrameRate !== "undefined") {
    // Older/alternate shape seen in some CEP->UXP migration reports.
    const vfr = settings.videoFrameRate;
    frameRate = vfr && typeof vfr.value === "number" ? vfr.value : vfr;
    } else {
    frameRateWarning = "getVideoFrameRate not available on this Premiere build (known Adobe docs/API mismatch).";
    }
} catch (frErr) {
    frameRateWarning = `frame rate lookup failed: ${frErr.message || frErr}`;
}
if (frameRateWarning) log(`getActiveSequenceInfo: ${frameRateWarning}`);

return {
    name: sequence.name, // plain property, no await needed
    durationSeconds: endTime.seconds, // TickTime.seconds is a plain property
    frameRate, // null if unavailable — see frameRateWarning in panel log
    videoTrackCount,
    audioTrackCount,
};
}

/* ---------------------------------------------------------------------- */

connect();