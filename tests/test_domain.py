import unittest

import constants as columns
from domain import (
    calculate_sum_for_logistic,
    column_index_to_letter,
    create_lk_articles,
    merge_dicts,
    process_local_vendor_code,
    process_string,
    validate_data,
)


class DomainHelpersTest(unittest.IsolatedAsyncioTestCase):
    def test_column_index_to_letter(self):
        self.assertEqual(column_index_to_letter(1), "A")
        self.assertEqual(column_index_to_letter(26), "Z")
        self.assertEqual(column_index_to_letter(27), "AA")
        self.assertEqual(column_index_to_letter(52), "AZ")

    def test_process_string_normalizes_wild_suffix(self):
        self.assertEqual(process_string("wild123 abc"), "wild123")
        self.assertEqual(process_local_vendor_code("wild7/extra"), "wild7")
        self.assertEqual(process_string("abc DEF"), "abc DEF")
        self.assertEqual(process_string("товар-1"), "товар-1")

    def test_merge_dicts_keeps_first_dict_priority(self):
        self.assertEqual(
            merge_dicts(
                {1: {"price": 100, "discount": 10}, 2: {"price": 200}},
                {1: {"discount": 10, "name": "item"}},
            ),
            {
                1: {"price": 100, "discount": 10, "name": "item"},
                2: {"price": 200},
            },
        )

    def test_calculate_sum_for_logistic(self):
        self.assertEqual(
            calculate_sum_for_logistic(for_one_liter=50, next_liters=10, length=10, width=10, height=10),
            50,
        )
        self.assertEqual(
            calculate_sum_for_logistic(for_one_liter=50, next_liters=10, length=20, width=10, height=10),
            60,
        )

    async def test_validate_data_collects_price_discount_and_dimensions(self):
        result = await validate_data(
            nm_ids_db_data={"123": {"sizes": [{"techSize": "0"}]}},
            data={
                "123": {
                    columns.WILD: "wild123",
                    columns.NET_PROFIT: "1 250₽",
                    columns.PRICE_DISCOUNT: {
                        columns.SET_NEW_PRICE: "500",
                        columns.SET_NEW_DISCOUNT: "20",
                    },
                    columns.DIMENSIONS: {
                        columns.NEW_HEIGHT: "10",
                        columns.NEW_LENGTH: "20",
                        columns.NEW_WIDTH: "30",
                    },
                }
            },
        )

        self.assertEqual(
            result,
            {
                123: {
                    "vendorCode": "wild123",
                    columns.PRICE_DISCOUNT: {"discount": 20, "price": 500},
                    "net_profit": 1250,
                    columns.DIMENSIONS: {"height": 10, "length": 20, "width": 30},
                    "sizes": [{"techSize": "0"}],
                }
            },
        )

    async def test_validate_data_skips_invalid_values(self):
        result = await validate_data(
            nm_ids_db_data={"123": {"sizes": []}},
            data={
                "not-number": {
                    columns.WILD: "wild",
                    columns.PRICE_DISCOUNT: {columns.SET_NEW_PRICE: "100"},
                },
                "123": {
                    columns.WILD: "wild123",
                    columns.NET_PROFIT: "100₽",
                    columns.PRICE_DISCOUNT: {
                        columns.SET_NEW_PRICE: "not-a-number",
                        columns.SET_NEW_DISCOUNT: "",
                    },
                    columns.DIMENSIONS: {
                        columns.NEW_HEIGHT: "10",
                        columns.NEW_LENGTH: "bad",
                        columns.NEW_WIDTH: "30",
                    },
                },
            },
        )

        self.assertEqual(result, {})

    def test_create_lk_articles_groups_by_account(self):
        self.assertEqual(
            create_lk_articles(
                {
                    100: {"account": "Wild1"},
                    101: {"account": "Wild1"},
                    200: {"account": "Wild2"},
                    300: {"without_account": True},
                }
            ),
            {"Wild1": {100, 101}, "Wild2": {200}},
        )


if __name__ == "__main__":
    unittest.main()
