"""Cached WB token access for one standalone job run."""

import json
from pathlib import Path
from typing import Any

try:
    from .account_names import missing_token_message, normalize_account_mapping, normalize_account_name
except ImportError:
    from account_names import missing_token_message, normalize_account_mapping, normalize_account_name


class TokenProvider:
    def __init__(self, tokens_file_name: str, logger=None):
        self.tokens_file_name = Path(tokens_file_name)
        self.logger = logger
        self._tokens: dict[str, Any] | None = None

    def get_all(self) -> dict[str, Any]:
        if self._tokens is None:
            with self.tokens_file_name.open("r", encoding="utf-8") as file:
                raw_tokens = json.load(file)
            self._tokens = normalize_account_mapping(
                raw_tokens,
                logger=self.logger,
                source=str(self.tokens_file_name),
            )
        return self._tokens

    def get(self, account: str) -> str:
        normalized_account = normalize_account_name(account)
        token = self.get_all().get(normalized_account)
        if not token:
            raise KeyError(missing_token_message(account, normalized_account))
        return token

    def get_optional(self, account: str):
        return self.get_all().get(normalize_account_name(account))

    def reload(self) -> dict[str, Any]:
        self._tokens = None
        return self.get_all()
