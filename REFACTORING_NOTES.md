# Refactoring notes for `job_check_edits_standalone.py`

Разбор текущих проблем и направлений рефакторинга. Это не план правок в коде, а карта мест, где standalone-джоба сейчас хрупкая, медленная или сложная для сопровождения.

## Implementation checkpoints

Этот блок - рабочий трекер по нашему ТЗ. Обновлять его после каждого шага рефакторинга.

| # | Направление | Статус | Что сделано | Что осталось |
|---|-------------|--------|-------------|--------------|
| 1 | Сохранить legacy-файл и сделать новый запуск | Done | `job_check_edits_standalone.py` сохранен, добавлены `main.py`, `job.py`, `legacy.py`, `__init__.py`; запуск идет через `python main.py`. | Ничего. |
| 2 | Разделить файл на модули без изменения логики | Partial | Создан первый слой: `main.py` как entrypoint, `job.py` как orchestration, `legacy.py` как compatibility boundary; добавлены `config.py`, `constants.py`, `domain.py`, `db.py`, `repositories.py`, `google_sheet.py`, `logging_setup.py`, `wb_api.py`, `services/card_actualization.py`; domain/db/repository/google-sheet helpers подключены к legacy через compatibility layer. | Перенести оставшиеся `services`; затем убрать зависимость `job.py` от legacy. |
| 3 | Кэшировать `tokens.json` | Partial | Добавлен `TokenProvider`; новый entrypoint грузит токены один раз за запуск и подменяет legacy `get_wb_tokens()`. | Убрать monkey-patch и передавать `TokenProvider` явно в сервисы/клиенты через конструкторы. |
| 4 | Зафиксировать текущее поведение тестами | Partial | Добавлены `unittest` для `TokenProvider`, `validate_data`, `create_lk_articles`, `merge_dicts`, логистики, vendor-code helpers, `CostPriceDBContainer`, helper-частей `GoogleSheet` (skip без gspread/pandas), DB-контекста `CardActualizationService` и `WBApiFactory`; ручные проверки импорта/компиляции сохранены. | Добавить tests для логики очистки edit-колонок, будущих моделей результата и оставшегося service orchestration. |
| 5 | Вынести константы колонок/статусов Google Sheets | Partial | Добавлен `constants.py`; новый `domain.py` использует константы в `validate_data`; domain helpers подключены к новому запуску через `legacy.configure_domain_helpers()`. | Постепенно заменить строковые литералы в service/google-sheet коде, добавить валидацию обязательных колонок. |
| 6 | Один DB pool/context на job | Partial | `Database1` вынесен в `db.py`; `CardActualizationService.actualize_card_data_in_db` теперь использует открытый `db` из job вместо создания новых pool; покрыто unit-тестом. | Убрать/обойти оставшиеся внутренние `Database1()` в legacy `ServiceGoogleSheet.add_new_data_from_table` для веток, которые пишут новые артикулы в БД. |
| 7 | Явная модель результата правок | Pending | Не начато. | Ввести dataclass/Pydantic-модели для price/discount, dimensions, qty, success/error/skipped; развязать успешность WB-операции, перечитывание карточки и очистку edit-полей. |
| 8 | Оптимизировать Google Sheets чтение/обновление | Pending | `GoogleSheet` и `safe_batch_update` вынесены в `google_sheet.py` без изменения логики. | Уменьшить `get_all_values/get_all_records`, построить индексы, убрать лишний pandas там, где он не нужен. |
| 9 | Унифицировать retry/backoff | Pending | Не начато. | Общий helper/decorator для 429/5xx/timeouts, убрать разбросанные `sleep`. |
| 10 | Перевести WB clients на async | Partial | Добавлен `wb_api.py` с async-клиентами для финальной актуализации карточек; `job.py` создает один `aiohttp.ClientSession` на запуск и передает его через `WBApiFactory`; legacy `ServiceGoogleSheet` при новом запуске использует async WB-операции для чтения карточек, цен, тарифов/комиссий, складов/остатков, изменения цен/скидок, габаритов и остатков. | Проверить поведение на реальных WB ответах/лимитах, затем убрать sync fallback из legacy-классов. |
| 11 | Параллелить обработку аккаунтов | Partial | `CardActualizationService.actualize_card_data_in_db` запускает кабинеты через `asyncio.gather`; legacy `ServiceGoogleSheet.add_new_data_from_table` и `change_cards_and_tables_data` при новом запуске тоже обрабатывают аккаунты через `asyncio.gather` без account-level cap по умолчанию; WB factory использует общую session и per-token endpoint semaphore-лимитеры. | Проверить на реальных WB ответах; `account_concurrency` оставить только как диагностический override, затем вынести account orchestration из legacy в отдельный service. |
| 12 | Улучшить summary/error logging | Pending | Не начато. | Итоговые counts по найдено/применено/skipped/error, контекст account/nm_id/warehouse_id. |

### Last completed checkpoint

- 2026-06-27: legacy `ServiceGoogleSheet` подключен к `WBApiFactory` из нового entrypoint; операции WB для применения правок и последующего чтения данных переведены на async-клиенты с общей `aiohttp.ClientSession`, per-token endpoint semaphore-лимитами и параллельной обработкой всех аккаунтов без account-level cap по умолчанию. Пройдено 12 тестов, 1 GoogleSheet-зависимый тест skip.
- 2026-06-26: добавлен `wb_api.py` и подключен в `job.py` через `WBApiFactory`; финальная актуализация карточек по аккаунтам теперь использует async WB-клиенты с одной `aiohttp.ClientSession` на запуск job. Пройдено 12 тестов, 1 GoogleSheet-зависимый тест skip в окружении без `gspread/pandas`.


## 1. Один файл содержит слишком много слоев

Сейчас в `job_check_edits_standalone.py` собраны сразу:

- конфигурация и логгер;
- подключение к PostgreSQL;
- repository-классы для таблиц;
- утилиты преобразования данных;
- Google Sheets client;
- WB API clients;
- бизнес-сервис работы с Google Sheets;
- сервис актуализации карточек в БД;
- сама job.

Проблема не только в размере файла. Из-за смешения слоев сложно понять, где заканчивается инфраструктура и начинается бизнес-логика. Любая правка в WB API или Google Sheets визуально выглядит как правка job, хотя это разные зоны ответственности.

Возможный рефакторинг:

- `config.py` - env/settings/tokens;
- `logging_setup.py` - логгер и `log_job`;
- `db.py` - `Database1`;
- `repositories.py` или отдельный пакет `repositories/`;
- `google_sheet.py` - низкоуровневый GoogleSheet;
- `wb_api.py` или пакет `wb/`;
- `services/google_sheet_service.py`;
- `services/card_actualization.py`;
- `job_check_edits_standalone.py` - только orchestration.

## 2. Синхронные WB-запросы внутри async-потока

Да, это одна из главных проблем.

В async job есть много синхронных `requests.*`:

- `ListOfCardsContent.get_list_of_cards`;
- `ListOfGoodsPricesAndDiscounts.get_log_for_nm_ids`;
- `ListOfGoodsPricesAndDiscounts.add_new_price_and_discount`;
- `CommissionTariffs.get_commission_on_subject`;
- `CommissionTariffs.get_tariffs_box_from_marketplace`;
- `LeftoversMarketplace.get_amount_from_warehouses`;
- `LeftoversMarketplace.edit_amount_from_warehouses`;
- `WarehouseMarketplaceWB.get_account_warehouse`;
- часть Google Sheets операций через `gspread` тоже синхронная.

Из-за этого event loop часто блокируется. Даже если внешний метод объявлен `async`, внутри он может последовательно ждать сетевые операции.

Особенно заметные места:

- `ServiceGoogleSheet.add_new_data_from_table` проходит по аккаунтам последовательно и внутри делает синхронные WB-запросы.
- `ServiceGoogleSheet.change_cards_and_tables_data` по каждому аккаунту синхронно меняет цены, габариты и остатки.
- Остатки обновляются по складам последовательно.

Оптимизация:

- сделать async-версии WB clients на `aiohttp`/`httpx.AsyncClient`;
- создать один HTTP session/client на job или сервис, а не открывать новый на каждый запрос;
- обрабатывать аккаунты параллельно через `asyncio.gather`;
- внутри аккаунта параллелить независимые запросы: карточки, цены, склады, комиссии;
- для лимитированных WB endpoints добавить rate limiter/semaphore, чтобы не получить 429.

Важно: параллелить без лимитов нельзя. WB часто отвечает 429, поэтому нужна управляемая конкурентность.

## 3. Создание `aiohttp.ClientSession` на каждый запрос

Даже там, где код уже async, session часто создается прямо внутри метода или цикла. Это дорого и хуже управляется.

Проблемы:

- нет общего connection pool;
- труднее задать единые timeout/retry headers;
- сложнее закрывать ресурсы корректно;
- больше накладных расходов на TLS/соединения.

Оптимизация:

- передавать `aiohttp.ClientSession` или `httpx.AsyncClient` в WB clients;
- создавать session один раз на время выполнения job;
- централизовать timeout/retry/backoff.

## 4. Блокирующие `time.sleep` внутри async job

В файле есть много `time.sleep(...)` в коде, который вызывается из async-потока.

Это полностью блокирует event loop. Если sleep стоит в обработке одного аккаунта, остальные async-задачи в этом же loop тоже не двигаются.

Примеры:

- retry в GoogleSheet/WB clients;
- задержки после изменения габаритов/цен;
- ожидание после 429/503;
- ожидание между попытками открытия Google Sheet.

Оптимизация:

- в async-ветках использовать `await asyncio.sleep(...)`;
- sync-клиенты либо вынести в thread executor, либо заменить на async;
- retry/backoff сделать единым helper-ом.

## 5. Последовательная обработка аккаунтов

Многие циклы идут так:

```python
for account, nm_ids in lk_articles.items():
    ...
```

И внутри каждого аккаунта выполняются сетевые вызовы. Если кабинетов несколько, общее время примерно суммируется.

Где это критично:

- актуализация данных после правок;
- изменение цен/скидок/габаритов/остатков;
- сбор фактических данных для записи в БД.

Оптимизация:

- `process_account(account, nm_ids)` как отдельная coroutine;
- запускать аккаунты через `asyncio.gather`;
- ограничить параллелизм semaphore-ом, например 2-4 аккаунта одновременно;
- отдельно лимитировать тяжелые WB endpoints.

## 6. Google Sheets - много полных чтений таблицы

Код часто делает:

- `get_all_values`;
- `get_all_records`;
- `row_values(1)`;
- полное построение DataFrame.

Для больших таблиц это дорого. Особенно если в одной job несколько раз читается один и тот же лист.

Проблемы:

- лишние запросы к Google API;
- большая нагрузка на память;
- медленная работа pandas там, где нужны только несколько колонок;
- риск quota/API limits.

Оптимизация:

- читать таблицу один раз на этап и передавать snapshot дальше;
- кэшировать headers;
- читать только нужные ranges;
- обновлять только реально измененные cells;
- там, где возможно, отказаться от pandas в пользу списков/словарей.

## 7. `update_rows` строит DataFrame и делает точечные updates

`GoogleSheet.update_rows`:

- читает всю таблицу;
- строит DataFrame;
- строит `json_df`;
- ищет matching rows;
- формирует список cell updates;
- отправляет batch update.

Это работает, но сложность высокая, и для больших листов может быть медленно.

Оптимизация:

- построить индекс `article -> row_number` один раз;
- работать с обычными dict/list вместо pandas;
- заранее сопоставить headers с колонками;
- формировать updates напрямую.

## 8. Много повторных чтений `tokens.json`

`get_wb_tokens()` читает JSON-файл каждый раз. Внутри циклов по аккаунтам это повторяется часто.

Оптимизация:

- загрузить tokens один раз на старт job;
- передавать tokens в сервисы;
- или сделать простой cached loader.

Риск: если токены должны горячо обновляться без перезапуска, нужен явный механизм refresh. Сейчас такого механизма в коде не видно.

## 9. БД pool создается несколько раз за один запуск

В job уже открыт `async with Database1() as db`, но внутри сервисов снова создаются новые `Database1()`:

- в `ServiceGoogleSheet.add_new_data_from_table`;
- в `Service.actualize_card_data_in_db`.

Проблемы:

- лишнее создание pool;
- сложнее контролировать транзакции;
- тяжелее тестировать;
- неочевидно, какие операции используют общий контекст, а какие открывают новый.

Оптимизация:

- передавать `db` явно в сервисы;
- один pool на job;
- транзакции открывать только там, где они действительно нужны.

## 10. Нет явной модели результата правок

Сейчас данные передаются через вложенные dict:

- `edit_data_from_table`;
- `edit_column_clean`;
- `updates_nm_ids_data`;
- `edit_nm_ids_data`.

Поля завязаны на русские названия колонок и строковые ключи. Это делает код хрупким.

Оптимизация:

- dataclass/Pydantic-модели для:
  - статусов сервиса;
  - правки цены/скидки;
  - правки габаритов;
  - правки остатка;
  - результата применения правок;
- отдельный mapper между Google Sheets columns и внутренними моделями.

## 11. Бизнес-логика завязана на названия колонок Google Sheets

В коде много строк вроде:

- `"Установить новую цену"`;
- `"Новая\nВысота (см)"`;
- `"Чистая прибыль 1ед."`;
- `"ВКЛ - 1 /ВЫКЛ - 0"`;
- `"Отрицательная \nЧП"`.

Если заголовок в таблице изменится, код сломается неявно.

Оптимизация:

- вынести имена колонок в constants;
- сделать валидацию заголовков при старте;
- выдавать понятную ошибку, если обязательной колонки нет.

## 12. Retry-логика размазана по коду

Повторы и ожидания есть в разных местах, но они разные:

- где-то 10 попыток;
- где-то 3;
- где-то sleep 60/63/75 секунд;
- где-то ловится `Exception`;
- где-то retry зависит от текста ответа.

Оптимизация:

- общий retry helper/decorator;
- единая политика по endpoint-ам;
- retry только на transient errors: 429, 5xx, network timeout;
- не retry на validation/business errors.

## 13. Ошибки часто логируются, но контекст неполный

Есть логи ошибок, но часто не хватает:

- account;
- nm_id;
- warehouse_id;
- endpoint;
- request payload size;
- response status/body.

При этом некоторые места логируют слишком много данных целиком, что может быть шумно и опасно.

Оптимизация:

- структурировать контекст ошибки;
- логировать payload не целиком, а count/list ids;
- отделить user/business errors от infrastructure errors.

## 14. Частичное применение изменений

В `change_cards_and_tables_data` одна job может:

- изменить цену;
- изменить габариты;
- изменить остаток;
- потом не суметь обновить таблицу;
- или наоборот обновить таблицу после частичного успеха.

Сейчас состояние успеха хранится в `edit_column_clean`, но общая модель частичного результата неявная.

Оптимизация:

- возвращать структурированный результат по каждому nm_id:
  - price_discount: success/error/skipped;
  - dimensions: success/error/skipped;
  - qty: success/error/skipped;
- очищать Google Sheet только по успешным операциям;
- логировать summary по итогам.

## 15. Cleanup-only update и fallback на корзину WB

Проблема из запуска 2026-07-03: job могла успешно отправить часть запросов в WB, но не вызвать `GoogleSheet.update_rows`, потому что последующее перечитывание актуальных данных вернуло пустой результат. В логах это выглядело как успешное завершение job, но без обновления/очистки строк в Google Sheet.

Текущая связка хрупкая:

- `change_cards_and_tables_data` применяет изменения в WB;
- затем `add_new_data_from_table(..., only_edits_data=True)` пытается перечитать полные данные карточек;
- если карточка не найдена обычным методом `/content/v2/get/cards/list`, итоговый `updated_data` может стать пустым;
- `check_edits_columns` вызывает `update_rows` только когда `edit_nm_ids_data` не пустой;
- из-за этого успешные edit-поля могут не очиститься.

Что нужно сделать позже:

- Добавить fallback поиска карточек в корзине WB для nmID, которые не вернул базовый метод чтения карточек. Это поможет обновлять данные по карточкам, которые не видны в обычном списке, но доступны в корзине.
- Развязать "WB принял изменение" и "мы смогли перечитать полные данные". Очистка edit-полей должна зависеть от фактического успеха операции WB, а не от того, получилось ли собрать полную строку для таблицы.
- Добавить cleanup-only режим для `GoogleSheet.update_rows`: если полные данные карточки не получены, передавать минимальную строку с `Артикул` и очищать только те edit-поля, которые реально успешно применены.
- Для price/discount не считать отправленный payload успешным автоматически. Ответ WB вида `All item Nos. are specified incorrectly, or the specified prices and discounts are already set` неоднозначный: часть товаров может быть неверной, а часть уже иметь нужные значения. Надежнее после отправки перечитать цены/скидки и признать успешными только nmID, где значения реально стали нужными или уже совпадали.
- Для dimensions очищать поля только если endpoint изменения габаритов вернул успешный результат.
- Для qty текущая логика уже ближе к нужной: очищать только nmID, где изменение прошло по складам и WB не вернул `NotFound` по `chrtId`.
- Добавить summary-лог: сколько nmID отправлено, сколько применено, сколько очищено в Google Sheet, сколько не удалось перечитать, сколько найдено fallback-ом в корзине.

Ожидаемый итоговый сценарий:

1. WB применил изменение - соответствующее edit-поле в Google Sheet очищается.
2. Полные данные карточки найдены обычным методом или через корзину - строка дополнительно обновляется актуальными значениями.
3. Полные данные не найдены нигде - успешное edit-поле все равно очищается, а в лог пишется warning с `account` и `nm_id`.

Оценка сложности: средняя. Это не требует переписывать всю job, но лучше делать после введения явной модели результата правок, иначе снова появятся неявные dict-связки между WB, Google Sheet и БД.

## 16. Тестируемость низкая

Из-за одного файла и прямого создания clients внутри методов трудно тестировать:

- Google Sheet чтение;
- WB responses;
- DB writes;
- обработку ошибок;
- частичные успехи.

Оптимизация:

- dependency injection для clients/repositories;
- pure-функции для parsing/validation;
- unit tests для `validate_data`, `create_lk_articles`, mapping колонок;
- integration-like tests с fake WB/Google clients.

## 17. Рекомендуемая очередность рефакторинга

Безопаснее идти так:

1. Разделить файл на модули без изменения логики и логов.
2. Вынести constants для колонок Google Sheets.
3. Кэшировать `tokens.json`.
4. Передавать один DB pool/context через сервисы.
5. Добавить модели результата правок.
6. Переписать WB clients на общий async client.
7. Параллелить обработку аккаунтов с semaphore/rate limits.
8. Оптимизировать Google Sheets чтение/обновление.
9. Добавить тесты на parsing/validation и частичные успехи.

Главный принцип: сначала разрезать код на слои и зафиксировать поведение, потом ускорять. Если сразу менять async и бизнес-логику, будет сложно понять, где появилась регрессия.
