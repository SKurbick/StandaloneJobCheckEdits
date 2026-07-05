import unittest

from repositories import CostPriceDBContainer


class RepositoryHelpersTest(unittest.TestCase):
    def test_cost_price_container_indexes_records_by_local_vendor_code(self):
        container = CostPriceDBContainer(
            [
                {"local_vendor_code": "wild1", "purchase_price": 100},
                {"local_vendor_code": "wild2", "purchase_price": 200},
            ]
        )

        self.assertEqual(container.local_vendor_code["wild1"]["purchase_price"], 100)
        self.assertEqual(container.local_vendor_code["wild2"]["purchase_price"], 200)


if __name__ == "__main__":
    unittest.main()
