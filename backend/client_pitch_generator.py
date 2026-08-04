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
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
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
- Lead with the strongest timely, evidence-supported fact.
- Explain why the story matters now.
- Connect the evidence directly to the client.
- State the proposed article, the client's contribution, and the reader value.
- Do not merely repeat the supporting-evidence field.

Supporting evidence:
- Include only 2 or 3 directly relevant evidence points.
- Ignore facts that are unsupported, ambiguous, or irrelevant to the client.

Return only valid JSON with these exact keys:
{{
  "pitch_angle": "",
  "suggested_story": "",
  "subject_line": "",
  "supporting_evidence": ""
}}
""".strip()


def _generate_once(model, tokenizer, messages) -> tuple[str, dict]:
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=800,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated = tokenizer.decode(
        output_ids[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True,
    )

    return generated, extract_json(generated)


def generate_pitch(model, tokenizer, client: Dict, evidence: Dict, mode: str) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You generate polished, evidence-grounded PR pitch ideas "
                "for luxury clients and return only valid JSON."
            ),
        },
        {
            "role": "user",
            "content": build_prompt(client, evidence, mode),
        },
    ]

    generated, data = _generate_once(model, tokenizer, messages)
    errors = validate_pitch(data)

    if errors:
        messages.extend([
            {"role": "assistant", "content": generated},
            {
                "role": "user",
                "content": (
                    "Revise the response to correct every validation error below. "
                    "Preserve evidence accuracy and return only the corrected JSON.\n\n"
                    + "\n".join(f"- {error}" for error in errors)
                ),
            },
        ])
        _, data = _generate_once(model, tokenizer, messages)

    return {
        "pitch_angle": str(data.get("pitch_angle", "")).strip(),
        "suggested_story": str(data.get("suggested_story", "")).strip(),
        "subject_line": str(data.get("subject_line", "")).strip(),
        "supporting_evidence": str(data.get("supporting_evidence", "")).strip(),
    }
