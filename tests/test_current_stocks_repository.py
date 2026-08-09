import unittest

from repositories import CurrentStocksQuantityTable


class FakeDB:
    def __init__(self):
        self.query = None
        self.data = None

    async def executemany(self, query, data):
        self.query = query
        self.data = data


class CurrentStocksQuantityTableTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_fbs_quantity_matches_only_article_and_type(self):
        db = FakeDB()

        await CurrentStocksQuantityTable(db).upsert_fbs_quantity([(767130070, 0)])

        self.assertEqual(db.data, [(767130070, 0)])
        self.assertNotIn("barcode", db.query)
        self.assertIn("WHERE article_id = $1::int4", db.query)
        self.assertIn("'ФБС'", db.query)
        self.assertIn("SET quantity = $2::int4", db.query)
        self.assertIn("WHERE NOT EXISTS (SELECT 1 FROM updated)", db.query)
