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
            "evidence": ["Boucheron introduced its latest collection."],
        })
        decision = classifier.classify(
            title="Boucheron launches a high-jewelry collection",
            publication="National Jeweler",
            article_text="Boucheron introduced its latest collection.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)
        self.assertEqual(
            ["Boucheron introduced its latest collection."],
            decision.luxury_evidence,
        )

    def test_rejects_relevant_category_without_grounded_evidence(self):
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
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)
        self.assertIn("[CLASSIFIER_EVIDENCE_WARNING]", output.getvalue())
        self.assertIn("[CLASSIFIER_POLICY_VETO]", output.getvalue())

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
                "Chanel bags and their craftsmanship lead the fall trend report."
            ],
        })
        decision = classifier.classify(
            title="This Fall's Best Bag Trends Really Mean Business",
            publication="Example",
            article_text="Chanel bags and their craftsmanship lead the fall trend report.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("luxury_product", decision.category)

    def test_rejects_royal_news_without_wardrobe_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_wardrobe",
            "evidence": [
                "Queen Camilla spent the afternoon shopping with her granddaughter."
            ],
        })
        output = StringIO()
        with redirect_stdout(output):
            decision = classifier.classify(
                title="Queen Camilla goes shopping with her granddaughter",
                publication="Example",
                article_text=(
                    "Queen Camilla spent the afternoon shopping with her "
                    "granddaughter."
                ),
            )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)

    def test_accepts_royal_wardrobe_with_concrete_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_wardrobe",
            "evidence": [
                "Queen Mary wore wide-leg jeans for the engagement."
            ],
        })
        decision = classifier.classify(
            title="Queen Mary returns to work in wide-leg jeans",
            publication="Example",
            article_text="Queen Mary wore wide-leg jeans for the engagement.",
        )
        self.assertTrue(decision.relevant)

    def test_routes_high_street_luxury_product_to_reserve(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "luxury_product",
            "evidence": ["The H&M blouse looks luxe and costs under £50."],
        })
        decision = classifier.classify(
            title="H&M blouse looks luxe for autumn",
            publication="Example",
            article_text="The H&M blouse looks luxe and costs under £50.",
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("high_street_fashion", decision.category)

    def test_routes_mass_consumer_collection_to_reserve(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "luxury_product",
            "evidence": [
                "Bath & Body Works introduced its new Reserve collection."
            ],
        })
        decision = classifier.classify(
            title="Bath & Body Works introduces Reserve",
            publication="Example",
            article_text=(
                "Bath & Body Works introduced its new Reserve collection."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("consumer_lifestyle", decision.category)

    def test_rejects_non_monarchy_royal_organization(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_royal_news",
            "evidence": [
                "The Royal Navy spent three days tracking Russian vessels."
            ],
        })
        decision = classifier.classify(
            title="Royal Navy tracks Russian vessels in UK waters",
            publication="The Guardian",
            article_text=(
                "The Royal Navy spent three days tracking Russian vessels."
            ),
        )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)

    def test_rejects_royal_sounding_location_without_monarchy_context(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_royal_news",
            "evidence": [
                "Prince Rupert Harbour is expanding its Canadian trade capacity."
            ],
        })
        decision = classifier.classify(
            title="Prince Rupert becomes central to Canadian trade",
            publication="Example",
            article_text=(
                "Prince Rupert Harbour is expanding its Canadian trade capacity."
            ),
        )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)

    def test_rejects_general_royal_news_with_monarchy_context(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_royal_news",
            "evidence": [
                "King Haakon ascended the throne following his father's death."
            ],
        })
        decision = classifier.classify(
            title="King Haakon begins his reign",
            publication="Example",
            article_text=(
                "King Haakon ascended the throne following his father's death."
            ),
        )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)

    def test_rejects_single_princess_story_with_person_context(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_royal_news",
            "evidence": [
                "Princess Anne visited the hospital during an official visit."
            ],
        })
        decision = classifier.classify(
            title="Princess Anne visits a London hospital",
            publication="Example",
            article_text=(
                "Princess Anne visited the hospital during an official visit."
            ),
        )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)

    def test_incidental_royal_air_force_does_not_veto_royal_jewelry(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_jewelry",
            "evidence": [
                "The Duke inherited the diamond brooch from his mother."
            ],
        })
        decision = classifier.classify(
            title="The Duke of Kent's inherited diamond brooch",
            publication="Example",
            article_text=(
                "The Duke inherited the diamond brooch from his mother. "
                "He previously served in the Royal Air Force."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("royal_jewelry", decision.category)

    def test_rescues_obvious_jewelry_story_from_irrelevant(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "irrelevant",
            "evidence": [],
        })
        decision = classifier.classify(
            title="Vhernier unveils the Freccia high-jewelry collection",
            publication="Example",
            article_text=(
                "The high jewelry collection features diamond necklaces, "
                "rings and bracelets made by the Italian jeweller."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)

    def test_accepts_nearly_exact_grounded_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "jewelry_product",
            "evidence": [
                "Vhernier unveiled its Freccia high jewelry collection in Milan."
            ],
        })
        decision = classifier.classify(
            title="Vhernier unveils Freccia",
            publication="Example",
            article_text=(
                "Vhernier unveiled the Freccia high-jewelry collection in Milan."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)

    def test_business_validation_uses_article_lead(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "luxury_business",
            "evidence": [
                "Tiffany and Bulgari led performance at their parent groups."
            ],
        })
        decision = classifier.classify(
            title="Tiffany and Bulgari lead luxury groups",
            publication="Example",
            article_text=(
                "Tiffany and Bulgari led performance at their parent groups. "
                "The companies reported revenue growth in the latest quarter."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("luxury_business", decision.category)

    def test_promotes_material_royal_jewelry_subject(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_royal_news",
            "evidence": [
                "The Queen owned the diamond brooch for several decades."
            ],
        })
        decision = classifier.classify(
            title="Royal diamond brooch heads to auction",
            publication="Example",
            article_text=(
                "The Queen owned the diamond brooch for several decades. "
                "The jewel will be offered at auction in London."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("royal_jewelry", decision.category)

    def test_promotes_material_non_royal_jewelry_subject(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "consumer_lifestyle",
            "evidence": [
                "Customs officers confiscated the counterfeit jewelry shipment."
            ],
        })
        decision = classifier.classify(
            title="Customs confiscates counterfeit jewelry",
            publication="Example",
            article_text=(
                "Customs officers confiscated the counterfeit jewelry shipment."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("jewelry_product", decision.category)

    def test_does_not_promote_metaphorical_gemstone_language(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "consumer_lifestyle",
            "evidence": [
                "The article describes a pearl of wisdom for home cooks."
            ],
        })
        decision = classifier.classify(
            title="A pearl of wisdom for home cooks",
            publication="Example",
            article_text=(
                "The article describes a pearl of wisdom for home cooks."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("consumer_lifestyle", decision.category)

    def test_routes_fashion_story_out_of_beauty_category(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "general_beauty_trend",
            "evidence": [
                "The designer is growing fabric to create her own dress."
            ],
        })
        decision = classifier.classify(
            title="The woman growing her own dress",
            publication="The Guardian",
            article_text=(
                "The designer is growing fabric to create her own dress."
            ),
        )
        self.assertTrue(decision.relevant)
        self.assertEqual("consumer_lifestyle", decision.category)

    def test_rejects_invented_evidence(self):
        classifier = ArticleRelevanceClassifier()
        classifier.model = FakeModel({
            "category": "royal_wardrobe",
            "evidence": ["The king wore a bespoke Dior uniform."],
        })
        decision = classifier.classify(
            title="The king issues a statement",
            publication="Example",
            article_text="The king issued a statement from the palace today.",
        )
        self.assertFalse(decision.relevant)
        self.assertEqual("irrelevant", decision.category)


if __name__ == "__main__":
    unittest.main()
