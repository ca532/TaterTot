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

RELEVANT_CATEGORIES = {
    "jewelry_product",
    "luxury_brand",
    "luxury_business",
    "designer_or_runway",
    "royal_jewelry",
    "royal_wardrobe",
    "luxury_market_trend",
}
ALL_CATEGORIES = RELEVANT_CATEGORIES | {"irrelevant"}

SYSTEM_PROMPT = """
You classify articles for a luxury and fine-jewelry PR roundup.

Choose exactly one category:
- jewelry_product
- luxury_brand
- luxury_business
- designer_or_runway
- royal_jewelry
- royal_wardrobe
- luxury_market_trend
- irrelevant

Use irrelevant unless the article's main subject is clearly related to jewelry, watches, gemstones, luxury brands, luxury business, designer fashion/runway, or material royal jewelry/wardrobe coverage.

Category guide:
jewelry_product: Jewelry, watches, gemstones, jewelry collections, launches, design, craftsmanship, engagement rings, or jewelry trends.
luxury_brand: A recognized luxury brand, its products, leadership, strategy, performance, or cultural impact.
luxury_business: Business news about luxury companies, revenue, acquisitions, tariffs, leadership, retail, manufacturing, or trade.
designer_or_runway: Designer fashion, couture, runway, fashion week, or significant designer work.
royal_jewelry: Jewelry worn, owned, inherited, commissioned, or discussed in connection with royalty.
royal_wardrobe: Specific clothing, designers, couture, uniforms, dress codes, or meaningful wardrobe choices involving royalty.
luxury_market_trend: Market-wide or consumer trends involving luxury, jewelry, watches, gemstones, or designer goods.
irrelevant: Generic celebrity news, generic royal news, entertainment, sports, politics, health, beauty, affordable shopping, or generic fashion without a clear luxury, designer, runway, jewelry, watch, gemstone, or luxury-business angle.

Rules:
- Classify by the article's main subject, not passing mentions.
- Jewelry can be relevant even without a named luxury brand.
- A royal person alone is not enough.
- Royal homes, holidays, births, health, relationships, charity events, or general appearances are irrelevant unless specific jewelry or wardrobe coverage is central.
- Generic celebrity outfits are irrelevant unless there is a clear luxury designer, runway, jewelry, or brand angle.
- If unsure, choose irrelevant.

Return JSON only in this format:

{
"category": "one allowed category",
"evidence": ["concise fact supporting the selected category"]
}"""


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
        raw_evidence = result.get("evidence", [])
        evidence = (
            [str(value).strip() for value in raw_evidence if str(value).strip()]
            if isinstance(raw_evidence, list)
            else []
        )
        if category not in ALL_CATEGORIES:
            raise ValueError(f"unsupported category: {category or '<empty>'}")
        relevant = category in RELEVANT_CATEGORIES
        if not evidence:
            print(
                "[CLASSIFIER_EVIDENCE_WARNING] "
                f"category={category} returned without supporting evidence; "
                "decision retained"
            )
        return RelevanceDecision(
            relevant=relevant,
            category=category,
            luxury_evidence=evidence,
            reason="; ".join(evidence)[:500],
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
