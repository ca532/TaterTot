import json
import re
import unittest
from pathlib import Path

from article_quality import relevance_gate_reason


FIXTURE_PATH = (
    Path(__file__).parent
    / "tests"
    / "fixtures"
    / "luxury_relevance_20260805.json"
)

CORE_KEYWORDS = {
    "luxury", "jewellery", "fine jewellery", "craftsmanship", "jewelry",
    "diamond", "engagement ring", "wedding ring", "lab grown diamonds",
    "diamond price", "jewels", "cartier", "tiffany", "bulgari", "chanel",
    "dior", "van cleef", "graff", "harry winston", "chopard", "piaget",
    "boucheron",
}
SUPPORTING_KEYWORDS = {
    "necklace", "bracelet", "earrings", "pendant", "brooch", "gold",
    "platinum", "silver", "emerald", "sapphire", "ruby", "watches",
    "timepiece", "crown", "tiara", "crown jewels", "regalia",
    "fashion", "accessories", "collection", "launch", "haute couture",
    "limited edition", "red carpet", "celebrity", "fashion week", "auction",
    "collaboration", "investment", "trends", "style", "luxury sector",
    "luxury marketing trends", "gold price", "royal collection",
}
WEAK_KEYWORDS = {
    "royal", "royals", "coronation", "queen", "king", "prince",
    "princess", "duchess", "duke", "royal family", "buckingham palace",
    "windsor", "state visit", "royal wedding", "monarchy", "sovereign",
    "palace",
}
TOPIC_ENTITIES = {
    "blue nile", "stephanie gottlieb", "rio grande", "boucheron",
    "tiffany", "bulgari", "lvmh", "vhernier", "graff", "carolina herrera",
    "ferragamo", "tom ford", "ralph lauren", "coach", "david webb",
    "swarovski", "diamonds factory", "kering", "richard mille", "chaumet",
}
SUPPORTING_CONCEPTS = {
    "copenhagen fashion week", "runway", "royal wardrobe", "dress code",
    "tartan", "equestrian", "high jewelry", "high jewellery", "bridal",
    "engagement ring", "lab grown diamonds", "red carpet", "formal tie",
    "sentimental necklace",
}


def _policy_map():
    policy = {}
    for keyword in CORE_KEYWORDS:
        policy[keyword] = {"tier": "core", "weight": 4.0}
    for keyword in SUPPORTING_KEYWORDS:
        policy[keyword] = {"tier": "supporting", "weight": 3.0}
    for keyword in WEAK_KEYWORDS:
        policy[keyword] = {"tier": "broad", "weight": 1.0}
    return policy


def _matches(text, policy):
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    words = set(normalized.split())

    def stem(token):
        return token[:-1] if len(token) > 4 and token.endswith("s") else token

    stemmed_words = {stem(word) for word in words}
    return sorted(
        keyword for keyword in policy
        if all(
            stem(token) in stemmed_words
            for token in re.sub(r"[^a-z0-9]+", " ", keyword).split()
        )
    )


def _signal_matches(text, signals):
    policy = {signal: {} for signal in signals}
    return _matches(text, policy)


class LuxuryRelevanceRegressionTests(unittest.TestCase):
    def test_august_5_roundup_fixture(self):
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        policy = _policy_map()
        expected_keep = [item for item in fixtures if item["expected"] == "keep"]
        expected_reject = [item for item in fixtures if item["expected"] == "reject"]
        self.assertEqual(39, len(expected_keep))
        self.assertEqual(45, len(expected_reject))

        false_negatives = []
        false_positives = []
        for item in fixtures:
            full_text = f'{item["title"]} {item["summary"]}'
            anchor_text = f'{item["title"]} {item["summary"][:1000]}'
            matched = _matches(full_text, policy)
            anchors = _matches(anchor_text, policy)
            score = sum(policy[keyword]["weight"] for keyword in matched)
            accepted = not relevance_gate_reason(
                score=score,
                matched_keywords=matched,
                anchor_keywords=anchors,
                title=item["title"],
                keyword_policy_map=policy,
                minimum_relevance_score=4.0,
                minimum_distinct_keywords=2,
                topic_entity_matches=_signal_matches(anchor_text, TOPIC_ENTITIES),
                supporting_concept_matches=_signal_matches(
                    anchor_text, SUPPORTING_CONCEPTS
                ),
            )

            if item["expected"] == "keep" and not accepted:
                false_negatives.append(item["title"])
            elif item["expected"] == "reject" and accepted:
                false_positives.append(item["title"])

        retained = len(expected_keep) - len(false_negatives)
        rejected = len(expected_reject) - len(false_positives)
        self.assertGreaterEqual(
            retained,
            36,
            f"Retained {retained}/39; false negatives: {false_negatives}",
        )
        self.assertGreaterEqual(
            rejected,
            42,
            f"Rejected {rejected}/45; false positives: {false_positives}",
        )


if __name__ == "__main__":
    unittest.main()
