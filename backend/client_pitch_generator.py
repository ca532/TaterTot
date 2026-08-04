from __future__ import annotations

import json
import os
import re
from typing import Dict

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


PITCH_MODEL_NAME = os.getenv("PITCH_MODEL_NAME", "Qwen/Qwen2.5-1.5B-Instruct")
GENERIC_SUBJECT_OPENINGS = (
    "discover",
    "explore",
    "celebrating",
    "introducing",
    "the art of",
)


def validate_pitch(data: dict) -> list[str]:
    errors = []
    subject = str(data.get("subject_line", "")).strip()
    story = str(data.get("suggested_story", "")).strip()
    evidence = str(data.get("supporting_evidence", "")).strip()
    pitch_angle = str(data.get("pitch_angle", "")).strip()
    subject_words = re.findall(r"\b[\w'-]+\b", subject)
    story_words = re.findall(r"\b[\w'-]+\b", story)
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", story)
        if paragraph.strip()
    ]

    if not pitch_angle:
        errors.append("pitch_angle is required")
    if not subject:
        errors.append("subject_line is required")
    else:
        if not 5 <= len(subject_words) <= 9:
            errors.append("subject_line must contain 5 to 9 words")
        if len(subject) > 60:
            errors.append("subject_line must be no more than 60 characters")
        if subject.lower().startswith(GENERIC_SUBJECT_OPENINGS):
            errors.append("subject_line begins with a prohibited generic opening")
    if not 100 <= len(story_words) <= 180:
        errors.append("suggested_story should contain 100 to 180 words")
    if len(paragraphs) != 2:
        errors.append("suggested_story must contain exactly two paragraphs")
    if not evidence:
        errors.append("supporting_evidence is required")

    return errors


def required_pitch_errors(data: dict) -> list[str]:
    errors = []
    for field in (
        "pitch_angle",
        "suggested_story",
        "subject_line",
        "supporting_evidence",
    ):
        if not str(data.get(field, "")).strip():
            errors.append(f"{field} is required")
    return errors


def pitch_quality_score(data: dict) -> tuple[int, int]:
    return len(required_pitch_errors(data)), len(validate_pitch(data))


def load_model():
    tokenizer = AutoTokenizer.from_pretrained(PITCH_MODEL_NAME, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        PITCH_MODEL_NAME,
        torch_dtype=torch.float32,
        trust_remote_code=True,
    )
    model.to("cpu")
    model.eval()
    return model, tokenizer


def _sanitize_model_json(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    raw = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = re.sub(r"(?m)^\s*\*{3,}\s*$", "", raw)
    raw = raw.replace("\\'", "'").strip()

    first_key = re.search(
        r'"(?:pitch_angle|suggested_story|subject_line|supporting_evidence)"\s*:',
        raw,
    )
    if first_key and not raw.lstrip().startswith("{"):
        raw = "{" + raw[first_key.start():]
    if raw.lstrip().startswith("{") and not raw.rstrip().endswith("}"):
        raw = raw.rstrip().rstrip(",") + "}"

    return raw


def extract_json(text: str) -> dict:
    raw = _sanitize_model_json(text)
    if not raw:
        return {}

    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"\{", raw):
        try:
            data, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    return {}


def build_prompt(client: Dict, evidence: Dict, mode: str) -> str:
    mode_note = (
        "This evidence comes from BERTopic trend analysis. You may call it a trend signal."
        if mode == "trend_signals"
        else "This evidence comes from recent article summaries only. Do not call it a trend."
    )

    return f"""
You are a senior luxury PR strategist.

Use only the client context and evidence below. Do not invent facts.

Client:
Name: {client.get("client_name", "")}
Description: {client.get("client_description", "")}

Mode: {mode}
Mode guidance: {mode_note}

Evidence:
{json.dumps(evidence, ensure_ascii=False, indent=2)}

Draft one polished client pitch idea.

Subject line:
- Use 5 to 9 words and no more than 60 characters.
- Lead with the strongest specific hook.
- Do not begin with Discover, Explore, Celebrating, Introducing, or The Art of.
- Make it compelling without unsupported claims or clickbait.

Suggested story:
- Write 100 to 180 words in two short paragraphs.
- Because the response is JSON, encode the paragraph break as \\n\\n inside the suggested_story string.
- Do not place literal unescaped line breaks inside any JSON string.
- Lead with the strongest timely, evidence-supported fact.
- Explain why the story matters now.
- Connect the evidence directly to the client.
- State the proposed article, the client's contribution, and the reader value.
- Do not merely repeat the supporting-evidence field.

Supporting evidence:
- Include only 2 or 3 directly relevant evidence points.
- Ignore facts that are unsupported, ambiguous, or irrelevant to the client.

The entire response must parse with json.loads(). Escape quotation marks and
line breaks inside JSON strings. Do not use Markdown code fences.

Return only valid JSON with these exact keys:
{{
  "pitch_angle": "",
  "suggested_story": "",
  "subject_line": "",
  "supporting_evidence": ""
}}
""".strip()


class PitchGenerationError(RuntimeError):
    pass


def _generate_once(model, tokenizer, messages, attempt: int) -> tuple[str, dict]:
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=800,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated = tokenizer.decode(
        output_ids[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True,
    ).strip()

    data = extract_json(generated)
    print(
        f"[PITCH_GENERATION] attempt={attempt} "
        f"output_chars={len(generated)} json_found={bool(data)}"
    )
    if not data:
        print(
            f"[PITCH_PARSE_FAILURE] attempt={attempt} "
            f"output_preview={generated[:1500]!r}"
        )

    return generated, data


def _clean_pitch(data: dict) -> dict:
    return {
        "pitch_angle": str(data.get("pitch_angle", "")).strip(),
        "suggested_story": str(data.get("suggested_story", "")).strip(),
        "subject_line": str(data.get("subject_line", "")).strip(),
        "supporting_evidence": str(data.get("supporting_evidence", "")).strip(),
    }


def generate_pitch(model, tokenizer, client: Dict, evidence: Dict, mode: str) -> dict:
    system_message = {
        "role": "system",
        "content": (
            "You generate polished, evidence-grounded PR pitch ideas "
            "for luxury clients. Return one valid JSON object only."
        ),
    }
    original_prompt = build_prompt(client, evidence, mode)
    first_messages = [
        system_message,
        {"role": "user", "content": original_prompt},
    ]

    _, first_data = _generate_once(model, tokenizer, first_messages, attempt=1)
    first_errors = validate_pitch(first_data)
    if not first_errors:
        return _clean_pitch(first_data)

    print(f"[PITCH_VALIDATION_FAILURE] attempt=1 errors={first_errors}")
    correction_prompt = f"""
Generate the pitch again from scratch.

Correct these quality issues:
{chr(10).join(f"- {error}" for error in first_errors)}

Return exactly one JSON object in this shape:
{{
  "pitch_angle": "A specific editorial angle",
  "suggested_story": "First paragraph of the proposed story.\\n\\nSecond paragraph connecting the evidence to the client and reader value.",
  "subject_line": "Specific Five to Nine Word Subject",
  "supporting_evidence": "Two or three concise evidence points"
}}

Requirements:
- Include all four fields with non-empty values.
- Keep suggested_story between 100 and 180 words.
- Use \\n\\n between its two paragraphs.
- Do not use Markdown, code fences, commentary, or asterisks.
- Use valid JSON escaping.
""".strip()
    retry_messages = [
        system_message,
        {"role": "user", "content": original_prompt},
        {"role": "user", "content": correction_prompt},
    ]
    _, retry_data = _generate_once(model, tokenizer, retry_messages, attempt=2)

    candidates = [
        candidate
        for candidate in (first_data, retry_data)
        if candidate and not required_pitch_errors(candidate)
    ]
    if not candidates:
        first_required = required_pitch_errors(first_data)
        retry_required = required_pitch_errors(retry_data)
        raise PitchGenerationError(
            "Neither generation attempt contained all required fields. "
            f"Attempt 1: {first_required or 'unparseable'}; "
            f"attempt 2: {retry_required or 'unparseable'}"
        )

    best_data = min(candidates, key=pitch_quality_score)
    remaining_errors = validate_pitch(best_data)
    if remaining_errors:
        print(f"[PITCH_ACCEPTED_BEST_EFFORT] errors={remaining_errors}")

    return _clean_pitch(best_data)
