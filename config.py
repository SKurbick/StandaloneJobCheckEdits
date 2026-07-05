"""Configuration for the refactored standalone job entrypoint."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


load_dotenv()


@dataclass(frozen=True)
class Settings:
    SHEET: str | None = os.getenv("SHEET")
    SPREADSHEET: str | None = os.getenv("SPREADSHEET")
    CREEDS_FILE_NAME: str = os.getenv("CREEDS_FILE_NAME", "creds.json")
    TOKENS_FILE_NAME: str = os.getenv("TOKENS_FILE_NAME", "tokens.json")
    PC_SHEET: str | None = os.getenv("PC_SHEET")
    PC_SPREADSHEET: str | None = os.getenv("PC_SPREADSHEET")


@dataclass(frozen=True)
class DBConfig:
    DB_USER: str | None = os.getenv("DB_USER")
    DB_PASSWORD: str | None = os.getenv("DB_PASSWORD")
    DB_NAME: str | None = os.getenv("DB_NAME")
    DB_HOST: str | None = os.getenv("DB_HOST")
    DB_PORT: str | None = os.getenv("DB_PORT")
    DB_POOL_MIN_SIZE: int = _env_int("DB_POOL_MIN_SIZE", 1)
    DB_POOL_MAX_SIZE: int = _env_int("DB_POOL_MAX_SIZE", 3)
    DB_CONNECT_TIMEOUT: int = _env_int("DB_CONNECT_TIMEOUT", 300)
    DB_COMMAND_TIMEOUT: int = _env_int("DB_COMMAND_TIMEOUT", 250)


settings = Settings()
database = DBConfig()
