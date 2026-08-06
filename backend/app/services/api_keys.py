from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Mapping

from app.core.config import get_settings


class ApiKeyStore:
    PROVIDERS = ("dashscope", "semantic_scholar", "ncbi", "crossref_mailto", "nasa_ads")
    SCIENCE125_ENVIRONMENT_VARIABLES: dict[str, tuple[str, ...]] = {
        "dashscope": ("DASHSCOPE_API_KEY",),
        "semantic_scholar": ("SCIENCE125_SEMANTIC_SCHOLAR_API_KEY",),
        "ncbi": ("SCIENCE125_NCBI_API_KEY",),
        "crossref_mailto": (
            "SCIENCE125_CROSSREF_MAILTO",
            "SCIENCE125_OPENALEX_MAILTO",
        ),
        "nasa_ads": ("SCIENCE125_NASA_ADS_API_TOKEN",),
    }

    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().api_key_store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def set_key(self, user_id: int, provider: str, api_key: str) -> None:
        if get_settings().is_production:
            raise RuntimeError("Plaintext API-key storage is disabled in production.")
        with self._lock:
            data = self._read()
            user_bucket = data.setdefault(str(user_id), {})
            user_bucket[provider] = api_key
            self._write(data)

    def get_key(self, user_id: int, provider: str = "dashscope") -> str | None:
        if get_settings().is_production:
            return None
        with self._lock:
            data = self._read()
            value = data.get(str(user_id), {}).get(provider)
            return value if value else None

    def configured(self, user_id: int) -> dict[str, bool]:
        if get_settings().is_production:
            return {provider: False for provider in self.PROVIDERS}
        with self._lock:
            data = self._read()
            bucket = data.get(str(user_id), {})
            return {provider: bool(bucket.get(provider)) for provider in self.PROVIDERS}

    def science125_environment(
        self,
        user_id: int,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Resolve Science 125 credentials without exposing stored values to callers."""
        resolved = dict(os.environ if environ is None else environ)
        if get_settings().is_production:
            return resolved
        with self._lock:
            bucket = self._read().get(str(user_id), {})
            for provider, variable_names in self.SCIENCE125_ENVIRONMENT_VARIABLES.items():
                value = bucket.get(provider)
                if not value:
                    continue
                for variable_name in variable_names:
                    resolved[variable_name] = value
        return resolved


api_key_store = ApiKeyStore()
