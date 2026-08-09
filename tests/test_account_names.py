import unittest

from account_names import normalize_account_mapping, normalize_account_name


class RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


class AccountNameNormalizationTest(unittest.TestCase):
    def test_normalizes_outer_regular_spaces(self):
        self.assertEqual(normalize_account_name(" СТАРТ0854 "), "Старт0854")

    def test_normalizes_outer_non_breaking_spaces(self):
        self.assertEqual(normalize_account_name("\u00a0СТАРТ0854\u00a0"), "Старт0854")

    def test_normalizes_mixed_case(self):
        self.assertEqual(normalize_account_name("СтАрТ0854"), "Старт0854")

    def test_identical_duplicate_values_are_kept_once_with_warning(self):
        logger = RecordingLogger()

        result = normalize_account_mapping(
            {"Старт0854": "token", " \u00a0СТАРТ0854 ": "token"},
            logger=logger,
            source="tokens.json",
        )

        self.assertEqual(result, {"Старт0854": "token"})
        self.assertEqual(len(logger.warnings), 1)

    def test_conflicting_duplicate_values_raise_configuration_error(self):
        with self.assertRaisesRegex(ValueError, "Конфликт WB-аккаунтов"):
            normalize_account_mapping(
                {"Старт0854": "token-a", " СТАРТ0854 ": "token-b"},
                source="tokens.json",
            )


if __name__ == "__main__":
    unittest.main()
