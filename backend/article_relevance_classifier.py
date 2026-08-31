import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    "celebrity_style",
    "high_street_fashion",
    "general_beauty_trend",
    "consumer_lifestyle",
    "publication_meta",
}
RELEVANT_CATEGORIES = PRIMARY_CATEGORIES | RESERVE_CATEGORIES
ALL_CATEGORIES = RELEVANT_CATEGORIES | {"general_royal_news", "irrelevant"}

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
general_royal_news: Royal deaths, succession, health, family, relationships,
official communications, biographies, or monarchy where jewelry, watches,
designer wardrobe, luxury brands, craftsmanship, luxury business, or a material
luxury PR angle is not central. This is an exclusion category.
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
- Royal status alone is not relevant. Generic royal deaths, succession,
  relationships, family news, biographies, historical retrospectives, health,
  letters, and official appearances must use general_royal_news and are excluded.
- Use royal_jewelry when jewelry, gemstones, watches, collections, auctions,
  ownership, provenance, inheritance, or design is material.
- Use royal_wardrobe only when specific garments, designers, couture, uniforms,
  craftsmanship, or meaningful luxury wardrobe analysis is material.
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


def _quote_is_grounded(quote: str, article_text: str) -> bool:
    normalized_quote = _normalized_for_evidence(quote)
    normalized_article = _normalized_for_evidence(article_text)
    quote_words = normalized_quote.split()
    article_words = normalized_article.split()

    if len(quote_words) < 5:
        return False
    if normalized_quote in normalized_article:
        return True

    minimum_window = max(5, len(quote_words) - 2)
    maximum_window = min(len(article_words), len(quote_words) + 2)
    for window_size in range(minimum_window, maximum_window + 1):
        for index in range(len(article_words) - window_size + 1):
            candidate = " ".join(article_words[index:index + window_size])
            if SequenceMatcher(None, normalized_quote, candidate).ratio() >= 0.86:
                return True
    return False


def _evidence_is_supported(evidence: list[str], article_text: str) -> bool:
    return bool(evidence) and all(
        _quote_is_grounded(quote, article_text) for quote in evidence
    )


def _decision(category: str, evidence: list[str], reason: str):
    return RelevanceDecision(
        relevant=category in RELEVANT_CATEGORIES,
        category=category,
        luxury_evidence=evidence,
        reason=reason[:500],
    )


def _high_confidence_jewelry_category(title: str, article_text: str) -> str:
    if not (
        _contains_any(title, JEWELRY_TERMS)
        and _contains_any(article_text, JEWELRY_CONTEXT_TERMS)
    ):
        return ""
    if _contains_any(f"{title} {article_text}", UNAMBIGUOUS_MONARCHY_TERMS):
        return "royal_jewelry"
    return "jewelry_product"


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
        evidence = decision.luxury_evidence
        evidence_text = " ".join(evidence)
        complete_text = f"{title} {article_text}"
        rescue_category = _high_confidence_jewelry_category(title, article_text)

        def finalize(candidate):
            if (
                rescue_category
                and candidate.category
                in JEWELRY_PROMOTION_CATEGORIES | {"irrelevant"}
            ):
                return _decision(
                    rescue_category,
                    evidence or [title],
                    "Headline and article establish jewelry as the material subject",
                )
            return candidate

        if decision.category == "irrelevant":
            return finalize(decision)

        if not _evidence_is_supported(evidence, complete_text):
            print(
                "[CLASSIFIER_POLICY_VETO] "
                f"category={decision.category} rule=unsupported_evidence "
                f"title={title!r}"
            )
            return finalize(_decision(
                "irrelevant",
                evidence,
                "Classifier evidence could not be grounded in the article",
            ))

        subject_text = f"{title} {evidence_text}"

        # Promote reserve classifications only when both the headline and the
        # supplied article establish jewelry as a material subject. This avoids
        # hard-coding brands or stories while rejecting metaphorical matches.
        decision = finalize(decision)

        # Validate that general royal news concerns monarchy rather than an
        # organization, location, company, or other use of a royal title.
        if decision.category == "general_royal_news":
            has_monarchy_context = (
                _contains_any(subject_text, UNAMBIGUOUS_MONARCHY_TERMS)
                or (
                    _contains_any(subject_text, ROYAL_PERSON_TITLES)
                    and _contains_any(subject_text, ROYAL_PERSON_CONTEXT_TERMS)
                )
            )
            if (
                _contains_any(subject_text, NON_MONARCHY_ROYAL_PHRASES)
                and not has_monarchy_context
            ):
                return finalize(_decision(
                    "irrelevant",
                    evidence,
                    "Royal refers to a non-monarchy organization",
                ))
            if not has_monarchy_context:
                return finalize(_decision(
                    "irrelevant",
                    evidence,
                    "No concrete monarchy subject",
                ))
            return finalize(_decision(
                "irrelevant",
                evidence,
                "Generic royal news without a material luxury or jewelry angle",
            ))

        # Preserve fashion coverage that Qwen mistakenly labels as beauty.
        if decision.category == "general_beauty_trend" and not _contains_any(
            f"{title} {evidence_text}", BEAUTY_TERMS
        ):
            if _contains_any(complete_text, FASHION_DESIGN_TERMS):
                return finalize(_decision(
                    "consumer_lifestyle",
                    evidence,
                    "Fashion or garment coverage without a beauty focus",
                ))
            return finalize(_decision(
                "irrelevant",
                evidence,
                "No concrete beauty or fashion subject",
            ))

        if decision.category == "royal_wardrobe" and not _contains_any(
            evidence_text, GARMENT_TERMS
        ):
            if _contains_any(complete_text, ROYAL_TERMS):
                return finalize(_decision(
                    "irrelevant",
                    evidence,
                    "Royal news without a material wardrobe or luxury angle",
                ))

        if decision.category == "royal_jewelry" and not _contains_any(
            evidence_text, JEWELRY_TERMS
        ):
            if _contains_any(complete_text, ROYAL_TERMS):
                return finalize(_decision(
                    "irrelevant",
                    evidence,
                    "Royal news without a material jewelry or luxury angle",
                ))

        business_validation_text = f"{title} {evidence_text} {article_text[:2500]}"
        if decision.category == "luxury_business" and not _contains_any(
            business_validation_text, BUSINESS_TERMS
        ):
            if _contains_any(complete_text, CELEBRITY_STYLE_TERMS):
                return finalize(_decision(
                    "celebrity_style",
                    evidence,
                    "Style coverage without luxury-business substance",
                ))
            return finalize(_decision(
                "irrelevant",
                evidence,
                "No concrete luxury-business evidence",
            ))

        market_validation_text = f"{title} {evidence_text} {article_text[:2500]}"
        if decision.category == "luxury_market_trend" and not _contains_any(
            market_validation_text, MARKET_TERMS
        ):
            if _contains_any(complete_text, BEAUTY_TERMS):
                return finalize(_decision(
                    "general_beauty_trend",
                    evidence,
                    "Beauty trend without luxury-market analysis",
                ))
            if _contains_any(complete_text, MASS_MARKET_BRANDS):
                return finalize(_decision(
                    "high_street_fashion",
                    evidence,
                    "High-street trend without luxury-market analysis",
                ))
            return finalize(_decision(
                "consumer_lifestyle",
                evidence,
                "Consumer trend without concrete luxury-market analysis",
            ))

        if _contains_any(complete_text, CONSUMER_LIFESTYLE_BRANDS):
            return finalize(_decision(
                "consumer_lifestyle",
                evidence,
                "Consumer product coverage without a material luxury angle",
            ))

        if _contains_any(complete_text, MASS_MARKET_BRANDS) and (
            _contains_any(complete_text, SHOPPING_TERMS)
            or decision.category == "luxury_product"
        ):
            return finalize(_decision(
                "high_street_fashion",
                evidence,
                "Mass-market or high-street shopping coverage",
            ))

        return finalize(decision)

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
