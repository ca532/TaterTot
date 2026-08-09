import json
import unittest

from article_relevance_classifier import ArticleRelevanceClassifier


class FakeModel:
    def __init__(self, result):
        self.result = result

    def create_chat_completion(self, **_kwargs):
        return {
            "choices": [{"message": {"content": json.dumps(self.result)}}]
        }


class ArticleRelevanceClassifierTests(unittest.TestCase):
    def test_accepts_supported_luxury_category_with_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "relevant": True,
            "category": "jewelry_product",
            "luxury_evidence": ["Boucheron high-jewelry collection"],
            "reason": "The launch is the primary subject.",
        })
        decision = classifier.classify(
            title="Boucheron launches a high-jewelry collection",
            publication="National Jeweler",
            article_text="Boucheron introduced its latest collection.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)

    def test_rejects_boolean_claim_without_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "relevant": True,
            "category": "luxury_brand",
            "luxury_evidence": [],
            "reason": "Unsupported assertion.",
        })
        decision = classifier.classify(
            title="Princess welcomes a baby",
            publication="Example",
            article_text="A royal family announcement.",
        )
        self.assertFalse(decision.relevant)


if __name__ == "__main__":
    unittest.main()
