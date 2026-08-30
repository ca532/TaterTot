import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from article_quality import keyword_matches, prepare_article_for_classification
from qwen_runtime import get_qwen_model


MODEL_REPO = os.getenv(
    "KEYWORD_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
).strip()
MODEL_FILE = os.getenv(
    "KEYWORD_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
).strip()

PRIMARY_CATEGORIES = {
    "jewelry_product",
    "luxury_product",
    "luxury_brand",
    "luxury_business",
    "designer_or_runway",
    "royal_jewelry",
    "royal_wardrobe",
    "luxury_market_trend",
}
RESERVE_CATEGORIES = {
    "general_royal_news",
    "celebrity_style",
    "high_street_fashion",
    "general_beauty_trend",
    "consumer_lifestyle",
    "publication_meta",
}
RELEVANT_CATEGORIES = PRIMARY_CATEGORIES | RESERVE_CATEGORIES
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
- general_royal_news
- celebrity_style
- high_street_fashion
- general_beauty_trend
- consumer_lifestyle
- publication_meta
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
general_royal_news: Material royal deaths, succession, health, family, official
communications, state occasions, palaces, relationships, or monarchy where
jewelry and wardrobe are not central.
celebrity_style: Celebrity clothing or appearance coverage with some fashion
relevance but without substantial luxury, jewelry, designer, or business analysis.
high_street_fashion: Shopping or trend coverage centered on affordable or
mass-market retailers.
general_beauty_trend: Beauty, hair, nail, makeup, or skincare trends without a
substantial luxury-business or luxury-brand angle.
consumer_lifestyle: Consumer products, fragrance, home, shopping, or lifestyle
collections that are premium-adjacent but not meaningfully luxury.
publication_meta: Guest-edited issues, magazine announcements, editorial
promotions, and other publication-focused stories.
irrelevant: Use when no relevant category is materially supported.

Rules:
- Choose the most specific materially supported category.
- Jewelry can be relevant without a named luxury brand. Jewelry retail,
  merchandising, market reporting, and editorial product selections may qualify.
- A royal person or royal property alone is not enough for a primary category.
  Use general_royal_news for material royal reporting without a jewelry or
  wardrobe focus.
- Use celebrity_style for generic celebrity appearances or outfits that have
  fashion relevance but lack a concrete luxury, jewelry, or designer focus.
- Use high_street_fashion for affordable or mass-market shopping coverage.
- Use general_beauty_trend for generic beauty, hair, nail, makeup, or skincare
  trends without meaningful luxury-brand or luxury-business coverage.
- Treat diamond, gold, silver, pearl, and aquamarine as irrelevant when used only
  as names, colours, metaphors, entertainment titles, or unrelated terms.
- For every category except irrelevant, evidence must contain 1-2 exact quotations
  copied from the article excerpt. Each quotation must contain at least five words.
- Do not return isolated keywords, category definitions, or wording copied from
  these instructions.
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


ROYAL_TERMS = {
    "royal", "king", "queen", "prince", "princess", "duke", "duchess",
    "monarch", "monarchy", "palace", "sovereign",
}
GARMENT_TERMS = {
    "dress", "gown", "coat", "jacket", "suit", "skirt", "trouser",
    "jeans", "tie", "tartan", "kilt", "uniform", "couture", "designer",
    "wardrobe", "outfit", "clothing", "garment",
}
NON_MONARCHY_ROYAL_PHRASES = {
    "royal navy", "royal air force", "royal marines", "royal society",
    "royal academy", "royal opera", "royal caribbean", "royal mail",
}
UNAMBIGUOUS_MONARCHY_TERMS = {
    "king", "queen", "duke", "duchess", "monarch", "monarchy",
    "throne", "succession", "coronation", "sovereign", "regent",
    "royal family", "royal household", "royal court", "state visit",
    "crown prince", "crown princess",
}
ROYAL_PERSON_TITLES = {"prince", "princess"}
ROYAL_PERSON_CONTEXT_TERMS = {
    "said", "announced", "attended", "visited", "met", "joined",
    "married", "wedding", "husband", "wife", "daughter", "son",
    "mother", "father", "family", "health", "hospital", "died",
    "death", "born", "reign", "engagement", "official visit",
}
FASHION_DESIGN_TERMS = GARMENT_TERMS | {
    "fashion", "textile", "fabric", "craftsmanship", "sustainable fashion",
}
JEWELRY_TERMS = {
    "jewel", "jewels", "jewelry", "jewellery", "diamond", "diamonds",
    "gemstone", "gemstones", "tiara", "tiaras", "brooch", "brooches",
    "necklace", "necklaces", "earring", "earrings", "bracelet",
    "bracelets", "ring", "rings", "pendant", "pendants", "bangle",
    "bangles", "choker", "chokers", "chain", "chains", "watch",
    "watches", "timepiece", "timepieces", "regalia", "sapphire",
    "sapphires", "emerald", "emeralds", "ruby", "rubies", "pearl",
    "pearls", "platinum", "carat", "carats", "karat", "karats",
}
JEWELRY_PROMOTION_CATEGORIES = {
    "general_royal_news", "celebrity_style", "general_beauty_trend",
    "consumer_lifestyle",
}
JEWELRY_CONTEXT_TERMS = {
    "jewel", "jewels", "jewelry", "jewellery", "jeweler", "jeweller",
    "brooch", "brooches", "necklace", "necklaces", "earring", "earrings",
    "bracelet", "bracelets", "ring", "rings", "pendant", "pendants",
    "bangle", "bangles", "choker", "chokers", "watch", "watches",
    "timepiece", "timepieces", "carat", "carats", "karat", "karats",
    "high jewelry", "high jewellery", "fine jewelry", "fine jewellery",
    "set with", "set in", "mounted", "auction",
}
BUSINESS_TERMS = {
    "revenue", "sales", "earnings", "profit", "acquisition", "investment",
    "leadership", "chief executive", "ceo", "expansion", "manufacturing",
    "retail", "store opening", "appointment", "departure", "performance",
    "results", "quarter", "fiscal", "margin", "strategy", "company",
    "executive", "shares", "stock",
}
MARKET_TERMS = {
    "market", "research", "analyst", "consumer spending", "demand", "growth",
    "decline", "forecast", "survey", "report", "market share",
}
MASS_MARKET_BRANDS = {
    "h&m", "mango", "zara", "primark", "asos", "uniqlo", "new look",
    "marks & spencer",
}
CONSUMER_LIFESTYLE_BRANDS = {"bath & body works"}
SHOPPING_TERMS = {
    "shop now", "buy now", "get the look", "under £", "under $", "affordable",
    "looks luxe", "looks expensive", "must-have", "buyers guide", "buyer's guide",
}
BEAUTY_TERMS = {
    "manicure", "nails", "makeup", "skincare", "haircut", "hairstyle",
    "beauty trend",
}
CELEBRITY_STYLE_TERMS = {
    "celebrity", "red carpet", "premiere", "date-night", "date night", "look",
    "fit", "outfit", "wore", "wearing", "style", "fashion", "ensemble",
    "costar", "actor", "actress",
}


def _normalized_for_evidence(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _contains_any(text: str, terms) -> bool:
    normalized = _normalized_for_evidence(text)
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(_normalized_for_evidence(term))}"
            rf"(?![a-z0-9])",
            normalized,
        )
        for term in terms
    )


def _evidence_is_supported(evidence: list[str], article_text: str) -> bool:
    normalized_article = _normalized_for_evidence(article_text)
    if not evidence:
        return False
    for quote in evidence:
        normalized_quote = _normalized_for_evidence(quote)
        if len(normalized_quote.split()) < 5:
            return False
        if normalized_quote not in normalized_article:
            return False
    return True


def _decision(category: str, evidence: list[str], reason: str):
    return RelevanceDecision(
        relevant=category in RELEVANT_CATEGORIES,
        category=category,
        luxury_evidence=evidence,
        reason=reason[:500],
    )


class ArticleRelevanceClassifier:
    def __init__(self):
        self.model = None
        self.disabled_reason = ""

    def _load(self):
        if self.model is not None:
            return self.model
        self.model = get_qwen_model(MODEL_REPO, MODEL_FILE)
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
                "decision remains subject to evidence policy"
            )
        return RelevanceDecision(
            relevant=relevant,
            category=category,
            luxury_evidence=evidence,
            reason="; ".join(evidence)[:500],
        )

    @staticmethod
    def _apply_evidence_policy(decision, title, article_text):
        if decision.category == "irrelevant":
            return decision

        evidence = decision.luxury_evidence
        evidence_text = " ".join(evidence)
        complete_text = f"{title} {article_text}"

        if not _evidence_is_supported(evidence, complete_text):
            print(
                "[CLASSIFIER_POLICY_VETO] "
                f"category={decision.category} rule=unsupported_evidence "
                f"title={title!r}"
            )
            return _decision(
                "irrelevant",
                evidence,
                "Classifier evidence was not copied from the article",
            )

        subject_text = f"{title} {evidence_text}"

        # Promote reserve classifications only when both the headline and the
        # supplied article establish jewelry as a material subject. This avoids
        # hard-coding brands or stories while rejecting metaphorical matches.
        if (
            decision.category in JEWELRY_PROMOTION_CATEGORIES
            and _contains_any(title, JEWELRY_TERMS)
            and _contains_any(article_text, JEWELRY_CONTEXT_TERMS)
        ):
            promoted_category = (
                "royal_jewelry"
                if _contains_any(complete_text, UNAMBIGUOUS_MONARCHY_TERMS)
                else "jewelry_product"
            )
            decision = _decision(
                promoted_category,
                evidence,
                "Headline and article establish jewelry as the material subject",
            )

        # Validate that general royal news concerns monarchy rather than an
        # organization, location, company, or other use of a royal title.
        if decision.category == "general_royal_news":
            if _contains_any(subject_text, NON_MONARCHY_ROYAL_PHRASES):
                return _decision(
                    "irrelevant",
                    evidence,
                    "Royal refers to a non-monarchy organization",
                )
            has_person_context = (
                _contains_any(subject_text, ROYAL_PERSON_TITLES)
                and _contains_any(subject_text, ROYAL_PERSON_CONTEXT_TERMS)
            )
            if (
                not _contains_any(subject_text, UNAMBIGUOUS_MONARCHY_TERMS)
                and not has_person_context
            ):
                return _decision(
                    "irrelevant",
                    evidence,
                    "No concrete monarchy subject",
                )

        # Preserve fashion coverage that Qwen mistakenly labels as beauty.
        if decision.category == "general_beauty_trend" and not _contains_any(
            f"{title} {evidence_text}", BEAUTY_TERMS
        ):
            if _contains_any(complete_text, FASHION_DESIGN_TERMS):
                return _decision(
                    "consumer_lifestyle",
                    evidence,
                    "Fashion or garment coverage without a beauty focus",
                )
            return _decision(
                "irrelevant",
                evidence,
                "No concrete beauty or fashion subject",
            )

        if decision.category == "royal_wardrobe" and not _contains_any(
            evidence_text, GARMENT_TERMS
        ):
            if _contains_any(complete_text, ROYAL_TERMS):
                return _decision(
                    "general_royal_news",
                    evidence,
                    "Royal news without concrete wardrobe coverage",
                )

        if decision.category == "royal_jewelry" and not _contains_any(
            evidence_text, JEWELRY_TERMS
        ):
            if _contains_any(complete_text, ROYAL_TERMS):
                return _decision(
                    "general_royal_news",
                    evidence,
                    "Royal news without concrete jewelry coverage",
                )

        if decision.category == "luxury_business" and not _contains_any(
            evidence_text, BUSINESS_TERMS
        ):
            if _contains_any(complete_text, CELEBRITY_STYLE_TERMS):
                return _decision(
                    "celebrity_style",
                    evidence,
                    "Style coverage without luxury-business substance",
                )
            return _decision(
                "irrelevant",
                evidence,
                "No concrete luxury-business evidence",
            )

        if decision.category == "luxury_market_trend" and not _contains_any(
            evidence_text, MARKET_TERMS
        ):
            if _contains_any(complete_text, BEAUTY_TERMS):
                return _decision(
                    "general_beauty_trend",
                    evidence,
                    "Beauty trend without luxury-market analysis",
                )
            if _contains_any(complete_text, MASS_MARKET_BRANDS):
                return _decision(
                    "high_street_fashion",
                    evidence,
                    "High-street trend without luxury-market analysis",
                )
            return _decision(
                "consumer_lifestyle",
                evidence,
                "Consumer trend without concrete luxury-market analysis",
            )

        if _contains_any(complete_text, CONSUMER_LIFESTYLE_BRANDS):
            return _decision(
                "consumer_lifestyle",
                evidence,
                "Consumer product coverage without a material luxury angle",
            )

        if _contains_any(complete_text, MASS_MARKET_BRANDS) and (
            _contains_any(complete_text, SHOPPING_TERMS)
            or decision.category == "luxury_product"
        ):
            return _decision(
                "high_street_fashion",
                evidence,
                "Mass-market or high-street shopping coverage",
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
                policy_decision = self._apply_evidence_policy(
                    decision,
                    payload["title"],
                    cleaned_article[:4000],
                )
                if (
                    decision.relevant
                    and not policy_decision.relevant
                    and policy_decision.reason
                    == "Classifier evidence was not copied from the article"
                ):
                    raise ValueError("unsupported evidence quotation")
                return policy_decision
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
