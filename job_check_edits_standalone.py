import asyncio
import datetime
import json
import os
import re
import time
from collections import ChainMap
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from pprint import pprint
from typing import Any, Dict, List, Set, Tuple

import aiohttp
import asyncpg
import gspread
import gspread.exceptions
import pandas as pd
import requests
from dotenv import load_dotenv
from gspread import Client, service_account
from loguru import logger as loguru_logger


load_dotenv()


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logging")
os.makedirs(LOG_DIR, exist_ok=True)


def get_logger():
    log_file = os.path.join(LOG_DIR, "job_check_edits_standalone.log")
    loguru_logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        rotation="10 MB",
        compression="zip",
        level="DEBUG",
        enqueue=True,
    )
    return loguru_logger


logger = get_logger()


def log_job(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        job_name = func.__name__
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
        job_file = __file__

        job_log_dir = os.path.join(LOG_DIR, job_name)
        os.makedirs(job_log_dir, exist_ok=True)
        log_filename = os.path.join(job_log_dir, f"{job_name}_{timestamp}.log")

        filter_func = lambda record: record["extra"].get("job") == job_name

        sink_id = loguru_logger.add(
            log_filename,
            format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
            level="INFO",
            filter=filter_func,
        )
        with loguru_logger.contextualize(job=job_name):
            loguru_logger.info(f"Начало выполнения задачи '{job_name}' в файле {job_file} (время: {timestamp})")
            try:
                result = await func(*args, **kwargs)
                loguru_logger.info(f"Задача '{job_name}' завершена успешно")
                return result
            except Exception as e:
                loguru_logger.error(f"Ошибка в задаче '{job_name}': {e}")
                raise
            finally:
                loguru_logger.remove(sink_id)

    return wrapper


@dataclass
class Settings:
    SHEET: str = os.getenv("SHEET")
    SPREADSHEET: str = os.getenv("SPREADSHEET")
    CREEDS_FILE_NAME: str = os.getenv("CREEDS_FILE_NAME", "creds.json")
    TOKENS_FILE_NAME: str = os.getenv("TOKENS_FILE_NAME", "tokens.json")
    PC_SHEET: str = os.getenv("PC_SHEET")
    PC_SPREADSHEET: str = os.getenv("PC_SPREADSHEET")


@dataclass
class DBConfig:
    DB_USER: str = os.getenv("DB_USER")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD")
    DB_NAME: str = os.getenv("DB_NAME")
    DB_HOST: str = os.getenv("DB_HOST")
    DB_PORT: int = os.getenv("DB_PORT")


settings = Settings()
DATABASE = DBConfig()

creds_json = settings.CREEDS_FILE_NAME
spreadsheet = settings.SPREADSHEET
sheet = settings.SHEET
logger.info(settings.SHEET)
logger.info(settings.SPREADSHEET)
logger.info("time to start:", datetime.datetime.now().time().strftime("%H:%M:%S"))


def get_wb_tokens() -> dict:
    with open(settings.TOKENS_FILE_NAME, "r", encoding="utf-8") as file:
        return json.load(file)



class Database1:
    def __init__(
        self,
        user=DATABASE.DB_USER,
        password=DATABASE.DB_PASSWORD,
        database=DATABASE.DB_NAME,
        host=DATABASE.DB_HOST,
        port=DATABASE.DB_PORT,
    ):
        self._user = user
        self._password = password
        self._database = database
        self._host = host
        self._port = port
        self._pool = None
        self._max_size = 15
        self._min_size = 5
        self._timeout = 300
        self._command_timeout = 250

    async def connect(self):
        logger.info("Connecting to database...")
        self._pool = await asyncpg.create_pool(
            user=self._user,
            password=self._password,
            database=self._database,
            host=self._host,
            port=self._port,
            max_size=self._max_size,
            min_size=self._min_size,
            timeout=self._timeout,
            command_timeout=self._command_timeout,
        )

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @asynccontextmanager
    async def acquire(self):
        if not self._pool:
            raise RuntimeError("Database pool is not initialized")
        async with self._pool.acquire() as connection:
            yield connection

    async def fetch(self, query, *args):
        async with self.acquire() as connection:
            return await connection.fetch(query, *args)

    async def executemany(self, query, args):
        async with self.acquire() as connection:
            return await connection.executemany(query, args)


class ArticleTable:
    def __init__(self, db):
        self.db = db

    async def check_nm_ids(self, account: str, nm_ids: list):
        nm_ids_str = ", ".join(f"({nm_id})" for nm_id in nm_ids)
        query = f"""
        SELECT nm_id
        FROM (VALUES {nm_ids_str}) AS input(nm_id)
        EXCEPT
        SELECT nm_id
        FROM article;
        """
        not_found_nm_ids = await self.db.fetch(query)
        return [result_nm_id["nm_id"] for result_nm_id in not_found_nm_ids]

    async def update_articles(self, data, filter_nm_ids):
        article_data = [
            (nm_id, data[nm_id]["account"], data[nm_id]["vendor_code"], data[nm_id]["wild"])
            for nm_id in filter_nm_ids
        ]
        query = """
        INSERT INTO article (nm_id, account, vendor_code, local_vendor_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (account, vendor_code) DO NOTHING;
        """
        await self.db.executemany(query, article_data)

    async def get_all_nm_ids(self):
        query = "SELECT * FROM article;"
        nm_ids = await self.db.fetch(query)
        return {str(data["nm_id"]): dict(data) for data in nm_ids}

    async def update_article_data(self, data):
        query = """
        INSERT INTO article (nm_id, account, local_vendor_code, vendor_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (nm_id) DO UPDATE
        SET account = EXCLUDED.account,
            local_vendor_code = EXCLUDED.local_vendor_code,
            vendor_code = EXCLUDED.vendor_code
        WHERE NOT EXISTS (
            SELECT 1 FROM article
            WHERE account = $2 AND vendor_code = $4
        );
        """
        await self.db.executemany(query, data)


class CardData:
    def __init__(self, db):
        self.db = db

    async def get_chrt_ids(self):
        return await self.db.fetch("SELECT article_id, chrt_id FROM card_data;")

    async def get_actual_information_to_db(self, article_ids: Set[int]):
        query = """
        SELECT cd.*, a.local_vendor_code
        FROM card_data cd
        JOIN article a ON cd.article_id = a.nm_id
        WHERE cd.article_id = ANY($1);
        """
        return await self.db.fetch(query, article_ids)

    async def update_card_data(self, data):
        query = """
        INSERT INTO card_data (article_id, barcode,commission_wb, discount, height, length,
                                logistic_from_wb_wh_to_opp, photo_link, price, subject_name, width, last_update_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (article_id) DO UPDATE
        SET barcode = EXCLUDED.barcode,
            commission_wb = EXCLUDED.commission_wb,
            discount = EXCLUDED.discount,
            height = EXCLUDED.height,
            length = EXCLUDED.length,
            logistic_from_wb_wh_to_opp = EXCLUDED.logistic_from_wb_wh_to_opp,
            photo_link = EXCLUDED.photo_link,
            price = EXCLUDED.price,
            subject_name = EXCLUDED.subject_name,
            width = EXCLUDED.width,
            last_update_time = EXCLUDED.last_update_time;
        """
        await self.db.executemany(query, data)


class CostPriceTable:
    def __init__(self, db):
        self.db = db

    async def get_current_data(self):
        query = """
        SELECT DISTINCT ON (local_vendor_code) *
        FROM cost_price
        ORDER BY local_vendor_code, created_at DESC;
        """
        return await self.db.fetch(query=query)


class CostPriceDBContainer:
    def __init__(self, records: list[dict]):
        self.local_vendor_code = {record["local_vendor_code"]: dict(record) for record in records}


class UnitEconomicsTable:
    def __init__(self, db):
        self.db = db

    async def update_data(self, data):
        query = """
        INSERT INTO unit_economics (article_id, commission_wb, discount,
                                logistic_from_wb_wh_to_opp, price, cost_price, percent_by_tax, last_update_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (article_id) DO UPDATE
        SET commission_wb = EXCLUDED.commission_wb,
            discount = EXCLUDED.discount,
            logistic_from_wb_wh_to_opp = EXCLUDED.logistic_from_wb_wh_to_opp,
            price = EXCLUDED.price,
            percent_by_tax = EXCLUDED.percent_by_tax,
            last_update_time = EXCLUDED.last_update_time,
            cost_price = EXCLUDED.cost_price;
        """
        await self.db.executemany(query, data)


def column_index_to_letter(index):
    letter = ""
    while index > 0:
        index -= 1
        letter = chr((index % 26) + 65) + letter
        index //= 26
    return letter


def process_string(s):
    wild_match = re.match(r"^wild(\d+).*$", s)
    if wild_match:
        return f"wild{wild_match.group(1)}"
    if re.match(r"^[a-zA-Z\s]+$", s):
        return s
    return s


def process_local_vendor_code(s):
    return process_string(s)


def merge_dicts(d1, d2):
    result = {}
    for key in d1.keys() | d2.keys():
        result[key] = dict(ChainMap({}, d1.get(key, {}), d2.get(key, {})))
    return result


def calculate_sum_for_logistic(for_one_liter: float, next_liters: float, length, width: int, height: int):
    volume_good = (length * width * height) / 1000
    if volume_good > 1.0:
        return (volume_good - 1) * next_liters + for_one_liter
    return for_one_liter


async def validate_data(nm_ids_db_data, data: dict):
    result_valid_data = {}
    for nm_id, edit_data in data.items():
        if nm_id.isdigit():
            nm_ids_data = {}
            price = edit_data.get("price_discount", {}).get("Установить новую цену", "")
            discount = edit_data.get("price_discount", {}).get("Установить новую скидку %", "")
            height = edit_data.get("dimensions", {}).get("Новая\nВысота (см)", "")
            length = edit_data.get("dimensions", {}).get("Новая\nДлина (см)", "")
            width = edit_data.get("dimensions", {}).get("Новая\nШирина (см)", "")

            if "price_discount" in edit_data:
                if str(discount).isdigit():
                    nm_ids_data.setdefault("price_discount", {})["discount"] = int(discount)
                if str(price).isdigit():
                    nm_ids_data.setdefault("price_discount", {})["price"] = int(price)

            if "dimensions" in edit_data and all(str(v).isdigit() for v in (height, length, width)):
                nm_ids_data.setdefault("dimensions", {})["height"] = int(height)
                nm_ids_data.setdefault("dimensions", {})["length"] = int(length)
                nm_ids_data.setdefault("dimensions", {})["width"] = int(width)

            if nm_ids_data:
                nm_ids_data["vendorCode"] = edit_data["wild"]
                if "price_discount" in nm_ids_data:
                    nm_ids_data["net_profit"] = int(
                        str(edit_data["Чистая прибыль 1ед."]).replace(" ", "").replace("₽", "")
                    )
                if "dimensions" in nm_ids_data:
                    nm_ids_data["sizes"] = nm_ids_db_data[nm_id]["sizes"]
                result_valid_data[int(nm_id)] = nm_ids_data
    return result_valid_data


def safe_batch_update(
        sheet: gspread.Worksheet,
        updates: List[Dict[str, Any]],
        chunk_size: int = 3000,
        max_retries: int = 5,
        start_chunk: int = 1  # Новый параметр - начинать с указанного чанка
) -> None:
    """
    Безопасное массовое обновление данных в Google Sheets с retry-логикой

    Args:
        sheet: Объект gspread Worksheet
        updates: Список словарей с обновлениями формата {'range': 'A1', 'values': [[value]]}
        chunk_size: Размер chunk'а (по умолчанию 3000)
        max_retries: Максимальное количество попыток (по умолчанию 5)
        start_chunk: Номер чанка, с которого начать обновление (по умолчанию 1)
    """
    total_updates = len(updates)
    if total_updates == 0:
        logger.info("Нет обновлений для выполнения")
        return

    total_chunks = (total_updates + chunk_size - 1) // chunk_size

    # Проверяем валидность start_chunk
    if start_chunk < 1:
        start_chunk = 1
    elif start_chunk > total_chunks:
        logger.info(f"start_chunk ({start_chunk}) превышает общее количество чанков ({total_chunks}). Ничего не обновляем.")
        return

    # Вычисляем стартовый индекс для среза updates
    start_index = (start_chunk - 1) * chunk_size

    logger.info(f"Начинаем обновление: {total_updates} ячеек, {total_chunks} chunks, начиная с chunk {start_chunk}")

    for chunk_index in range(start_index, total_updates, chunk_size):
        chunk = updates[chunk_index:chunk_index + chunk_size]
        chunk_number = chunk_index // chunk_size + 1

        for attempt in range(max_retries):
            try:
                sheet.batch_update(chunk)
                logger.info(
                    f"Успешно обновлен chunk {chunk_number}/{total_chunks} "
                    f"({len(chunk)} ячеек, всего {min(chunk_index + chunk_size, total_updates)}/{total_updates})"
                )
                break  # Успех, переходим к следующему chunk

            except gspread.exceptions.APIError as e:
                if '503' in str(e) and attempt < max_retries - 1:
                    wait_time = 10 ** attempt  # Экспоненциальная задержка
                    logger.warning(
                        f"Ошибка 503 в chunk {chunk_number}, "
                        f"попытка {attempt + 1}/{max_retries}, жду {wait_time} сек"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"Не удалось обновить chunk {chunk_number} после {max_retries} попыток: {e}"
                    )
                    raise e
        else:
            logger.error(f"Chunk {chunk_number} не удалось обновить после {max_retries} попыток")
            raise Exception(f"Failed to update chunk {chunk_number} after {max_retries} attempts")

    logger.info(f"Все обновления завершены успешно: {total_updates} ячеек")


class GoogleSheet:
    def __init__(self, spreadsheet: str, sheet: str, creds_json='creds.json'):
        self.creds_json = creds_json
        self.spreadsheet = spreadsheet
        client = self.client_init_json()
        for _ in range(10):
            try:
                print(sheet, "sheet")
                spreadsheet = client.open(self.spreadsheet)
                self.sheet = spreadsheet.worksheet(sheet)
                break
            except (gspread.exceptions.APIError, requests.exceptions.ConnectionError) as e:
                logger.error(e)
                logger.info("time sleep 60 sec")
                time.sleep(60)

    def client_init_json(self) -> Client:
        return service_account(filename=self.creds_json)

    @staticmethod
    def get_column_letter(col_idx: int) -> str:
        result = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def insert_wild_data_correct_preinsert(self, data_dict: dict, sheet_header="wild", corrected_to_int =True) -> None:
        """
        Оптимизированная версия - обновляет данные целыми столбцами.
        """
        try:
            # 1. ТОЧНО КАК В ПЕРВОЙ ФУНКЦИИ: добавляем отсутствующие значения

            # Получаем все ключи из data_dict
            wilds_list = list(data_dict.keys())

            # Получаем существующие данные
            existing_data = self.sheet.get_all_records()

            # Определяем имя ключевой колонки для поиска
            # В первой функции это было 'Артикул', здесь используем sheet_header
            # НО: нужно понять, как называется эта колонка в заголовках таблицы

            # Сначала получим заголовки
            headers = self.sheet.row_values(1)

            # Найдем точное название колонки, содержащей sheet_header
            wild_column_name = None
            for header in headers:
                if sheet_header in header.lower():
                    wild_column_name = header
                    break

            if wild_column_name is None:
                logger.error(f"Колонка с {sheet_header} не найдена в таблице")
                return

            # Собираем существующие значения из найденной колонки
            existing_wilds = set()
            for row in existing_data:
                if wild_column_name in row and row[wild_column_name]:
                    existing_wilds.add(str(row[wild_column_name]))

            # Находим отсутствующие значения
            missing_wilds = [wild for wild in wilds_list if wild not in existing_wilds]

            # Добавляем все отсутствующие значения одним запросом
            if missing_wilds:
                # Подготавливаем строки для вставки
                new_rows = []
                for wild in missing_wilds:
                    # Создаем строку с нужным количеством колонок
                    row = [''] * len(headers)
                    # Находим индекс колонки, куда вставлять
                    col_idx = headers.index(wild_column_name)
                    if corrected_to_int:
                        wild = int(wild)
                    row[col_idx] = wild
                    new_rows.append(row)

                self.sheet.append_rows(new_rows)
                logger.info(f"Добавлено {len(missing_wilds)} новых записей")

                # Пауза как в первой функции
                import time
                time.sleep(2)

            # 2. ТОЧНО КАК ВО ВТОРОЙ ФУНКЦИИ: обновляем данные

            # Обновляем заголовки после добавления новых строк
            headers = self.sheet.row_values(1)

            # Находим индекс колонки wild (ключевой)
            wild_col_idx = None
            for idx, header in enumerate(headers):
                if sheet_header in header.lower():
                    wild_col_idx = idx
                    break

            if wild_col_idx is None:
                logger.error(f"Колонка {sheet_header} не найдена в таблице")
                return

            # Находим индексы целевых колонок
            target_headers = list(next(iter(data_dict.values())).keys()) if data_dict else []
            target_indices = []

            for header in target_headers:
                if header in headers:
                    target_indices.append(headers.index(header))

            if not target_indices:
                logger.error("Целевые заголовки не найдены в таблице")
                return

            # ДАЛЕЕ ИДЁТ ТОЧНО ОРИГИНАЛЬНЫЙ КОД ИЗ ВТОРОЙ ФУНКЦИИ
            # БЕЗ ЛЮБЫХ ИЗМЕНЕНИЙ

            target_indices.sort()
            is_consecutive = all(target_indices[i] + 1 == target_indices[i + 1]
                                 for i in range(len(target_indices) - 1))

            all_data = self.sheet.get_all_values()

            updates = []

            if is_consecutive and len(target_indices) > 1:
                start_col = target_indices[0]
                end_col = target_indices[-1]

                start_col_letter = self.get_column_letter(start_col + 1)
                end_col_letter = self.get_column_letter(end_col + 1)
                update_range = f"{start_col_letter}2:{end_col_letter}{len(all_data)}"

                logger.info(f"Обновляем диапазон: {update_range}")

                update_matrix = [['' for _ in range(len(target_indices))] for _ in range(len(all_data) - 1)]

                for row_idx in range(1, len(all_data)):
                    row = all_data[row_idx]
                    if len(row) > wild_col_idx:
                        current_wild = row[wild_col_idx]
                        if current_wild in data_dict:
                            wild_data = data_dict[current_wild]
                            for i, col_idx in enumerate(target_indices):
                                header = headers[col_idx]
                                if header in wild_data:
                                    update_matrix[row_idx - 1][i] = wild_data[header]
                                else:
                                    update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''
                        else:
                            for i, col_idx in enumerate(target_indices):
                                update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''
                    else:
                        for i, col_idx in enumerate(target_indices):
                            update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''

                updates.append({
                    'range': update_range,
                    'values': update_matrix
                })
            else:
                for col_idx in target_indices:
                    header = headers[col_idx]
                    col_letter = self.get_column_letter(col_idx + 1)
                    col_range = f"{col_letter}2:{col_letter}{len(all_data)}"

                    logger.info(f"Обновляем колонку: {col_range}")

                    column_data = []
                    for row_idx in range(1, len(all_data)):
                        row = all_data[row_idx]
                        if len(row) > wild_col_idx:
                            current_wild = row[wild_col_idx]
                            if current_wild in data_dict and header in data_dict[current_wild]:
                                column_data.append([data_dict[current_wild][header]])
                            else:
                                column_data.append([row[col_idx] if col_idx < len(row) else ''])
                        else:
                            column_data.append([row[col_idx] if col_idx < len(row) else ''])

                    updates.append({
                        'range': col_range,
                        'values': column_data
                    })

            if updates:
                for i, update in enumerate(updates):
                    try:
                        self.sheet.update(update['range'], update['values'], value_input_option='USER_ENTERED')
                        logger.info(f"Успешно обновлен диапазон {update['range']} ({i + 1}/{len(updates)})")
                    except Exception as e:
                        logger.error(f"Ошибка при обновлении {update['range']}: {e}")

        except Exception as e:
            logger.error(f"Ошибка при вставке данных: {e}")
            raise

    def insert_wild_data_correct(self, data_dict: dict, sheet_header="wild") -> None:
        """
        Оптимизированная версия - обновляет данные целыми столбцами.
        """
        try:
            # Получаем заголовки таблицы
            headers = self.sheet.row_values(1)
            # print(headers)
            # Находим индекс колонки wild
            wild_col_idx = None
            for idx, header in enumerate(headers):
                if sheet_header in header.lower():
                    wild_col_idx = idx
                    print(wild_col_idx)

            if wild_col_idx is None:
                logger.error(f"Колонка {sheet_header} не найдена в таблице")
                return

            # Находим индексы и диапазон наших целевых колонок
            # target_headers = list(next(iter(data_dict.values())).keys()) if data_dict else []
            target_headers = list(set().union(*(item.keys() for item in data_dict.values())))
            print(f"Все целевые заголовки: {target_headers}")

            target_indices = []

            for header in target_headers:
                if header in headers:
                    target_indices.append(headers.index(header))

            if not target_indices:
                logger.error("Целевые заголовки не найдены в таблице")
                return

            # Сортируем индексы и проверяем, что они идут подряд
            target_indices.sort()
            is_consecutive = all(target_indices[i] + 1 == target_indices[i + 1]
                                 for i in range(len(target_indices) - 1))

            # Получаем все данные таблицы
            all_data = self.sheet.get_all_values()

            # Создаем матрицу для обновления (строки x колонки)
            updates = []

            if is_consecutive and len(target_indices) > 1:
                # ОПТИМИЗАЦИЯ: обновляем целым диапазоном столбцов
                start_col = target_indices[0]
                end_col = target_indices[-1]

                # ПРАВИЛЬНО формируем диапазон: "AX2:BA5886"
                start_col_letter = self.get_column_letter(start_col + 1)
                end_col_letter = self.get_column_letter(end_col + 1)
                update_range = f"{start_col_letter}2:{end_col_letter}{len(all_data)}"

                logger.info(f"Обновляем диапазон: {update_range}")

                # Создаем матрицу обновлений
                update_matrix = [['' for _ in range(len(target_indices))] for _ in range(len(all_data) - 1)]

                # Заполняем матрицу данными
                for row_idx in range(1, len(all_data)):
                    row = all_data[row_idx]
                    if len(row) > wild_col_idx:
                        current_wild = row[wild_col_idx]
                        if current_wild in data_dict:
                            wild_data = data_dict[current_wild]
                            for i, col_idx in enumerate(target_indices):
                                header = headers[col_idx]
                                if header in wild_data:
                                    update_matrix[row_idx - 1][i] = wild_data[header]
                                else:
                                    # Сохраняем оригинальное значение если нет в словаре
                                    update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''
                        else:
                            # Сохраняем оригинальные значения для строк без совпадения
                            for i, col_idx in enumerate(target_indices):
                                update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''
                    else:
                        # Для строк без wild данных
                        for i, col_idx in enumerate(target_indices):
                            update_matrix[row_idx - 1][i] = row[col_idx] if col_idx < len(row) else ''

                updates.append({
                    'range': update_range,
                    'values': update_matrix
                })
            else:
                # Если колонки не подряд, обновляем каждую колонку отдельно
                for col_idx in target_indices:
                    header = headers[col_idx]
                    col_letter = self.get_column_letter(col_idx + 1)
                    # ПРАВИЛЬНЫЙ формат: "AX2:AX5886"
                    col_range = f"{col_letter}2:{col_letter}{len(all_data)}"

                    logger.info(f"Обновляем колонку: {col_range}")

                    # Подготавливаем данные для столбца
                    column_data = []
                    for row_idx in range(1, len(all_data)):
                        row = all_data[row_idx]
                        if len(row) > wild_col_idx:
                            current_wild = row[wild_col_idx]
                            if current_wild in data_dict and header in data_dict[current_wild]:
                                column_data.append([data_dict[current_wild][header]])
                            else:
                                column_data.append([row[col_idx] if col_idx < len(row) else ''])
                        else:
                            column_data.append([row[col_idx] if col_idx < len(row) else ''])

                    updates.append({
                        'range': col_range,
                        'values': column_data
                    })

            # pprint(updates)

            # Выполняем обновление
            if updates:
                for i, update in enumerate(updates):
                    try:
                        self.sheet.update(update['range'], update['values'], value_input_option='USER_ENTERED')
                        logger.info(f"Успешно обновлен диапазон {update['range']} ({i + 1}/{len(updates)})")
                    except Exception as e:
                        logger.error(f"Ошибка при обновлении {update['range']}: {e}")
                        # Можно добавить повторные попытки или продолжить

        except Exception as e:
            logger.error(f"Ошибка при вставке данных: {e}")
            raise

    def update_rows(self, data_json, edit_column_clean: dict = None):
        logger.info("Попал в функцию обновления таблицы")
        data = self.sheet.get_all_records(expected_headers=[])
        df = pd.DataFrame(data)
        json_df = pd.DataFrame(list(data_json.values()))
        if "Артикул" not in json_df.columns:
            logger.warning("Нет валидных строк с Артикул для обновления таблицы")
            return False
        before_filter_count = len(json_df)
        json_df = json_df[json_df["Артикул"].notna()]
        if len(json_df) != before_filter_count:
            logger.warning(
                "Пропущены строки без Артикул при обновлении таблицы: {}",
                before_filter_count - len(json_df),
            )
        if json_df.empty:
            logger.warning("После фильтрации нет строк для обновления таблицы")
            return False

        clean_rules = {}
        if edit_column_clean is not None:
            for column, rule in edit_column_clean.items():
                clean_rules[column] = (
                    rule if isinstance(rule, bool)
                    else {str(article) for article in rule}
                )

        def should_clean(column, article):
            rule = clean_rules.get(column, False)
            return rule if isinstance(rule, bool) else str(article) in rule

        try:
            json_df = json_df.drop(["vendor_code", "account"], axis=1)
        except KeyError as e:
            logger.error(f"[func:update_rows] {e} 'vendor_code', 'account'")
        # Преобразуем все значения в json_df в типы данных, которые могут быть сериализованы в JSON
        json_df = json_df.astype(object).where(pd.notnull(json_df), None)
        # Обновите данные в основном DataFrame на основе "Артикул"
        for index, row in json_df.iterrows():
            matching_rows = df[df["Артикул"] == row["Артикул"]].index
            for idx in matching_rows:
                for column in row.index:
                    if pd.isna(df.at[idx, column]) or df.at[idx, column] == "":
                        df.at[idx, column] = row[column]

                if edit_column_clean is not None:
                    if should_clean("price_discount", row["Артикул"]):
                        df.at[idx, 'Установить новую скидку %'] = ""
                        df.at[idx, 'Установить новую цену'] = ""

                    if should_clean("dimensions", row["Артикул"]):
                        df.at[idx, 'Новая\nДлина (см)'] = ""
                        df.at[idx, 'Новая\nШирина (см)'] = ""
                        df.at[idx, 'Новая\nВысота (см)'] = ""

                    if should_clean("qty", row["Артикул"]):
                        df.at[idx, 'Новый остаток'] = ""

        # Обновите Google Таблицу только для измененных строк
        updates = []
        headers = df.columns.tolist()
        for index, row in json_df.iterrows():
            matching_rows = df[df["Артикул"] == row["Артикул"]].index
            for idx in matching_rows:
                # +2 потому что индексация в Google Таблицах начинается с 1, а первая строка - заголовки
                row_number = idx + 2
                for column in row.index:
                    if column in headers:
                        # +1 потому что индексация в Google Таблицах начинается с 1
                        column_index = headers.index(column) + 1
                        column_letter = column_index_to_letter(column_index)
                        updates.append({'range': f'{column_letter}{row_number}', 'values': [[row[column]]]})
                if edit_column_clean is not None:
                    if should_clean("price_discount", row["Артикул"]):
                        updates.append({'range': f'L{row_number}',
                                        'values': [['']]})  # Очистка столбца 'Установить новую скидку %'
                        updates.append(
                            {'range': f'J{row_number}', 'values': [['']]})  # Очистка столбца 'Установить новую цену'
                    if should_clean("dimensions", row["Артикул"]):
                        updates.append({'range': f'T{row_number}', 'values': [['']]})
                        updates.append({'range': f'U{row_number}', 'values': [['']]})
                        updates.append({'range': f'V{row_number}', 'values': [['']]})

                    if should_clean("qty", row["Артикул"]):
                        updates.append({'range': f'AF{row_number}', 'values': [['']]})

        # pprint(updates)
        # self.sheet.batch_update(updates)
        safe_batch_update(
            sheet=self.sheet,
            updates=updates,
            chunk_size=1000,  # Можно настроить под свои needs
            max_retries=5  # Можно настроить количество попыток
            # start_chunk=10
        )
        logger.info("Данные успешно обновлены.")
        return True

    @staticmethod
    def get_article_dict(service_google_sheet, row, row_article):
        article_dict = {'wild': row_article["vendor_code"],
                        'Чистая прибыль 1ед.': row['Чистая прибыль 1ед.'].replace('\xa0', '')}
        if service_google_sheet["Цены/Скидки"] and str(row['Чистая прибыль 1ед.'].replace('\xa0', '')).lstrip(
                '-').isdigit():
            article_dict["price_discount"] = \
                {'Установить новую цену': row['Установить новую цену'].replace('\xa0', ''),
                 'Установить новую скидку %': row['Установить новую скидку %'].replace('\xa0', '')}
        if service_google_sheet["Габариты"]:
            article_dict["dimensions"] = {'Новая\nДлина (см)': row['Новая\nДлина (см)'].replace('\xa0', ''),
                                          'Новая\nШирина (см)': row['Новая\nШирина (см)'].replace('\xa0', ''),
                                          'Новая\nВысота (см)': row['Новая\nВысота (см)'].replace('\xa0', '')}
        return article_dict

    @staticmethod
    def update_result_qty_edit_data(service_google_sheet, result_qty_edit_data, account, row, chrt_ids_by_nm_id:dict):

        if service_google_sheet["Остаток"]:
            if account not in result_qty_edit_data:
                result_qty_edit_data[account] = {"stocks": [], "nm_ids": []}
            if str(row["Новый остаток"]).isdigit():
                # старая реализация - по баркоду
                # result_qty_edit_data[account]["stocks"].append(
                #     {"sku": row["Баркод"], "amount": int(row["Новый остаток"].replace('\xa0', ''))},
                # )
                # новая реализация - по chrt_id
                article_id = int(row["Артикул"])
                chrt_id = chrt_ids_by_nm_id.get(article_id)
                if chrt_id is None:
                    logger.warning(
                        "Не найден chrtId для изменения остатка. account: {} nm_id: {}",
                        account,
                        article_id,
                    )
                    return
                result_qty_edit_data[account]["stocks"].append(
                    {"chrtId": chrt_id, "amount": int(row["Новый остаток"].replace('\xa0', ''))},
                )

                # nm_id нам будет нужен для функции обновления данных почему в список?
                result_qty_edit_data[account]["nm_ids"].append(article_id)

    async def get_edit_data(self, db_nm_ids_data, service_google_sheet, chrt_ids_by_nm_id):
        """
        Получает данные с запросом на изменение с таблицы
        """
        data = self.sheet.get_all_values()

        # Преобразуйте данные в DataFrame
        df = pd.DataFrame(data[1:], columns=data[0])
        result_nm_ids_data = {}
        result_qty_edit_data = {}
        for index, row in df.iterrows():
            article = row['Артикул']
            account = str(row['ЛК']).capitalize()
            # if any([not article.isdigit(), not account.strip(), article not in db_nm_ids_data.keys(),
            #         "vendor_code" not in db_nm_ids_data[article]]):
            #     continue
            if not article.isdigit() or not account.strip() or article not in db_nm_ids_data or "vendor_code" not in db_nm_ids_data[article]:
                continue
            article_dict = self.get_article_dict(service_google_sheet, row, db_nm_ids_data[article])
            self.update_result_qty_edit_data(service_google_sheet, result_qty_edit_data, account, row, chrt_ids_by_nm_id)
            if account not in result_nm_ids_data:
                result_nm_ids_data[account] = {}
            result_nm_ids_data[account][article] = article_dict

        return {"nm_ids_edit_data": result_nm_ids_data, "qty_edit_data": result_qty_edit_data}

    def create_lk_articles_list(self):
        """Создает словарь из ключей кабинета и его Артикулов"""
        data = self.sheet.get_all_records()
        df = pd.DataFrame(data)
        lk_articles_dict = {}
        for index, row in df.iterrows():

            article = row['Артикул']
            lk = row['ЛК'].upper()
            # Пропускаем строки с пустыми значениями в столбце "ЛК" "Артикул"
            if pd.isna(lk) or lk == "":
                continue
            if pd.isna(article) or article == "":
                continue

            # если ячейки, выделенные для изменения, будут иметь число, то они не будут отобраны для обновления данных
            # if True in (
            #         # str(row['Новая\nДлина (см)']).replace('\xa0', '').isdigit(),
            #         # str(row['Новая\nШирина (см)']).replace('\xa0', '').isdigit(),
            #         # str(row['Новая\nВысота (см)']).replace('\xa0', '').isdigit(),
            #         str(row['Установить новую цену']).replace('\xa0', '').isdigit(),
            #         str(row['Установить новую скидку %']).replace('\xa0', '').isdigit(),
            #         str(row["Новый остаток"]).replace('\xa0', '').isdigit()):
            #     continue
            if lk.upper() not in lk_articles_dict:
                lk_articles_dict[lk.upper()] = []
            lk_articles_dict[lk.upper()].append(article)
        return lk_articles_dict

    def check_status_service_sheet(self):
        all_data = self.sheet.get_all_values()

        def try_parse_int(value):
            try:
                return int(value)
            except ValueError:
                return value

        first_header_values = all_data[0]
        first_data_values = [try_parse_int(value) for value in all_data[1]]
        second_header_values = all_data[3]
        second_data_values = [try_parse_int(value) for value in all_data[4]]
        result = dict(zip(first_header_values, first_data_values))
        result.update(dict(zip(second_header_values, second_data_values)))
        return result

    @staticmethod
    def check_new_nm_ids(account, nm_ids: list):
        return nm_ids


class ListOfCardsContent:
    """API Список товаров """

    def __init__(self, token):
        self.url = "https://content-api.wildberries.ru/content/v2/get/cards/{}"
        self.update_url = "https://content-api.wildberries.ru/content/v2/cards/{}"
        self.token = token
        self.headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'

        }

    def get_list_of_cards(self, nm_ids_list: list, limit: int = 1, eng_json_data: bool = False,
                          only_edits_data=False, update_articles_data=False, add_data_in_db=True, account=None) -> json:
        """Получение всех карточек  по совпадению с nm_ids_list"""
        nm_ids_list_for_edit = [*nm_ids_list]
        url = self.url.format("list")
        card_result_for_match = {}
        nm_ids_data_for_database = {}
        data_for_warehouse = {}
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
        count = 0
        while True:
            count += 1
            try:
                for i in range(5):
                    response = requests.post(url, headers=self.headers, json=json_obj)
                    if response.status_code >= 400:
                        logger.info(f"[ERROR] {account}  {response.status_code} попытка {i}")
                        logger.info("ожидание 1 минута")
                        if i == 4:
                            return card_result_for_match
                        time.sleep(60)
                    else:
                        break
            except Exception as e:
                time.sleep(60)
                logger.info(e)
                continue
            request_wb = response.json()
            for card in request_wb["cards"]:
                if eng_json_data is False:

                    if card["nmID"] in nm_ids_list_for_edit:
                        # добавляем в словарь данные по карточке по ключу артикула на русском
                        card_result_for_match[card["nmID"]] = {
                            "Артикул": card["nmID"],
                            "Текущая\nДлина (см)": card["dimensions"]["length"],
                            "Текущая\nШирина (см)": card["dimensions"]["width"],
                            "Текущая\nВысота (см)": card["dimensions"]["height"],
                            "Предмет": card["subjectName"],
                            "Баркод": card["sizes"][0]["skus"][-1],
                            "wild": process_string(card["vendorCode"]),
                            "vendor_code": card["vendorCode"],
                            "account": account
                        }
                        # if update_articles_data is False:
                        #     photo = "НЕТ"
                        #     if "photos" in card:
                        #         photo = card["photos"][0]["tm"]
                        #
                        #     card_result_for_match[card["nmID"]].update({
                        #         "Фото": photo,
                        #         # для таблицы будет использоваться последний баркод из списка
                        #     })
                        # добавляем данные по размерам в БД
                        nm_ids_data_for_database[str(card["nmID"])] = {
                            "sizes": card["sizes"],
                            "vendorCode": card["vendorCode"],
                        }

                        # добавляем данные по skus с ключом кабинета и артикула
                        if account not in data_for_warehouse.keys():
                            data_for_warehouse[account] = {}
                        data_for_warehouse[account].update({str(card["nmID"]): {"skus": card["sizes"][0]["skus"]}})

                        nm_ids_list_for_edit.remove(card["nmID"])

                if not nm_ids_list_for_edit:
                    break

            if request_wb["cursor"]["total"] < limit or not nm_ids_list_for_edit:
                logger.info(f"total: {request_wb['cursor']['total']}")
                break

            else:
                update_data = {
                    "updatedAt": request_wb["cursor"]["updatedAt"],
                    "nmID": request_wb["cursor"]["nmID"]
                }

                json_obj["settings"]["cursor"].update(update_data)

        # добавляем данные по размерам и баркодам в БД
        if add_data_in_db is True:
            add_data_for_nm_ids(nm_ids_data_for_database)
            add_data_from_warehouse(data_for_warehouse)
        logger.info("get_list_of_cards")
        # pprint(card_result_for_match)
        if nm_ids_list_for_edit:
            logger.info("if len(nm_ids_list_for_edit) > 0:")
            logger.info(f"нет карточек по этим артикулам в кабинете {account}: {nm_ids_list_for_edit}")
            if only_edits_data is False:
                logger.info("if only_edits_data is False:")
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
        # print(card_result_for_match)
        return card_result_for_match

    def size_edit(self, data: list):

        url = self.update_url.format("update")

        response = requests.post(url=url, headers=self.headers, json=data)
        logger.info(f"size edit result: {response.json()}")
        time.sleep(2)
        if False is response.json()["error"]:
            return True

        # {'data': {'id': 38529453, 'alreadyExists': True}, 'error': False, 'errorText': 'Task already exists'}
        # {'data': {}, 'error': False, 'errorText': '', 'additionalErrors': {}}

    async def get_list_of_cards_async(self, nm_ids_list: list, limit: int = 100,
                                      account=None) -> json:
        """Получение всех карточек  по совпадению с nm_ids_list"""
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
        # данные для пагинации списков с карточками
        total = 1
        updated_at = None
        cursor_nm_id = None
        while True:
            for i in range(10):
                try:
                    async with ClientSession() as session:
                        async with session.post(url, headers=self.headers, json=json_obj) as response:
                            response_result = await response.json()
                            # переопределяем количество предоставленное в запросе
                            total = response_result["cursor"]["total"]
                            if total == 0:
                                break
                            updated_at = response_result["cursor"]["updatedAt"]
                            cursor_nm_id = response_result["cursor"]["nmID"]
                            for card in response_result["cards"]:
                                if card["nmID"] in nm_ids_list_for_edit:
                                    # добавляем в словарь данные по карточке по ключу артикула на русском
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
                                        "skus": card["sizes"][0]["skus"]
                                    }
                                    photo = "НЕТ"
                                    if "photos" in card:
                                        photo = card["photos"][0]["tm"]
                                    card_result_for_match[card["nmID"]].update({
                                        "photo_link": photo,
                                    })
                                    # удаление артикула найденного из полученного списка
                                    nm_ids_list_for_edit.remove(card["nmID"])
                                # остановить поиск если все артикулы в списке для поиска закончены
                                if len(nm_ids_list_for_edit) == 0:
                                    break
                    # что бы прервать range
                    break
                except aiohttp.ClientError as e:
                    logger.warning(f"Exception {e} sleep 63 sec")
                    await asyncio.sleep(63)
                    continue
                except Exception as e:
                    logger.warning(f"Exception {e} {account} sleep 36 sec")
                    logger.warning(f"response {response_result}")
                    await asyncio.sleep(36)

            if total < limit or len(nm_ids_list_for_edit) == 0:
                logger.info(f"account: {account} total: {total}")
                break
            else:
                update_data = {
                    "updatedAt": updated_at,
                    "nmID": cursor_nm_id
                }
                json_obj["settings"]["cursor"].update(update_data)
        logger.info(f"account: {account} len(card_result_for_match): {len(card_result_for_match)}")
        logger.info("Невалидные артикулы в запросе:")
        logger.info(f"nm_ids_list_for_edit : {nm_ids_list_for_edit}")
        return card_result_for_match


class ListOfGoodsPricesAndDiscounts:
    """API Список товаров """

    def __init__(self, token, limit: int = 1, offset: int = 0):
        self.limit = limit
        self.offset = offset
        self.token = token
        self.url = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/{}"
        self.post_url = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"

        self.headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'
        }

    def get_log_for_nm_ids(self, filter_nm_ids, eng_json_data: bool = False) -> json:
        """Получение цен и скидок по совпадению с nmID"""
        url = self.url.format("filter")
        nm_ids = [*filter_nm_ids]
        nm_ids_list = {}
        logger.info("попали в функцию get_log_for_nm_ids")
        logger.info(f"filter_nm_ids len: {len(filter_nm_ids)}")
        offset = 0
        limit = 1000
        while True:
            params = {
                "limit": limit,
                "offset": offset,
            }
            response = requests.get(url, headers=self.headers, params=params)
            if "data" not in response.json() or response.status_code > 400:
                for i in range(1, 10):
                    try:
                        response = requests.get(url, headers=self.headers, params=params)
                        if "data" in response.json():
                            break
                    except Exception as e:
                        time.sleep(30)
                        logger.exception(e)
                        logger.error(f"Ошибка на просмотре цены и скидки по артикулам. Попытка {i}")

            try:
                for card in response.json()["data"]["listGoods"]:
                    if card["nmID"] in nm_ids:
                        if eng_json_data is False:
                            nm_ids_list[card["nmID"]] = {
                                "Цена на WB без скидки": card["sizes"][0]["price"],
                                "Скидка %": card["discount"]
                            }
                        nm_ids.remove(card["nmID"])
            except Exception as e:
                logger.exception(e)
                break

            if len(nm_ids) == 0:
                break
            else:
                offset += limit
        logger.info("НЕВАЛИДНЫЕ АРТИКУЛЫ get_log_for_nm_ids")
        logger.info(nm_ids)
        return nm_ids_list

    async def get_log_for_nm_ids_async(self, filter_nm_ids, account=None) -> dict:
        """Получение цен и скидок по совпадению с nmID"""
        url = self.url.format("filter")
        nm_ids = [*filter_nm_ids]
        nm_ids_list = {}
        logger.info("В функции get_log_for_nm_ids")
        offset = 0
        limit = 1000
        while True:
            params = {
                "limit": limit,
                "offset": offset,
            }

            for i in range(1, 10):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=self.headers, params=params, timeout=60) as response:
                            response_result = await response.json()
                            if "data" in response_result:
                                if response_result['data'] is not None:
                                    for card in response_result["data"]["listGoods"]:
                                        if card["nmID"] in nm_ids:
                                            nm_ids_list[card["nmID"]] = {
                                                "Цена на WB без скидки": card["sizes"][0]["price"],
                                                "Скидка %": card["discount"]
                                            }
                                            nm_ids.remove(card["nmID"])
                                    break
                                else:
                                    break
                            elif len(response_result) == 0:
                                break
                            elif response.status == 429:
                                logger.info(nm_ids)
                                logger.info(f"попытка: {i} sleep 10 sec")
                                await asyncio.sleep(10)
                                continue
                            else:
                                break
                except (aiohttp.ClientError, aiohttp.ClientResponseError, aiohttp.ConnectionTimeoutError,
                        asyncio.TimeoutError) as e:
                    logger.error(f"[ERROR] func -get_log_for_nm_ids_async {e} sleep 36 sec")
                    await asyncio.sleep(36)

            logger.info("Дошел до условия прерывания бесконечного цикла")
            logger.info(f"offset {offset}")
            if len(nm_ids) == 0 or i == 9 or "data" not in response_result or response_result['data'] is None or \
                    response_result["data"]["listGoods"] is None or len(response_result["data"]["listGoods"]) == 0:
                logger.info("прерывание бесконечного цикла")
                # для того что бы прервать бесконечный цикл
                break
            else:  # пагинация
                offset += limit
        if len(nm_ids) != 0:
            logger.info(f"в запросе просмотра цен есть невалидные артикулы -> {account}: {nm_ids}")
        return nm_ids_list

    def add_new_price_and_discount(self, data: list, step=1000):
        url = self.post_url
        for start in range(0, len(data), step):
            butch_data = data[start: start + step]
            for _ in range(10):
                try:
                    response = requests.post(url=url, headers=self.headers, json={"data": butch_data})
                    logger.info(f"Артикулы на изменение цены: {butch_data}")
                    logger.info(f"price and discount edit result: {response.json()}")
                    time.sleep(2)
                    if (response.status_code in (200, 208) or response.json()['errorText'] in
                            ("Task already exists", "No goods for process", "The specified prices and discounts are already set", "Specified prices and discounts are already set")):
                        break

                except (Exception, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
                    logger.exception(e)
                    time.sleep(63)


class CommissionTariffs:
    def __init__(self, token):
        self.url = "https://common-api.wildberries.ru/api/v1/tariffs/{}"
        self.headers = {
            "Authorization": token,
            'Content-Type': 'application/json'
        }

    def get_commission_on_subject(self, subject_names) -> dict:
        url = self.url.format("commission")

        result_commission_data = {}
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            logger.info(response.json())
        if response.status_code == 429:
            logger.info("превысил лимит запросов, ограничение запроса в 1 минуту. Сервис упал в сон на 1 минуту")
            time.sleep(60)
            response = requests.get(url, headers=self.headers)

        for subject_name in subject_names:
            for i in response.json()["report"]:
                if i["subjectName"] == subject_name:
                    result_commission_data[subject_name] = i['kgvpMarketplace']
                    break
        return result_commission_data

    def get_tariffs_box_from_marketplace(self) -> dict or None:
        url = self.url.format("box")
        date = datetime.date.today()
        params = {
            "date": date
        }

        response = requests.get(url=url, headers=self.headers, params=params)
        for warehouse_data in response.json()["response"]["data"]["warehouseList"]:
            if warehouse_data["warehouseName"] == "Маркетплейс":
                current_tariffs_data = {'boxDeliveryBase': warehouse_data['boxDeliveryBase'],
                                        'boxDeliveryLiter': warehouse_data['boxDeliveryLiter']}

                return current_tariffs_data  # 'boxDeliveryBase': '...', 'boxDeliveryLiter': '...'

        else:
            return None

    async def get_tariffs_box_from_marketplace_async(self) -> dict or None:
        url = self.url.format("box")

        date = str(datetime.date.today())
        params = {
            "date": date
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url=url, headers=self.headers, params=params) as response:
                    response_result = await response.json()

                    for warehouse_data in response_result["response"]["data"]["warehouseList"]:
                        if warehouse_data["warehouseName"] == "Маркетплейс":
                            current_tariffs_data = {'boxDeliveryBase': warehouse_data['boxDeliveryBase'],
                                                    'boxDeliveryLiter': warehouse_data['boxDeliveryLiter']}

                            return current_tariffs_data  # 'boxDeliveryBase': '...', 'boxDeliveryLiter': '...'

                        else:
                            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            logger.error(f"Error : {e}")
            return None

    async def get_commission_on_subject_async(self, subject_names) -> dict | None:
        url = self.url.format("commission")
        result_commission_data = {}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    response_result = await response.json()
                    if response.status != 200:
                        logger.info(f"response_result : {response_result}")
                    if response.status == 429:
                        logger.error("429 Просмотр комиссии по Предметам. Повторная попытка через минуту")
                        await asyncio.sleep(60)
                        async with aiohttp.ClientSession() as session2:
                            async with session2.get(url, headers=self.headers) as response2:
                                response_result = await response2.json()

                    for subject_name in subject_names:
                        for i in response_result["report"]:
                            if i["subjectName"] == subject_name:
                                result_commission_data[subject_name] = i['kgvpMarketplace']
                                break
                    return result_commission_data

        except (aiohttp.ClientError, aiohttp.ClientResponseError, Exception) as e:
            logger.info(f"response_result : {response_result}")
            logger.error(f"Error : {e}")
            return None


class LeftoversMarketplace:
    REQUEST_TIMEOUT = (10, 60)
    REQUEST_RETRIES = 3

    def __init__(self, token):
        self.token = token
        self.url = "https://marketplace-api.wildberries.ru/api/v3/stocks/{}"
        self.headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'
        }

    def get_amount_from_warehouses(self, warehouse_id, barcodes, step=1000):
        url = self.url.format(f"{warehouse_id}")
        barcodes_quantity = []
        for start in range(0, len(barcodes), step):
            barcodes_part = barcodes[start: start + step]

            json_data = {
                "skus": barcodes_part
            }
            response = requests.post(url=url, headers=self.headers, json=json_data)

            stocks = response.json()["stocks"]
            if len(stocks) > 0:
                for stock in stocks:
                    barcodes_quantity.append(
                        {
                            "Баркод": stock["sku"],
                            "остаток": stock["amount"]
                        }
                    )
        return barcodes_quantity

    @staticmethod
    def _get_not_found_chrt_ids(response_data):
        """Извлекает chrtId, которые WB явно отклонил как NotFound."""
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

    def edit_amount_from_warehouses(self, warehouse_id, edit_barcodes_list, step=1000):
        """Возвращает позиции, успешно отправленные и отклонённые на этом складе."""
        url = self.url.format(f"{warehouse_id}")
        result = {"successful": [], "failed": []}
        batches_to_process = [
            edit_barcodes_list[start: start + step]
            for start in range(0, len(edit_barcodes_list), step)
        ]

        while batches_to_process:
            barcodes_part = batches_to_process.pop(0)
            logger.info(barcodes_part)
            json_data = {"stocks": barcodes_part}
            response = None

            for attempt in range(1, self.REQUEST_RETRIES + 1):
                try:
                    response = requests.put(
                        url=url,
                        headers=self.headers,
                        json=json_data,
                        timeout=self.REQUEST_TIMEOUT,
                    )
                    break
                except requests.exceptions.RequestException as e:
                    logger.error(
                        "Ошибка соединения при изменении остатков. Склад: {}. Попытка {}/{}. Ошибка: {}",
                        warehouse_id,
                        attempt,
                        self.REQUEST_RETRIES,
                        e,
                    )
                    if attempt < self.REQUEST_RETRIES:
                        time.sleep(10 * attempt)

            if response is None:
                logger.error(
                    "Ошибка запроса на изменение остатков. Пачка пропущена после {} попыток. Склад: {}",
                    self.REQUEST_RETRIES,
                    warehouse_id,
                )
                result["failed"].extend(barcodes_part)
                continue

            if response.status_code <= 399:
                logger.info(f"Запрос на изменение остатков. Код: {response.status_code}")
                result["successful"].extend(barcodes_part)
                continue

            try:
                response_data = response.json()
            except requests.exceptions.JSONDecodeError:
                response_data = response.text or "<пустой ответ>"

            not_found_chrt_ids = set()
            if response.status_code == 409:
                not_found_chrt_ids = self._get_not_found_chrt_ids(response_data)

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
                logger.error(
                    "WB отклонил chrtId как NotFound. Склад: {}. Исключены: {}",
                    warehouse_id,
                    sorted(not_found_chrt_ids),
                )
                if stocks_to_retry:
                    batches_to_process.insert(0, stocks_to_retry)
                continue

            logger.error(
                "Ошибка запроса на изменение остатков. Пачка пропущена. Код: {}. Content-Type: {}. Ответ: {}",
                response.status_code,
                response.headers.get("Content-Type", "<не указан>"),
                response_data,
            )
            result["failed"].extend(barcodes_part)

        return result


class WarehouseMarketplaceWB:
    """API складов маркетплейс"""

    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'
        }
        self.url = "https://marketplace-api.wildberries.ru/api/v3/warehouses"

    def get_account_warehouse(self, ):
        response = requests.get(url=self.url, headers=self.headers)
        if response.status_code > 400:
            try:
                for _ in range(10):
                    response = requests.get(url=self.url, headers=self.headers)
                    if response.status_code < 400:
                        time.sleep(60)
                        break

            except Exception as e:
                logger.exception(e)

        return response.json()


class ServiceGoogleSheet:
    def __init__(self, token, spreadsheet: str, sheet: str, creds_json="creds.json"):
        self.wb_api_token = token
        self.gs_connect = GoogleSheet(creds_json=creds_json, spreadsheet=spreadsheet, sheet=sheet)
        self.sheet = sheet
        self.spreadsheet = spreadsheet
        self.creds_json = creds_json

    @staticmethod
    def check_status():
        for i in range(10):
            try:
                sheet_status = GoogleSheet(creds_json="creds.json",
                                           spreadsheet="UNIT 2.0 (tested)", sheet="ВКЛ/ВЫКЛ Бот")
                return sheet_status.check_status_service_sheet()
            except gspread.exceptions.APIError as e:
                logger.error(f"попытка {i} {e} следующая попытка через 75 секунд")
                time.sleep(75)

        return False

    @staticmethod
    def __validate_value(value: str, type_value: type) -> Any:
        try:
            return type_value(value)
        except (ValueError, TypeError):
            return

    def __convert_to_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "Артикул": self.__validate_value(data["article_id"], int),
            "Предмет": self.__validate_value(data["subject_name"], str),
            "Скидка %": self.__validate_value(data["discount"], int),
            "Текущая\nДлина (см)": self.__validate_value(data["length"], int),
            "Текущая\nШирина (см)": self.__validate_value(data["width"], int),
            "Текущая\nВысота (см)": self.__validate_value(data["height"], int),
            "Баркод": self.__validate_value(data["barcode"], str),
            "Логистика от склада WB до ПВЗ": self.__validate_value(data["logistic_from_wb_wh_to_opp"], float),
            "Комиссия WB": self.__validate_value(data["commission_wb"], float),
            "Цена на WB без скидки": self.__validate_value(data["price"], float),
            "Рейтинг": self.__validate_value(data["rating"], float),
            "wild": self.__validate_value(data["local_vendor_code"], str),
            "Фото": self.__validate_value(data["photo_link"], str),
            "ВЕС": self.__validate_value(data["weight_brutto"], float),
        }

    async def get_actually_data_from_db(self, db: Database1, article_ids: Set[int]) -> Dict[int, Dict[str, Any]]:
        card_data_result = await CardData(db).get_actual_information_to_db(article_ids)
        return {data["article_id"]: self.__convert_to_dict(data) for data in card_data_result}

    async def get_actually_data_to_table_refactor(self, db: Database1) -> tuple[Any]:
        """
        Асинхронно собирает актуальные данные из базы данных для всех артикулов из гугл-таблицы.
        Создает и выполняет асинхронные задачи для каждого аккаунта.
        Возвращает:
            list: Список словарей с актуальными данными по артикулам
        """
        logger.info("Получение артикулов из гугл-таблицы")
        lk_articles = self.gs_connect.create_lk_articles_list()
        tasks = []
        logger.info("Получение актуальных данных из базы данных")
        for account, articles in lk_articles.items():
            task = self.get_actually_data_from_db(db, set(articles))
            tasks.append(task)
        return await asyncio.gather(*tasks)

    @staticmethod
    def _get_photos_and_filter_empty_value(article_id_to_update: List[Dict[str, Any]]) -> Tuple[
        Dict[str, Any], Dict[str, Any]]:
        """
        Извлекает ссылки на фотографии товаров и удаляет пустые значения из данных.
        Аргументы:
            article_id_to_update (list): Список словарей с данными о товарах
        Возвращает:
            tuple: (отфильтрованные данные, данные о фотографиях)
                - отфильтрованные данные - список словарей с данными товаров без пустых значений
                - данные о фотографиях - список словарей, содержащих только артикулы и ссылки на фото
        """
        logger.info("Извлечение ссылок на фотографии товаров")
        photos = [{k: {"Фото": v.pop('Фото')} for k, v in data.items()} for data in article_id_to_update]
        logger.info("Удаление пустых значений")
        for items in article_id_to_update:
            for k, v in items.items():
                for key, value in list(v.items()):
                    if value is None:
                        items[k].pop(key)
        article_id_to_update = {k: v for d in article_id_to_update for k, v in d.items()}
        photos = {k: v for d in photos for k, v in d.items()}
        return article_id_to_update, photos

    async def add_actually_data_to_table(self, db: Database1):
        """
        Обновляет данные в гугл-таблице актуальной информацией из базы данных.
        """
        if ServiceGoogleSheet.check_status()['ВКЛ - 1 /ВЫКЛ - 0']:
            logger.info(f"[INFO] {datetime.datetime.now()} актуализируем данные в таблице")
            article_id_to_update = await self.get_actually_data_to_table_refactor(db=db)
            article_id_to_update, photos = self._get_photos_and_filter_empty_value(article_id_to_update)
            logger.info(f"[INFO] {datetime.datetime.now()} обновляем данные в таблице")
            # self.gs_connect.update_rows(data_json=article_id_to_update)
            data_str_keys = {str(k): v for k, v in article_id_to_update.items()}
            self.gs_connect.insert_wild_data_correct(data_dict=data_str_keys, sheet_header='артикул')

            if len(photos) > 0:
                logger.info(f"[INFO] {datetime.datetime.now()} обновляем данные в таблице ФОТО")
                gs_connect_photo = GoogleSheet(creds_json=self.creds_json, spreadsheet=self.spreadsheet, sheet="ФОТО")
                photos_str_keys = {str(k): v for k, v in photos.items()}
                pprint(photos_str_keys)
                gs_connect_photo.insert_wild_data_correct_preinsert(data_dict=photos_str_keys, sheet_header="артикул")

    async def add_new_data_from_table(self, lk_articles, edit_column_clean=None, only_edits_data=False,
                                      add_data_in_db=True, check_nm_ids_in_db=True):
        """Функция была изменена. Теперь она просто выдает данные на добавления в таблицу, а не добавляет таблицу внутри функции"""
        wb_api_factory = globals().get("WB_API_FACTORY")
        tokens = get_wb_tokens()

        async def process_account(account, nm_ids):
            account_photos = {}
            account_result = {}
            account_filter_nm_ids = []
            nm_ids_for_add = []
            token = tokens[account.capitalize()]
            nm_ids_result = nm_ids

            if check_nm_ids_in_db:
                "поиск всех артикулов которых нет в БД"
                nm_ids_result = self.gs_connect.check_new_nm_ids(account=account, nm_ids=nm_ids)

                if len(nm_ids_result) > 0:
                    logger.info(f"КАБИНЕТ: {account}")
                    logger.info(f"новые артикулы в таблице {nm_ids_result}")

            account_filter_nm_ids.extend(nm_ids_result)

            if len(nm_ids_result) == 0:
                return account_photos, account_result, account_filter_nm_ids, nm_ids_for_add

            """Обновление/добавление данных по артикулам в гугл таблицу с WB api"""
            if wb_api_factory is not None:
                wb_api_content = wb_api_factory.ListOfCardsContent(token=token)
                wb_api_price_and_discount = wb_api_factory.ListOfGoodsPricesAndDiscounts(token=token)
                warehouses = wb_api_factory.WarehouseMarketplaceWB(token=token)
                barcodes_quantity = wb_api_factory.LeftoversMarketplace(token=token)
                card_from_nm_ids_filter = await wb_api_content.get_list_of_cards_for_table_async(
                    nm_ids_list=nm_ids_result,
                    limit=100,
                    only_edits_data=only_edits_data,
                    account=account,
                )
            else:
                wb_api_content = ListOfCardsContent(token=token)
                wb_api_price_and_discount = ListOfGoodsPricesAndDiscounts(token=token)
                warehouses = WarehouseMarketplaceWB(token=token)
                barcodes_quantity = LeftoversMarketplace(token=token)
                card_from_nm_ids_filter = wb_api_content.get_list_of_cards(nm_ids_list=nm_ids_result, limit=100,
                                                                           only_edits_data=only_edits_data,
                                                                           account=account)
            goods_nm_ids = await wb_api_price_and_discount.get_log_for_nm_ids_async(filter_nm_ids=nm_ids_result,
                                                                                    account=account)
            commission_traffics = (
                wb_api_factory.CommissionTariffs(token=token)
                if wb_api_factory is not None
                else CommissionTariffs(token=token)
            )

            merge_json_data = merge_dicts(card_from_nm_ids_filter, goods_nm_ids)
            if only_edits_data:
                skipped_nm_ids = [
                    nm_id for nm_id, data in merge_json_data.items()
                    if not isinstance(data, dict) or "Артикул" not in data or not data.get("account")
                ]
                if skipped_nm_ids:
                    logger.warning(
                        "Пропускаем неполные данные WB при обновлении после редактирования. account: {} nm_ids: {}",
                        account,
                        skipped_nm_ids,
                    )
                merge_json_data = {
                    nm_id: data for nm_id, data in merge_json_data.items()
                    if isinstance(data, dict) and "Артикул" in data and data.get("account")
                }
            subject_names = set()
            account_barcodes = []
            if wb_api_factory is not None:
                current_tariffs_data = await commission_traffics.get_tariffs_box_from_marketplace_async()
            else:
                current_tariffs_data = commission_traffics.get_tariffs_box_from_marketplace()

            for i in merge_json_data.values():
                if "wild" in i and i["wild"] != "не найдено":
                    subject_names.add(i["Предмет"])
                    account_barcodes.append(i["Баркод"])
                    # result_log_value = calculate_sum_for_logistic(
                    #     for_one_liter=float(current_tariffs_data["boxDeliveryBase"].replace(',', '.')),
                    #     next_liters=float(current_tariffs_data["boxDeliveryLiter"].replace(',', '.')),
                    #     height=int(i['Текущая\nВысота (см)']),
                    #     length=int(i['Текущая\nДлина (см)']),
                    #     width=int(i['Текущая\nШирина (см)']), )
                    # i["Логистика от склада WB до ПВЗ"] = result_log_value

                if only_edits_data is False:
                    try:
                        account_photos[int(i["Артикул"])] = i.pop("Фото")
                    except KeyError:
                        logger.info(f"не получено фото из массива")

            barcodes_quantity_result = []
            if wb_api_factory is not None:
                account_warehouses = await warehouses.get_account_warehouse_async()
            else:
                account_warehouses = warehouses.get_account_warehouse()
            for warehouse_id in account_warehouses:
                if wb_api_factory is not None:
                    bqs_result = await barcodes_quantity.get_amount_from_warehouses_async(
                        warehouse_id=warehouse_id['id'],
                        barcodes=account_barcodes)
                else:
                    bqs_result = barcodes_quantity.get_amount_from_warehouses(
                        warehouse_id=warehouse_id['id'],
                        barcodes=account_barcodes)
                barcodes_quantity_result.extend(bqs_result)

            subject_commissions = None
            try:
                if wb_api_factory is not None:
                    subject_commissions = await commission_traffics.get_commission_on_subject_async(subject_names=subject_names)
                else:
                    subject_commissions = commission_traffics.get_commission_on_subject(subject_names=subject_names)
            except (Exception, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
                logger.info(f"[ERROR]  Запрос получения комиссии по предметам завершился ошибкой: {e}")
            for card in merge_json_data.values():
                if subject_commissions is not None:
                    for sc in subject_commissions.items():
                        if "Предмет" in card and sc[0] == card["Предмет"]:
                            card["Комиссия WB"] = sc[1]
                for bq_result in barcodes_quantity_result:
                    if "Баркод" in card and bq_result["Баркод"] == card["Баркод"]:
                        card["ФБС"] = bq_result["остаток"]

            account_result.update(merge_json_data)
            if add_data_in_db is True:
                nm_ids_for_add = list(nm_ids_result)
            return account_photos, account_result, account_filter_nm_ids, nm_ids_for_add

        async def run_account(account, nm_ids, semaphore=None):
            if semaphore is None:
                return await process_account(account, nm_ids)
            async with semaphore:
                return await process_account(account, nm_ids)

        if wb_api_factory is not None:
            account_limit = getattr(wb_api_factory, "account_concurrency", None)
            account_semaphore = asyncio.Semaphore(account_limit) if account_limit else None
            account_results = await asyncio.gather(*[
                run_account(account, nm_ids, account_semaphore)
                for account, nm_ids in lk_articles.items()
            ])
        else:
            account_results = []
            for account, nm_ids in lk_articles.items():
                account_results.append(await run_account(account, nm_ids))

        nm_ids_photo = {}
        result_nm_ids_data = {}
        filter_nm_ids_data = []
        for account, account_result in zip(lk_articles.keys(), account_results):
            account_photos, account_data, account_filter_nm_ids, nm_ids_for_add = account_result
            nm_ids_photo.update(account_photos)
            result_nm_ids_data.update(account_data)
            filter_nm_ids_data.extend(account_filter_nm_ids)
            if nm_ids_for_add:
                logger.info(f"Новые артикулы будут добавлены в PostgreSQL. account={account}, nm_ids={nm_ids_for_add}")

        if nm_ids_photo:
            self.gs_connect.add_photo(nm_ids_photo)

        if result_nm_ids_data:
            try:
                async with Database1() as connection:
                    psql_article = ArticleTable(db=connection)
                    filter_nm_ids = await psql_article.check_nm_ids(account="None", nm_ids=filter_nm_ids_data)
                    if filter_nm_ids:
                        logger.info(f"filter_nm_ids {filter_nm_ids}")
                        await psql_article.update_articles(data=result_nm_ids_data, filter_nm_ids=filter_nm_ids)
                    logger.info("данные по артикулам добавлены в таблицу article psql")
            except Exception as e:
                logger.exception(e)

        return result_nm_ids_data

    async def change_cards_and_tables_data(self, db_nm_ids_data, edit_data_from_table):
        sheet_statuses = ServiceGoogleSheet.check_status()
        net_profit_status = sheet_statuses['Отрицательная \nЧП']
        price_discount_edit_status = sheet_statuses['Цены/Скидки']
        dimensions_edit_status = sheet_statuses['Габариты']
        quantity_edit_status = sheet_statuses['Остаток']
        wb_api_factory = globals().get("WB_API_FACTORY")
        tokens = get_wb_tokens()

        logger.info("Получил данные по ячейкам на изменение товара")

        async def process_account(account, nm_ids_data):
            account_updates = []
            account_clean = {"price_discount": set(), "dimensions": set(), "qty": set()}
            valid_data_result = await validate_data(db_nm_ids_data, nm_ids_data)
            token = tokens[account.capitalize()]
            if wb_api_factory is not None:
                warehouses = wb_api_factory.WarehouseMarketplaceWB(token=token)
                warehouses_qty_edit = wb_api_factory.LeftoversMarketplace(token=token)
            else:
                warehouses = WarehouseMarketplaceWB(token=token)
                warehouses_qty_edit = LeftoversMarketplace(token=token)

            if len(valid_data_result) > 0:
                logger.info("Данные валидны")
                if wb_api_factory is not None:
                    wb_api_price_and_discount = wb_api_factory.ListOfGoodsPricesAndDiscounts(token=token)
                    wb_api_content = wb_api_factory.ListOfCardsContent(token=token)
                else:
                    wb_api_price_and_discount = ListOfGoodsPricesAndDiscounts(token=token)
                    wb_api_content = ListOfCardsContent(token=token)

                size_edit_data = []
                price_discount_data = []
                for nm_id, data in valid_data_result.items():
                    if ("price_discount" in data and price_discount_edit_status and
                            (data['net_profit'] >= 0 or net_profit_status)):
                        price_discount_data.append({"nmID": nm_id, **data["price_discount"]})
                    if "dimensions" in data and dimensions_edit_status:
                        size_edit_data.append({"nmID": nm_id, "vendorCode": data["vendorCode"],
                                               "sizes": data["sizes"], "dimensions": data["dimensions"]})

                if price_discount_data:
                    if wb_api_factory is not None:
                        await wb_api_price_and_discount.add_new_price_and_discount_async(price_discount_data)
                    else:
                        wb_api_price_and_discount.add_new_price_and_discount(price_discount_data)
                    account_clean["price_discount"].update(item["nmID"] for item in price_discount_data)

                if size_edit_data:
                    if wb_api_factory is not None:
                        await wb_api_content.size_edit_async(size_edit_data)
                    else:
                        wb_api_content.size_edit(size_edit_data)
                    account_clean["dimensions"].update(item["nmID"] for item in size_edit_data)

                account_updates.extend(int(nm_ids_str) for nm_ids_str in valid_data_result.keys())

            if (account in edit_data_from_table["qty_edit_data"] and
                    (len(edit_data_from_table["qty_edit_data"][account]["stocks"]) > 0 and quantity_edit_status)):
                logger.info("изменение остатков на всех складах продавца")
                qty_edit_data = edit_data_from_table["qty_edit_data"][account]
                valid_qty_pairs = [
                    (stock, nm_id)
                    for stock, nm_id in zip(qty_edit_data["stocks"], qty_edit_data["nm_ids"])
                    if stock.get("chrtId") is not None
                ]
                skipped_qty_nm_ids = [
                    nm_id
                    for stock, nm_id in zip(qty_edit_data["stocks"], qty_edit_data["nm_ids"])
                    if stock.get("chrtId") is None
                ]
                if skipped_qty_nm_ids:
                    logger.warning(
                        "Пропускаем изменение остатков без chrtId. account: {} nm_ids: {}",
                        account,
                        skipped_qty_nm_ids,
                    )

                qty_stocks = [stock for stock, _ in valid_qty_pairs]
                qty_nm_ids = [nm_id for _, nm_id in valid_qty_pairs]
                requested_chrt_ids = {stock["chrtId"] for stock in qty_stocks}
                successful_chrt_ids = requested_chrt_ids.copy()
                if wb_api_factory is not None:
                    account_warehouses = await warehouses.get_account_warehouse_async()
                else:
                    account_warehouses = warehouses.get_account_warehouse()

                if not account_warehouses or not qty_stocks:
                    successful_chrt_ids.clear()

                for warehouse in account_warehouses or []:
                    if wb_api_factory is not None:
                        warehouse_result = await warehouses_qty_edit.edit_amount_from_warehouses_async(
                            warehouse_id=warehouse["id"],
                            edit_barcodes_list=qty_stocks,
                        )
                    else:
                        warehouse_result = warehouses_qty_edit.edit_amount_from_warehouses(
                            warehouse_id=warehouse["id"],
                            edit_barcodes_list=qty_stocks,
                        )
                    warehouse_successful_chrt_ids = {
                        stock["chrtId"]
                        for stock in warehouse_result["successful"]
                        if stock.get("chrtId") is not None
                    }
                    successful_chrt_ids.intersection_update(warehouse_successful_chrt_ids)

                account_clean["qty"].update(
                    nm_id
                    for stock, nm_id in zip(qty_stocks, qty_nm_ids)
                    if stock["chrtId"] in successful_chrt_ids
                )
                if qty_nm_ids:
                    account_updates.extend(qty_nm_ids)

            return account, account_updates, account_clean

        async def run_account(account, nm_ids_data, semaphore=None):
            if semaphore is None:
                return await process_account(account, nm_ids_data)
            async with semaphore:
                return await process_account(account, nm_ids_data)

        accounts_data = list(edit_data_from_table["nm_ids_edit_data"].items())
        if wb_api_factory is not None:
            account_limit = getattr(wb_api_factory, "account_concurrency", None)
            account_semaphore = asyncio.Semaphore(account_limit) if account_limit else None
            account_results = await asyncio.gather(*[
                run_account(account, nm_ids_data, account_semaphore)
                for account, nm_ids_data in accounts_data
            ])
        else:
            account_results = []
            for account, nm_ids_data in accounts_data:
                account_results.append(await run_account(account, nm_ids_data))

        updates_nm_ids_data = {}
        edit_column_clean = {"price_discount": set(), "dimensions": set(), "qty": set()}
        for account, account_updates, account_clean in account_results:
            if account_updates:
                updates_nm_ids_data[account] = account_updates
            for clean_key, clean_values in account_clean.items():
                edit_column_clean[clean_key].update(clean_values)

        if updates_nm_ids_data:
            await asyncio.sleep(5)
            updated_data = await self.add_new_data_from_table(
                lk_articles=updates_nm_ids_data,
                only_edits_data=True,
                add_data_in_db=False,
                check_nm_ids_in_db=False,
            )
            return updated_data, edit_column_clean

        return updates_nm_ids_data, edit_column_clean


def gs_connection():
    return GoogleSheet(
        creds_json=settings.CREEDS_FILE_NAME,
        spreadsheet=settings.SPREADSHEET,
        sheet=settings.SHEET,
    )


def gs_service_for_schedule_connection():
    return ServiceGoogleSheet(
        token=None,
        sheet=settings.SHEET,
        spreadsheet=settings.SPREADSHEET,
        creds_json=settings.CREEDS_FILE_NAME,
    )


def create_lk_articles(edit_nm_ids_data: Dict[str, Any]) -> dict[Any, set[str]]:
    result = {}
    for k, v in edit_nm_ids_data.items():
        account = v.get("account") if isinstance(v, dict) else None
        if not account:
            logger.warning(
                "Пропускаем артикул без account при группировке обновлений: {} -> {}",
                k,
                v,
            )
            continue
        if account not in result:
            result[account] = {k}
        else:
            result[account].add(k)
    return result


async def check_edits_columns(db: Database1):
    service_google_sheet = ServiceGoogleSheet.check_status()
    if service_google_sheet['ВКЛ - 1 /ВЫКЛ - 0']:
        try:
            gs_connect = gs_connection()
            if (service_google_sheet["Остаток"] or service_google_sheet["Цены/Скидки"]
                    or service_google_sheet["Габариты"]):
                logger.info("СЕРВИС РЕДАКТИРОВАНИЯ АКТИВЕН. Оцениваем ячейки по изменениям товара")
                db_nm_ids_data = await ArticleTable(db).get_all_nm_ids()
                chrt_ids_by_nm_id = {}
                print("получаем из бд артикулы и chrt_id")
                chrt_ids = await CardData(db=db).get_chrt_ids()
                for cd in chrt_ids:
                    # print(cd)
                    chrt_ids_by_nm_id[cd['article_id']] = cd['chrt_id']
                edit_data_from_table = await gs_connect.get_edit_data(db_nm_ids_data, service_google_sheet, chrt_ids_by_nm_id)
                if edit_data_from_table:
                    service_gs_table = ServiceGoogleSheet(
                        token=None, sheet=sheet, spreadsheet=spreadsheet, creds_json=creds_json)

                    (edit_nm_ids_data, successful_edits) = await (
                        service_gs_table.change_cards_and_tables_data(
                            db_nm_ids_data=db_nm_ids_data,
                            edit_data_from_table=edit_data_from_table,
                        )
                    )
                    if edit_nm_ids_data:
                        gs_connect.update_rows(
                            data_json=edit_nm_ids_data,
                            edit_column_clean=successful_edits,
                        )
                        return create_lk_articles(edit_nm_ids_data)
                    return None
                return None

            else:
                logger.info("Сервис заблокирован на изменения: (Цены/Скидки, Остаток, Габариты)")
                return None
        except Exception as e:
            logger.info(f"[ERROR] СЕРВИС РЕДАКТИРОВАНИЯ {e}")
            raise e
    return None


class Service:
    async def actualize_card_data_in_db(self, account_articles: Dict[str, int]):
        """Обновление состояния данных карточек по всем кабинетам"""
        logger.info("Обновление состояния данных карточек по всем кабинетам в бд")
        time_start = datetime.datetime.now()
        tasks = []
        for account, nm_ids in account_articles.items():
            tokens = get_wb_tokens()
            token = tokens[account.capitalize()]
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
        async with Database1() as db:
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
                        logger.error(f"Error in -func actualize_card_data_in_db {article} : {e}")
        async with Database1() as db:
            async with db.acquire() as connection:
                async with connection.transaction():
                    card_data_db = CardData(db=connection)
                    article = ArticleTable(db=connection)
                    unit_economics = UnitEconomicsTable(db=connection)
                    await article.update_article_data(data=to_update_article)
                    await card_data_db.update_card_data(data=to_update_card_data)
                    await unit_economics.update_data(data=to_update_unit_economics)
            logger.info(
                f"Обновление состояния данных карточек по всем кабинетам в бд завершено. Время выполнения: {datetime.datetime.now() - time_start}")

    async def get_actually_data_by_account(self, account, token, articles):
        """Получение данных по кабинету:
              article_id, account, length, width, height, barcode, local_vendor_code, vendor_code,
              skus, photo_link, logistic_from_wb_wh_to_opp, commission_wb, price, discount, subject_name,
        """
        wb_api_content = ListOfCardsContent(token=token)
        wb_api_price_and_discount = ListOfGoodsPricesAndDiscounts(token=token)
        commission_traffics = CommissionTariffs(token=token)

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
                    logger.info(f"ERROR by calculate_sum_for_logistic : {str(e)}")
            else:
                logger.info(f"article : {article}, data : {data}")
        # получение комиссии WB
        subject_commissions = await commission_traffics.get_commission_on_subject_async(subject_names=subject_names)
        for card in merge_json_data.values():
            if subject_commissions is not None:
                for sc in subject_commissions.items():
                    if "subject_name" in card and sc[0] == card["subject_name"]:
                        card["commission_wb"] = sc[1]
        return merge_json_data


@log_job
async def job_check_edits_columns_and_add_actually_data_to_table():
    logger.info("Запуск :"
                "Актуализация информации по ценам, скидкам, габаритам, комиссии, логистики от склада WB до ПВЗ")
    gs_service = gs_service_for_schedule_connection()
    service = Service()
    async with Database1() as db:
        await gs_service.add_actually_data_to_table(db=db)
        logger.info("Завершение :"
                    "Актуализация информации по ценам, скидкам, габаритам, комиссии, логистики от склада WB до ПВЗ")
        logger.info("Запуск : Смотрит в таблицу, оценивает изменения")
        result = await check_edits_columns(db=db)
        pprint(result)
        if result:
            logger.info("Завершение : Внесение изменений в таблицу")
            await service.actualize_card_data_in_db(result)


if __name__ == "__main__":
    asyncio.run(job_check_edits_columns_and_add_actually_data_to_table())
