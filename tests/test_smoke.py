import unittest
from unittest.mock import patch

from scripts.smoke_test import _node_executable, run_smoke_test


class ThreeServiceSmokeTests(unittest.TestCase):
    def test_node_executable_is_resolved_from_path(self) -> None:
        with patch("scripts.smoke_test.shutil.which", return_value="/opt/node"):
            self.assertEqual(_node_executable(), "/opt/node")

    def test_missing_node_has_a_clear_error(self) -> None:
        with patch("scripts.smoke_test.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Node.js executable not found"):
                _node_executable()

    def test_product_and_chat_requests_cross_all_service_boundaries(self) -> None:
        run_smoke_test()


if __name__ == "__main__":
    unittest.main()
