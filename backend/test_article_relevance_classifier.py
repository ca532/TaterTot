import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

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
            "category": "jewelry_product",
            "evidence": ["Boucheron high-jewelry collection"],
        })
        decision = classifier.classify(
            title="Boucheron launches a high-jewelry collection",
            publication="National Jeweler",
            article_text="Boucheron introduced its latest collection.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)
        self.assertEqual(
            ["Boucheron high-jewelry collection"], decision.luxury_evidence
        )

    def test_accepts_relevant_category_without_evidence_and_warns(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "luxury_brand",
            "evidence": [],
        })
        output = StringIO()
        with redirect_stdout(output):
            decision = classifier.classify(
                title="Luxury brand announces a new creative director",
                publication="Example",
                article_text="A luxury house announced its appointment.",
            )
        self.assertTrue(decision.relevant)
        self.assertEqual([], decision.luxury_evidence)
        self.assertIn("[CLASSIFIER_EVIDENCE_WARNING]", output.getvalue())
        self.assertIn("decision retained", output.getvalue())

    def test_rejects_irrelevant_category(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "irrelevant",
            "evidence": ["The article is about a royal holiday home."],
        })
        decision = classifier.classify(
            title="Inside the royal family's holiday home",
            publication="Example",
            article_text="The article describes a royal residence.",
        )
        self.assertFalse(decision.relevant)

    def test_accepts_luxury_product_category(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "luxury_product",
            "evidence": [
                "The article substantially covers Chanel bag trends and craftsmanship."
            ],
        })
        decision = classifier.classify(
            title="This Fall's Best Bag Trends Really Mean Business",
            publication="Example",
            article_text="Chanel bags and their craftsmanship lead the fall trend report.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("luxury_product", decision.category)

    def test_vetoes_royal_wardrobe_without_concrete_wardrobe_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_wardrobe",
            "evidence": [
                "Queen Camilla went shopping with her granddaughter."
            ],
        })
        output = StringIO()
        with redirect_stdout(output):
            decision = classifier.classify(
                title="Queen Camilla goes shopping with her granddaughter",
                publication="Example",
                article_text="The pair spent the afternoon shopping together.",
            )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)
        self.assertIn("[CLASSIFIER_POLICY_VETO]", output.getvalue())

    def test_accepts_royal_wardrobe_with_concrete_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_wardrobe",
            "evidence": [
                "Queen Mary wore wide-leg jeans for her return to work."
            ],
        })
        decision = classifier.classify(
            title="Queen Mary returns to work in wide-leg jeans",
            publication="Example",
            article_text="Queen Mary wore wide-leg jeans for the engagement.",
        )
        self.assertTrue(decision.relevant)


if __name__ == "__main__":
    unittest.main()
