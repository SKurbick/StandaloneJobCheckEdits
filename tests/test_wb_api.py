import unittest

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


if __name__ == "__main__":
    unittest.main()
