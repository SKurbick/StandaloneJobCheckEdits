import asyncio
import unittest
from unittest.mock import patch

from services.card_actualization import CardActualizationService


class FakeAcquire:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.acquire_count += 1
        return FakeConnection()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeConnection:
    def transaction(self):
        return FakeTransaction()


class FakeDB:
    def __init__(self):
        self.acquire_count = 0

    def acquire(self):
        return FakeAcquire(self)


class FakeTokenProvider:
    def get_optional(self, account):
        return f"token-{account}"


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakeCostPriceTable:
    def __init__(self, db):
        self.db = db

    async def get_current_data(self):
        return [{"local_vendor_code": "wild1", "purchase_price": 42}]


class FakeArticleTable:
    updated = None

    def __init__(self, db):
        self.db = db

    async def update_article_data(self, data):
        FakeArticleTable.updated = data


class FakeCardData:
    updated = None

    def __init__(self, db):
        self.db = db

    async def update_card_data(self, data):
        FakeCardData.updated = data


class FakeUnitEconomicsTable:
    updated = None

    def __init__(self, db):
        self.db = db

    async def update_data(self, data):
        FakeUnitEconomicsTable.updated = data


class CardActualizationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_actualize_card_data_uses_provided_db_context(self):
        service = CardActualizationService(
            token_provider=FakeTokenProvider(),
            wb_api=object(),
            logger=FakeLogger(),
        )

        async def fake_get_actually_data_by_account(account, token, articles):
            self.assertEqual(token, "token-Wild1")
            self.assertEqual(articles, {123})
            return {
                123: {
                    "account": account,
                    "local_vendor_code": "wild1",
                    "vendor_code": "vendor1",
                    "barcode": "barcode1",
                    "commission_wb": 12,
                    "discount": 5,
                    "height": 10,
                    "length": 20,
                    "logistic_from_wb_wh_to_opp": 33,
                    "photo_link": "photo",
                    "price": 100,
                    "subject_name": "subject",
                    "width": 30,
                }
            }

        service.get_actually_data_by_account = fake_get_actually_data_by_account
        fake_db = FakeDB()

        with (
            patch("services.card_actualization.CostPriceTable", FakeCostPriceTable),
            patch("services.card_actualization.ArticleTable", FakeArticleTable),
            patch("services.card_actualization.CardData", FakeCardData),
            patch("services.card_actualization.UnitEconomicsTable", FakeUnitEconomicsTable),
        ):
            await service.actualize_card_data_in_db({"Wild1": {123}}, db=fake_db)

        self.assertEqual(fake_db.acquire_count, 2)
        self.assertEqual(FakeArticleTable.updated, [(123, "Wild1", "wild1", "vendor1")])
        self.assertEqual(FakeCardData.updated[0][:11], (
            123,
            "barcode1",
            12,
            5,
            10,
            20,
            33,
            "photo",
            100,
            "subject",
            30,
        ))
        self.assertEqual(FakeUnitEconomicsTable.updated[0][:7], (123, 12, 5, 33, 100, 42, 8))


    async def test_missing_token_and_failed_account_do_not_stop_batch(self):
        class SelectiveTokenProvider:
            def get_optional(self, account):
                return {
                    " СТАРТ0854 ": "token-start",
                    "Broken": "token-broken",
                }.get(account)

        class RecordingLogger(FakeLogger):
            def __init__(self):
                self.warnings = []
                self.errors = []

            def warning(self, message, *args, **kwargs):
                self.warnings.append(message)

            def error(self, message, *args, **kwargs):
                self.errors.append(message)

        logger = RecordingLogger()
        service = CardActualizationService(
            token_provider=SelectiveTokenProvider(),
            wb_api=object(),
            logger=logger,
        )
        called_accounts = []

        async def fake_get_actually_data_by_account(account, token, articles):
            called_accounts.append(account)
            if account == "Broken":
                raise RuntimeError("account failure")
            return {
                123: {
                    "account": account,
                    "local_vendor_code": "wild1",
                    "vendor_code": "vendor1",
                    "barcode": "barcode1",
                    "commission_wb": 12,
                    "discount": 5,
                    "height": 10,
                    "length": 20,
                    "logistic_from_wb_wh_to_opp": 33,
                    "photo_link": "photo",
                    "price": 100,
                    "subject_name": "subject",
                    "width": 30,
                }
            }

        service.get_actually_data_by_account = fake_get_actually_data_by_account
        fake_db = FakeDB()

        with (
            patch("services.card_actualization.CostPriceTable", FakeCostPriceTable),
            patch("services.card_actualization.ArticleTable", FakeArticleTable),
            patch("services.card_actualization.CardData", FakeCardData),
            patch("services.card_actualization.UnitEconomicsTable", FakeUnitEconomicsTable),
        ):
            await service.actualize_card_data_in_db(
                {" СТАРТ0854 ": {123}, "Missing": {456}, "Broken": {789}},
                db=fake_db,
            )

        self.assertCountEqual(called_accounts, [" СТАРТ0854 ", "Broken"])
        self.assertNotIn("Missing", called_accounts)
        self.assertEqual(FakeArticleTable.updated[0][0], 123)
        self.assertEqual(len(logger.warnings), 1)
        self.assertEqual(len(logger.errors), 1)
        current_task = asyncio.current_task()
        pending_tasks = [
            task for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]
        self.assertEqual(pending_tasks, [])


if __name__ == "__main__":
    unittest.main()
