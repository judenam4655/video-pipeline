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

PROMPT VARIANTS
---------------
Switch the active prompt by changing PROMPT_VARIANT just below (or by setting
the REMOVAL_PROMPT_VARIANT environment variable, which overrides it — handy
for A/B runs without editing the file).

    v0_baseline     original prompt, unmodified (control)
    v1_role_xml     role prompting + XML-tagged sections + sharpened rules
    v2_fewshot      v1 + worked positive/negative examples for known failure modes
    v3_cot          v1 + examples + <analysis> scratchpad, answer in <answer> tags
                    (higher max_tokens; slower/costlier; output contract differs
                    but is handled internally)
    v4_long_context v1 rules, transcript-first user turn with a reminder +
                    consistency self-check appended AFTER the transcript
    v5_recommended  v2 prompt + v4 user-turn structure (bare-JSON contract)
"""

import json
import os
import re
import time
from anthropic import Anthropic

# ===========================================================================
# >>> CHANGE THESE TO SWITCH BEHAVIOR <<<
# ===========================================================================
PROMPT_VARIANT = "v0_baseline"  # one of the keys listed in the docstring above
USE_PREFILL = False  # if True, forces the reply to start with "[" (bare-JSON variants only;
                     # ignored for v3_cot). Leave False if your model/API version rejects prefill.
# ===========================================================================

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-6"  # update if/when a newer default becomes standard practice for this project

# Original, unmodified — used by v0_baseline. rule ids here are the source of
# truth for validation in every variant.
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

# Sharpened wording (v1+): same four rules, each now names the failure mode it
# must avoid (dialect-inconsistent transcription, callbacks that add new info,
# digressions that return, non-literal cut requests, "삭제" as tax content,
# multi-minute typo-fix gaps).
REFINED_RULESET = [
    {
        "id": "repetition",
        "description": (
            "The speaker fully re-explains something already covered earlier "
            "(often within ~5 minutes, but can be longer), adding no new "
            "information. Judge this by MEANING, not exact wording - the "
            "transcript may render the same phrase inconsistently due to "
            "dialect or mispronunciation (e.g. '이것은요' vs '요거는요'), so two "
            "passages can be a true repeat even if the words differ, and two "
            "passages can look similar in text but NOT be a repeat if the "
            "underlying content differs. Do NOT flag a callback to earlier "
            "content that adds new information or connects it to a new point - "
            "that is normal explanatory speech, not redundant repetition."
        ),
    },
    {
        "id": "context_drift",
        "description": (
            "The speaker starts a thought or sentence and genuinely abandons "
            "it - never returning to finish it - moving on to an unrelated "
            "topic. Do NOT flag a brief digression that the speaker returns to "
            "and completes on their own; that is normal speech, not drift. "
            "Only flag when the original thought is left permanently "
            "unfinished."
        ),
    },
    {
        "id": "explicit_request",
        "description": (
            "The speaker directly asks for a segment to be cut/removed from "
            "the video itself (a meta-comment about the recording, not the "
            "content). The exact phrase does not have to be '이건 삭제해주세요' - "
            "other phrasings that mean the same thing count too (e.g. '이 부분 "
            "빼주세요', '여기는 편집에서 잘라주세요', '다시 찍을게요'). Do NOT flag "
            "the speaker using words like 삭제/지우다/제거 as ordinary tax content "
            "(e.g. explaining how to delete a line item in accounting "
            "software) - that is substantive material, not a request about "
            "the recording."
        ),
    },
    {
        "id": "ppt_typo_reexplain",
        "description": (
            "The speaker notices a typo in their on-screen slide/writing "
            "mid-explanation, stops, corrects it, and re-explains the same "
            "point afterward. The fix-and-re-explain can happen well after the "
            "original explanation (correcting the slide itself can take a "
            "while) - a multi-minute gap does not disqualify this rule. Flag "
            "the correction-and-re-explanation segment, since it duplicates "
            "the original."
        ),
    },
]


def _ruleset_text(ruleset: list[dict]) -> str:
    return "\n".join(f"- {r['id']}: {r['description']}" for r in ruleset)


# ---------------------------------------------------------------------------
# Prompt building blocks (plain concatenation — no str.format — so literal
# JSON braces never need escaping)
# ---------------------------------------------------------------------------

# v0: original prompt, verbatim.
_V0_PREFIX = """You are reviewing a full transcript of a Korean tax-accountant's \
recorded video, with the goal of flagging segments that should be considered for \
deletion before final edit. You are NOT deleting anything yourself — you are \
proposing candidates for a human (the video's creator) to review as markers in \
Premiere Pro. Be conservative: when genuinely unsure whether something fits a \
rule, do not flag it. False positives cost the reviewer's trust; missed obvious \
cases are more recoverable since she reviews everything.

Apply ONLY the following rules. Do not invent additional criteria beyond these:

"""
_V0_SUFFIX = """

For each flagged segment, output an entry with:
- start_ts: start time in seconds (float)
- end_ts: end time in seconds (float)
- rule_id: which rule above it matches, exactly as given
- reasoning: one concise sentence (Korean or English is fine) explaining why, \
specific enough that the reviewer can quickly judge whether to accept it in Premiere.

Respond with ONLY a JSON array of such entries, no other text, no markdown fences.
If nothing should be flagged, respond with an empty JSON array: []
"""

# v1+: role / task / guardrails, then rules.
_HEAD = """<role>
You are a meticulous assistant video editor for a Korean tax accountant's
instructional YouTube channel. You have reviewed many hours of her raw
footage and know her recording habits: she sometimes fully re-explains
something she already covered, loses her train of thought mid-sentence,
catches a typo on her slide and has to redo a section, or occasionally
says out loud that a part should be cut.
</role>

<task>
Read the full transcript below and flag candidate segments for deletion.
You are proposing candidates for a human editor to review as non-destructive
markers in Premiere Pro - you are not deleting anything, and every flag you
raise will be reviewed before any cut is made.
</task>

<guardrails>
- Be conservative. When genuinely unsure whether a segment fits a rule, do NOT flag it.
- False positives cost the reviewer's trust in this tool; missed cases are recoverable, since she reviews the full transcript herself regardless.
- Apply ONLY the rules listed in <rules>. Do not invent additional criteria, and do not flag something merely because it seems clumsy, low-quality, or off-topic - only flag what matches a listed rule.
</guardrails>

<rules>
"""

_OUTPUT_BARE = """
</rules>

<output_format>
Respond with ONLY a JSON array, no markdown fences, no commentary before or after it.
Each element has exactly these fields:
- start_ts: start time in seconds (float)
- end_ts: end time in seconds (float)
- rule_id: exactly one of the ids listed in <rules>
- reasoning: one concise sentence (Korean or English) specific enough that the reviewer can judge the flag in Premiere without re-listening to the segment

If nothing should be flagged, respond with exactly: []
</output_format>
"""

_OUTPUT_COT = """
</rules>

<output_format>
First, work through the transcript once in an <analysis> block: note every
segment that plausibly matches a rule (including ones you ultimately decide
NOT to flag), and for each one write a single line stating which rule it
might match and why you will or will not flag it, referencing the guardrails
above. This is your private reasoning and will not be shown to the reviewer.

Then, inside an <answer> block, output ONLY a JSON array - no markdown
fences, no commentary - with one element per flagged segment:
- start_ts: start time in seconds (float)
- end_ts: end time in seconds (float)
- rule_id: exactly one of the ids listed in <rules>
- reasoning: one concise sentence (Korean or English) specific enough that the reviewer can judge the flag in Premiere without re-listening to the segment

If nothing should be flagged, the <answer> block should contain exactly: []

Nothing should appear after the closing </answer> tag.
</output_format>
"""

# v4: short system prompt (role + rules + guardrails); output instructions live
# in the user-turn reminder placed after the transcript.
_V4_SYSTEM_HEAD = """<role>
You are a meticulous assistant video editor for a Korean tax accountant's
instructional YouTube channel, reviewing full recording transcripts to flag
candidate segments for deletion. You are proposing candidates for a human
editor to review as non-destructive markers in Premiere Pro - you are not
deleting anything.
</role>

<rules>
"""
_V4_SYSTEM_TAIL = """
</rules>

<guardrails>
Be conservative: when genuinely unsure, do not flag. False positives cost
the reviewer's trust; missed cases are recoverable, since she reviews the
full transcript herself regardless. Apply ONLY the rules above.
</guardrails>
"""

_EXAMPLES = """
<examples>

<example>
<transcript_excerpt>
[723.00-761.00] 이것은요, 사업소득에서 필요경비로 인정되는 부분입니다. 사업과 직접 관련된 지출이어야 하고, 증빙 자료가 있어야 돼요.
[2055.00-2092.00] 요거는요, 사업소득 필요경비로 인정되는 부분이에요. 사업이랑 직접 관련된 지출이어야 하고, 증빙자료 있어야 되고요.
</transcript_excerpt>
<correct_output>
[{"start_ts": 2055.00, "end_ts": 2092.00, "rule_id": "repetition", "reasoning": "동일한 필요경비 요건 설명을 새로운 정보 없이 그대로 반복함 (앞선 723초 구간과 동일 내용, 표현만 다름)"}]
</correct_output>
<why>
"이것은요" vs "요거는요" is a dialect/transcription variant of the same word.
The content is a full repeat with nothing new added, so this IS repetition
even though the wording differs.
</why>
</example>

<example>
<transcript_excerpt>
[300.00-320.00] 필요경비는 사업과 관련된 지출이어야 합니다.
[930.00-970.00] 아까 말씀드린 필요경비 요건 기억나시죠? 여기에 하나 더 추가하면, 감가상각비도 필요경비로 인정됩니다.
</transcript_excerpt>
<correct_output>
[]
</correct_output>
<why>
The second passage references the earlier point but adds new information
(depreciation expense) and builds on the argument rather than restating it.
This is normal explanatory speech, not redundant repetition - do not flag.
</why>
</example>

<example>
<transcript_excerpt>
[492.00-525.00] 부가가치세율은 10%입니다. 여기 화면에 보시는 것처럼...
[526.00-531.00] 어, 잠깐만요, 슬라이드에 오타가 있네요. 잠시 수정할게요.
[680.00-712.00] 다시 설명드리면, 부가가치세율은 10%입니다. 이 부분이 중요한데요...
</transcript_excerpt>
<correct_output>
[{"start_ts": 526.00, "end_ts": 712.00, "rule_id": "ppt_typo_reexplain", "reasoning": "슬라이드 오타를 발견하고 수정한 뒤 이미 설명한 부가가치세율 10% 내용을 처음부터 다시 설명함 (수정에 약 2분 30초 소요)"}]
</correct_output>
<why>
The correction takes several minutes (the gap between noticing the typo and
finishing the fix), then the same point is re-explained from scratch. A
multi-minute gap does not disqualify this rule - it is the expected shape of
this failure mode.
</why>
</example>

<example>
<transcript_excerpt>
[1205.00-1222.00] 그리고 이 부분에서 중요한 건... 아 참, 오늘 날씨가 너무 덥네요. 에어컨 좀 틀어야겠어요.
</transcript_excerpt>
<correct_output>
[{"start_ts": 1205.00, "end_ts": 1222.00, "rule_id": "context_drift", "reasoning": "설명 중이던 문장을 끝맺지 않고 날씨 얘기로 전환, 이후 원래 내용으로 돌아오지 않음"}]
</correct_output>
<why>
The thought is genuinely abandoned and the speaker never returns to it.
</why>
</example>

<example>
<transcript_excerpt>
[1205.00-1240.00] 이 부분에서 중요한 건... 아, 참고로 이거 저번 주 세미나에서도 얘기가 나왔었는데, 아무튼 다시 본론으로 돌아가면, 이 필요경비 요건은 매년 조금씩 바뀌니까 주의하셔야 됩니다.
</transcript_excerpt>
<correct_output>
[]
</correct_output>
<why>
The speaker digresses briefly but explicitly returns to and completes the
original thought. This is a natural digression, not context_drift - do not
flag.
</why>
</example>

<example>
<transcript_excerpt>
[2411.00-2416.00] 어, 이 부분은 편집에서 빼주세요.
</transcript_excerpt>
<correct_output>
[{"start_ts": 2411.00, "end_ts": 2416.00, "rule_id": "explicit_request", "reasoning": "편집자에게 직접 이 구간을 편집에서 빼달라고 요청함"}]
</correct_output>
<why>
The trigger phrase does not have to be the literal "이건 삭제해주세요" - "빼주세요"
here means the same thing: a direct meta-request about the recording.
</why>
</example>

<example>
<transcript_excerpt>
[2520.00-2550.00] 홈택스에서 잘못 입력한 항목은 삭제 버튼을 눌러서 지우시면 됩니다.
</transcript_excerpt>
<correct_output>
[]
</correct_output>
<why>
This uses "삭제"/"지우다" but as substantive tax content (how to delete an
entry in Hometax software), not a meta-comment about the video itself.
explicit_request only applies to requests about the recording - do not flag.
</why>
</example>

</examples>

Use the examples above only to calibrate your judgment on similar situations - do not copy their timestamps or reasoning text into your actual output.
"""

_V4_REMINDER = """
---
You have now read the full transcript above. Before answering, re-apply the
same standard to the whole thing, start to finish - it's easy to get stricter
or looser partway through a long transcript; check that a segment near the
end would get the same verdict as an equivalent segment near the start.

Remember the four rules: repetition, context_drift, explicit_request,
ppt_typo_reexplain. Be conservative - when unsure, do not flag.

Respond with ONLY a JSON array (no markdown fences, no other text), each
element: {"start_ts": float, "end_ts": float, "rule_id": str, "reasoning": str}.
If nothing should be flagged, respond with exactly: []
"""


# ---------------------------------------------------------------------------
# Transcript formatting (user turn)
# ---------------------------------------------------------------------------

def build_transcript_text(segments: list[dict]) -> str:
    """Formats transcript segments with timestamps for the prompt."""
    lines = [f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments]
    return "\n".join(lines)


def build_transcript_text_reminder(segments: list[dict]) -> str:
    """Transcript first, then the reminder/self-check block (long-context variants)."""
    return build_transcript_text(segments) + "\n" + _V4_REMINDER


# ---------------------------------------------------------------------------
# Output extraction (per variant)
# ---------------------------------------------------------------------------

def _extract_bare(raw_text: str) -> str:
    return raw_text


def _extract_cot_answer(raw_text: str) -> str:
    """Pulls the JSON out of <answer>...</answer>, discarding the <analysis> scratchpad."""
    match = re.search(r"<answer>(.*?)</answer>", raw_text, re.DOTALL)
    if not match:
        raise ValueError(
            f"CoT response had no complete <answer>...</answer> block "
            f"(possibly truncated by max_tokens). Raw output:\n{raw_text}"
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Variant registry — each entry fully defines one prompt configuration
# ---------------------------------------------------------------------------

_V1_SYSTEM = _HEAD + _ruleset_text(REFINED_RULESET) + _OUTPUT_BARE
_V2_SYSTEM = _HEAD + _ruleset_text(REFINED_RULESET) + _OUTPUT_BARE.rstrip() + "\n" + _EXAMPLES
_V3_SYSTEM = _HEAD + _ruleset_text(REFINED_RULESET) + _OUTPUT_COT.rstrip() + "\n" + _EXAMPLES
_V4_SYSTEM = _V4_SYSTEM_HEAD + _ruleset_text(REFINED_RULESET) + _V4_SYSTEM_TAIL

VARIANTS: dict[str, dict] = {
    "v0_baseline": {
        "system": _V0_PREFIX + _ruleset_text(RULESET) + _V0_SUFFIX,
        "build_user": build_transcript_text,
        "extract": _extract_bare,
        "max_tokens": 4000,
        "prefill_ok": True,
    },
    "v1_role_xml": {
        "system": _V1_SYSTEM,
        "build_user": build_transcript_text,
        "extract": _extract_bare,
        "max_tokens": 4000,
        "prefill_ok": True,
    },
    "v2_fewshot": {
        "system": _V2_SYSTEM,
        "build_user": build_transcript_text,
        "extract": _extract_bare,
        "max_tokens": 4000,
        "prefill_ok": True,
    },
    "v3_cot": {
        "system": _V3_SYSTEM,
        "build_user": build_transcript_text,
        "extract": _extract_cot_answer,
        "max_tokens": 8000,  # scratchpad consumes output tokens before the JSON
        "prefill_ok": False,
    },
    "v4_long_context": {
        "system": _V4_SYSTEM,
        "build_user": build_transcript_text_reminder,
        "extract": _extract_bare,
        "max_tokens": 4000,
        "prefill_ok": True,
    },
    "v5_recommended": {
        "system": _V2_SYSTEM,
        "build_user": build_transcript_text_reminder,
        "extract": _extract_bare,
        "max_tokens": 4000,
        "prefill_ok": True,
    },
}


def get_active_variant_name() -> str:
    name = os.environ.get("REMOVAL_PROMPT_VARIANT", PROMPT_VARIANT)
    if name not in VARIANTS:
        raise ValueError(f"Unknown prompt variant '{name}'. Valid options: {', '.join(VARIANTS)}")
    return name


# Kept for backward compatibility with any code importing SYSTEM_PROMPT.
SYSTEM_PROMPT = VARIANTS["v0_baseline"]["system"]


def detect_deletions(transcript_segments: list[dict], log=print) -> list[dict]:
    """
    Runs the removal-detection pass over a full transcript in one call.
    `log` receives progress/diagnostic lines (main.py passes job.note).
    The prompt used is selected by PROMPT_VARIANT (see top of file).

    NOTE: assumes the full transcript fits comfortably in context (fine for
    20-30 min recordings). Revisit a sliding window only if rule-following
    degrades on much longer recordings.
    """
    variant_name = get_active_variant_name()
    variant = VARIANTS[variant_name]

    user_text = variant["build_user"](transcript_segments)
    messages = [{"role": "user", "content": user_text}]
    prefill = USE_PREFILL and variant["prefill_ok"]
    if prefill:
        messages.append({"role": "assistant", "content": "["})

    log(
        f"[detect_deletions] variant={variant_name} | model={MODEL} | "
        f"{len(transcript_segments)} segments | {len(user_text)} chars sent | "
        f"prefill={'on' if prefill else 'off'} | rules: {', '.join(r['id'] for r in RULESET)}"
    )
    t0 = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=variant["max_tokens"],
        system=variant["system"],
        messages=messages,
    )
    usage = getattr(response, "usage", None)
    log(
        f"[detect_deletions] API responded in {time.monotonic() - t0:.1f}s | "
        f"stop_reason={getattr(response, 'stop_reason', '?')} | "
        f"tokens in={getattr(usage, 'input_tokens', '?')} out={getattr(usage, 'output_tokens', '?')}"
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        log("[detect_deletions] WARNING: output hit max_tokens and was likely truncated -> JSON may be invalid")

    raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
    if prefill:
        raw_text = "[" + raw_text  # the API does not echo the prefilled text back

    try:
        deletions = _parse_json_array(variant["extract"](raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Removal agent did not return valid JSON. Raw output:\n{raw_text}"
        ) from e

    log(f"[detect_deletions] parsed {len(deletions) if isinstance(deletions, list) else '?'} entries from model output")
    return _validate_deletions(deletions, log)


def _parse_json_array(raw_text: str) -> list:
    """Tolerates ```json fences and stray text around the array."""
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("["):
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def _validate_deletions(deletions, log=print) -> list[dict]:
    """
    Drops malformed entries (logging why) instead of letting one bad item
    (e.g. an unknown rule_id, which place_marker's schema rejects) fail the job.
    """
    if not isinstance(deletions, list):
        raise ValueError(f"Removal agent returned {type(deletions).__name__}, expected a JSON array.")

    valid_ids = {r["id"] for r in RULESET}
    kept = []
    for i, d in enumerate(deletions, 1):
        reason = None
        try:
            start, end = float(d["start_ts"]), float(d["end_ts"])
            if d["rule_id"] not in valid_ids:
                reason = f"unknown rule_id '{d['rule_id']}'"
            elif end <= start:
                reason = f"end {end} <= start {start}"
            elif not str(d["reasoning"]).strip():
                reason = "empty reasoning"
        except (KeyError, TypeError, ValueError) as e:
            reason = f"malformed ({type(e).__name__}: {e})"

        if reason:
            log(f"[detect_deletions] DROPPED entry #{i}: {reason} | {d}")
            continue
        kept.append({"start_ts": start, "end_ts": end, "rule_id": d["rule_id"], "reasoning": str(d["reasoning"])})
    return kept