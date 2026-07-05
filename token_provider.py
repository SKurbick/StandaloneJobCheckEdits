"""Cached WB token access for one standalone job run."""

import json
from pathlib import Path
from typing import Any


class TokenProvider:
    def __init__(self, tokens_file_name: str):
        self.tokens_file_name = Path(tokens_file_name)
        self._tokens: dict[str, Any] | None = None

    def get_all(self) -> dict[str, Any]:
        if self._tokens is None:
            with self.tokens_file_name.open("r", encoding="utf-8") as file:
                self._tokens = json.load(file)
        return self._tokens

    def get(self, account: str) -> str:
        return self.get_all()[account.capitalize()]

    def reload(self) -> dict[str, Any]:
        self._tokens = None
        return self.get_all()
