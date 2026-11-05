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

        self.assertEqual(
            result["model_requests"][0]["authorization"], "Bearer smoke-test-key"
        )
        self.assertEqual(
            result["model_requests"][0]["messages"],
            [
                {"role": "system", "content": "You are the smoke-test assistant."},
                {"role": "user", "content": "first question"},
            ],
        )
        self.assertEqual(
            result["model_requests"][1]["messages"],
            [
                {"role": "system", "content": "You are the smoke-test assistant."},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "second question"},
            ],
        )
        self.assertEqual(result["initial_conversations"], [])
        self.assertEqual(result["detail"]["turns"][0]["assistant_content"], "First reply")
        self.assertEqual(result["detail"]["title"], "first question")
        self.assertTrue(result["database_exists"])
        self.assertNotIn("user_id", result["created_conversation"])
        self.assertNotIn("smoke-test-key", str(result["response_bodies"]))


if __name__ == "__main__":
    unittest.main()
