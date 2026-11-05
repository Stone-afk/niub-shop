import json
import unittest
from unittest.mock import patch

from scripts.smoke_test import _node_executable, run_smoke_test


class ThreeServiceSmokeTests(unittest.TestCase):
    def test_node_executable_is_resolved_from_path(self) -> None:
        with patch("scripts.smoke_test.os.getenv", return_value=None), patch(
            "scripts.smoke_test.shutil.which", return_value="/opt/node"
        ):
            self.assertEqual(_node_executable(), "/opt/node")

    def test_missing_node_has_a_clear_error(self) -> None:
        with patch("scripts.smoke_test.os.getenv", return_value=None), patch(
            "scripts.smoke_test.shutil.which", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "Node.js executable not found"):
                _node_executable()

    def test_persisted_conversation_crosses_all_service_boundaries(self) -> None:
        result = run_smoke_test()

        self.assertEqual(len(result["model_requests"]), 3)
        self.assertEqual(
            result["model_requests"][0]["authorization"], "Bearer smoke-test-key"
        )
        self.assertEqual(
            [tool["function"]["name"] for tool in result["model_requests"][0]["tools"]],
            ["search_products", "submit_product_recommendation"],
        )
        self.assertEqual(result["model_requests"][0]["tool_choice"], "auto")
        self.assertEqual(
            result["model_requests"][0]["messages"][:2],
            [
                {"role": "system", "content": "You are the smoke-test assistant."},
                {"role": "user", "content": "first question"},
            ],
        )
        self.assertIn(
            "search_products",
            result["model_requests"][0]["messages"][-1]["content"],
        )

        tool_transcript = result["model_requests"][1]["messages"]
        search_call = tool_transcript[-2]["tool_calls"][0]
        self.assertEqual(search_call["id"], "smoke-search-1")
        self.assertEqual(
            search_call["function"]["arguments"],
            '{"keyword":"coffee","category":"drinkware","max_price_cents":50000,"sort":"price_asc","limit":2}',
        )
        self.assertEqual(tool_transcript[-1]["tool_call_id"], "smoke-search-1")
        trusted_result = json.loads(tool_transcript[-1]["content"])
        self.assertEqual(trusted_result["items"][0]["id"], "ceramic-mug")
        self.assertEqual(trusted_result["items"][0]["availability"], "in_stock")

        second_messages = result["model_requests"][2]["messages"]
        self.assertEqual(
            second_messages[:4],
            [
                {"role": "system", "content": "You are the smoke-test assistant."},
                {"role": "user", "content": "first question"},
                {
                    "role": "assistant",
                    "content": "推荐陶瓷马克杯 / I recommend the Ceramic Mug.",
                },
                {"role": "user", "content": "second question"},
            ],
        )
        self.assertFalse(any(message["role"] == "tool" for message in second_messages))
        self.assertNotIn("recommended_product_ids", str(second_messages))
        self.assertEqual(result["initial_conversations"], [])
        self.assertEqual(
            result["detail"]["turns"][0]["assistant_content"],
            "推荐陶瓷马克杯 / I recommend the Ceramic Mug.",
        )
        self.assertEqual(
            result["detail"]["turns"][0]["recommended_product_ids"],
            ["ceramic-mug"],
        )
        self.assertEqual(result["detail"]["title"], "first question")
        product = next(
            item for item in result["products"]["items"] if item["id"] == "ceramic-mug"
        )
        self.assertEqual(product["category"], "drinkware")
        self.assertEqual(product["availability"], "in_stock")
        self.assertEqual(product["tags"], ["coffee", "tea"])
        self.assertIn(
            'event: recommendations\ndata: {"product_ids": ["ceramic-mug"]}',
            result["response_bodies"][0],
        )
        self.assertTrue(result["database_exists"])
        self.assertNotIn("user_id", result["created_conversation"])
        self.assertNotIn("smoke-test-key", str(result["response_bodies"]))


if __name__ == "__main__":
    unittest.main()
