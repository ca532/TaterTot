import json
import os
from datetime import datetime, timezone

import gspread
import torch
from google.oauth2.service_account import Credentials

from client_pitch_generator import PITCH_MODEL_NAME, load_model


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


def extract_generated_json(text):
    decoder = json.JSONDecoder()
    value = text or ""
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def policy_from_absolute_score(value):
    score = round(_bounded_float(value, 20.0, 0.0, 100.0), 2)
    if score >= 80:
        weight, tier, standalone = 4.0, "core", True
    elif score >= 60:
        weight, tier, standalone = 3.0, "supporting", False
    elif score >= 40:
        weight, tier, standalone = 2.0, "supporting", False
    elif score >= 20:
        weight, tier, standalone = 1.0, "broad", False
    else:
        weight, tier, standalone = 0.5, "broad", False
    return {
        "absolute_score": score,
        "weight": weight,
        "tier": tier,
        "standalone_eligible": standalone,
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
    generated = extract_generated_json(text)
    raw_selected = generated.get("selected_keywords", []) if isinstance(generated, dict) else []
    allowed = set(keywords)
    selected = list(dict.fromkeys(
        str(keyword).strip().lower()
        for keyword in raw_selected
        if str(keyword).strip().lower() in allowed
    ))
    selected = selected[:maximum]
    if len(selected) < minimum:
        remaining = [keyword for keyword in keywords if keyword not in selected]
        selected.extend(remaining[:minimum - len(selected)])

    print(
        f"Qwen selected {len(raw_selected)} entries; "
        f"using {len(selected)} validated keywords"
    )
    return selected


def generate_policy(model, tokenizer, topic_name, keywords, summary_prompt):
    messages = [{
        "role": "user",
        "content": (
            "Score keyword relevance for a media-monitoring pipeline.\n"
            f"Topic: {topic_name}\n"
            f"Summary objective: {summary_prompt}\n"
            f"Keywords: {json.dumps(keywords)}\n\n"
            "For every supplied keyword, assign an absolute score from 0 to 100 answering: "
            "how strongly does this keyword independently establish that an article is relevant "
            "to the topic and summary objective? Score 80-100 only for unmistakable independent "
            "topic signals, 60-79 for strong signals needing context, 40-59 for supporting terms, "
            "and 0-39 for broad or ambiguous terms. General sports, health advice, crime, unrelated "
            "politics, generic celebrity news, and shopping terms must score below 40 unless the "
            "keyword itself is an unmistakable topic signal. Do not add or remove keywords. "
            "Return only valid JSON in this shape: "
            '{"keyword_scores":{"keyword":75}}'
        ),
    }]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=3000)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=900,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
        )
    text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    generated = extract_generated_json(text)
    raw_scores = generated.get("keyword_scores", {}) if isinstance(generated, dict) else {}
    if not raw_scores and isinstance(generated, dict):
        supplied = set(keywords)
        direct_scores = {
            str(keyword).strip().lower(): score
            for keyword, score in generated.items()
            if str(keyword).strip().lower() in supplied
        }
        raw_scores = direct_scores
    if not raw_scores:
        print(f"Qwen score response was not parseable; preview: {text[:300]!r}")
    normalized_scores = {
        str(keyword).strip().lower(): score
        for keyword, score in raw_scores.items()
    } if isinstance(raw_scores, dict) else {}
    missing_keywords = [keyword for keyword in keywords if keyword not in normalized_scores]
    if missing_keywords:
        print(
            f"Qwen omitted {len(missing_keywords)} keyword scores; "
            "using conservative score 20 for those keywords"
        )

    return {
        "keyword_policies": {
            keyword: policy_from_absolute_score(normalized_scores.get(keyword, 20.0))
            for keyword in keywords
        },
        "minimum_relevance_score": 4.0,
        "minimum_distinct_keywords": 2,
        "high_weight_threshold": 4.0,
    }


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
