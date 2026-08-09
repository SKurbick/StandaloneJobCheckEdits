import importlib.util
import unittest


class FakeSheet:
    def __init__(self, values):
        self._values = values

    def get_all_values(self):
        return self._values


class GoogleSheetHelpersTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("gspread") is None or importlib.util.find_spec("pandas") is None:
            raise unittest.SkipTest("GoogleSheet tests require standalone Google Sheets dependencies")
        from google_sheet import GoogleSheet

        cls.GoogleSheet = GoogleSheet

    def test_get_column_letter(self):
        self.assertEqual(self.GoogleSheet.get_column_letter(1), "A")
        self.assertEqual(self.GoogleSheet.get_column_letter(26), "Z")
        self.assertEqual(self.GoogleSheet.get_column_letter(27), "AA")

    def test_check_status_service_sheet_parses_int_values(self):
        google_sheet = object.__new__(self.GoogleSheet)
        google_sheet.sheet = FakeSheet(
            [
                ["ВКЛ - 1 /ВЫКЛ - 0", "Остаток"],
                ["1", "0"],
                [],
                ["Цены/Скидки", "Габариты"],
                ["1", "not-int"],
            ]
        )

        self.assertEqual(
            google_sheet.check_status_service_sheet(),
            {
                "ВКЛ - 1 /ВЫКЛ - 0": 1,
                "Остаток": 0,
                "Цены/Скидки": 1,
                "Габариты": "not-int",
            },
        )

    def test_get_article_dict_ignores_dimension_edit_fields(self):
        row = {
            "Чистая прибыль 1ед.": "100",
            "Установить новую цену": "500",
            "Установить новую скидку %": "10",
            "Новая\nДлина (см)": "20",
            "Новая\nШирина (см)": "30",
            "Новая\nВысота (см)": "40",
        }

        result = self.GoogleSheet.get_article_dict(
            {"Цены/Скидки": 1, "Габариты": 1},
            row,
            {"vendor_code": "wild123"},
        )

        self.assertNotIn("dimensions", result)
        self.assertEqual(
            result["price_discount"],
            {"Установить новую цену": "500", "Установить новую скидку %": "10"},
        )


    async def test_sheet_account_matches_differently_formatted_db_account(self):
        google_sheet = object.__new__(self.GoogleSheet)
        headers = [
            "Артикул",
            "ЛК",
            "Чистая прибыль 1ед.",
            "Установить новую цену",
            "Установить новую скидку %",
            "Новый остаток",
        ]
        google_sheet.sheet = FakeSheet(
            [headers, ["767130070", "\u00a0СТАРТ0854\u00a0", "100", "", "", "0"]]
        )

        result = await google_sheet.get_edit_data(
            {
                "767130070": {
                    "account": " старт0854 ",
                    "vendor_code": "wild802",
                }
            },
            {"Цены/Скидки": 0, "Остаток": 1},
            {767130070: 1120421845},
        )

        self.assertIn("Старт0854", result["nm_ids_edit_data"])
        self.assertEqual(
            result["qty_edit_data"]["Старт0854"]["stocks"],
            [{"chrtId": 1120421845, "amount": 0}],
        )
        self.assertEqual(
            result["requested_edits"]["Старт0854"][767130070],
            {"qty"},
        )


if __name__ == "__main__":
    unittest.main()
