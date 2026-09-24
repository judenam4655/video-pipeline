"""
The one place real AI judgment happens in this pipeline. Everything else in
the orchestrator is deterministic plumbing; this step reads the full
transcript and decides what should be flagged for deletion.

This is a direct Claude API call, not an MCP tool call — it's pure
text-in/JSON-out with no need for tool access, so MCP would just be
overhead here.

RULESET is intentionally kept small and modular (per your note: few rules,
each independently editable/addable) rather than one big prose instruction.
Add new rules as new dict entries; each should be small and self-contained.
"""

import json
import os
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-6"  # update if/when a newer default becomes standard practice for this project

RULESET = [
    {
        "id": "repetition",
        "description": (
            "The speaker fully re-explains something they already explained, "
            "typically within the last ~5 minutes, where the repeat adds no "
            "new information and would feel redundant/boring to a viewer."
        ),
    },
    {
        "id": "context_drift",
        "description": (
            "The speaker starts a thought or sentence and abandons it without "
            "finishing, moving to an unrelated topic mid-sentence."
        ),
    },
    {
        "id": "explicit_request",
        "description": (
            "The speaker directly says to cut something, e.g. '이건 삭제해주세요' "
            "or equivalent — always honor these regardless of content."
        ),
    },
    {
        "id": "ppt_typo_reexplain",
        "description": (
            "The speaker notices a typo in their on-screen slide/writing mid-"
            "explanation, stops, corrects it, and re-explains the same point "
            "afterward. Flag the correction-and-re-explanation segment, since "
            "it duplicates the original explanation."
        ),
    },
]

SYSTEM_PROMPT = """You are reviewing a full transcript of a Korean tax-accountant's \
recorded video, with the goal of flagging segments that should be considered for \
deletion before final edit. You are NOT deleting anything yourself — you are \
proposing candidates for a human (the video's creator) to review as markers in \
Premiere Pro. Be conservative: when genuinely unsure whether something fits a \
rule, do not flag it. False positives cost the reviewer's trust; missed obvious \
cases are more recoverable since she reviews everything.

Apply ONLY the following rules. Do not invent additional criteria beyond these:

{ruleset}

For each flagged segment, output an entry with:
- start_ts: start time in seconds (float)
- end_ts: end time in seconds (float)
- rule_id: which rule above it matches, exactly as given
- reasoning: one concise sentence (Korean or English is fine) explaining why, \
specific enough that the reviewer can quickly judge whether to accept it in Premiere.

Respond with ONLY a JSON array of such entries, no other text, no markdown fences.
If nothing should be flagged, respond with an empty JSON array: []
"""


def build_transcript_text(segments: list[dict]) -> str:
    """Formats transcript segments with timestamps for the prompt."""
    lines = [f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments]
    return "\n".join(lines)


def detect_deletions(transcript_segments: list[dict]) -> list[dict]:
    """
    Runs the removal-detection pass over a full transcript in one call.

    NOTE: this assumes the full transcript fits comfortably in context, which
    holds for a 20-30 min recording (see earlier sizing discussion — roughly
    4,000-7,000 words). If you later work with much longer recordings and see
    rule-following degrade across the length of the document, that's the
    trigger to revisit the sliding-window approach discussed earlier — don't
    add that complexity preemptively.
    """
    transcript_text = build_transcript_text(transcript_segments)
    ruleset_text = "\n".join(f"- {r['id']}: {r['description']}" for r in RULESET)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT.format(ruleset=ruleset_text),
        messages=[{"role": "user", "content": transcript_text}],
    )

    raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))

    try:
        deletions = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Removal agent did not return valid JSON. Raw output:\n{raw_text}"
        ) from e

    return deletions
