"""Async Wildberries API clients for the standalone check-edits job."""

import asyncio
import datetime
from collections import defaultdict
from contextlib import asynccontextmanager

try:
    from .article_state import ArticleState
except ImportError:
    from article_state import ArticleState

try:
    from .domain import process_local_vendor_code, process_string
except ImportError:
    from domain import process_local_vendor_code, process_string


def _require_aiohttp():
    import aiohttp

    return aiohttp


def _aiohttp_transient_errors(aiohttp):
    errors = [aiohttp.ClientError, asyncio.TimeoutError]
    for name in ("ClientResponseError", "ConnectionTimeoutError", "ServerTimeoutError"):
        error_type = getattr(aiohttp, name, None)
        if error_type is not None and error_type not in errors:
            errors.append(error_type)
    return tuple(errors)


@asynccontextmanager
async def create_client_session(timeout=60):
    aiohttp = _require_aiohttp()
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        yield session


class _WBClient:
    def __init__(self, token, session=None, logger=None, limiter=None):
        self.token = token
        self.session = session
        self.logger = logger
        self.limiter = limiter
        self.headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

    @asynccontextmanager
    async def _session_scope(self):
        if self.session is not None:
            yield self.session
            return

        async with create_client_session() as session:
            yield session

    def _info(self, message):
        if self.logger is not None:
            self.logger.info(message)

    def _warning(self, message):
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message):
        if self.logger is not None:
            self.logger.error(message)

    @asynccontextmanager
    async def _limited(self, endpoint):
        if self.limiter is None:
            yield
            return

        async with self.limiter.limit(self.token, endpoint):
            yield


class WBRateLimiter:
    """Per-token endpoint semaphores for WB API calls."""

    DEFAULT_LIMITS = {
        "content_read": 2,
        "content_write": 1,
        "prices_read": 2,
        "prices_write": 1,
        "tariffs": 1,
        "warehouses": 2,
        "stocks_read": 2,
        "stocks_write": 1,
    }

    def __init__(self, limits=None):
        self.limits = {**self.DEFAULT_LIMITS, **(limits or {})}
        self._semaphores = defaultdict(self._make_semaphore)

    def _make_semaphore(self):
        return asyncio.Semaphore(1)

    @asynccontextmanager
    async def limit(self, token, endpoint):
        key = (token, endpoint)
        if key not in self._semaphores:
            self._semaphores[key] = asyncio.Semaphore(self.limits.get(endpoint, 1))
        async with self._semaphores[key]:
            yield


class ListOfCardsContent(_WBClient):
    """API Список товаров."""

    def __init__(self, token, session=None, logger=None, limiter=None):
        super().__init__(token=token, session=session, logger=logger, limiter=limiter)
        self.url = "https://content-api.wildberries.ru/content/v2/get/cards/{}"
        self.update_url = "https://content-api.wildberries.ru/content/v2/cards/{}"

    async def _find_requested_nm_ids(self, endpoint, requested_nm_ids, limit=100):
        """Return matching ids and whether the paginated scan completed."""
        aiohttp = _require_aiohttp()
        requested = {int(nm_id) for nm_id in requested_nm_ids}
        found = set()
        url = self.url.format(endpoint)
        payload = {
            "settings": {
                "sort": {"ascending": True},
                "cursor": {"limit": limit},
                "filter": {"withPhoto": -1},
            }
        }

        while requested - found:
            response_result = None
            page_loaded = False
            for attempt in range(1, 4):
                try:
                    async with self._limited("content_read"):
                        async with self._session_scope() as session:
                            async with session.post(url, headers=self.headers, json=payload) as response:
                                status = response.status
                                try:
                                    response_result = await response.json()
                                except Exception:
                                    response_result = None
                    if status == 200 and isinstance(response_result, dict):
                        page_loaded = True
                        break
                    self._warning(
                        f"Не удалось проверить карточки WB. endpoint={endpoint} "
                        f"status={status} attempt={attempt}/3 response={response_result}"
                    )
                except _aiohttp_transient_errors(aiohttp) as exc:
                    self._warning(
                        f"Ошибка соединения при проверке карточек WB. endpoint={endpoint} "
                        f"attempt={attempt}/3 error={exc}"
                    )
                if attempt < 3:
                    await asyncio.sleep(20)

            if not page_loaded:
                return found, False

            cursor = response_result.get("cursor")
            cards = response_result.get("cards")
            if not isinstance(cursor, dict) or not isinstance(cards, list):
                self._warning(
                    f"Некорректный ответ проверки карточек WB. endpoint={endpoint} "
                    f"response={response_result}"
                )
                return found, False

            for card in cards:
                if isinstance(card, dict) and card.get("nmID") in requested:
                    found.add(card["nmID"])

            total = cursor.get("total")
            if not isinstance(total, int):
                return found, False
            if total < limit or not cards:
                return found, True

            updated_at = cursor.get("updatedAt")
            cursor_nm_id = cursor.get("nmID")
            if updated_at is None or cursor_nm_id is None:
                return found, False
            payload["settings"]["cursor"].update(
                {"updatedAt": updated_at, "nmID": cursor_nm_id}
            )

        return found, True

    async def get_article_states_async(self, nm_ids, account=None):
        """Classify articles without treating an API failure as absence."""
        requested = {int(nm_id) for nm_id in nm_ids}
        states = {}
        active_ids, active_complete = await self._find_requested_nm_ids("list", requested)
        states.update({nm_id: ArticleState.ACTIVE for nm_id in active_ids})

        unresolved = requested - active_ids
        if not active_complete:
            states.update({nm_id: ArticleState.CHECK_FAILED for nm_id in unresolved})
            self._warning(
                f"Проверка активных карточек завершилась ошибкой. "
                f"account={account} unresolved_nm_ids={sorted(unresolved)}"
            )
            return states

        trash_ids, trash_complete = await self._find_requested_nm_ids("trash", unresolved.copy())
        states.update({nm_id: ArticleState.IN_TRASH for nm_id in trash_ids})
        unresolved = unresolved - trash_ids
        final_state = ArticleState.NOT_FOUND if trash_complete else ArticleState.CHECK_FAILED
        states.update({nm_id: final_state for nm_id in unresolved})
        return states

    async def get_list_of_cards_async(self, nm_ids_list: list, limit: int = 100, account=None) -> dict:
        """Получение всех карточек по совпадению с nm_ids_list."""
        aiohttp = _require_aiohttp()
        nm_ids_list_for_edit = [*nm_ids_list]
        url = self.url.format("list")
        card_result_for_match = {}
        json_obj = {
            "settings": {
                "cursor": {
                    "limit": limit
                },
                "filter": {
                    "withPhoto": -1
                }
            }
        }
        total = 1
        updated_at = None
        cursor_nm_id = None
        response_result = None
        page = 0

        while True:
            for _ in range(10):
                try:
                    async with self._limited("content_read"):
                        async with self._session_scope() as session:
                            async with session.post(url, headers=self.headers, json=json_obj) as response:
                                response_result = await response.json()
                    total = response_result["cursor"]["total"]
                    page += 1
                    cursor = response_result.get("cursor", {})
                    cards_count = len(response_result.get("cards", []))
                    cursor_nm_id_value = cursor.get("nmID")
                    cursor_updated_at_value = cursor.get("updatedAt")
                    self._info(
                        f"WB async cards table page loaded. account={account} page={page} "
                        f"cards={cards_count} cursor_total={total} "
                        f"cursor_nm_id={cursor_nm_id_value} "
                        f"cursor_updated_at={cursor_updated_at_value} "
                        f"nm_ids_left_before={len(nm_ids_list_for_edit)}"
                    )
                    if total == 0:
                        break
                    updated_at = response_result["cursor"]["updatedAt"]
                    cursor_nm_id = response_result["cursor"]["nmID"]
                    for card in response_result["cards"]:
                        if card["nmID"] in nm_ids_list_for_edit:
                            card_result_for_match[card["nmID"]] = {
                                "article_id": card["nmID"],
                                "length": card["dimensions"]["length"],
                                "width": card["dimensions"]["width"],
                                "height": card["dimensions"]["height"],
                                "subject_name": card["subjectName"],
                                "barcode": card["sizes"][0]["skus"][-1],
                                "local_vendor_code": process_local_vendor_code(card["vendorCode"]),
                                "vendor_code": card["vendorCode"],
                                "account": account,
                                "skus": card["sizes"][0]["skus"],
                            }
                            photo = "НЕТ"
                            if "photos" in card:
                                photo = card["photos"][0]["tm"]
                            card_result_for_match[card["nmID"]].update({
                                "photo_link": photo,
                            })
                            nm_ids_list_for_edit.remove(card["nmID"])
                        if len(nm_ids_list_for_edit) == 0:
                            break
                    break
                except aiohttp.ClientError as e:
                    self._warning(f"Exception {e} sleep 63 sec")
                    await asyncio.sleep(63)
                    continue
                except Exception as e:
                    self._warning(f"Exception {e} {account} sleep 36 sec")
                    self._warning(f"response {response_result}")
                    await asyncio.sleep(36)

            if total < limit or len(nm_ids_list_for_edit) == 0:
                self._info(f"account: {account} total: {total}")
                break

            update_data = {
                "updatedAt": updated_at,
                "nmID": cursor_nm_id
            }
            json_obj["settings"]["cursor"].update(update_data)

        self._info(f"account: {account} len(card_result_for_match): {len(card_result_for_match)}")
        self._info("Невалидные артикулы в запросе:")
        self._info(f"nm_ids_list_for_edit : {nm_ids_list_for_edit}")
        return card_result_for_match


    async def get_list_of_cards_for_table_async(
        self,
        nm_ids_list: list,
        limit: int = 100,
        account=None,
        only_edits_data=False,
    ) -> dict:
        """Legacy table-shaped card data, but fetched asynchronously."""
        aiohttp = _require_aiohttp()
        nm_ids_list_for_edit = [*nm_ids_list]
        url = self.url.format("list")
        card_result_for_match = {}
        json_obj = {
            "settings": {
                "cursor": {"limit": limit},
                "filter": {"withPhoto": -1},
            }
        }
        total = 1
        updated_at = None
        cursor_nm_id = None
        response_result = None
        page = 0

        while True:
            for _ in range(10):
                try:
                    async with self._limited("content_read"):
                        async with self._session_scope() as session:
                            async with session.post(url, headers=self.headers, json=json_obj) as response:
                                response_result = await response.json()
                    total = response_result["cursor"]["total"]
                    page += 1
                    cursor = response_result.get("cursor", {})
                    cards_count = len(response_result.get("cards", []))
                    cursor_nm_id_value = cursor.get("nmID")
                    cursor_updated_at_value = cursor.get("updatedAt")
                    self._info(
                        f"WB async cards table page loaded. account={account} page={page} "
                        f"cards={cards_count} cursor_total={total} "
                        f"cursor_nm_id={cursor_nm_id_value} "
                        f"cursor_updated_at={cursor_updated_at_value} "
                        f"nm_ids_left_before={len(nm_ids_list_for_edit)}"
                    )
                    if total == 0:
                        break
                    updated_at = response_result["cursor"]["updatedAt"]
                    cursor_nm_id = response_result["cursor"]["nmID"]
                    for card in response_result["cards"]:
                        if card["nmID"] in nm_ids_list_for_edit:
                            card_result_for_match[card["nmID"]] = {
                                "Артикул": card["nmID"],
                                "Текущая\nДлина (см)": card["dimensions"]["length"],
                                "Текущая\nШирина (см)": card["dimensions"]["width"],
                                "Текущая\nВысота (см)": card["dimensions"]["height"],
                                "Предмет": card["subjectName"],
                                "Баркод": card["sizes"][0]["skus"][-1],
                                "wild": process_string(card["vendorCode"]),
                                "vendor_code": card["vendorCode"],
                                "account": account,
                            }
                            if not only_edits_data:
                                photo = "НЕТ"
                                if "photos" in card:
                                    photo = card["photos"][0]["tm"]
                                card_result_for_match[card["nmID"]]["Фото"] = photo
                            nm_ids_list_for_edit.remove(card["nmID"])
                        if len(nm_ids_list_for_edit) == 0:
                            break
                    break
                except aiohttp.ClientError as e:
                    self._warning(f"Exception {e} sleep 63 sec")
                    await asyncio.sleep(63)
                    continue
                except Exception as e:
                    self._warning(f"Exception {e} {account} sleep 36 sec")
                    self._warning(f"response {response_result}")
                    await asyncio.sleep(36)

            if total < limit or len(nm_ids_list_for_edit) == 0:
                self._info(f"account: {account} total: {total}")
                break

            json_obj["settings"]["cursor"].update({
                "updatedAt": updated_at,
                "nmID": cursor_nm_id,
            })

        if nm_ids_list_for_edit:
            self._warning(
                f"WB async cards table finished with unresolved nm_ids. account={account} "
                f"pages={page} unresolved_nm_ids={nm_ids_list_for_edit} "
                f"found={list(card_result_for_match.keys())}"
            )

        if nm_ids_list_for_edit and not only_edits_data:
            for nm_id in nm_ids_list_for_edit:
                card_result_for_match[nm_id] = {
                    "Артикул": nm_id,
                    "Текущая\nДлина (см)": "не найдено",
                    "Текущая\nШирина (см)": "не найдено",
                    "Текущая\nВысота (см)": "не найдено",
                    "Предмет": "не найдено",
                    "Баркод": "не найдено",
                    "wild": "не найдено",
                    "vendor_code": "не найдено",
                    "account": account,
                    "Фото": "НЕТ",
                }
        return card_result_for_match

    async def size_edit_async(self, data: list):
        url = self.update_url.format("update")
        aiohttp = _require_aiohttp()
        try:
            async with self._limited("content_write"):
                async with self._session_scope() as session:
                    async with session.post(url=url, headers=self.headers, json=data) as response:
                        response_result = await response.json()
            self._info(f"size edit result: {response_result}")
            await asyncio.sleep(2)
            return response_result.get("error") is False
        except aiohttp.ClientError as e:
            self._error(f"size edit error: {e}")
            return False


class ListOfGoodsPricesAndDiscounts(_WBClient):
    """API Список товаров."""

    def __init__(self, token, limit: int = 1, offset: int = 0, session=None, logger=None, limiter=None):
        super().__init__(token=token, session=session, logger=logger, limiter=limiter)
        self.limit = limit
        self.offset = offset
        self.url = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/{}"
        self.post_url = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"

    async def get_log_for_nm_ids_async(self, filter_nm_ids, account=None) -> dict:
        """Получение цен и скидок по совпадению с nmID."""
        aiohttp = _require_aiohttp()
        url = self.url.format("filter")
        nm_ids = [*filter_nm_ids]
        nm_ids_list = {}
        self._info("В функции get_log_for_nm_ids")
        offset = 0
        limit = 1000
        response_result = {}
        retry_number = 0

        while True:
            params = {
                "limit": limit,
                "offset": offset,
            }

            for retry_number in range(1, 10):
                try:
                    async with self._limited("prices_read"):
                        async with self._session_scope() as session:
                            async with session.get(url, headers=self.headers, params=params, timeout=60) as response:
                                response_result = await response.json()
                            if "data" in response_result:
                                if response_result["data"] is not None:
                                    for card in response_result["data"]["listGoods"]:
                                        if card["nmID"] in nm_ids:
                                            nm_ids_list[card["nmID"]] = {
                                                "Цена на WB без скидки": card["sizes"][0]["price"],
                                                "Скидка %": card["discount"],
                                            }
                                            nm_ids.remove(card["nmID"])
                                    break
                                break
                            if len(response_result) == 0:
                                break
                            if response.status == 429:
                                self._info(nm_ids)
                                self._info(f"попытка: {retry_number} sleep 10 sec")
                                await asyncio.sleep(10)
                                continue
                            break
                except _aiohttp_transient_errors(aiohttp) as e:
                    self._error(f"[ERROR] func -get_log_for_nm_ids_async {e} sleep 36 sec")
                    await asyncio.sleep(36)

            self._info("Дошел до условия прерывания бесконечного цикла")
            self._info(f"offset {offset}")
            if (
                len(nm_ids) == 0
                or retry_number == 9
                or "data" not in response_result
                or response_result["data"] is None
                or response_result["data"]["listGoods"] is None
                or len(response_result["data"]["listGoods"]) == 0
            ):
                self._info("прерывание бесконечного цикла")
                break
            offset += limit

        if len(nm_ids) != 0:
            self._info(f"в запросе просмотра цен есть невалидные артикулы -> {account}: {nm_ids}")
        return nm_ids_list


    async def add_new_price_and_discount_async(self, data: list, step=1000):
        aiohttp = _require_aiohttp()
        url = self.post_url
        for start in range(0, len(data), step):
            batch_data = data[start: start + step]
            for _ in range(10):
                try:
                    async with self._limited("prices_write"):
                        async with self._session_scope() as session:
                            async with session.post(url=url, headers=self.headers, json={"data": batch_data}) as response:
                                response_result = await response.json()
                                status = response.status
                    self._info(f"Артикулы на изменение цены: {batch_data}")
                    self._info(f"price and discount edit result: {response_result}")
                    await asyncio.sleep(2)
                    if (
                        status in (200, 208)
                        or response_result.get("errorText") in (
                            "Task already exists",
                            "No goods for process",
                            "The specified prices and discounts are already set",
                            "Specified prices and discounts are already set",
                        )
                    ):
                        break
                except aiohttp.ClientError as e:
                    self._error(f"price and discount edit error: {e}")
                    await asyncio.sleep(63)


class CommissionTariffs(_WBClient):
    def __init__(self, token, session=None, logger=None, limiter=None):
        super().__init__(token=token, session=session, logger=logger, limiter=limiter)
        self.url = "https://common-api.wildberries.ru/api/v1/tariffs/{}"

    async def get_tariffs_box_from_marketplace_async(self) -> dict | None:
        aiohttp = _require_aiohttp()
        url = self.url.format("box")
        params = {
            "date": str(datetime.date.today())
        }

        try:
            async with self._limited("tariffs"):
                async with self._session_scope() as session:
                    async with session.get(url=url, headers=self.headers, params=params) as response:
                        response_result = await response.json()

            for warehouse_data in response_result["response"]["data"]["warehouseList"]:
                if warehouse_data["warehouseName"] == "Маркетплейс":
                    return {
                        "boxDeliveryBase": warehouse_data["boxDeliveryBase"],
                        "boxDeliveryLiter": warehouse_data["boxDeliveryLiter"],
                    }
                return None
        except aiohttp.ClientError as e:
            self._error(f"Error : {e}")
            return None

    async def get_commission_on_subject_async(self, subject_names) -> dict | None:
        aiohttp = _require_aiohttp()
        url = self.url.format("commission")
        result_commission_data = {}
        response_result = None

        try:
            async with self._limited("tariffs"):
                async with self._session_scope() as session:
                    async with session.get(url, headers=self.headers) as response:
                        response_result = await response.json()
                        if response.status != 200:
                            self._info(f"response_result : {response_result}")
                        if response.status == 429:
                            self._error("429 Просмотр комиссии по Предметам. Повторная попытка через минуту")
                            await asyncio.sleep(60)
                            async with session.get(url, headers=self.headers) as response2:
                                response_result = await response2.json()

            for subject_name in subject_names:
                for item in response_result["report"]:
                    if item["subjectName"] == subject_name:
                        result_commission_data[subject_name] = item["kgvpMarketplace"]
                        break
            return result_commission_data

        except (aiohttp.ClientError, aiohttp.ClientResponseError, Exception) as e:
            self._info(f"response_result : {response_result}")
            self._error(f"Error : {e}")
            return None


class LeftoversMarketplace(_WBClient):
    REQUEST_RETRIES = 3

    def __init__(self, token, session=None, logger=None, limiter=None):
        super().__init__(token=token, session=session, logger=logger, limiter=limiter)
        self.url = "https://marketplace-api.wildberries.ru/api/v3/stocks/{}"

    async def get_amount_from_warehouses_async(self, warehouse_id, barcodes, step=1000):
        url = self.url.format(f"{warehouse_id}")
        barcodes_quantity = []
        async with self._session_scope() as session:
            for start in range(0, len(barcodes), step):
                barcodes_part = barcodes[start: start + step]
                async with self._limited("stocks_read"):
                    async with session.post(url=url, headers=self.headers, json={"skus": barcodes_part}) as response:
                        response_result = await response.json()
                for stock in response_result.get("stocks", []):
                    barcodes_quantity.append({"Баркод": stock["sku"], "остаток": stock["amount"]})
        return barcodes_quantity

    @staticmethod
    def _get_not_found_chrt_ids(response_data):
        errors = response_data if isinstance(response_data, list) else [response_data]
        not_found_chrt_ids = set()
        for error in errors:
            if not isinstance(error, dict) or error.get("code") != "NotFound":
                continue
            error_data = error.get("data", [])
            if isinstance(error_data, dict):
                error_data = [error_data]
            for stock in error_data:
                if isinstance(stock, dict) and stock.get("chrtId") is not None:
                    not_found_chrt_ids.add(str(stock["chrtId"]))
        return not_found_chrt_ids

    async def edit_amount_from_warehouses_async(self, warehouse_id, edit_barcodes_list, step=1000):
        url = self.url.format(f"{warehouse_id}")
        result = {"successful": [], "failed": []}
        batches_to_process = [
            edit_barcodes_list[start: start + step]
            for start in range(0, len(edit_barcodes_list), step)
        ]
        aiohttp = _require_aiohttp()

        async with self._session_scope() as session:
            while batches_to_process:
                barcodes_part = batches_to_process.pop(0)
                self._info(barcodes_part)
                response_result = None
                response_status = None
                response_headers = {}

                for attempt in range(1, self.REQUEST_RETRIES + 1):
                    try:
                        async with self._limited("stocks_write"):
                            async with session.put(
                                url=url,
                                headers=self.headers,
                                json={"stocks": barcodes_part},
                            ) as response:
                                response_status = response.status
                                response_headers = response.headers
                                try:
                                    response_result = await response.json()
                                except Exception:
                                    response_result = await response.text()
                        break
                    except aiohttp.ClientError as e:
                        self._error(
                            f"Ошибка соединения при изменении остатков. Склад: {warehouse_id}. "
                            f"Попытка {attempt}/{self.REQUEST_RETRIES}. Ошибка: {e}"
                        )
                        if attempt < self.REQUEST_RETRIES:
                            await asyncio.sleep(10 * attempt)

                if response_status is None:
                    self._error(
                        f"Ошибка запроса на изменение остатков. Пачка пропущена после "
                        f"{self.REQUEST_RETRIES} попыток. Склад: {warehouse_id}"
                    )
                    result["failed"].extend(barcodes_part)
                    continue

                if response_status <= 399:
                    self._info(f"Запрос на изменение остатков. Код: {response_status}")
                    result["successful"].extend(barcodes_part)
                    continue

                not_found_chrt_ids = set()
                if response_status == 409:
                    not_found_chrt_ids = self._get_not_found_chrt_ids(response_result)

                rejected_stocks = [
                    stock for stock in barcodes_part
                    if str(stock.get("chrtId")) in not_found_chrt_ids
                ]
                stocks_to_retry = [
                    stock for stock in barcodes_part
                    if str(stock.get("chrtId")) not in not_found_chrt_ids
                ]

                if rejected_stocks and len(stocks_to_retry) < len(barcodes_part):
                    result["failed"].extend(rejected_stocks)
                    self._error(
                        f"WB отклонил chrtId как NotFound. Склад: {warehouse_id}. "
                        f"Исключены: {sorted(not_found_chrt_ids)}"
                    )
                    if stocks_to_retry:
                        batches_to_process.insert(0, stocks_to_retry)
                    continue

                self._error(
                    f"Ошибка запроса на изменение остатков. Пачка пропущена. Код: {response_status}. "
                    f"Content-Type: {response_headers.get('Content-Type', '<не указан>')}. "
                    f"Ответ: {response_result}"
                )
                result["failed"].extend(barcodes_part)

        return result


class WarehouseMarketplaceWB(_WBClient):
    def __init__(self, token, session=None, logger=None, limiter=None):
        super().__init__(token=token, session=session, logger=logger, limiter=limiter)
        self.url = "https://marketplace-api.wildberries.ru/api/v3/warehouses"

    async def get_account_warehouse_async(self):
        async with self._session_scope() as session:
            async with self._limited("warehouses"):
                async with session.get(url=self.url, headers=self.headers) as response:
                    response_result = await response.json()
                    status = response.status
            if status > 400:
                for _ in range(10):
                    async with self._limited("warehouses"):
                        async with session.get(url=self.url, headers=self.headers) as retry_response:
                            response_result = await retry_response.json()
                            if retry_response.status < 400:
                                await asyncio.sleep(60)
                                break
            return response_result


class WBApiFactory:
    """Factory with the legacy class names expected by CardActualizationService."""

    def __init__(self, session=None, logger=None, limiter=None, account_concurrency=None):
        self.session = session
        self.logger = logger
        self.limiter = limiter or WBRateLimiter()
        self.account_concurrency = account_concurrency

    def ListOfCardsContent(self, token):
        return ListOfCardsContent(token=token, session=self.session, logger=self.logger, limiter=self.limiter)

    def ListOfGoodsPricesAndDiscounts(self, token):
        return ListOfGoodsPricesAndDiscounts(token=token, session=self.session, logger=self.logger, limiter=self.limiter)

    def CommissionTariffs(self, token):
        return CommissionTariffs(token=token, session=self.session, logger=self.logger, limiter=self.limiter)

    def LeftoversMarketplace(self, token):
        return LeftoversMarketplace(token=token, session=self.session, logger=self.logger, limiter=self.limiter)

    def WarehouseMarketplaceWB(self, token):
        return WarehouseMarketplaceWB(token=token, session=self.session, logger=self.logger, limiter=self.limiter)
