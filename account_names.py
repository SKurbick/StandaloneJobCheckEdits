"""Canonical WB account keys used only for cross-source matching."""

from collections.abc import Mapping
import os
import warnings
from typing import Any


def normalize_account_name(account: str) -> str:
    """Normalize whitespace and case for a WB account dictionary key."""
    return str(account).strip().capitalize()


def is_fbs_stocks_disabled(account: str, disabled_accounts: str | None = None) -> bool:
    """Return whether FBS stock writes are disabled for an account."""
    raw_value = (
        os.getenv("FBS_STOCKS_DISABLED_ACCOUNTS", "")
        if disabled_accounts is None
        else disabled_accounts
    )
    configured_accounts = {
        normalize_account_name(item)
        for item in raw_value.split(",")
        if item.strip()
    }
    return "*" in configured_accounts or normalize_account_name(account) in configured_accounts


def normalize_account_mapping(
    mapping: Mapping[Any, Any],
    *,
    logger=None,
    source: str = "mapping",
) -> dict[str, Any]:
    """Normalize account keys and reject ambiguous duplicate configuration."""
    normalized: dict[str, Any] = {}
    original_keys: dict[str, Any] = {}

    for original_key, value in mapping.items():
        normalized_key = normalize_account_name(original_key)
        if not normalized_key:
            raise ValueError(f"Пустое имя WB-аккаунта в {source}: {original_key!r}")

        if normalized_key in normalized:
            previous_key = original_keys[normalized_key]
            if normalized[normalized_key] != value:
                raise ValueError(
                    f"Конфликт WB-аккаунтов в {source}: ключи {previous_key!r} и "
                    f"{original_key!r} нормализуются в {normalized_key!r}, но содержат "
                    "разные значения"
                )
            warning_message = (
                f"Дублирующиеся ключи WB-аккаунта в {source}: {previous_key!r} и "
                f"{original_key!r} нормализуются в {normalized_key!r}; "
                "используется одно значение"
            )
            if logger is not None:
                logger.warning(warning_message)
            else:
                warnings.warn(warning_message, RuntimeWarning, stacklevel=2)
            continue

        normalized[normalized_key] = value
        original_keys[normalized_key] = original_key

    return normalized


def missing_token_message(account: Any, normalized_account: str) -> str:
    return (
        f"Не найден токен WB для аккаунта {account!r} "
        f"(нормализованный ключ: {normalized_account!r})"
    )
