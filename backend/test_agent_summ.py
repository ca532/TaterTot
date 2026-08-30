import json
import unittest

from AgentSumm import ArticleSummarizer


SOURCE_SENTENCES = [
    "Bulgari introduced the Eclettica collection in Milan with 160 high jewelry creations for international clients.",
    "The presentation included necklaces, bracelets, rings, and brooches made with diamonds and colored gemstones.",
    "Executives described the launch as a major expression of the house's long-term creative direction.",
    "The collection brought together Italian design references, technical craftsmanship, and several exceptional one-of-a-kind pieces.",
    "Guests viewed the jewels in an exhibition designed to connect each piece with contemporary art and architecture.",
    "Bulgari said the event supports its strategy of presenting high jewelry through immersive cultural experiences.",
    "The company will introduce selected creations to clients in additional international markets later this year.",
    "The launch also emphasized the commercial importance of high jewelry within Bulgari's wider luxury portfolio.",
]
SOURCE_TEXT = " ".join(SOURCE_SENTENCES)
VALID_SUMMARY = SOURCE_TEXT


class FakeModel:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def create_chat_completion(self, **_kwargs):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return {
            "choices": [{"message": {"content": json.dumps(result)}}]
        }


def summarizer_with_model(model):
    summarizer = ArticleSummarizer.__new__(ArticleSummarizer)
    summarizer.model = model
    summarizer.model_repo = "test/repo"
    summarizer.model_file = "test.gguf"
    summarizer.custom_prompt = ""
    return summarizer


class ArticleSummarizerTests(unittest.TestCase):
    def test_retries_invalid_qwen_summary_and_keeps_structured_facts(self):
        model = FakeModel([
            {
                "summary": "Bulgari introduced a collection [...]",
                "source_facts": [SOURCE_SENTENCES[0]],
            },
            {
                "summary": VALID_SUMMARY,
                "source_facts": [SOURCE_SENTENCES[0], SOURCE_SENTENCES[1]],
            },
        ])
        summarizer = summarizer_with_model(model)

        result = summarizer.summarize_article(
            SOURCE_TEXT,
            "https://example.com/bulgari-eclettica",
            "Example",
            "Bulgari Introduces Eclettica",
            "Example Author",
        )

        self.assertIsNotNone(result)
        self.assertEqual(2, model.calls)
        self.assertEqual(VALID_SUMMARY, result.summary)
        self.assertEqual(
            [SOURCE_SENTENCES[0], SOURCE_SENTENCES[1]],
            result.source_facts,
        )

    def test_uses_extractive_fallback_after_two_qwen_failures(self):
        model = FakeModel([RuntimeError("generation failed")])
        summarizer = summarizer_with_model(model)

        result = summarizer.summarize_article(
            SOURCE_TEXT,
            "https://example.com/bulgari-eclettica",
            "Example",
            "Bulgari Introduces Eclettica",
            "Example Author",
        )

        self.assertIsNotNone(result)
        self.assertEqual(2, model.calls)
        self.assertGreaterEqual(len(result.summary.split()), 80)
        self.assertNotIn("...", result.summary)

    def test_extractive_fallback_never_exceeds_140_words(self):
        summarizer = summarizer_with_model(FakeModel([]))
        oversized = " ".join(["oversized"] * 150) + "."
        source = " ".join([oversized] + SOURCE_SENTENCES)

        summary = summarizer._build_extractive_summary(source)

        self.assertGreaterEqual(len(summary.split()), 80)
        self.assertLessEqual(len(summary.split()), 140)
        self.assertNotIn("oversized", summary)


if __name__ == "__main__":
    unittest.main()
