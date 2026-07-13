from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class ProductionConfigurationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def test_production_rejects_insecure_session_and_cors_settings(self) -> None:
        from app.core.config import get_settings

        insecure_cases = [
            {
                "CHALK_SESSION_SECRET": "chalk-web-local-dev-secret",
                "CHALK_SECURE_COOKIES": "true",
                "CHALK_CORS_ORIGINS": "https://chalk.example.com",
            },
            {
                "CHALK_SESSION_SECRET": "x" * 48,
                "CHALK_SECURE_COOKIES": "false",
                "CHALK_CORS_ORIGINS": "https://chalk.example.com",
            },
            {
                "CHALK_SESSION_SECRET": "x" * 48,
                "CHALK_SECURE_COOKIES": "true",
                "CHALK_CORS_ORIGINS": "*",
            },
            {
                "CHALK_SESSION_SECRET": "x" * 48,
                "CHALK_SECURE_COOKIES": "true",
                "CHALK_CORS_ORIGINS": "http://chalk.example.com",
            },
        ]

        for case in insecure_cases:
            with self.subTest(case=case), patch.dict(
                os.environ,
                {"CHALK_WEB_ENV": "production", **case},
                clear=False,
            ):
                get_settings.cache_clear()
                with self.assertRaises(ValueError):
                    get_settings()

    def test_production_accepts_explicit_https_configuration(self) -> None:
        from app.core.config import get_settings

        with patch.dict(
            os.environ,
            {
                "CHALK_WEB_ENV": "production",
                "CHALK_SESSION_SECRET": "x" * 48,
                "CHALK_SECURE_COOKIES": "true",
                "CHALK_CORS_ORIGINS": "https://chalk.example.com",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertTrue(settings.is_production)

    def test_production_disables_openapi_and_docs(self) -> None:
        from app.core.config import get_settings

        with patch.dict(
            os.environ,
            {
                "CHALK_WEB_ENV": "production",
                "CHALK_SESSION_SECRET": "x" * 48,
                "CHALK_SECURE_COOKIES": "true",
                "CHALK_CORS_ORIGINS": "https://chalk.example.com",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            from app.main import create_app

            app = create_app()

        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.openapi_url)


if __name__ == "__main__":
    unittest.main()
