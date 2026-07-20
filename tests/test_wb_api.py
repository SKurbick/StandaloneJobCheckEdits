import unittest
from unittest.mock import AsyncMock

from article_state import ArticleState

from wb_api import (
    CommissionTariffs,
    LeftoversMarketplace,
    ListOfCardsContent,
    ListOfGoodsPricesAndDiscounts,
    WBApiFactory,
    WarehouseMarketplaceWB,
)


class FakeSession:
    pass


class WBApiFactoryTest(unittest.TestCase):
    def test_factory_passes_shared_session_and_logger_to_clients(self):
        session = FakeSession()
        logger = object()
        factory = WBApiFactory(session=session, logger=logger)

        clients = [
            factory.ListOfCardsContent("token"),
            factory.ListOfGoodsPricesAndDiscounts("token"),
            factory.CommissionTariffs("token"),
            factory.LeftoversMarketplace("token"),
            factory.WarehouseMarketplaceWB("token"),
        ]

        self.assertIsInstance(clients[0], ListOfCardsContent)
        self.assertIsInstance(clients[1], ListOfGoodsPricesAndDiscounts)
        self.assertIsInstance(clients[2], CommissionTariffs)
        self.assertIsInstance(clients[3], LeftoversMarketplace)
        self.assertIsInstance(clients[4], WarehouseMarketplaceWB)
        for client in clients:
            self.assertIs(client.session, session)
            self.assertIs(client.logger, logger)
            self.assertIs(client.limiter, factory.limiter)
            self.assertEqual(client.headers["Authorization"], "token")
        self.assertIsNone(factory.account_concurrency)


class ArticleStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_classifies_active_trash_and_not_found(self):
        client = ListOfCardsContent("token", session=FakeSession())
        client._find_requested_nm_ids = AsyncMock(
            side_effect=[({101}, True), ({202}, True)]
        )

        states = await client.get_article_states_async([101, 202, 303], account="Wild1")

        self.assertEqual(
            states,
            {
                101: ArticleState.ACTIVE,
                202: ArticleState.IN_TRASH,
                303: ArticleState.NOT_FOUND,
            },
        )
        client._find_requested_nm_ids.assert_any_await("list", {101, 202, 303})
        client._find_requested_nm_ids.assert_any_await("trash", {202, 303})

    async def test_active_scan_failure_is_not_reported_as_not_found(self):
        client = ListOfCardsContent("token", session=FakeSession())
        client._find_requested_nm_ids = AsyncMock(return_value=({101}, False))

        states = await client.get_article_states_async([101, 202], account="Wild1")

        self.assertEqual(states[101], ArticleState.ACTIVE)
        self.assertEqual(states[202], ArticleState.CHECK_FAILED)
        self.assertEqual(client._find_requested_nm_ids.await_count, 1)

    async def test_trash_scan_failure_is_not_reported_as_not_found(self):
        client = ListOfCardsContent("token", session=FakeSession())
        client._find_requested_nm_ids = AsyncMock(
            side_effect=[(set(), True), ({202}, False)]
        )

        states = await client.get_article_states_async([202, 303], account="Wild1")

        self.assertEqual(states[202], ArticleState.IN_TRASH)
        self.assertEqual(states[303], ArticleState.CHECK_FAILED)


if __name__ == "__main__":
    unittest.main()
