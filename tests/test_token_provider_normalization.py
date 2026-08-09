import json
import tempfile
import unittest
from pathlib import Path

from token_provider import TokenProvider


class TokenProviderNormalizationTest(unittest.TestCase):
    def make_provider(self, tokens):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        tokens_file = Path(temporary_directory.name) / "tokens.json"
        tokens_file.write_text(json.dumps(tokens), encoding="utf-8")
        return TokenProvider(str(tokens_file))

    def test_db_account_matches_differently_formatted_token_key(self):
        provider = self.make_provider({"\u00a0СТАРТ0854\u00a0": "token-a"})
        self.assertEqual(provider.get(" старт0854 "), "token-a")

    def test_get_optional_returns_none_for_missing_token(self):
        provider = self.make_provider({"Старт0854": "token-a"})
        self.assertIsNone(provider.get_optional("НетТокена"))

    def test_required_lookup_has_clear_error(self):
        provider = self.make_provider({"Старт0854": "token-a"})
        with self.assertRaisesRegex(KeyError, "нормализованный ключ: 'Неттокена'"):
            provider.get(" НетТокена ")

    def test_conflicting_normalized_token_keys_fail_on_load(self):
        provider = self.make_provider(
            {"Старт0854": "token-a", " СТАРТ0854 ": "token-b"}
        )
        with self.assertRaisesRegex(ValueError, "Конфликт WB-аккаунтов"):
            provider.get_all()


if __name__ == "__main__":
    unittest.main()
