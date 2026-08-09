import json
import os
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from article_quality import keyword_matches, relevance_gate_reason


TOPIC_NAME = os.getenv("TOPIC_NAME", "__all__").strip()
TOPIC_SHEET = os.getenv("TOPIC_CONFIG_SHEET", "Topic Config")
KEYWORD_MODEL_REPO = os.getenv(
    "KEYWORD_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
).strip()
KEYWORD_MODEL_FILE = os.getenv(
    "KEYWORD_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
).strip()
KEYWORD_MODEL_ID = f"{KEYWORD_MODEL_REPO}:{KEYWORD_MODEL_FILE}"
GENERATION_ATTEMPTS = 3
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


def load_keyword_model():
    model_path = hf_hub_download(
        repo_id=KEYWORD_MODEL_REPO,
        filename=KEYWORD_MODEL_FILE,
    )
    return Llama(
        model_path=model_path,
        n_ctx=4096,
        n_threads=max(1, (os.cpu_count() or 2) - 1),
        n_batch=256,
        verbose=False,
    )


def generate_json(model, messages, label, validator=None):
    last_error = "no response"
    for attempt in range(1, GENERATION_ATTEMPTS + 1):
        try:
            response = model.create_chat_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=700,
                response_format={"type": "json_object"},
            )
            text = response["choices"][0]["message"]["content"]
            generated = extract_generated_json(text)
            if generated and (validator is None or validator(generated)):
                return generated
            last_error = "response JSON was missing or failed schema validation"
        except Exception as exc:
            last_error = str(exc)
        print(
            f"  {label} attempt {attempt}/{GENERATION_ATTEMPTS} failed: "
            f"{last_error[:160]}"
        )
    raise RuntimeError(f"{label} failed after {GENERATION_ATTEMPTS} attempts: {last_error}")


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
        weight, tier, standalone = 0.5, "weak", False
    return {
        "absolute_score": score,
        "weight": weight,
        "tier": tier,
        "standalone_eligible": standalone,
    }


def generate_topic_context(model, topic_name, keywords, summary_prompt):
    messages = [{
        "role": "user",
        "content": (
            "Create secondary relevance signals for a media-monitoring topic.\n"
            f"Topic: {topic_name}\n"
            f"Summary objective: {summary_prompt}\n"
            f"Configured keywords: {json.dumps(keywords)}\n\n"
            "Return up to 25 topic_entities and up to 20 supporting_concepts. "
            "Topic entities are recognizable companies, brands, institutions, people, "
            "products, or organizations that strongly establish relevance. Supporting "
            "concepts are specific industry events, product categories, heritage themes, "
            "or professional editorial concepts that can establish relevance when paired "
            "with another topic signal. Avoid generic words, broad news categories, and "
            "entities unrelated to the summary objective. Use short lowercase phrases. "
            "Return only valid JSON in this shape:\n"
            '{"topic_entities":["entity"],"supporting_concepts":["concept"]}'
        ),
    }]
    generated = generate_json(
        model,
        messages,
        "Topic context generation",
        validator=lambda value: (
            isinstance(value.get("topic_entities"), list)
            and len(value["topic_entities"]) >= 3
            and isinstance(value.get("supporting_concepts"), list)
            and len(value["supporting_concepts"]) >= 3
        ),
    )

    def validated_list(field, maximum):
        raw = generated.get(field, []) if isinstance(generated, dict) else []
        return list(dict.fromkeys(
            str(value).strip().lower()
            for value in raw
            if isinstance(value, str)
            and 2 <= len(value.strip()) <= 80
            and not value.strip().lower().startswith(("http://", "https://"))
        ))[:maximum]

    entities = validated_list("topic_entities", 25)
    concepts = validated_list("supporting_concepts", 20)
    if len(entities) < 3:
        raise RuntimeError(
            f"Topic context returned only {len(entities)} entities; at least 3 are required"
        )
    if len(concepts) < 3:
        raise RuntimeError(
            f"Topic context returned only {len(concepts)} supporting concepts; at least 3 are required"
        )
    print(
        f"Generated topic context: {len(entities)} entities, "
        f"{len(concepts)} supporting concepts"
    )
    return entities, concepts


def generate_policy(
    model,
    topic_name,
    keywords,
    summary_prompt,
    batch_size=15,
):
    all_scores = {}

    for start in range(0, len(keywords), batch_size):
        batch = keywords[start:start + batch_size]
        batch_number = (start // batch_size) + 1
        total_batches = (len(keywords) + batch_size - 1) // batch_size

        messages = [{
            "role": "user",
            "content": (
                "Score keyword relevance for a media-monitoring pipeline.\n"
                f"Topic: {topic_name}\n"
                f"Summary objective: {summary_prompt}\n"
                f"Keywords: {json.dumps(batch)}\n\n"
                "For every supplied keyword, assign an absolute score from 0 to 100. "
                "The score answers how strongly that keyword independently establishes "
                "that an article is relevant to the topic and summary objective.\n\n"
                "Use this scale:\n"
                "- 80-100: unmistakable independent topic signal\n"
                "- 60-79: strong signal that may need context\n"
                "- 40-59: useful supporting term\n"
                "- 20-39: broad or ambiguous term\n"
                "- 0-19: weak, misleading, or generally irrelevant term\n\n"
                "Sports, health advice, crime, unrelated politics, generic celebrity "
                "news, and generic shopping terms must score below 40 unless the "
                "keyword itself unmistakably establishes relevance to this topic.\n\n"
                "Return every supplied keyword exactly once. "
                "Do not add, remove, rename, combine, or paraphrase keywords. "
                "Return only valid JSON in this exact shape:\n"
                '{"keyword_scores":{"keyword":75}}'
            ),
        }]

        def complete_keyword_batch(value):
            raw = value.get("keyword_scores", value)
            if not isinstance(raw, dict):
                return False
            normalized = {
                str(keyword).strip().lower(): score
                for keyword, score in raw.items()
            }
            return all(
                keyword in normalized
                and isinstance(normalized[keyword], (int, float))
                and not isinstance(normalized[keyword], bool)
                and 0 <= float(normalized[keyword]) <= 100
                for keyword in batch
            )

        generated = generate_json(
            model,
            messages,
            f"Keyword batch {batch_number}/{total_batches}",
            validator=complete_keyword_batch,
        )
        raw_scores = (
            generated.get("keyword_scores", {})
            if isinstance(generated, dict)
            else {}
        )

        if not raw_scores and isinstance(generated, dict):
            allowed = set(batch)
            raw_scores = {
                str(keyword).strip().lower(): score
                for keyword, score in generated.items()
                if str(keyword).strip().lower() in allowed
            }

        normalized_scores = {
            str(keyword).strip().lower(): float(score)
            for keyword, score in raw_scores.items()
            if isinstance(score, (int, float))
            and not isinstance(score, bool)
            and 0 <= float(score) <= 100
        } if isinstance(raw_scores, dict) else {}

        missing = []
        for keyword in batch:
            if keyword in normalized_scores:
                all_scores[keyword] = normalized_scores[keyword]
            else:
                missing.append(keyword)

        print(
            f"Scored keyword batch {batch_number}/{total_batches}: "
            f"{len(batch) - len(missing)}/{len(batch)} returned by Qwen"
        )
        if missing:
            raise RuntimeError(
                f"Keyword batch {batch_number}/{total_batches} omitted: "
                f"{', '.join(missing)}"
            )

    topic_entities, supporting_concepts = generate_topic_context(
        model=model,
        topic_name=topic_name,
        keywords=keywords,
        summary_prompt=summary_prompt,
    )
    return {
        "keyword_policies": {
            keyword: policy_from_absolute_score(all_scores[keyword])
            for keyword in keywords
        },
        "minimum_relevance_score": 4.0,
        "minimum_distinct_keywords": 2,
        "high_weight_threshold": 4.0,
        "topic_entities": topic_entities,
        "supporting_concepts": supporting_concepts,
    }


def validate_policy_against_fixture(topic_name, keywords, policy):
    """Block publication of a Luxury policy that regresses the labeled corpus."""
    if topic_name.strip().lower() != "luxury":
        return
    fixture_path = (
        Path(__file__).parent / "tests" / "fixtures" / "luxury_relevance_20260805.json"
    )
    if not fixture_path.exists():
        raise RuntimeError(f"Luxury relevance fixture is missing: {fixture_path}")

    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    policy_map = policy["keyword_policies"]
    entities = policy["topic_entities"]
    concepts = policy["supporting_concepts"]
    false_positives = []
    false_negatives = []

    for item in fixtures:
        full_text = f'{item["title"]} {item["summary"]}'
        anchor_text = f'{item["title"]} {item["summary"][:1000]}'
        matched = keyword_matches(full_text, keywords)
        anchors = keyword_matches(anchor_text, keywords)
        score = sum(policy_map[keyword]["weight"] for keyword in matched)
        accepted = not relevance_gate_reason(
            score=score,
            matched_keywords=matched,
            anchor_keywords=anchors,
            title=item["title"],
            keyword_policy_map=policy_map,
            minimum_relevance_score=policy["minimum_relevance_score"],
            minimum_distinct_keywords=policy["minimum_distinct_keywords"],
            topic_entity_matches=keyword_matches(anchor_text, entities),
            supporting_concept_matches=keyword_matches(anchor_text, concepts),
        )
        if item["expected"] == "keep" and not accepted:
            false_negatives.append(item["title"])
        elif item["expected"] == "reject" and accepted:
            false_positives.append(item["title"])

    expected_keeps = sum(item["expected"] == "keep" for item in fixtures)
    expected_rejects = len(fixtures) - expected_keeps
    retained = expected_keeps - len(false_negatives)
    rejected = expected_rejects - len(false_positives)
    if retained < 36 or rejected < 42:
        raise RuntimeError(
            "Generated Luxury policy failed regression: "
            f"retained {retained}/{expected_keeps}, rejected {rejected}/{expected_rejects}; "
            f"false positives={false_positives[:5]}, false negatives={false_negatives[:5]}"
        )
    print(
        f"Luxury policy regression passed: retained {retained}/{expected_keeps}, "
        f"rejected {rejected}/{expected_rejects}"
    )


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
            and str(record.get("weighting_model", "")).strip() == KEYWORD_MODEL_ID
        ):
            continue
        selected.append((index, record, keywords))

    if not matched_topic:
        raise RuntimeError(f"Active topic configuration '{TOPIC_NAME}' was not found")

    if not selected:
        print("No topic keyword weights require generation")
        return

    model = load_keyword_model()
    for row_index, record, keywords in selected:
        try:
            topic_name = str(record.get("topic_name", "")).strip()
            summary_prompt = str(record.get("summary_prompt", "")).strip()
            print(
                f"Generating scores for all {len(keywords)} "
                f"{topic_name} keywords in batches"
            )
            policy = generate_policy(
                model=model,
                topic_name=topic_name,
                keywords=keywords,
                summary_prompt=summary_prompt,
            )
            validate_policy_against_fixture(topic_name, keywords, policy)
            weights = {
                keyword: item["weight"]
                for keyword, item in policy["keyword_policies"].items()
            }
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
            worksheet.update_cell(
                row_index,
                columns["weighting_model"],
                KEYWORD_MODEL_ID,
            )
            worksheet.update_cell(row_index, columns["updated"], datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception:
            worksheet.update_cell(row_index, columns["weighting_status"], "failed")
            raise


if __name__ == "__main__":
    main()
