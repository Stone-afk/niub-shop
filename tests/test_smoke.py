import unittest

from scripts.smoke_test import run_smoke_test


class ThreeServiceSmokeTests(unittest.TestCase):
    def test_product_and_chat_requests_cross_all_service_boundaries(self) -> None:
        run_smoke_test()


if __name__ == "__main__":
    unittest.main()
