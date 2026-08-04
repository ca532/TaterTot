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
    if not 120 <= len(story_words) <= 180:
        errors.append("suggested_story must contain 120 to 180 words")
    if len(paragraphs) != 2:
        errors.append("suggested_story must contain exactly two paragraphs")
    if not evidence:
        errors.append("supporting_evidence is required")

    return errors


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


def extract_json(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}

    fenced = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*",
        raw,
        flags=re.S | re.I,
    )
    if fenced:
        raw = fenced.group(1).strip()

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
- Write 120 to 180 words in exactly two short paragraphs.
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
    messages = [
        {
            "role": "system",
            "content": (
                "You generate polished, evidence-grounded PR pitch ideas "
                "for luxury clients. Return one valid JSON object only."
            ),
        },
        {
            "role": "user",
            "content": build_prompt(client, evidence, mode),
        },
    ]

    generated, data = _generate_once(model, tokenizer, messages, attempt=1)
    errors = validate_pitch(data)

    if not errors:
        return _clean_pitch(data)

    print(f"[PITCH_VALIDATION_FAILURE] attempt=1 errors={errors}")
    messages.extend([
        {"role": "assistant", "content": generated},
        {
            "role": "user",
            "content": (
                "Your previous response failed validation. Return a corrected "
                "JSON object only. Do not use Markdown fences. Encode the "
                "paragraph break in suggested_story as \\n\\n.\n\n"
                "Validation errors:\n"
                + "\n".join(f"- {error}" for error in errors)
            ),
        },
    ])

    generated, data = _generate_once(model, tokenizer, messages, attempt=2)
    errors = validate_pitch(data)

    if errors:
        print(f"[PITCH_VALIDATION_FAILURE] attempt=2 errors={errors}")
        print(
            "[PITCH_FINAL_INVALID_OUTPUT] "
            f"output_preview={generated[:1500]!r}"
        )
        raise PitchGenerationError(
            "Pitch remained invalid after two generation attempts: "
            + "; ".join(errors)
        )

    return _clean_pitch(data)
