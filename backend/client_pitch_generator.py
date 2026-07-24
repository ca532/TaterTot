from __future__ import annotations

import json
import os
import re
from typing import Dict

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


PITCH_MODEL_NAME = os.getenv("PITCH_MODEL_NAME", "Qwen/Qwen2.5-1.5B-Instruct")


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

Return only valid JSON with these exact keys:
{{
  "pitch_angle": "",
  "suggested_story": "",
  "subject_line": "",
  "supporting_evidence": ""
}}
""".strip()


def generate_pitch(model, tokenizer, client: Dict, evidence: Dict, mode: str) -> dict:
    messages = [
        {
            "role": "system",
            "content": "You generate concise, polished, evidence-grounded PR pitch ideas for luxury clients.",
        },
        {
            "role": "user",
            "content": build_prompt(client, evidence, mode),
        },
    ]

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=360,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated = tokenizer.decode(
        output_ids[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True,
    )

    data = extract_json(generated)

    return {
        "pitch_angle": str(data.get("pitch_angle", "")).strip(),
        "suggested_story": str(data.get("suggested_story", "")).strip(),
        "subject_line": str(data.get("subject_line", "")).strip(),
        "supporting_evidence": str(data.get("supporting_evidence", "")).strip(),
    }
