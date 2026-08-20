import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from article_quality import keyword_matches, prepare_article_for_classification


MODEL_REPO = os.getenv(
    "KEYWORD_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
).strip()
MODEL_FILE = os.getenv(
    "KEYWORD_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
).strip()

RELEVANT_CATEGORIES = {
    "jewelry_product",
    "luxury_product",
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
- luxury_product
- luxury_brand
- luxury_business
- designer_or_runway
- royal_jewelry
- royal_wardrobe
- luxury_market_trend
- irrelevant

Select a relevant category when the corresponding subject is either:
1. the article's main subject; or
2. a substantial recurring angle that provides useful design, cultural,
   commercial, retail, market, trend, craftsmanship, or PR insight.

A passing, incidental, metaphorical, or unsupported mention is not enough.

Category guide:
jewelry_product: Jewelry, watches, gemstones, collections, launches, design,
craftsmanship, engagement rings, jewelry trends, jewelry retail, merchandising,
or noteworthy product selections. A named luxury brand is not required.
luxury_product: Material coverage of luxury bags, footwear, fragrance, clothing,
accessories, hospitality products, or other premium goods. It must substantially
discuss a recognized luxury brand, designer, collection, craftsmanship, product
development, or commercially meaningful luxury trend.
luxury_brand: A recognized luxury brand, its products, leadership, strategy,
performance, creative direction, retail presence, hospitality, or cultural impact.
luxury_business: Material business news involving luxury companies, revenue,
acquisitions, tariffs, leadership, retail, manufacturing, expansion, investment,
appointments, departures, influential industry figures, or trade.
designer_or_runway: Designer fashion, couture, runway collections, fashion week,
or substantial coverage of significant designer work.
royal_jewelry: Jewelry worn, owned, inherited, commissioned, designed, or
materially discussed in connection with royalty.
royal_wardrobe: Specific clothing, designers, couture, uniforms, dress codes, or
meaningful wardrobe choices involving royalty. The wardrobe coverage must be
concrete and substantial.
luxury_market_trend: Market-wide, retail, or consumer trends involving luxury,
jewelry, watches, gemstones, designer goods, or luxury hospitality.
irrelevant: Use when no relevant category is materially supported.

Rules:
- Choose the most specific materially supported category.
- Jewelry can be relevant without a named luxury brand. Jewelry retail,
  merchandising, market reporting, and editorial product selections may qualify.
- A royal person or royal property alone is never enough.
- Generic royal births, pregnancy, health, relationships, naming, family news,
  homes, palaces, castles, holidays, travel, charity events, and general
  appearances are irrelevant unless specific jewelry or wardrobe coverage is central.
- Generic celebrity relationships, parties, appearances, and outfits are
  irrelevant unless a luxury product, designer, runway, jewelry, or brand angle
  is concrete and substantial.
- Affordable or high-street shopping is irrelevant without a material luxury or
  jewelry angle.
- Generic beauty and fashion are irrelevant without meaningful luxury, designer,
  runway, jewelry, watch, gemstone, or luxury-business coverage.
- Treat diamond, gold, silver, pearl, and aquamarine as irrelevant when used only
  as names, colours, metaphors, entertainment titles, or unrelated terms.
- Evidence must contain 1-3 concise article-specific facts. Do not return isolated
  keywords, category definitions, or wording copied from these instructions.
- Do not invent evidence.

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

    @staticmethod
    def _apply_evidence_policy(decision, title):
        evidence_text = " ".join(decision.luxury_evidence).lower()
        weak_evidence = (
            not decision.luxury_evidence
            or all(len(item.split()) <= 2 for item in decision.luxury_evidence)
            or "generic celebrity news" in evidence_text
            or "generic royal news" in evidence_text
        )
        if weak_evidence:
            print(
                "[CLASSIFIER_WEAK_EVIDENCE] "
                f"category={decision.category} title={title!r}; decision retained"
            )

        required_terms = {
            "royal_jewelry": {
                "jewel", "jewelry", "jewellery", "diamond", "gemstone",
                "tiara", "brooch", "necklace", "earring", "bracelet", "ring",
                "crown", "regalia", "sapphire", "emerald", "ruby", "pearl",
            },
            "royal_wardrobe": {
                "dress", "gown", "coat", "jacket", "suit", "skirt", "trouser",
                "jeans", "tie", "tartan", "kilt", "uniform", "couture",
                "designer", "wardrobe", "outfit", "clothing", "garment",
            },
        }
        terms = required_terms.get(decision.category)
        if decision.relevant and decision.luxury_evidence and terms:
            if not keyword_matches(evidence_text, terms):
                print(
                    "[CLASSIFIER_POLICY_VETO] "
                    f"category={decision.category} rule=missing_concrete_evidence "
                    f"title={title!r}"
                )
                return RelevanceDecision(
                    relevant=False,
                    category="irrelevant",
                    luxury_evidence=decision.luxury_evidence,
                    reason=(
                        f"Rejected unsupported {decision.category}: "
                        + "; ".join(decision.luxury_evidence)
                    )[:500],
                )
        return decision

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

        cleaned_article = prepare_article_for_classification(article_text)
        payload = {
            "title": str(title or "").strip(),
            "publication": str(publication or "").strip(),
            "article_excerpt": cleaned_article[:4000],
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
                decision = self._decision_from_result(self._parse_json(content))
                return self._apply_evidence_policy(decision, payload["title"])
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
