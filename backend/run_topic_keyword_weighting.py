import json
import os
from datetime import datetime, timezone

import gspread
import torch
from google.oauth2.service_account import Credentials

from client_pitch_generator import PITCH_MODEL_NAME, extract_json, load_model


TOPIC_NAME = os.getenv("TOPIC_NAME", "__all__").strip()
TOPIC_SHEET = os.getenv("TOPIC_CONFIG_SHEET", "Topic Config")


def open_topic_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    raw_credentials = os.getenv("GOOGLE_CREDENTIALS", "").strip()
    if raw_credentials:
        credentials = Credentials.from_service_account_info(json.loads(raw_credentials), scopes=scopes)
    else:
        credentials = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    return gspread.authorize(credentials).open_by_key(os.environ["GOOGLE_SHEET_ID"]).worksheet(TOPIC_SHEET)


def clean_keywords(raw):
    return list(dict.fromkeys(
        value.strip().lower()
        for value in str(raw or "").split(",")
        if value.strip()
    ))


def validated_weights(keywords, generated):
    raw_weights = generated.get("keyword_weights", {}) if isinstance(generated, dict) else {}
    normalized = {str(key).strip().lower(): value for key, value in raw_weights.items()}
    weights = {}
    for keyword in keywords:
        try:
            value = float(normalized.get(keyword, 1.0))
        except (TypeError, ValueError):
            value = 1.0
        weights[keyword] = round(max(0.5, min(value, 4.0)), 2)
    return weights


def generate_weights(model, tokenizer, topic_name, keywords, summary_prompt):
    messages = [{
        "role": "user",
        "content": (
            "Configure deterministic article-relevance scoring.\n"
            f"Topic: {topic_name}\n"
            f"Summary objective: {summary_prompt}\n"
            f"Keywords: {json.dumps(keywords)}\n\n"
            "Assign every supplied keyword a relevance weight from 0.5 to 4.0. "
            "Use 4.0 for essential specific signals, 3.0 for strongly relevant signals, "
            "2.0 for supporting signals, 1.0 for broad or ambiguous signals, and 0.5 for weak signals. "
            "Do not add or remove keywords. Return only valid JSON in this shape: "
            '{"keyword_weights":{"keyword":1.0}}'
        ),
    }]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=700, do_sample=False)
    text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    return validated_weights(keywords, extract_json(text))


def main():
    worksheet = open_topic_sheet()
    required_headers = [
        "topic_name", "keywords", "summary_prompt", "active", "updated",
        "keyword_weights", "weighting_status", "weighting_model",
    ]
    current_headers = worksheet.row_values(1)
    for column, header in enumerate(required_headers, start=1):
        if column > len(current_headers) or not current_headers[column - 1]:
            worksheet.update_cell(1, column, header)

    records = worksheet.get_all_records()
    headers = worksheet.row_values(1)
    columns = {name: headers.index(name) + 1 for name in headers}
    selected = []
    matched_topic = TOPIC_NAME == "__all__"
    for index, record in enumerate(records, start=2):
        name = str(record.get("topic_name", "")).strip()
        if not name or str(record.get("active", "TRUE")).upper() == "FALSE":
            continue
        if TOPIC_NAME != "__all__" and name.lower() != TOPIC_NAME.lower():
            continue
        matched_topic = True
        keywords = clean_keywords(record.get("keywords", ""))
        if not keywords:
            worksheet.update_cell(index, columns["weighting_status"], "not_required")
            continue
        if str(record.get("weighting_status", "")).strip().lower() == "complete" and record.get("keyword_weights"):
            continue
        selected.append((index, record, keywords))

    if not matched_topic:
        raise RuntimeError(f"Active topic configuration '{TOPIC_NAME}' was not found")

    if not selected:
        print("No topic keyword weights require generation")
        return

    model, tokenizer = load_model()
    for row_index, record, keywords in selected:
        try:
            weights = generate_weights(
                model, tokenizer, str(record.get("topic_name", "")).strip(), keywords,
                str(record.get("summary_prompt", "")).strip(),
            )
            worksheet.update_cell(row_index, columns["keyword_weights"], json.dumps(weights, sort_keys=True))
            worksheet.update_cell(row_index, columns["weighting_status"], "complete")
            worksheet.update_cell(row_index, columns["weighting_model"], PITCH_MODEL_NAME)
            worksheet.update_cell(row_index, columns["updated"], datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception:
            worksheet.update_cell(row_index, columns["weighting_status"], "failed")
            raise


if __name__ == "__main__":
    main()
