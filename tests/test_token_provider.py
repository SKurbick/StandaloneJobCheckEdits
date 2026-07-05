import json
import tempfile
import unittest
from pathlib import Path

from token_provider import TokenProvider


class TokenProviderTest(unittest.TestCase):
    def test_get_uses_capitalized_account_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tokens_file = Path(tmp_dir) / "tokens.json"
            tokens_file.write_text(json.dumps({"Wild1": "token-a"}), encoding="utf-8")

            provider = TokenProvider(str(tokens_file))

            self.assertEqual(provider.get("wild1"), "token-a")

    def test_tokens_are_cached_until_reload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tokens_file = Path(tmp_dir) / "tokens.json"
            tokens_file.write_text(json.dumps({"Wild1": "token-a"}), encoding="utf-8")
            provider = TokenProvider(str(tokens_file))

            self.assertEqual(provider.get("wild1"), "token-a")

            tokens_file.write_text(json.dumps({"Wild1": "token-b"}), encoding="utf-8")

            self.assertEqual(provider.get("wild1"), "token-a")
            self.assertEqual(provider.reload()["Wild1"], "token-b")
            self.assertEqual(provider.get("wild1"), "token-b")


if __name__ == "__main__":
    unittest.main()
