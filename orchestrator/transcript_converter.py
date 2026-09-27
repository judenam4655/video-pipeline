"""
Converts the native `get_transcript` MCP output (word-level, with
disfluency tags) into the flat {start, end, text} segment list that
removal_agent.detect_deletions() expects.

Design notes (see chat discussion):

- The native `segments` array is NOT used as the grouping unit. In sample
  data it's wildly uneven — a single native segment can span 50+ seconds
  and multiple full sentences (e.g. cam2.mp4, segment at 29.22s). We
  instead flatten every word across all native segments into one ordered
  stream and re-group ourselves.

- Grouping happens on the FULL word stream, disfluency words included,
  because pause-gap timing must be measured on real elapsed time. Only
  after grouping do we drop disfluency words from the resulting `text`.

- A chunk boundary is created when either:
    (a) the previous word has eos == True (native sentence-end flag), or
    (b) the gap between the previous word's end and this word's start
        exceeds GAP_THRESHOLD_SECONDS (a pause worth treating as a break
        even mid-"sentence", e.g. Adobe not marking eos correctly).

- Disfluency words have empty `text` already in the native format, so
  "filtering" is just: skip them when joining text, but still record
  their (start, end) in `disfluency_spans` so a caller can snap a
  detect_deletions boundary to the nearest real word if a filler sits
  exactly on a flagged edge.

ASSUMPTION TO VERIFY against more real data: every word object has
"type": "word", including disfluency tokens and punctuation glued onto a
word's text (e.g. "사실."). If a different `type` ever shows up (e.g. a
standalone punctuation token), _extract_words needs a filter added.
"""

from dataclasses import dataclass, field

GAP_THRESHOLD_SECONDS = 1.5


@dataclass
class WordToken:
    start: float
    end: float
    text: str
    is_disfluency: bool
    eos: bool


def _extract_words(raw_json: dict) -> list[WordToken]:
    """
    The ONLY place that knows native field names. If Adobe's export shape
    changes, this is the sole function that needs editing.
    """
    tokens: list[WordToken] = []
    for seg in raw_json.get("segments", []):
        for w in seg.get("words", []):
            if w.get("type") != "word":
                continue  # unknown token type; skip rather than guess
            start = w["start"]
            end = start + w.get("duration", 0.0)
            tags = w.get("tags", []) or []
            tokens.append(
                WordToken(
                    start=start,
                    end=end,
                    text=w.get("text", "") or "",
                    is_disfluency="disfluency" in tags,
                    eos=bool(w.get("eos", False)),
                )
            )
    return tokens


def convert_transcript(raw_json: dict) -> tuple[list[dict], list[dict]]:
    """
    Returns (segments, disfluency_spans).

    segments: [{"start": float, "end": float, "text": str}, ...]
        Ready to feed straight into detect_deletions(). Disfluency words
        are excluded from `text`.

    disfluency_spans: [{"start": float, "end": float}, ...]
        Timestamps of every filtered filler word, for optional boundary
        snapping later. Not sent to the removal agent.
    """
    words = _extract_words(raw_json)
    if not words:
        return [], []

    segments: list[dict] = []
    disfluency_spans: list[dict] = []

    chunk_words: list[WordToken] = []

    def flush_chunk():
        if not chunk_words:
            return
        real_words = [w for w in chunk_words if not w.is_disfluency]
        if real_words:  # a chunk made entirely of disfluency is dropped
            text = " ".join(w.text for w in real_words if w.text).strip()
            if text:
                segments.append(
                    {
                        "start": chunk_words[0].start,
                        "end": chunk_words[-1].end,
                        "text": text,
                    }
                )
        chunk_words.clear()

    prev: WordToken | None = None
    for w in words:
        if w.is_disfluency:
            disfluency_spans.append({"start": w.start, "end": w.end})

        if prev is not None:
            gap = w.start - prev.end
            if prev.eos or gap > GAP_THRESHOLD_SECONDS:
                flush_chunk()

        chunk_words.append(w)
        prev = w

    flush_chunk()
    return segments, disfluency_spans


if __name__ == "__main__":
    import json
    import sys

    raw = json.loads(sys.stdin.read())
    segs, disfluencies = convert_transcript(raw)
    print(json.dumps({"segments": segs, "disfluency_spans": disfluencies}, ensure_ascii=False, indent=2))
