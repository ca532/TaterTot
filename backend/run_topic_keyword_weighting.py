import json
import os
from datetime import datetime, timezone

import gspread
import torch
from google.oauth2.service_account import Credentials

from client_pitch_generator import PITCH_MODEL_NAME, extract_json, load_model


TOPIC_NAME = os.getenv("TOPIC_NAME", "__all__").strip()
TOPIC_SHEET = os.getenv("TOPIC_CONFIG_SHEET", "Topic Config")
TOPIC_HEADERS = [
    "topic_name", "keywords", "summary_prompt", "active", "updated",
    "keyword_weights", "scoring_policy", "minimum_relevance_score",
    "minimum_distinct_keywords", "high_weight_threshold", "lookback_days",
    "max_articles_per_publication", "require_keyword_in_url",
    "weighting_status", "weighting_model",
]


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


def ensure_topic_headers(worksheet):
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(TOPIC_HEADERS)
        return
    existing_headers = [str(value).strip() for value in values[0]]
    if existing_headers == TOPIC_HEADERS:
        return

    migrated = []
    for row in values[1:]:
        record = {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(existing_headers)
            if header
        }
        if not str(record.get("topic_name", "")).strip():
            continue
        migrated.append([
            record.get("topic_name", ""), record.get("keywords", ""),
            record.get("summary_prompt", ""), record.get("active", "TRUE"),
            record.get("updated", ""), record.get("keyword_weights", ""),
            record.get("scoring_policy", ""), record.get("minimum_relevance_score", "4.0"),
            record.get("minimum_distinct_keywords", "2"),
            record.get("high_weight_threshold", "2.5"), record.get("lookback_days", "14"),
            record.get("max_articles_per_publication", "5"),
            record.get("require_keyword_in_url", "FALSE"),
            record.get("weighting_status", "pending" if record.get("keywords") else "not_required"),
            record.get("weighting_model", ""),
        ])
    worksheet.clear()
    worksheet.update(range_name="A1:O1", values=[TOPIC_HEADERS])
    if migrated:
        worksheet.append_rows(migrated, value_input_option="USER_ENTERED")


def _bounded_float(value, default, minimum, maximum):
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


def _bounded_int(value, default, minimum, maximum):
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def validated_policy(keywords, generated):
    raw_policies = generated.get("keyword_policies", {}) if isinstance(generated, dict) else {}
    normalized = {str(key).strip().lower(): value for key, value in raw_policies.items()}
    policies = {}
    for keyword in keywords:
        item = normalized.get(keyword, {})
        if not isinstance(item, dict):
            item = {}
        weight = round(_bounded_float(item.get("weight"), 1.0, 0.5, 4.0), 2)
        tier = str(item.get("tier", "broad")).strip().lower()
        if tier not in {"core", "supporting", "broad"}:
            tier = "broad"
        standalone = item.get("standalone_eligible") is True
        policies[keyword] = {
            "weight": weight,
            "tier": tier,
            "standalone_eligible": standalone and tier != "broad" and weight >= 2.5,
        }

    return {
        "keyword_policies": policies,
        "minimum_relevance_score": round(
            _bounded_float(generated.get("minimum_relevance_score"), 4.0, 2.0, 8.0), 2
        ),
        "minimum_distinct_keywords": _bounded_int(
            generated.get("minimum_distinct_keywords"), 2, 1, 3
        ),
        "high_weight_threshold": round(
            _bounded_float(generated.get("high_weight_threshold"), 2.5, 2.0, 4.0), 2
        ),
    }


def condense_keywords(
    model,
    tokenizer,
    topic_name,
    keywords,
    summary_prompt,
    minimum=20,
    maximum=30,
):
    if len(keywords) <= maximum:
        return keywords

    messages = [{
        "role": "user",
        "content": (
            "Select the strongest keywords for an article relevance filter.\n"
            f"Topic: {topic_name}\n"
            f"Summary objective: {summary_prompt}\n"
            f"Available keywords: {json.dumps(keywords)}\n\n"
            f"Return between {minimum} and {maximum} keywords.\n"
            "Keep specific, high-signal topic terms and important named entities.\n"
            "Keep a small number of supporting terms only when they add useful coverage.\n"
            "Remove duplicates, weak terms, and broad ambiguous terms that frequently "
            "match unrelated sports, health, politics, crime, celebrity, or shopping stories.\n"
            "Do not invent keywords.\n"
            "Return only valid JSON in this shape:\n"
            '{"selected_keywords":["keyword one","keyword two"]}'
        ),
    }]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=700,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
        )
    text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    generated = extract_json(text)
    raw_selected = generated.get("selected_keywords", []) if isinstance(generated, dict) else []
    allowed = set(keywords)
    selected = list(dict.fromkeys(
        str(keyword).strip().lower()
        for keyword in raw_selected
        if str(keyword).strip().lower() in allowed
    ))
    if not minimum <= len(selected) <= maximum:
        raise ValueError(
            "Qwen returned an invalid condensed keyword list: "
            f"expected {minimum}-{maximum}, received {len(selected)}"
        )
    return selected


def generate_policy(model, tokenizer, topic_name, keywords, summary_prompt):
    messages = [{
        "role": "user",
        "content": (
            "Configure deterministic article-relevance scoring for a media-monitoring pipeline.\n"
            f"Topic: {topic_name}\n"
            f"Summary objective: {summary_prompt}\n"
            f"Keywords: {json.dumps(keywords)}\n\n"
            "General exclusions are sports, health advice, crime, unrelated politics, generic celebrity news, "
            "shopping lists, and landing pages unless directly relevant to the topic objective.\n"
            "For every supplied keyword return a weight from 0.5 to 4.0, a tier of core/supporting/broad, "
            "and standalone_eligible=true only when that keyword independently establishes topic relevance. "
            "Broad or ambiguous terms must not qualify independently. Reserve 3.5-4.0 for unambiguous core "
            "signals and assign broad terms 0.5-1.5. Do not add or remove keywords. "
            "Recommend a minimum_relevance_score from 2.0-8.0, minimum_distinct_keywords from 1-3, and "
            "high_weight_threshold from 2.0-4.0. Return only valid JSON in this shape: "
            '{"keyword_policies":{"keyword":{"weight":1.0,"tier":"broad",'
            '"standalone_eligible":false}},"minimum_relevance_score":4.0,'
            '"minimum_distinct_keywords":2,"high_weight_threshold":2.5}'
        ),
    }]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=1800,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
        )
    text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    generated = extract_json(text)
    raw_policies = generated.get("keyword_policies", {}) if isinstance(generated, dict) else {}
    returned_keywords = {str(key).strip().lower() for key in raw_policies}
    missing_keywords = set(keywords).difference(returned_keywords)
    if not raw_policies or missing_keywords:
        raise ValueError(
            "Qwen returned an incomplete scoring policy; missing keywords: "
            f"{sorted(missing_keywords)}"
        )
    return validated_policy(keywords, generated)


def main():
    worksheet = open_topic_sheet()
    ensure_topic_headers(worksheet)

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
        if (
            str(record.get("weighting_status", "")).strip().lower() == "complete"
            and record.get("scoring_policy")
        ):
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
            topic_name = str(record.get("topic_name", "")).strip()
            summary_prompt = str(record.get("summary_prompt", "")).strip()
            condensed_keywords = condense_keywords(
                model=model,
                tokenizer=tokenizer,
                topic_name=topic_name,
                keywords=keywords,
                summary_prompt=summary_prompt,
            )
            print(
                f"Condensed {topic_name} keywords: "
                f"{len(keywords)} -> {len(condensed_keywords)}"
            )
            policy = generate_policy(
                model=model,
                tokenizer=tokenizer,
                topic_name=topic_name,
                keywords=condensed_keywords,
                summary_prompt=summary_prompt,
            )
            weights = {
                keyword: item["weight"]
                for keyword, item in policy["keyword_policies"].items()
            }
            worksheet.update_cell(
                row_index, columns["keywords"], ", ".join(condensed_keywords)
            )
            worksheet.update_cell(row_index, columns["keyword_weights"], json.dumps(weights, sort_keys=True))
            worksheet.update_cell(row_index, columns["scoring_policy"], json.dumps(policy, sort_keys=True))
            worksheet.update_cell(
                row_index, columns["minimum_relevance_score"], policy["minimum_relevance_score"]
            )
            worksheet.update_cell(
                row_index, columns["minimum_distinct_keywords"], policy["minimum_distinct_keywords"]
            )
            worksheet.update_cell(
                row_index, columns["high_weight_threshold"], policy["high_weight_threshold"]
            )
            worksheet.update_cell(row_index, columns["weighting_status"], "complete")
            worksheet.update_cell(row_index, columns["weighting_model"], PITCH_MODEL_NAME)
            worksheet.update_cell(row_index, columns["updated"], datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception:
            worksheet.update_cell(row_index, columns["weighting_status"], "failed")
            raise


if __name__ == "__main__":
    main()
