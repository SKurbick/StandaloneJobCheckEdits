# Standalone job_check_edits

Отдельная версия job `job_check_edits_columns_and_add_actually_data_to_table` для запуска независимо от основного проекта.

По умолчанию Docker запускает `scheduler.py`: контейнер живет постоянно и выполняет job каждые 15 минут через APScheduler. Для ручной проверки остался разовый entrypoint `main.py`.

## Документация

- [Полная документация](docs/FULL_DOCUMENTATION.md)
- [Заметки по рефакторингу](REFACTORING_NOTES.md)
- [Пример .env](.env.example)
- [Пример tokens.json](tokens.example.json)

## Быстрый запуск в Docker

```bash
cd /home/skurbick/PROJECTS/StandaloneJobCheckEdits
cp .env.example .env
# заполнить .env, положить creds.json и tokens.json рядом
docker compose up --build -d
```

Логи контейнера:

```bash
docker compose logs -f
```

Логи приложения сохраняются в `./logging`.

## Разовый запуск локально

```bash
cd /home/skurbick/PROJECTS/StandaloneJobCheckEdits
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполнить .env, положить creds.json и tokens.json рядом
python main.py
```

## Нужные файлы

Рядом с приложением должны быть доступны:

- `.env` - переменные окружения;
- `creds.json` - Google service account credentials;
- `tokens.json` - WB токены по кабинетам.

Секреты и логи добавлены в локальный `.gitignore`, чтобы каталог можно было безопаснее вынести в отдельный репозиторий.

## Проверка

```bash
python3 -B -m unittest discover -s tests
python3 -B -m py_compile scheduler.py job.py main.py
```

