# Полная документация standalone job_check_edits

## Назначение

`standalone_job_check_edits` - отдельная версия задачи `job_check_edits_columns_and_add_actually_data_to_table`. Она читает управляющие флаги и edit-колонки из Google Sheets, получает и обновляет данные Wildberries, пишет актуальные данные в PostgreSQL и очищает/актуализирует таблицу после применения изменений.

Каталог подготовлен так, чтобы его можно было вынести в отдельный репозиторий. Внутри уже находятся исходники, Dockerfile, docker-compose, requirements, тесты и документация. Код не должен зависеть от импорта модулей из родительского проекта.

## Режимы запуска

Есть два entrypoint-режима.

`main.py` - разовый запуск job. Подходит для ручной проверки, отладки, запуска из cron/systemd или локального теста.

`scheduler.py` - долгоживущий процесс с APScheduler. Именно он запускается в Docker по умолчанию. Scheduler выполняет job каждые 15 минут.

Текущий Dockerfile содержит:

```dockerfile
CMD ["python", "scheduler.py"]
```

Если нужен одноразовый контейнер, команду можно переопределить:

```bash
docker compose run --rm standalone_job_check_edits python main.py
```

## Быстрый запуск локально

```bash
cd /home/skurbick/PROJECTS/StandaloneJobCheckEdits
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Перед реальным запуском нужно заполнить `.env` и положить рядом `creds.json` и `tokens.json`.

## Быстрый запуск в Docker

```bash
cd /home/skurbick/PROJECTS/StandaloneJobCheckEdits
docker compose up --build -d
```

Проверить статус:

```bash
docker compose ps
```

Смотреть stdout/stderr контейнера:

```bash
docker compose logs -f
```

Остановить scheduler:

```bash
docker compose down
```

Перезапустить после изменения кода:

```bash
docker compose up --build -d
```


## Docker Compose и сеть базы

Standalone compose рассчитан на отдельный запуск сервиса и подключает контейнер к внешней Docker-сети `vector_db_default`:

```yaml
networks:
  vector_db_default:
    external: true
```

Это повторяет схему старого общего compose, где `VectorProject` был в той же сети с БД. Перед запуском на сервере сеть должна уже существовать:

```bash
docker network ls | grep vector_db_default
```

Если PostgreSQL находится в этой сети, в `.env` можно использовать docker-имя сервиса/контейнера базы в `DB_HOST`. Если PostgreSQL доступен по внешнему IP или DNS, укажи этот адрес в `DB_HOST`; подключение к `vector_db_default` при этом не мешает.

Для scheduler-контейнера задано `restart: unless-stopped`, чтобы он поднимался после перезапуска Docker/сервера и после аварийного падения процесса.

## Расписание

Расписание задано в `scheduler.py`:

```python
@scheduler.scheduled_job(IntervalTrigger(minutes=15), coalesce=True)
```

Параметры scheduler:

- `minutes=15` - запуск каждые 15 минут.
- `coalesce=True` - если несколько запусков были пропущены, APScheduler выполнит только один ближайший запуск, а не все накопленные.
- `max_instances=1` - не допускает параллельного выполнения одной и той же job, если предыдущий запуск еще не завершился.
- `misfire_grace_time=2000` - допустимое окно для запоздавшего запуска.

Если job иногда выполняется дольше 15 минут, `max_instances=1` защищает от наложения запусков.

## Переменные окружения

Переменные читаются из `.env` через `python-dotenv`.

Обязательные:

```dotenv
SHEET=
SPREADSHEET=
CREEDS_FILE_NAME=creds.json
TOKENS_FILE_NAME=tokens.json
DB_USER=
DB_PASSWORD=
DB_NAME=
DB_HOST=
DB_PORT=
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=3
DB_CONNECT_TIMEOUT=300
DB_COMMAND_TIMEOUT=250
```

Дополнительные, если используются соответствующие участки legacy-логики:

```dotenv
PC_SHEET=
PC_SPREADSHEET=
```

Описание:

| Переменная | Назначение |
|---|---|
| `SHEET` | Лист Google Sheets с основной таблицей. |
| `SPREADSHEET` | Название Google spreadsheet. |
| `CREEDS_FILE_NAME` | Путь к Google service account credentials. По умолчанию `creds.json`. |
| `TOKENS_FILE_NAME` | Путь к JSON-файлу с WB токенами. По умолчанию `tokens.json`. |
| `DB_USER` | Пользователь PostgreSQL. |
| `DB_PASSWORD` | Пароль PostgreSQL. |
| `DB_NAME` | Имя базы PostgreSQL. |
| `DB_HOST` | Хост PostgreSQL. Для Docker часто это имя сервиса/хост в docker network, не `localhost`. |
| `DB_PORT` | Порт PostgreSQL. |
| `DB_POOL_MIN_SIZE` | Минимальное число соединений в asyncpg pool. По умолчанию `1`. |
| `DB_POOL_MAX_SIZE` | Максимальное число соединений в asyncpg pool. По умолчанию `3`. |
| `DB_CONNECT_TIMEOUT` | Timeout подключения к PostgreSQL. По умолчанию `300`. |
| `DB_COMMAND_TIMEOUT` | Timeout SQL-команд. По умолчанию `250`. |
| `PC_SHEET` | Лист для расчетов/плановой себестоимости, если нужен legacy-ветке. |
| `PC_SPREADSHEET` | Spreadsheet для расчетов/плановой себестоимости, если нужен legacy-ветке. |

## Секреты и файлы рядом с приложением

Нужные runtime-файлы:

- `.env` - окружение, не коммитить.
- `creds.json` - Google service account credentials, не коммитить.
- `tokens.json` - токены Wildberries по кабинетам, не коммитить.

В Docker compose эти файлы монтируются так:

```yaml
volumes:
  - ./logging:/app/logging
  - ./creds.json:/app/creds.json:ro
  - ./tokens.json:/app/tokens.json:ro
```

`creds.json` и `tokens.json` внутри контейнера read-only.

## Формат tokens.json

`TokenProvider` ожидает JSON-объект, где ключ - название кабинета, значение - WB token.

Пример:

```json
{
  "Wild1": "wb-token-for-wild1",
  "Wild2": "wb-token-for-wild2"
}
```

При запросе токена имя аккаунта капитализируется: `wild1` превращается в `Wild1`. Поэтому ключи в `tokens.json` должны соответствовать этому формату.

Токены кэшируются на время одного запуска job. В scheduler-режиме каждый запуск job создает новый `TokenProvider`, поэтому изменения `tokens.json` будут подхвачены на следующем выполнении job.

## Google credentials

`creds.json` - файл сервисного аккаунта Google. Сервисный аккаунт должен иметь доступ к нужным Google Sheets. Обычно нужно открыть spreadsheet в Google Sheets и выдать доступ email-адресу сервисного аккаунта из `client_email`.

## PostgreSQL

Подключение создается через `asyncpg.create_pool` в `db.py`.

Текущие параметры pool задаются через `.env` и имеют компактные дефолты для standalone scheduler:

- `DB_POOL_MIN_SIZE=1`
- `DB_POOL_MAX_SIZE=3`
- `DB_CONNECT_TIMEOUT=300`
- `DB_COMMAND_TIMEOUT=250`

Для этого сервиса нет смысла заранее держать 5 соединений: job одна, DB-участки короткие, а основной параллелизм идет во внешние WB/Google API. Если на сервере появится реальная потребность, лимиты можно поднять в `.env` без изменения кода.

Если сервис запускается в Docker, `DB_HOST=localhost` почти всегда неправильный, если база не внутри того же контейнера. Нужно указать доступный контейнеру адрес базы: имя сервиса в общей docker network, IP/host сервера или `host.docker.internal`, если окружение это поддерживает.

## Логи

Логи пишутся в каталог:

```text
logging/
```

В Docker этот каталог примонтирован как volume bind-mount:

```yaml
- ./logging:/app/logging
```

То есть логи остаются на хосте после перезапуска контейнера.

`log_job` создает отдельные файлы по имени job и дате. Общие ошибки scheduler также пишутся через legacy logger.

## Структура проекта

```text
standalone_job_check_edits/
  main.py                         # разовый запуск job
  scheduler.py                    # APScheduler, запуск каждые 15 минут
  job.py                          # orchestration текущей job
  legacy.py                       # compatibility layer к legacy-файлу
  job_check_edits_standalone.py   # сохраненная legacy-реализация
  config.py                       # env/settings
  db.py                           # asyncpg pool/context
  token_provider.py               # кэшированное чтение tokens.json
  wb_api.py                       # async WB clients/factory
  google_sheet.py                 # Google Sheets helpers
  domain.py                       # чистые domain helpers
  constants.py                    # названия колонок/статусов Google Sheets
  repositories.py                 # DB repositories
  services/
    card_actualization.py         # актуализация карточек/данных в БД
  tests/                          # unittest-тесты
  Dockerfile
  docker-compose.yaml
  requirements.txt
  README.md
  REFACTORING_NOTES.md
  docs/FULL_DOCUMENTATION.md
```

## Архитектура текущего состояния

Текущий код находится в промежуточном состоянии рефакторинга.

`job_check_edits_standalone.py` сохранен как legacy-файл. В нем еще много логики: Google Sheets, WB API, БД, orchestration и бизнес-правила.

Новый слой состоит из:

- `main.py` - вызывает `run()` из `job.py`.
- `scheduler.py` - запускает ту же job по расписанию.
- `job.py` - собирает зависимости на один запуск: токены, legacy compatibility, WB aiohttp session, DB context.
- `legacy.py` - подменяет часть legacy-зависимостей на новые модули, чтобы переносить код постепенно без большого переписывания.

Это сделано специально: можно выносить сервис в отдельный репозиторий уже сейчас, а рефакторинг продолжать итерационно.

## Что делает job на верхнем уровне

Упрощенный порядок выполнения:

1. Загружает токены WB через `TokenProvider`.
2. Настраивает legacy compatibility layer.
3. Создает Google Sheets service через `gs_service_for_schedule_connection()`.
4. Создает один `aiohttp.ClientSession` на запуск job.
5. Создает WB API factory на базе этой session.
6. Открывает один DB pool/context.
7. Добавляет актуальные данные в таблицу.
8. Читает edit-колонки и определяет изменения.
9. Если изменения есть, применяет их в WB/БД и актуализирует карточки.

## Тесты

Запуск всех тестов:

```bash
python3 -B -m unittest discover -s tests
```

Проверка синтаксиса основных entrypoint-файлов:

```bash
python3 -B -m py_compile scheduler.py job.py main.py
```

Часть тестов Google Sheets может быть пропущена, если в окружении нет `gspread`/`pandas` или реальных credentials. Это нормально для unit-уровня.

## Как выносить в отдельный репозиторий

Проект уже перенесен как root-проект в каталог:

```text
/home/skurbick/PROJECTS/StandaloneJobCheckEdits
```

Тестовые импорты в этом каталоге адаптированы под root-структуру (`from token_provider import ...`, `from wb_api import ...`).

Рекомендуемый порядок после переноса:

1. Проверить, что в git не попадут `.env`, `creds.json`, `tokens.json`, `logging/`, `.venv/`. Они уже перечислены в `.gitignore`.
2. Выполнить локально из корня проекта:

```bash
cd /home/skurbick/PROJECTS/StandaloneJobCheckEdits
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -B -m unittest discover -s tests
python3 -B -m py_compile scheduler.py job.py main.py
```

3. Проверить ручной запуск:

```bash
python main.py
```

4. Проверить Docker:

```bash
docker compose build
docker compose run --rm standalone_job_check_edits python main.py
```

5. После успешной ручной проверки запустить scheduler:

```bash
docker compose up -d
```

## Git ignore для отдельного репозитория

В будущем standalone-репозитории обязательно игнорировать:

```gitignore
.env
creds.json
tokens.json
logging/
.venv/
__pycache__/
*.pyc
```

В этот каталог уже добавлен локальный `.gitignore` с такими правилами.

## Эксплуатационный чеклист

Перед запуском на сервере проверить:

- `.env` заполнен.
- `creds.json` лежит рядом и сервисный аккаунт имеет доступ к spreadsheet.
- `tokens.json` лежит рядом и содержит актуальные WB токены.
- PostgreSQL доступен из контейнера/хоста по `DB_HOST:DB_PORT`.
- Каталог `logging/` создается и доступен на запись.
- Контейнер стартует: `docker compose ps`.
- В логах нет ошибок импорта: `docker compose logs -f`.
- Job не выполняется параллельно другим экземпляром из старого основного проекта.

## Важное про дублирование запусков

Если старая job остается включенной в основном проекте, а standalone scheduler тоже запущен, они могут одновременно читать/менять одну и ту же таблицу и WB-данные.

Перед включением standalone scheduler нужно убедиться, что аналогичная job в основном проекте отключена или не стартует.

В старом `main.py` основного проекта такая job была оформлена через `APScheduler` и `IntervalTrigger(minutes=15)`. При миграции должен остаться один активный источник расписания.

## Известные ограничения

- Часть тяжелой логики все еще живет в `job_check_edits_standalone.py`.
- Compatibility layer в `legacy.py` использует monkey-patch, чтобы подключить новые модули к legacy-коду.
- Google Sheets API через `gspread` остается синхронным.
- Не все WB-операции окончательно вынесены из legacy-классов.
- Модель результата правок пока основана на вложенных dict, а не на явных dataclass/Pydantic-моделях.
- Нужно внимательно следить за лимитами WB и Google Sheets при увеличении параллелизма.

## Направления дальнейшего рефакторинга

Подробный список есть в `REFACTORING_NOTES.md`. Ключевые направления:

- окончательно убрать зависимость `job.py` от legacy orchestration;
- перенести оставшиеся сервисы из `job_check_edits_standalone.py` в отдельные модули;
- ввести явные модели результата правок;
- централизовать retry/backoff для WB и Google Sheets;
- уменьшить количество полных чтений Google Sheets;
- убрать лишние `Database1()` внутри сервисов и использовать один DB context на job;
- добавить summary logging: найдено, применено, skipped, error;
- расширить тесты на очистку edit-колонок и orchestration.

## Частые команды

Разовый запуск локально:

```bash
python main.py
```

Scheduler локально:

```bash
python scheduler.py
```

Docker scheduler:

```bash
docker compose up --build -d
```

Docker одноразовая job:

```bash
docker compose run --rm standalone_job_check_edits python main.py
```

Логи Docker:

```bash
docker compose logs -f
```

Остановка:

```bash
docker compose down
```

Тесты:

```bash
python3 -B -m unittest discover -s tests
```

## Минимальная диагностика проблем

Если контейнер сразу завершается:

```bash
docker compose logs --tail=200
```

Если не видит `.env`:

- запускать команды из каталога, где лежит `docker-compose.yaml`;
- проверить имя файла: именно `.env`, не `.env.txt`.

Если не видит `creds.json` или `tokens.json`:

- проверить, что файлы лежат рядом с `docker-compose.yaml`;
- проверить volume mounts в `docker-compose.yaml`.

Если ошибка подключения к БД:

- проверить `DB_HOST` изнутри контейнера;
- убедиться, что PostgreSQL принимает подключения с этого адреса;
- проверить docker network, если база тоже в Docker.

Если Google Sheets возвращает permission error:

- проверить `client_email` в `creds.json`;
- выдать этому email доступ к spreadsheet.

Если WB возвращает 401/403:

- проверить актуальность token в `tokens.json`;
- проверить, что ключ кабинета совпадает с ожидаемым именем аккаунта.
