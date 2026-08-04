from __future__ import annotations

import ast
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
    else:
        if re.search(r"https?://|www\.", evidence, flags=re.I):
            errors.append("supporting_evidence must not contain URLs")
        if evidence.lstrip().startswith(("[", "{")):
            errors.append(
                "supporting_evidence must be plain language, not a data structure"
            )
        if any(
            token in evidence.lower()
            for token in (
                "article_url",
                "source_url",
                "supporting_urls",
                "evidence_points",
                "'score':",
                '"score":',
            )
        ):
            errors.append(
                "supporting_evidence must not contain internal field names"
            )

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


MODEL_HIDDEN_EVIDENCE_KEYS = {
    "url",
    "urls",
    "article_url",
    "source_url",
    "canonical_url",
    "supporting_urls",
    "run_id",
    "run_ids",
    "window_days",
}


def evidence_for_prompt(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key.startswith("_"):
                continue
            if normalized_key in MODEL_HIDDEN_EVIDENCE_KEYS:
                continue
            if normalized_key.endswith("_url") or normalized_key.endswith("_urls"):
                continue
            cleaned[key] = evidence_for_prompt(item)
        return cleaned

    if isinstance(value, list):
        return [evidence_for_prompt(item) for item in value]

    return value


def build_prompt(client: Dict, evidence: Dict, mode: str) -> str:
    mode_note = (
        "This evidence comes from BERTopic trend analysis. You may call it a trend signal."
        if mode == "trend_signals"
        else "This evidence comes from recent article summaries only. Do not call it a trend."
    )

    return f"""
You are a senior luxury PR strategist.

Use only the client context and reference coverage below. Do not invent facts.

The reference coverage describes the wider media landscape. It does not
necessarily describe the client. Never claim that the client launched a
product, attended an event, dressed a celebrity, received press coverage,
formed a partnership, or took another action unless that fact appears
explicitly in the client description.

Use unrelated brands, events, products, and celebrities only as evidence of
an editorial theme or media opportunity. Do not transfer their actions or
coverage to the client. Frame the result as a proposed story opportunity,
not as an event that has already happened.

Client:
Name: {client.get("client_name", "")}
Description: {client.get("client_description", "")}

Mode: {mode}
Mode guidance: {mode_note}

Reference coverage:
{json.dumps(evidence_for_prompt(evidence), ensure_ascii=False, indent=2)}

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
- Describe a proposed article or pitch opportunity.
- Clearly distinguish reference-coverage facts from known client facts.
- Explain why the coverage theme matters now.
- Connect the theme to the client's documented positioning.
- State what the client could contribute and what readers would gain.
- Do not claim that the client participated in any referenced event, launch, collaboration, endorsement, or media appearance.

Supporting evidence:
- Write 2 or 3 concise, plain-language coverage insights.
- Describe themes found in the reference coverage.
- Do not include URLs, domain names, JSON, Python structures, field names, scores, dates, or source metadata.
- Do not state or imply that the coverage is about the client unless the supplied text explicitly says so.
- Separate the insights with semicolons.

The entire response must parse with json.loads(). Escape quotation marks and
line breaks inside JSON strings. Do not use Markdown code fences.

Return only valid JSON with these exact keys:
{{
  "pitch_angle": "A proposed editorial angle for the client",
  "suggested_story": "First paragraph describing the timely coverage theme and proposed story.\\n\\nSecond paragraph explaining the client's possible contribution and reader value.",
  "subject_line": "Specific Five to Nine Word Subject",
  "supporting_evidence": "Coverage insight one; coverage insight two"
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


EVIDENCE_METADATA_KEYS = {
    "url",
    "urls",
    "article_url",
    "source_url",
    "canonical_url",
    "supporting_urls",
    "score",
    "date",
    "published_date",
    "run_id",
}


def _extract_evidence_points(value) -> list[str]:
    if isinstance(value, dict):
        points = []
        for key in (
            "evidence_points",
            "evidence",
            "insights",
            "points",
            "summary",
        ):
            if key in value:
                points.extend(_extract_evidence_points(value[key]))

        if points:
            return points

        for key, item in value.items():
            if str(key).lower() in EVIDENCE_METADATA_KEYS:
                continue
            points.extend(_extract_evidence_points(item))
        return points

    if isinstance(value, list):
        points = []
        for item in value:
            points.extend(_extract_evidence_points(item))
        return points

    text = str(value or "").strip()
    return [text] if text else []


def _clean_supporting_evidence(value) -> str:
    parsed = value
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("[", "{")):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    parsed = raw

    points = _extract_evidence_points(parsed)
    cleaned = []
    seen = set()

    for point in points:
        point = re.sub(r"https?://\S+", "", point).strip(" -|,;")
        point = re.sub(r"\s+", " ", point)
        if not point:
            continue

        dedupe_key = point.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(point)
        if len(cleaned) >= 3:
            break

    return "; ".join(cleaned)


def _clean_pitch(data: dict) -> dict:
    return {
        "pitch_angle": str(data.get("pitch_angle", "")).strip(),
        "suggested_story": str(data.get("suggested_story", "")).strip(),
        "subject_line": str(data.get("subject_line", "")).strip(),
        "supporting_evidence": _clean_supporting_evidence(
            data.get("supporting_evidence", "")
        ),
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
