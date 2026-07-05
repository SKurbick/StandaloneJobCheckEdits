"""Google Sheets helpers for the standalone check-edits job."""

import time
from typing import Any, Dict, List

import gspread
import pandas as pd
import requests
from gspread import Client, service_account
from loguru import logger

try:
    from .domain import column_index_to_letter
except ImportError:
    from domain import column_index_to_letter


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
