"""Card data actualization service using the job-scoped database pool."""

import asyncio
import datetime
from typing import Dict

try:
    from ..domain import calculate_sum_for_logistic, merge_dicts
    from ..repositories import ArticleTable, CardData, CostPriceDBContainer, CostPriceTable, UnitEconomicsTable
except ImportError:
    from domain import calculate_sum_for_logistic, merge_dicts
    from repositories import ArticleTable, CardData, CostPriceDBContainer, CostPriceTable, UnitEconomicsTable


class CardActualizationService:
    def __init__(self, token_provider, wb_api, logger):
        self.token_provider = token_provider
        self.wb_api = wb_api
        self.logger = logger

    async def actualize_card_data_in_db(self, account_articles: Dict[str, int], db):
        """Обновление состояния данных карточек по всем кабинетам"""
        self.logger.info("Обновление состояния данных карточек по всем кабинетам в бд")
        time_start = datetime.datetime.now()
        tasks = []
        for account, nm_ids in account_articles.items():
            token = self.token_provider.get(account)
            task = asyncio.create_task(self.get_actually_data_by_account(
                token=token,
                account=account,
                articles=nm_ids
            ))
            tasks.append(task)

        together_results = await asyncio.gather(*tasks)

        to_update_card_data = []
        to_update_article = []
        to_update_unit_economics = []
        current_time = datetime.datetime.now()

        async with db.acquire() as conn:
            cost_price = await CostPriceTable(db=conn).get_current_data()
            cost_price_model = CostPriceDBContainer(cost_price)  # получение закупочной стоимости по wild

        for results in together_results:
            for article, card_data in results.items():
                try:
                    to_update_article.append(
                        (article, card_data['account'], card_data['local_vendor_code'], card_data['vendor_code']))
                    to_update_card_data.append(
                        (article, card_data.get('barcode', None), card_data.get('commission_wb', None),
                         card_data.get('discount', None),
                         card_data.get('height', None), card_data.get('length', None),
                         card_data.get('logistic_from_wb_wh_to_opp', None), card_data.get('photo_link', None),
                         card_data.get('price', None),
                         card_data.get('subject_name', None), card_data.get('width', None),
                         current_time)
                    )
                    cost_price = cost_price_model.local_vendor_code.get(card_data['local_vendor_code'],
                                                                        None)  # получение закупочной стоимости
                    percent_by_tax = 8  # плохая реализация #todo перенести данные в бд, вставка от запроса api, вставка значения по умолчанию
                    if cost_price is not None:
                        cost_price = cost_price.get("purchase_price") if isinstance(cost_price, dict) else cost_price.purchase_price
                    to_update_unit_economics.append(
                        (article, card_data.get('commission_wb', None),
                         card_data.get('discount', None),
                         card_data.get('logistic_from_wb_wh_to_opp', None),
                         card_data.get('price', None),
                         cost_price,
                         percent_by_tax,
                         current_time)
                    )
                except KeyError as e:
                    self.logger.error(f"Error in -func actualize_card_data_in_db {article} : {e}")

        async with db.acquire() as connection:
            async with connection.transaction():
                card_data_db = CardData(db=connection)
                article = ArticleTable(db=connection)
                unit_economics = UnitEconomicsTable(db=connection)
                await article.update_article_data(data=to_update_article)
                await card_data_db.update_card_data(data=to_update_card_data)
                await unit_economics.update_data(data=to_update_unit_economics)
        self.logger.info(
            f"Обновление состояния данных карточек по всем кабинетам в бд завершено. Время выполнения: {datetime.datetime.now() - time_start}")

    async def get_actually_data_by_account(self, account, token, articles):
        """Получение данных по кабинету:
              article_id, account, length, width, height, barcode, local_vendor_code, vendor_code,
              skus, photo_link, logistic_from_wb_wh_to_opp, commission_wb, price, discount, subject_name,
        """
        wb_api_content = self.wb_api.ListOfCardsContent(token=token)
        wb_api_price_and_discount = self.wb_api.ListOfGoodsPricesAndDiscounts(token=token)
        commission_traffics = self.wb_api.CommissionTariffs(token=token)

        task1 = wb_api_content.get_list_of_cards_async(nm_ids_list=articles, limit=100, account=account)
        task2 = wb_api_price_and_discount.get_log_for_nm_ids_async(filter_nm_ids=articles, account=account)
        task3 = commission_traffics.get_tariffs_box_from_marketplace_async()

        card_from_nm_ids_filter, goods_nm_ids, current_tariffs_data = await asyncio.gather(task1, task2, task3)

        merge_json_data = merge_dicts(goods_nm_ids, card_from_nm_ids_filter)

        subject_names = set()  # итог всех полученных с карточек предметов
        for article, data in merge_json_data.items():
            if "local_vendor_code" in data and data["local_vendor_code"] != "не найдено":
                subject_names.add(data["subject_name"])  # собираем множество с предметами
                try:
                    result_log_value = calculate_sum_for_logistic(
                        # на лету считаем "Логистика от склада WB до ПВЗ"
                        for_one_liter=float(current_tariffs_data["boxDeliveryBase"].replace(',', '.')),
                        next_liters=float(current_tariffs_data["boxDeliveryLiter"].replace(',', '.')),
                        height=int(data['height']),
                        length=int(data['length']),
                        width=int(data['width']), )
                    # добавляем результат вычислений в итоговые данные
                    data["logistic_from_wb_wh_to_opp"] = result_log_value
                except Exception as e:
                    self.logger.info(f"ERROR by calculate_sum_for_logistic : {str(e)}")
            else:
                self.logger.info(f"article : {article}, data : {data}")
        # получение комиссии WB
        subject_commissions = await commission_traffics.get_commission_on_subject_async(subject_names=subject_names)
        for card in merge_json_data.values():
            if subject_commissions is not None:
                for sc in subject_commissions.items():
                    if "subject_name" in card and sc[0] == card["subject_name"]:
                        card["commission_wb"] = sc[1]
        return merge_json_data
