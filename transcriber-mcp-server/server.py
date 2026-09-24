"""
transcriber-mcp-server

Wraps whatever transcription tool already exists on the Mac and exposes it
as a single MCP tool: transcribe(audio_or_video_path) -> segments with
timestamps.

IMPORTANT: I don't know the specifics of your existing local transcriber
(is it a CLI tool, a Python library, a standalone app with its own webpage
UI?). This file is written against the most common case — a local
Whisper-family model run via CLI or Python — with the exact call to swap in
clearly marked below. If your existing tool is instead something with its
own web UI and no CLI/API, you have two real options:
  (a) find/enable a CLI or HTTP API mode for it (most transcription apps
      have one even if the webpage is the primary interface), or
  (b) replace it here with a direct faster-whisper/whisper.cpp call instead
      of wrapping the existing app, since that guarantees scriptability.
Tell me which situation you're actually in and I'll adjust this file.

Output format (what step 4's removal agent will consume):
{
  "segments": [
    {"start": 12.34, "end": 15.10, "text": "이것은 예시 문장입니다"},
    ...
  ],
  "full_text": "..."
}
"""

import asyncio
import json
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("transcriber-bridge")


@mcp.tool()
async def transcribe(sequence_audio_path: str, language: str = "ko") -> str:
    """
    Transcribes the given audio/video file and returns timestamped segments
    as a JSON string.

    Args:
        sequence_audio_path: Absolute path to the exported audio (or video)
            of the synced+filler-cut Premiere sequence.
        language: Language code for transcription (default Korean).
    """
    path = Path(sequence_audio_path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {sequence_audio_path}")

    # --- OPTION A: existing CLI-based local transcriber ------------------
    # Swap in your actual tool's command. Example shape:
    #
    # result = subprocess.run(
    #     ["your-transcriber-cli", "--input", str(path), "--lang", language, "--output-format", "json"],
    #     capture_output=True, text=True, check=True,
    # )
    # raw = json.loads(result.stdout)
    # segments = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in raw["segments"]]

    # --- OPTION B: faster-whisper directly (uncomment requirements.txt) --
    # from faster_whisper import WhisperModel
    # model = WhisperModel("large-v3", device="auto", compute_type="auto")
    # segments_gen, info = model.transcribe(str(path), language=language)
    # segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segments_gen]

    raise NotImplementedError(
        "Wire this up to your actual local transcriber — see OPTION A / OPTION B comments above. "
        "Tell me which one applies and I'll fill this in for real."
    )

    # full_text = " ".join(s["text"].strip() for s in segments)
    # return json.dumps({"segments": segments, "full_text": full_text}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
