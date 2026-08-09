import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from article_quality import keyword_matches


MODEL_REPO = os.getenv(
    "KEYWORD_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
).strip()
MODEL_FILE = os.getenv(
    "KEYWORD_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
).strip()

ACCEPTED_CATEGORIES = {
    "jewelry_product",
    "luxury_brand",
    "luxury_business",
    "designer_or_runway",
    "royal_jewelry",
    "royal_wardrobe",
    "luxury_market_trend",
}
ALL_CATEGORIES = ACCEPTED_CATEGORIES | {
    "generic_royal_news",
    "generic_celebrity_news",
    "generic_fashion",
    "mass_market_shopping",
    "sports",
    "health",
    "politics",
    "entertainment",
    "irrelevant",
}

SYSTEM_PROMPT = """You classify articles for a luxury and fine-jewelry PR roundup.

An article is relevant only when luxury, jewelry, a recognized luxury brand,
designer work, runway activity, luxury-sector business, or an approved royal
wardrobe/jewelry angle is a primary subject. A passing mention is insufficient.

Generic royal births, health, relationships, family news, sports, politics,
entertainment, beauty, affordable shopping, or items that merely look luxurious
are irrelevant unless the article contains a material approved luxury angle.

Use exactly one category from this list:
jewelry_product, luxury_brand, luxury_business, designer_or_runway,
royal_jewelry, royal_wardrobe, luxury_market_trend, generic_royal_news,
generic_celebrity_news, generic_fashion, mass_market_shopping, sports, health,
politics, entertainment, irrelevant.

Examples:
- Princess welcomes newborn -> generic_royal_news, irrelevant.
- Royal hospitalized after heart failure -> generic_royal_news, irrelevant.
- Princess wears Garrard diamond tiara -> royal_jewelry, relevant.
- Kering jewelry revenue rises 14% -> luxury_business, relevant.
- Boucheron launches high-jewelry collection -> jewelry_product, relevant.
- GBP 28 jacket looks luxurious -> mass_market_shopping, irrelevant.

Return JSON only with relevant (boolean), category (string), luxury_evidence
(array of exact facts from the input), and reason (short string)."""


@dataclass
class RelevanceDecision:
    relevant: bool
    category: str
    luxury_evidence: list[str]
    reason: str


class ArticleRelevanceClassifier:
    def __init__(self):
        self.model = None
        self.disabled_reason = ""

    def _load(self):
        if self.model is not None:
            return self.model
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
        self.model = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_threads=max(1, (os.cpu_count() or 2) - 1),
            n_batch=256,
            verbose=False,
        )
        return self.model

    @staticmethod
    def _parse_json(text):
        decoder = json.JSONDecoder()
        value = str(text or "")
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

    @staticmethod
    def _decision_from_result(result):
        category = str(result.get("category", "")).strip().lower()
        raw_evidence = result.get("luxury_evidence", [])
        evidence = (
            [str(value).strip() for value in raw_evidence if str(value).strip()]
            if isinstance(raw_evidence, list)
            else []
        )
        if category not in ALL_CATEGORIES:
            raise ValueError(f"unsupported category: {category or '<empty>'}")
        relevant = (
            result.get("relevant") is True
            and category in ACCEPTED_CATEGORIES
            and bool(evidence)
        )
        return RelevanceDecision(
            relevant=relevant,
            category=category,
            luxury_evidence=evidence,
            reason=str(result.get("reason", "")).strip()[:500],
        )

    def classify(
        self,
        title,
        publication,
        article_text,
        matched_keywords=None,
        matched_entities=None,
        matched_concepts=None,
    ):
        if self.disabled_reason:
            return RelevanceDecision(
                relevant=True,
                category="classifier_unavailable",
                luxury_evidence=["deterministic relevance gate fallback"],
                reason=self.disabled_reason,
            )

        payload = {
            "title": str(title or "").strip(),
            "publication": str(publication or "").strip(),
            "article_excerpt": str(article_text or "")[:2000],
            "matched_keywords": list(matched_keywords or []),
            "matched_entities": list(matched_entities or []),
            "matched_concepts": list(matched_concepts or []),
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        last_error = "no response"
        for attempt in range(1, 4):
            try:
                response = self._load().create_chat_completion(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=300,
                    response_format={"type": "json_object"},
                )
                content = response["choices"][0]["message"]["content"]
                return self._decision_from_result(self._parse_json(content))
            except Exception as exc:
                last_error = str(exc)
                print(
                    f"[CLASSIFIER_RETRY] attempt={attempt}/3 "
                    f"title={payload['title']!r} error={last_error[:160]}"
                )
        print(
            f"[CLASSIFIER_ERROR] title={payload['title']!r} "
            f"reason={last_error[:300]}"
        )
        if self.model is None:
            self.disabled_reason = f"Classifier unavailable: {last_error[:300]}"
            print(
                "[CLASSIFIER_FALLBACK] model unavailable; deterministic relevance "
                "gate will be used for the rest of this run"
            )
            return RelevanceDecision(
                relevant=True,
                category="classifier_unavailable",
                luxury_evidence=["deterministic relevance gate fallback"],
                reason=self.disabled_reason,
            )
        return RelevanceDecision(
            relevant=False,
            category="irrelevant",
            luxury_evidence=[],
            reason="Classifier failed to return valid output",
        )

    def evaluate_fixture(self, keywords):
        fixture_path = (
            Path(__file__).parent / "tests" / "fixtures" / "luxury_relevance_20260805.json"
        )
        if not fixture_path.exists():
            print(f"[CLASSIFIER_EVAL_WARNING] fixture missing: {fixture_path}")
            return {}

        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        tp = fp = tn = fn = 0
        categories = defaultdict(lambda: {"total": 0, "correct": 0, "errors": 0})
        for item in fixtures:
            text = f'{item["title"]} {item["summary"]}'
            decision = self.classify(
                title=item["title"],
                publication=item.get("publication", ""),
                article_text=item["summary"],
                matched_keywords=keyword_matches(text, keywords),
            )
            expected = item["expected"] == "keep"
            correct = decision.relevant == expected
            stats = categories[decision.category]
            stats["total"] += 1
            stats["correct"] += int(correct)
            stats["errors"] += int(not correct)
            if expected and decision.relevant:
                tp += 1
            elif expected:
                fn += 1
                print(
                    f"[CLASSIFIER_FALSE_NEGATIVE] predicted={decision.category} "
                    f"title={item['title']!r}"
                )
            elif decision.relevant:
                fp += 1
                print(
                    f"[CLASSIFIER_FALSE_POSITIVE] category={decision.category} "
                    f"title={item['title']!r}"
                )
            else:
                tn += 1

        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)
        false_positive_rate = fp / max(1, fp + tn)
        print(
            "[CLASSIFIER_EVAL] "
            f"tp={tp} fp={fp} tn={tn} fn={fn} precision={precision:.3f} "
            f"recall={recall:.3f} false_positive_rate={false_positive_rate:.3f}"
        )
        for category, stats in sorted(categories.items()):
            print(
                f"[CLASSIFIER_CATEGORY] category={category} total={stats['total']} "
                f"correct={stats['correct']} errors={stats['errors']}"
            )
        return {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "false_positive_rate": false_positive_rate,
            "categories": dict(categories),
        }
