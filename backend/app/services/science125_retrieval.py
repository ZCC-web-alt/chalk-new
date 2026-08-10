"""Science 125 literature provider registry and rate-governed retrieval.

This module is deliberately independent from the legacy literature search
implementation.  It describes approved providers, applies their documented
request policies, and accepts an injected transport/adapter for actual I/O.
Importing it never performs a network request.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

import requests
from requests import exceptions as requests_exceptions
from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


RateLimitMode = Literal["fixed_interval", "token_bucket", "header_driven"]
PolicySource = Literal["official", "local_safety"]

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_TOTAL_ATTEMPTS = 4
DEFAULT_RETRY_AFTER_MS = 5_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted and value is not None:
            return str(value).strip()
    return None


def _parse_retry_after(value: Any, now: datetime) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed.astimezone(UTC) - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderRatePolicy:
    """A provider's documented policy plus a conservative local floor."""

    mode: RateLimitMode
    policy_source_url: str
    policy_checked_at: str
    min_interval_ms: int
    max_concurrency: int = 1
    burst: int = 1
    retry_after_header: str = "Retry-After"
    quota_headers: tuple[str, ...] = ()
    license_policy: str = "provider terms and source license apply"
    policy_source: PolicySource = "official"
    official_min_interval_ms: int | None = None
    fallback_retry_after_ms: int = DEFAULT_RETRY_AFTER_MS
    policy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.mode not in {"fixed_interval", "token_bucket", "header_driven"}:
            raise ValueError("Unsupported provider rate-limit mode.")
        if not self.policy_source_url.startswith("https://"):
            raise ValueError("Provider policy source must use HTTPS.")
        if self.min_interval_ms < 0:
            raise ValueError("min_interval_ms must be non-negative.")
        if self.max_concurrency < 1 or self.burst < 1:
            raise ValueError("Provider concurrency and burst must be positive.")
        if self.fallback_retry_after_ms < 0:
            raise ValueError("fallback_retry_after_ms must be non-negative.")
        payload = {
            "mode": self.mode,
            "policySourceUrl": self.policy_source_url,
            "policyCheckedAt": self.policy_checked_at,
            "minIntervalMs": self.min_interval_ms,
            "maxConcurrency": self.max_concurrency,
            "burst": self.burst,
            "retryAfterHeader": self.retry_after_header,
            "quotaHeaders": self.quota_headers,
            "licensePolicy": self.license_policy,
            "policySource": self.policy_source,
            "officialMinIntervalMs": self.official_min_interval_ms,
            "fallbackRetryAfterMs": self.fallback_retry_after_ms,
        }
        object.__setattr__(self, "policy_hash", _hash_text(_canonical_json(payload)))

    @property
    def is_fixed_official_limit(self) -> bool:
        return self.official_min_interval_ms is not None and self.policy_source == "official"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "policySourceUrl": self.policy_source_url,
            "policyCheckedAt": self.policy_checked_at,
            "policyHash": self.policy_hash,
            "minIntervalMs": self.min_interval_ms,
            "maxConcurrency": self.max_concurrency,
            "burst": self.burst,
            "retryAfterHeader": self.retry_after_header,
            "quotaHeaders": list(self.quota_headers),
            "licensePolicy": self.license_policy,
            "policySource": self.policy_source,
            "officialMinIntervalMs": self.official_min_interval_ms,
        }


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider_id: str
    base_url: str
    docs_url: str
    domains: tuple[str, ...]
    family: str
    policy: ProviderRatePolicy
    required_env_vars: tuple[str, ...] = ()
    optional_env_vars: tuple[str, ...] = ()
    required_eligible: bool = False
    capabilities: tuple[str, ...] = ("metadata",)

    def __post_init__(self) -> None:
        if not self.provider_id or not self.base_url.startswith("https://"):
            raise ValueError("Provider identifiers and HTTPS base URLs are required.")
        if not self.docs_url.startswith("https://"):
            raise ValueError("Provider documentation URL must use HTTPS.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "baseUrl": self.base_url,
            "docsUrl": self.docs_url,
            "domains": list(self.domains),
            "family": self.family,
            "requiredEnvVars": list(self.required_env_vars),
            "optionalEnvVars": list(self.optional_env_vars),
            "requiredEligible": self.required_eligible,
            "capabilities": list(self.capabilities),
            "policy": self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    definition: ProviderDefinition
    policy: ProviderRatePolicy
    credential_scope: str
    missing_configuration_codes: tuple[str, ...] = ()

    @property
    def provider_id(self) -> str:
        return self.definition.provider_id

    @property
    def ready(self) -> bool:
        return not self.missing_configuration_codes


@dataclass(frozen=True, slots=True)
class ProviderReadiness:
    provider_id: str
    ready: bool
    credential_scope: str
    missing_configuration_codes: tuple[str, ...] = ()
    can_be_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "ready": self.ready,
            "credentialScope": self.credential_scope,
            "missingConfigurationCodes": list(self.missing_configuration_codes),
            "canBeRequired": self.can_be_required,
        }


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    profile_id: str
    primary: tuple[str, ...]
    conditional: tuple[str, ...] = ()
    fallback: tuple[str, ...] = ()
    max_providers: int = 4
    min_accepted_evidence: int = 3
    min_provider_families: int = 2
    required_providers: tuple[str, ...] = ()
    required_env_vars: tuple[str, ...] = ()
    query_adapter: str = "default"
    cache_ttl_seconds: int = 24 * 60 * 60

    def __post_init__(self) -> None:
        all_ids = (*self.primary, *self.conditional, *self.fallback)
        if len(set(all_ids)) != len(all_ids):
            raise ValueError(f"Duplicate provider in retrieval profile {self.profile_id}.")
        if self.max_providers < 1 or self.min_accepted_evidence < 1 or self.min_provider_families < 1:
            raise ValueError("Retrieval profile limits must be positive.")
        if self.cache_ttl_seconds < 60:
            raise ValueError("Retrieval cache TTL must be at least 60 seconds.")

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return (*self.primary, *self.conditional, *self.fallback)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "primary": list(self.primary),
            "conditional": list(self.conditional),
            "fallback": list(self.fallback),
            "maxProviders": self.max_providers,
            "minAcceptedEvidence": self.min_accepted_evidence,
            "minProviderFamilies": self.min_provider_families,
            "requiredProviders": list(self.required_providers),
            "requiredEnvVars": list(self.required_env_vars),
            "queryAdapter": self.query_adapter,
            "cacheTtlSeconds": self.cache_ttl_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProfileReadiness:
    profile_id: str
    ready: bool
    providers: tuple[ProviderReadiness, ...]
    missing_configuration_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "ready": self.ready,
            "providers": [provider.to_dict() for provider in self.providers],
            "missingConfigurationCodes": list(self.missing_configuration_codes),
        }


@dataclass(frozen=True, slots=True)
class ProviderRateState:
    provider_id: str
    credential_scope: str
    last_request_at: datetime | None
    cooldown_until: datetime | None
    remaining_quota: int | None
    policy_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredRetrievalCache:
    profile_id: str
    query_hash: str
    window_key: str
    user_scope: str
    credential_scope: str
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime


class RateStateBase(DeclarativeBase):
    pass


class ProviderRateStateRow(RateStateBase):
    __tablename__ = "science125_provider_rate_state"

    provider_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    credential_scope: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalCacheRow(RateStateBase):
    __tablename__ = "science125_retrieval_cache"

    profile_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    credential_scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Locks are process-local; SQLite transactions still make each update atomic.
_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()
_LIMITER_SEMAPHORES: dict[tuple[str, str, str], threading.BoundedSemaphore] = {}
_LIMITER_SEMAPHORES_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.RLock())


class ProviderRateStateStore:
    """Durable provider cooldown and reservation state in the Web SQLite DB."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        RateStateBase.metadata.create_all(self.engine)
        self._lock = _path_lock(self.path)

    @staticmethod
    def _stored(row: ProviderRateStateRow | None) -> ProviderRateState | None:
        if row is None:
            return None
        return ProviderRateState(
            provider_id=row.provider_id,
            credential_scope=row.credential_scope,
            last_request_at=_as_utc(row.last_request_at),
            cooldown_until=_as_utc(row.cooldown_until),
            remaining_quota=row.remaining_quota,
            policy_hash=row.policy_hash,
            updated_at=_as_utc(row.updated_at) or _utc_now(),
        )

    def get(self, provider_id: str, credential_scope: str) -> ProviderRateState | None:
        with self.session_factory() as session:
            row = session.get(ProviderRateStateRow, (provider_id, credential_scope))
            return self._stored(row)

    @staticmethod
    def _stored_cache(row: RetrievalCacheRow | None) -> StoredRetrievalCache | None:
        if row is None:
            return None
        try:
            payload = json.loads(row.result_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return StoredRetrievalCache(
            profile_id=row.profile_id,
            query_hash=row.query_hash,
            window_key=row.window_key,
            user_scope=row.user_scope,
            credential_scope=row.credential_scope,
            payload=payload,
            created_at=_as_utc(row.created_at) or _utc_now(),
            expires_at=_as_utc(row.expires_at) or _utc_now(),
        )

    def get_cached(
        self,
        *,
        profile_id: str,
        query_hash: str,
        window_key: str,
        user_scope: str,
        credential_scope: str,
        now: datetime,
    ) -> StoredRetrievalCache | None:
        observed_at = _as_utc(now) or _utc_now()
        with self._lock:
            with self.session_factory() as session:
                row = session.get(
                    RetrievalCacheRow,
                    (profile_id, query_hash, window_key, user_scope, credential_scope),
                )
                if row is None:
                    return None
                expires_at = _as_utc(row.expires_at)
                if expires_at is None or expires_at <= observed_at:
                    session.delete(row)
                    session.commit()
                    return None
                cached = self._stored_cache(row)
                if cached is None:
                    session.delete(row)
                    session.commit()
                    return None
                return cached

    def put_cached(
        self,
        *,
        profile_id: str,
        query_hash: str,
        window_key: str,
        user_scope: str,
        credential_scope: str,
        payload: Mapping[str, Any],
        now: datetime,
        ttl_seconds: int,
    ) -> None:
        observed_at = _as_utc(now) or _utc_now()
        expires_at = observed_at + timedelta(seconds=max(60, int(ttl_seconds)))
        serialized = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock:
            with self.session_factory() as session:
                row = session.get(
                    RetrievalCacheRow,
                    (profile_id, query_hash, window_key, user_scope, credential_scope),
                )
                if row is None:
                    row = RetrievalCacheRow(
                        profile_id=profile_id,
                        query_hash=query_hash,
                        window_key=window_key,
                        user_scope=user_scope,
                        credential_scope=credential_scope,
                        result_json=serialized,
                        created_at=observed_at,
                        expires_at=expires_at,
                    )
                    session.add(row)
                else:
                    row.result_json = serialized
                    row.created_at = observed_at
                    row.expires_at = expires_at
                session.commit()

    def delete_cached(
        self,
        *,
        profile_id: str,
        query_hash: str,
        window_key: str,
        user_scope: str,
        credential_scope: str,
    ) -> None:
        with self._lock:
            with self.session_factory() as session:
                row = session.get(
                    RetrievalCacheRow,
                    (profile_id, query_hash, window_key, user_scope, credential_scope),
                )
                if row is not None:
                    session.delete(row)
                    session.commit()

    def reserve(
        self,
        provider: ResolvedProvider,
        *,
        now: datetime,
    ) -> float:
        """Reserve a request slot and return seconds to wait before using it."""
        now = _as_utc(now) or _utc_now()
        with self._lock:
            with self.session_factory() as session:
                row = session.get(ProviderRateStateRow, (provider.provider_id, provider.credential_scope))
                if row is None:
                    row = ProviderRateStateRow(
                        provider_id=provider.provider_id,
                        credential_scope=provider.credential_scope,
                        last_request_at=None,
                        cooldown_until=None,
                        remaining_quota=None,
                        policy_hash=provider.policy.policy_hash,
                        updated_at=now,
                    )
                    session.add(row)
                    session.flush()
                last = _as_utc(row.last_request_at)
                cooldown = _as_utc(row.cooldown_until)
                earliest = now
                interval = timedelta(milliseconds=provider.policy.min_interval_ms)
                if last is not None:
                    earliest = max(earliest, last + interval)
                if cooldown is not None:
                    earliest = max(earliest, cooldown)
                row.last_request_at = earliest
                row.policy_hash = provider.policy.policy_hash
                row.updated_at = now
                session.commit()
                return max(0.0, (earliest - now).total_seconds())

    def apply_cooldown(
        self,
        provider: ResolvedProvider,
        *,
        until: datetime,
        remaining_quota: int | None = None,
        now: datetime | None = None,
    ) -> ProviderRateState:
        until = _as_utc(until) or _utc_now()
        observed_at = _as_utc(now) or _utc_now()
        with self._lock:
            with self.session_factory() as session:
                row = session.get(ProviderRateStateRow, (provider.provider_id, provider.credential_scope))
                if row is None:
                    row = ProviderRateStateRow(
                        provider_id=provider.provider_id,
                        credential_scope=provider.credential_scope,
                        last_request_at=None,
                        cooldown_until=until,
                        remaining_quota=remaining_quota,
                        policy_hash=provider.policy.policy_hash,
                        updated_at=observed_at,
                    )
                    session.add(row)
                else:
                    current = _as_utc(row.cooldown_until)
                    row.cooldown_until = max(current or until, until)
                    if remaining_quota is not None:
                        row.remaining_quota = remaining_quota
                    row.policy_hash = provider.policy.policy_hash
                    row.updated_at = observed_at
                session.commit()
                return self._stored(row)  # type: ignore[return-value]

    def mark_request_started(self, provider: ResolvedProvider, *, observed_at: datetime) -> None:
        """Record an oversleep-adjusted start time without moving reservations backward."""
        observed_at = _as_utc(observed_at) or _utc_now()
        with self._lock:
            with self.session_factory() as session:
                row = session.get(ProviderRateStateRow, (provider.provider_id, provider.credential_scope))
                if row is None:
                    return
                reserved = _as_utc(row.last_request_at)
                if reserved is None or observed_at > reserved:
                    row.last_request_at = observed_at
                row.updated_at = observed_at
                session.commit()

    def record_response(
        self,
        provider: ResolvedProvider,
        *,
        status_code: int | None,
        headers: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ProviderRateState:
        now = _as_utc(now) or _utc_now()
        headers = headers or {}
        retry_delay = _parse_retry_after(_header(headers, provider.policy.retry_after_header), now)
        remaining = _header_int(headers, provider.policy.quota_headers, contains="remaining")
        reset = _header_int(headers, provider.policy.quota_headers, contains="reset")
        if status_code == 429:
            if retry_delay is None:
                retry_delay = provider.policy.fallback_retry_after_ms / 1000.0
            return self.apply_cooldown(
                provider,
                until=now + timedelta(seconds=max(0.0, retry_delay)),
                remaining_quota=remaining,
                now=now,
            )
        if remaining == 0 and reset is not None:
            # APIs vary between epoch seconds and relative seconds.
            reset_delay = max(0.0, reset - now.timestamp()) if reset > now.timestamp() else float(reset)
            return self.apply_cooldown(
                provider,
                until=now + timedelta(seconds=reset_delay),
                remaining_quota=remaining,
                now=now,
            )
        with self._lock:
            with self.session_factory() as session:
                row = session.get(ProviderRateStateRow, (provider.provider_id, provider.credential_scope))
                if row is None:
                    row = ProviderRateStateRow(
                        provider_id=provider.provider_id,
                        credential_scope=provider.credential_scope,
                        last_request_at=None,
                        cooldown_until=None,
                        remaining_quota=remaining,
                        policy_hash=provider.policy.policy_hash,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    if remaining is not None:
                        row.remaining_quota = remaining
                    row.policy_hash = provider.policy.policy_hash
                    row.updated_at = now
                session.commit()
                return self._stored(row)  # type: ignore[return-value]

    def dispose(self) -> None:
        self.engine.dispose()


def _header_int(headers: Mapping[str, Any], names: Sequence[str], *, contains: str) -> int | None:
    for name in names:
        if contains.casefold() not in name.casefold():
            continue
        value = _safe_int(_header(headers, name))
        if value is not None:
            return value
    # Accept dynamically named X-RateLimit-* headers even when not registered.
    for key, raw in headers.items():
        if contains.casefold() in str(key).casefold():
            value = _safe_int(raw)
            if value is not None:
                return value
    return None


class ProviderRateLimiter:
    def __init__(
        self,
        provider: ResolvedProvider,
        store: ProviderRateStateStore,
        *,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.store = store
        self.now = now
        self.sleep = sleep
        key = (str(store.path), provider.provider_id, provider.credential_scope)
        with _LIMITER_SEMAPHORES_GUARD:
            self._semaphore = _LIMITER_SEMAPHORES.setdefault(
                key,
                threading.BoundedSemaphore(provider.policy.max_concurrency),
            )

    def acquire(self) -> float:
        self._semaphore.acquire()
        try:
            delay = self.store.reserve(self.provider, now=self.now())
            if delay > 0:
                self.sleep(delay)
            self.store.mark_request_started(self.provider, observed_at=self.now())
            return delay
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()

    def __enter__(self) -> "ProviderRateLimiter":
        self.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    provider: str
    stable_id: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str = ""
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    ads_id: str | None = None
    full_text_url: str | None = None
    access_status: str = "metadata"
    license: str | None = None
    retrieved_at: datetime = field(default_factory=_utc_now)
    query_hash: str = ""
    warning: str | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.stable_id or not self.title:
            raise ValueError("EvidenceRecord requires provider, stable_id, and title.")
        if len(self.title) > 2_000 or len(self.abstract) > 100_000:
            raise ValueError("EvidenceRecord text exceeds the supported limit.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "stableId": self.stable_id,
            "title": self.title,
            "authors": list(self.authors),
            "abstract": self.abstract,
            "doi": self.doi,
            "pmid": self.pmid,
            "arxivId": self.arxiv_id,
            "adsId": self.ads_id,
            "fullTextUrl": self.full_text_url,
            "accessStatus": self.access_status,
            "license": self.license,
            "retrievedAt": self.retrieved_at.isoformat(),
            "queryHash": self.query_hash,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class ProviderSearchResponse:
    status_code: int
    headers: Mapping[str, Any] = field(default_factory=dict)
    records: tuple[EvidenceRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    provider: str
    status: str
    status_code: int | None = None
    attempts: int = 0
    message: str | None = None
    missing_configuration_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "statusCode": self.status_code,
            "attempts": self.attempts,
            "message": self.message,
            "missingConfigurationCodes": list(self.missing_configuration_codes),
        }


@dataclass(frozen=True, slots=True)
class ProviderRequestResult:
    response: Any
    attempts: int
    retry_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Science125SearchResult:
    profile_id: str
    query_hash: str
    cache_key: str
    evidence: tuple[EvidenceRecord, ...]
    diagnostics: tuple[ProviderDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "queryHash": self.query_hash,
            "cacheKey": self.cache_key,
            "evidence": [record.to_dict() for record in self.evidence],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


class ProviderNotReadyError(RuntimeError):
    def __init__(self, provider_id: str, codes: Sequence[str]):
        self.provider_id = provider_id
        self.codes = tuple(codes)
        super().__init__(f"Provider {provider_id} is not ready: {', '.join(self.codes)}")


class RetrievalRequestError(RuntimeError):
    def __init__(self, provider_id: str, cause: BaseException, attempts: int):
        self.provider_id = provider_id
        self.cause = cause
        self.attempts = attempts
        super().__init__(f"Retrieval request for {provider_id} failed after {attempts} attempt(s): {cause}")


class ProviderAdapter(Protocol):
    def __call__(self, provider: ProviderDefinition, query: str) -> ProviderSearchResponse: ...


class Science125RetrievalClient:
    """Execute injected provider calls under durable rate and retry policy."""

    def __init__(
        self,
        *,
        store: ProviderRateStateStore,
        environ: Mapping[str, str] | None = None,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ):
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)
        self.now = now
        self.sleep = sleep
        self.random_uniform = random_uniform
        self._limiters: dict[tuple[str, str], ProviderRateLimiter] = {}

    def _limiter(self, provider: ResolvedProvider) -> ProviderRateLimiter:
        key = (provider.provider_id, provider.credential_scope)
        limiter = self._limiters.get(key)
        if limiter is None:
            limiter = ProviderRateLimiter(provider, self.store, now=self.now, sleep=self.sleep)
            self._limiters[key] = limiter
        return limiter

    def request_result(
        self,
        provider_id: str,
        transport: Callable[[], Any],
        *,
        max_attempts: int = MAX_TOTAL_ATTEMPTS,
    ) -> ProviderRequestResult:
        provider = resolve_provider(provider_id, environ=self.environ)
        if not provider.ready:
            raise ProviderNotReadyError(provider_id, provider.missing_configuration_codes)
        attempts_allowed = min(MAX_TOTAL_ATTEMPTS, max(1, int(max_attempts)))
        limiter = self._limiter(provider)
        retry_reasons: list[str] = []
        last_error: BaseException | None = None
        for attempt in range(1, attempts_allowed + 1):
            limiter.acquire()
            try:
                try:
                    response = transport()
                except (TimeoutError, ConnectionError, requests_exceptions.Timeout, requests_exceptions.ConnectionError) as exc:
                    last_error = exc
                    reason = f"transport_{type(exc).__name__.lower()}"
                    retry_reasons.append(reason)
                    self.store.apply_cooldown(
                        provider,
                        until=self.now() + timedelta(seconds=self._backoff_seconds(attempt)),
                        now=self.now(),
                    )
                    if attempt >= attempts_allowed:
                        raise RetrievalRequestError(provider_id, exc, attempt) from exc
                    continue
                status_code = int(getattr(response, "status_code", 0) or 0)
                headers = getattr(response, "headers", {}) or {}
                self.store.record_response(provider, status_code=status_code, headers=headers, now=self.now())
                if status_code in RETRYABLE_STATUS_CODES and attempt < attempts_allowed:
                    reason = f"http_{status_code}"
                    retry_reasons.append(reason)
                    if status_code != 429:
                        self.store.apply_cooldown(
                            provider,
                            until=self.now() + timedelta(seconds=self._backoff_seconds(attempt)),
                            now=self.now(),
                        )
                    continue
                return ProviderRequestResult(response=response, attempts=attempt, retry_reasons=tuple(retry_reasons))
            finally:
                limiter.release()
        if last_error is not None:
            raise RetrievalRequestError(provider_id, last_error, attempts_allowed) from last_error
        raise RetrievalRequestError(provider_id, RuntimeError("provider request ended without a response"), attempts_allowed)

    def request(self, provider_id: str, transport: Callable[[], Any], *, max_attempts: int = MAX_TOTAL_ATTEMPTS) -> Any:
        return self.request_result(provider_id, transport, max_attempts=max_attempts).response

    def _backoff_seconds(self, attempt: int) -> float:
        ceiling = min(60.0, float(2 ** max(0, attempt - 1)))
        return max(0.0, self.random_uniform(0.0, ceiling))


def retrieval_cache_key(
    profile_id: str,
    query: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    window: str = "default",
) -> str:
    normalized_query = unicodedata.normalize("NFC", " ".join(str(query).split())).casefold()
    payload = {
        "profile": str(profile_id).strip(),
        "query": normalized_query,
        "parameters": parameters or {},
        "window": str(window),
    }
    return _hash_text(_canonical_json(payload))


def query_hash(query: str) -> str:
    normalized = unicodedata.normalize("NFC", " ".join(str(query).split())).casefold()
    return _hash_text(normalized)


def _cache_scope(prefix: str, value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "anonymous").strip() or "anonymous")
    return f"{prefix}:{_hash_text(text)[:40]}"


def _combined_credential_scope(
    provider_ids: Sequence[str],
    environ: Mapping[str, str],
    explicit_scope: str | None,
) -> str:
    components = [str(explicit_scope or "derived")]
    for provider_id in provider_ids:
        resolved = resolve_provider(provider_id, environ=environ)
        components.append(
            f"{provider_id}:{resolved.credential_scope}:{resolved.policy.policy_hash}"
        )
    return _cache_scope("credential", "|".join(components))


def _cache_window_key(window: str, parameters: Mapping[str, Any] | None) -> str:
    return _hash_text(_canonical_json({"window": str(window), "parameters": parameters or {}}))


def _parse_cached_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("Invalid cached retrieval timestamp.") from None
    return _as_utc(parsed) or _utc_now()


def _search_result_from_payload(payload: Mapping[str, Any]) -> Science125SearchResult:
    raw_evidence = payload.get("evidence")
    raw_diagnostics = payload.get("diagnostics")
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
        raise ValueError("Invalid cached evidence payload.")
    if not isinstance(raw_diagnostics, Sequence) or isinstance(raw_diagnostics, (str, bytes)):
        raise ValueError("Invalid cached diagnostics payload.")
    evidence: list[EvidenceRecord] = []
    for item in raw_evidence:
        if not isinstance(item, Mapping):
            raise ValueError("Invalid cached evidence record.")
        authors = item.get("authors", [])
        evidence.append(EvidenceRecord(
            provider=str(item.get("provider") or ""),
            stable_id=str(item.get("stableId") or ""),
            title=str(item.get("title") or ""),
            authors=tuple(str(author) for author in authors)
            if isinstance(authors, Sequence) and not isinstance(authors, (str, bytes))
            else (),
            abstract=str(item.get("abstract") or ""),
            doi=str(item.get("doi") or "").strip() or None,
            pmid=str(item.get("pmid") or "").strip() or None,
            arxiv_id=str(item.get("arxivId") or "").strip() or None,
            ads_id=str(item.get("adsId") or "").strip() or None,
            full_text_url=str(item.get("fullTextUrl") or "").strip() or None,
            access_status=str(item.get("accessStatus") or "metadata"),
            license=str(item.get("license") or "").strip() or None,
            retrieved_at=_parse_cached_datetime(item.get("retrievedAt")),
            query_hash=str(item.get("queryHash") or ""),
            warning=str(item.get("warning") or "").strip() or None,
        ))
    diagnostics: list[ProviderDiagnostic] = []
    for item in raw_diagnostics:
        if not isinstance(item, Mapping):
            raise ValueError("Invalid cached provider diagnostic.")
        missing = item.get("missingConfigurationCodes", [])
        diagnostics.append(ProviderDiagnostic(
            provider=str(item.get("provider") or ""),
            status=str(item.get("status") or ""),
            status_code=_safe_int(item.get("statusCode")),
            attempts=max(0, _safe_int(item.get("attempts")) or 0),
            message=str(item.get("message") or "").strip() or None,
            missing_configuration_codes=tuple(str(code) for code in missing)
            if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes))
            else (),
        ))
    return Science125SearchResult(
        profile_id=str(payload.get("profileId") or ""),
        query_hash=str(payload.get("queryHash") or ""),
        cache_key=str(payload.get("cacheKey") or ""),
        evidence=tuple(evidence),
        diagnostics=tuple(diagnostics),
    )


def _credential_scope(provider: ProviderDefinition, environ: Mapping[str, str]) -> str:
    secret = next(
        (
            str(environ.get(name, "")).strip()
            for name in (*provider.required_env_vars, *provider.optional_env_vars)
            if "EMAIL" not in name and "MAILTO" not in name and str(environ.get(name, "")).strip()
        ),
        "",
    )
    if not secret:
        return "anonymous"
    return f"key:{_hash_text(secret)[:16]}"


def _missing_codes(provider: ProviderDefinition, environ: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        f"MISSING_{name}"
        for name in provider.required_env_vars
        if not str(environ.get(name, "")).strip()
    )


def _provider(
    provider_id: str,
    *,
    base_url: str,
    docs_url: str,
    domains: tuple[str, ...],
    family: str,
    policy: ProviderRatePolicy,
    required_env_vars: tuple[str, ...] = (),
    optional_env_vars: tuple[str, ...] = (),
    required_eligible: bool = False,
    capabilities: tuple[str, ...] = ("metadata",),
) -> ProviderDefinition:
    return ProviderDefinition(
        provider_id=provider_id,
        base_url=base_url,
        docs_url=docs_url,
        domains=domains,
        family=family,
        policy=policy,
        required_env_vars=required_env_vars,
        optional_env_vars=optional_env_vars,
        required_eligible=required_eligible,
        capabilities=capabilities,
    )


POLICY_DATE = "2026-07-20"


def _official_policy(
    *,
    mode: RateLimitMode,
    source: str,
    min_interval_ms: int,
    official_min_interval_ms: int | None,
    fallback_retry_after_ms: int = DEFAULT_RETRY_AFTER_MS,
    quota_headers: tuple[str, ...] = (
        "X-RateLimit-Remaining",
        "X-Rate-Limit-Remaining",
        "RateLimit-Remaining",
        "X-RateLimit-Reset",
        "X-Rate-Limit-Reset",
        "RateLimit-Reset",
    ),
    policy_source: PolicySource = "official",
) -> ProviderRatePolicy:
    return ProviderRatePolicy(
        mode=mode,
        policy_source_url=source,
        policy_checked_at=POLICY_DATE,
        min_interval_ms=min_interval_ms,
        max_concurrency=1,
        burst=1,
        quota_headers=quota_headers,
        official_min_interval_ms=official_min_interval_ms,
        fallback_retry_after_ms=fallback_retry_after_ms,
        policy_source=policy_source,
    )


_ARXIV_POLICY = _official_policy(
    mode="fixed_interval",
    source="https://info.arxiv.org/help/api/user-manual.html",
    min_interval_ms=3_000,
    official_min_interval_ms=3_000,
    fallback_retry_after_ms=3_000,
)
_NCBI_POLICY_ANON = _official_policy(
    mode="token_bucket",
    source="https://www.ncbi.nlm.nih.gov/books/NBK25497/",
    min_interval_ms=334,
    official_min_interval_ms=334,
    fallback_retry_after_ms=1_000,
)
_NCBI_POLICY_KEYED = _official_policy(
    mode="token_bucket",
    source="https://www.ncbi.nlm.nih.gov/books/NBK25497/",
    min_interval_ms=100,
    official_min_interval_ms=100,
    fallback_retry_after_ms=1_000,
)
_SEMANTIC_POLICY = _official_policy(
    mode="fixed_interval",
    source="https://www.semanticscholar.org/product/api",
    min_interval_ms=1_000,
    official_min_interval_ms=1_000,
    fallback_retry_after_ms=1_000,
)
_HEADER_POLICY_SOURCES = {
    "crossref": "https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/",
    "openalex": "https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication",
    "europe_pmc": "https://europepmc.org/RestfulWebService",
    "inspire": "https://github.com/inspirehep/rest-api-doc",
    "nasa_ads": "https://ui.adsabs.harvard.edu/help/api/",
    "dblp": "https://dblp.org/faq/How+to+use+the+dblp+search+API.html",
    "gbif": "https://techdocs.gbif.org/en/openapi/v1/literature",
    "osti": "https://www.osti.gov/api",
    "doaj": "https://doaj.org/api/docs",
    "clinical_trials": "https://clinicaltrials.gov/data-api/api",
    "materials_project": "https://docs.materialsproject.org/downloading-data/using-the-api/tips-for-large-downloads",
}


def _header_policy(provider_id: str) -> ProviderRatePolicy:
    return _official_policy(
        mode="header_driven",
        source=_HEADER_POLICY_SOURCES[provider_id],
        # This is explicitly a local safety floor, not an assertion of an
        # undocumented provider quota.
        min_interval_ms=1_000,
        official_min_interval_ms=None,
        policy_source="local_safety",
        fallback_retry_after_ms=5_000,
    )


_provider_defs: dict[str, ProviderDefinition] = {
    "arxiv": _provider(
        "arxiv",
        base_url="https://export.arxiv.org/api/query",
        docs_url="https://info.arxiv.org/help/api/user-manual.html",
        domains=("mathematics", "physics", "astronomy", "ai", "information"),
        family="open_preprint",
        policy=_ARXIV_POLICY,
        required_eligible=True,
        capabilities=("metadata", "abstract", "open_full_text"),
    ),
    "ncbi": _provider(
        "ncbi",
        base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
        docs_url="https://www.ncbi.nlm.nih.gov/books/NBK25497/",
        domains=("medicine", "biology", "neuroscience", "chemistry"),
        family="biomedical_index",
        policy=_NCBI_POLICY_ANON,
        required_env_vars=("SCIENCE125_NCBI_TOOL_EMAIL",),
        optional_env_vars=("SCIENCE125_NCBI_API_KEY",),
        required_eligible=True,
        capabilities=("metadata", "abstract", "open_full_text"),
    ),
    "semantic_scholar": _provider(
        "semantic_scholar",
        base_url="https://api.semanticscholar.org/graph/v1",
        docs_url="https://www.semanticscholar.org/product/api",
        domains=("all",),
        family="scholarly_graph",
        policy=_SEMANTIC_POLICY,
        required_env_vars=("SCIENCE125_SEMANTIC_SCHOLAR_API_KEY",),
        required_eligible=True,
        capabilities=("metadata", "abstract", "citation_graph", "open_full_text"),
    ),
    "crossref": _provider(
        "crossref",
        base_url="https://api.crossref.org/works",
        docs_url=_HEADER_POLICY_SOURCES["crossref"],
        domains=("all",),
        family="doi_registry",
        policy=_header_policy("crossref"),
        optional_env_vars=("SCIENCE125_CROSSREF_MAILTO",),
    ),
    "openalex": _provider(
        "openalex",
        base_url="https://api.openalex.org/works",
        docs_url=_HEADER_POLICY_SOURCES["openalex"],
        domains=("all",),
        family="scholarly_index",
        policy=_header_policy("openalex"),
        optional_env_vars=("SCIENCE125_OPENALEX_MAILTO",),
        capabilities=("metadata", "abstract", "open_full_text"),
    ),
    "europe_pmc": _provider(
        "europe_pmc",
        base_url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        docs_url=_HEADER_POLICY_SOURCES["europe_pmc"],
        domains=("medicine", "biology", "neuroscience", "chemistry"),
        family="biomedical_index",
        policy=_header_policy("europe_pmc"),
        capabilities=("metadata", "abstract", "open_full_text"),
    ),
    "inspire": _provider(
        "inspire",
        base_url="https://inspirehep.net/api/literature",
        docs_url=_HEADER_POLICY_SOURCES["inspire"],
        domains=("physics", "astronomy"),
        family="hep_index",
        policy=_header_policy("inspire"),
    ),
    "nasa_ads": _provider(
        "nasa_ads",
        base_url="https://api.adsabs.harvard.edu/v1/search/query",
        docs_url=_HEADER_POLICY_SOURCES["nasa_ads"],
        domains=("astronomy", "physics"),
        family="astronomy_index",
        policy=_header_policy("nasa_ads"),
        required_env_vars=("SCIENCE125_NASA_ADS_API_TOKEN",),
        required_eligible=False,
        capabilities=("metadata", "abstract", "citation_graph"),
    ),
    "dblp": _provider(
        "dblp",
        base_url="https://dblp.org/search/publ/api",
        docs_url=_HEADER_POLICY_SOURCES["dblp"],
        domains=("information", "ai"),
        family="computer_science_index",
        policy=_header_policy("dblp"),
    ),
    "gbif": _provider(
        "gbif",
        base_url="https://api.gbif.org/v1/literature/search",
        docs_url=_HEADER_POLICY_SOURCES["gbif"],
        domains=("ecology", "biology"),
        family="biodiversity_index",
        policy=_header_policy("gbif"),
    ),
    "osti": _provider(
        "osti",
        base_url="https://www.osti.gov/api/v1/records",
        docs_url=_HEADER_POLICY_SOURCES["osti"],
        domains=("energy", "physics", "engineering_materials", "chemistry"),
        family="government_index",
        policy=_header_policy("osti"),
    ),
    "doaj": _provider(
        "doaj",
        base_url="https://doaj.org/api/search/articles",
        docs_url=_HEADER_POLICY_SOURCES["doaj"],
        domains=("all",),
        family="open_access_index",
        policy=_header_policy("doaj"),
        capabilities=("metadata", "abstract", "open_full_text"),
    ),
    "clinical_trials": _provider(
        "clinical_trials",
        base_url="https://clinicaltrials.gov/api/v2/studies",
        docs_url=_HEADER_POLICY_SOURCES["clinical_trials"],
        domains=("medicine", "biology", "neuroscience"),
        family="clinical_registry",
        policy=_header_policy("clinical_trials"),
        capabilities=("metadata", "study_registry"),
    ),
    "materials_project": _provider(
        "materials_project",
        base_url="https://api.materialsproject.org/materials/summary/",
        docs_url="https://docs.materialsproject.org/downloading-data/using-the-api/getting-started",
        domains=("chemistry", "engineering_materials", "energy", "physics"),
        family="materials_database",
        policy=_header_policy("materials_project"),
        required_env_vars=("MATERIALS_PROJECT_API_KEY",),
        required_eligible=False,
        capabilities=("structured_material_data",),
    ),
}

# Keep the public provider id used by the routing manifest while sharing the
# same policy and endpoint definition as the canonical GBIF entry.
_provider_defs["gbif_literature"] = replace(_provider_defs["gbif"], provider_id="gbif_literature")

PROVIDER_REGISTRY: Mapping[str, ProviderDefinition] = dict(_provider_defs)


def _profile(
    profile_id: str,
    primary: tuple[str, ...],
    *,
    conditional: tuple[str, ...] = (),
    fallback: tuple[str, ...] = (),
    required: tuple[str, ...] = (),
    required_env_vars: tuple[str, ...] = (),
    query_adapter: str = "default",
    max_providers: int = 4,
) -> RetrievalProfile:
    return RetrievalProfile(
        profile_id=profile_id,
        primary=primary,
        conditional=conditional,
        fallback=fallback,
        required_providers=required,
        required_env_vars=required_env_vars,
        query_adapter=query_adapter,
        max_providers=max_providers,
    )


RETRIEVAL_PROFILE_REGISTRY: Mapping[str, RetrievalProfile] = {
    "retrieval.mathematics.v1": _profile("retrieval.mathematics.v1", ("arxiv", "openalex", "crossref")),
    "retrieval.chemistry.v1": _profile(
        "retrieval.chemistry.v1",
        ("crossref", "openalex", "europe_pmc"),
        conditional=("materials_project", "arxiv", "osti", "semantic_scholar"),
    ),
    "retrieval.chem.interface.v1": _profile(
        "retrieval.chem.interface.v1",
        ("crossref", "openalex", "europe_pmc"),
        conditional=("semantic_scholar", "materials_project"),
        required=("semantic_scholar",),
        required_env_vars=("SCIENCE125_CROSSREF_MAILTO", "SCIENCE125_OPENALEX_MAILTO"),
        query_adapter="chem_interface_v1",
        max_providers=5,
    ),
    "retrieval.medicine.v1": _profile("retrieval.medicine.v1", ("europe_pmc", "ncbi", "crossref", "openalex"), conditional=("clinical_trials",)),
    "retrieval.biology.v1": _profile("retrieval.biology.v1", ("europe_pmc", "ncbi", "crossref", "openalex"), conditional=("clinical_trials",)),
    "retrieval.bio.genome_editing.v1": _profile(
        "retrieval.bio.genome_editing.v1",
        ("europe_pmc", "ncbi", "crossref"),
        conditional=("clinical_trials",),
        required=("ncbi",),
        required_env_vars=("SCIENCE125_NCBI_API_KEY", "SCIENCE125_CROSSREF_MAILTO"),
        query_adapter="bio_genome_editing_v1",
    ),
    "retrieval.astronomy.v1": _profile("retrieval.astronomy.v1", ("arxiv", "inspire", "openalex", "crossref")),
    "retrieval.astro.high_energy.v1": _profile(
        "retrieval.astro.high_energy.v1",
        ("arxiv", "inspire", "openalex"),
        conditional=("nasa_ads",),
        # ADS remains conditional because it publishes dynamic quota headers
        # rather than a stable numeric ceiling. Its token is still a pilot
        # readiness gate, but the provider is not treated as mandatory
        # evidence until the rate policy is manually re-reviewed.
        required_env_vars=("SCIENCE125_NASA_ADS_API_TOKEN", "SCIENCE125_OPENALEX_MAILTO"),
        query_adapter="astro_high_energy_v1",
    ),
    "retrieval.physics.v1": _profile("retrieval.physics.v1", ("arxiv", "inspire", "openalex", "crossref"), conditional=("osti",)),
    "retrieval.engineering_materials.v1": _profile("retrieval.engineering_materials.v1", ("crossref", "openalex", "osti", "arxiv")),
    "retrieval.information.v1": _profile("retrieval.information.v1", ("dblp", "arxiv", "openalex", "crossref")),
    "retrieval.information_science.v1": _profile("retrieval.information_science.v1", ("dblp", "arxiv", "openalex", "crossref")),
    "retrieval.neuroscience.v1": _profile("retrieval.neuroscience.v1", ("europe_pmc", "ncbi", "crossref", "openalex"), conditional=("clinical_trials",)),
    "retrieval.ecology.v1": _profile("retrieval.ecology.v1", ("gbif", "openalex", "crossref", "europe_pmc"), conditional=("doaj",)),
    "retrieval.energy.v1": _profile("retrieval.energy.v1", ("osti", "crossref", "openalex", "arxiv"), conditional=("doaj",)),
    "retrieval.ai.v1": _profile("retrieval.ai.v1", ("arxiv", "dblp", "openalex", "crossref"), conditional=("semantic_scholar",)),
}


def _validate_registries() -> None:
    for profile in RETRIEVAL_PROFILE_REGISTRY.values():
        unknown = set(profile.provider_ids) - set(PROVIDER_REGISTRY)
        if unknown:
            raise ValueError(f"Retrieval profile {profile.profile_id} contains unknown providers: {sorted(unknown)}")
        if not set(profile.required_providers).issubset(profile.provider_ids):
            raise ValueError(f"Retrieval profile {profile.profile_id} has an invalid required provider.")
        for provider_id in profile.required_providers:
            if not PROVIDER_REGISTRY[provider_id].required_eligible:
                raise ValueError(
                    f"Provider {provider_id} cannot be required until its official numeric policy is verified."
                )


_validate_registries()


def get_provider(provider_id: str) -> ProviderDefinition:
    try:
        return PROVIDER_REGISTRY[str(provider_id).strip()]
    except KeyError:
        raise KeyError(f"Unknown Science 125 retrieval provider: {provider_id}") from None


def resolve_provider(provider_id: str, *, environ: Mapping[str, str] | None = None) -> ResolvedProvider:
    definition = get_provider(provider_id)
    env = dict(os.environ if environ is None else environ)
    policy = definition.policy
    # NCBI documents separate anonymous and API-key request ceilings.
    if definition.provider_id == "ncbi" and str(env.get("SCIENCE125_NCBI_API_KEY", "")).strip():
        policy = _NCBI_POLICY_KEYED
    return ResolvedProvider(
        definition=definition,
        policy=policy,
        credential_scope=_credential_scope(definition, env),
        missing_configuration_codes=_missing_codes(definition, env),
    )


def provider_readiness(provider_id: str, *, environ: Mapping[str, str] | None = None) -> ProviderReadiness:
    resolved = resolve_provider(provider_id, environ=environ)
    return ProviderReadiness(
        provider_id=provider_id,
        ready=resolved.ready,
        credential_scope=resolved.credential_scope,
        missing_configuration_codes=resolved.missing_configuration_codes,
        can_be_required=resolved.definition.required_eligible,
    )


def get_retrieval_profile(profile_id: str) -> RetrievalProfile:
    try:
        return RETRIEVAL_PROFILE_REGISTRY[str(profile_id).strip()]
    except KeyError:
        raise KeyError(f"Unknown Science 125 retrieval profile: {profile_id}") from None


def get_science125_retrieval_profile(profile_id: str) -> RetrievalProfile:
    return get_retrieval_profile(profile_id)


def profile_readiness(profile_id: str, *, environ: Mapping[str, str] | None = None) -> ProfileReadiness:
    profile = get_retrieval_profile(profile_id)
    env = dict(os.environ if environ is None else environ)
    providers = tuple(provider_readiness(provider_id, environ=environ) for provider_id in profile.provider_ids)
    missing: list[str] = [
        f"MISSING_{name}"
        for name in profile.required_env_vars
        if not str(env.get(name, "")).strip()
    ]
    for provider_id in profile.required_providers:
        readiness = next(item for item in providers if item.provider_id == provider_id)
        missing.extend(readiness.missing_configuration_codes)
    return ProfileReadiness(
        profile_id=profile_id,
        ready=not missing,
        providers=providers,
        missing_configuration_codes=tuple(dict.fromkeys(missing)),
    )


def _deduplicate_records(records: Iterable[EvidenceRecord]) -> tuple[EvidenceRecord, ...]:
    seen: set[str] = set()
    output: list[EvidenceRecord] = []
    for record in records:
        identity = (record.doi or record.pmid or record.arxiv_id or record.ads_id or record.stable_id or record.title).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        output.append(record)
    return tuple(output)


def _response_headers(response: Any) -> Mapping[str, Any]:
    headers = getattr(response, "headers", {}) or {}
    return headers if isinstance(headers, Mapping) else {}


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _response_json(response: Any) -> Mapping[str, Any]:
    payload = response.json() if callable(getattr(response, "json", None)) else {}
    return payload if isinstance(payload, Mapping) else {}


def _strip_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", " ", text).strip()


def _author_names(items: Any) -> tuple[str, ...]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return ()
    names: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            given = str(item.get("given") or item.get("firstName") or "").strip()
            family = str(item.get("family") or item.get("lastName") or item.get("name") or "").strip()
            name = " ".join(part for part in (given, family) if part)
        else:
            name = str(item).strip()
        if name:
            names.append(name)
    return tuple(names)


def _crossref_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        params: dict[str, Any] = {"query.bibliographic": query, "rows": 20}
        mailto = str(environ.get("SCIENCE125_CROSSREF_MAILTO", "")).strip()
        if mailto:
            params["mailto"] = mailto
        response = session.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": "ChalkScience125/1.0"},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        message = _response_json(response).get("message", {})
        items = message.get("items", []) if isinstance(message, Mapping) else []
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            doi = str(item.get("DOI") or "").strip() or None
            title_raw = item.get("title", "")
            title = _strip_markup(title_raw[0] if isinstance(title_raw, Sequence) and not isinstance(title_raw, (str, bytes)) and title_raw else title_raw)
            if not title:
                continue
            stable_id = f"doi:{doi}" if doi else f"crossref:{_hash_text(title)[:24]}"
            raw_licenses = item.get("license")
            license_url = None
            if (
                isinstance(raw_licenses, Sequence)
                and not isinstance(raw_licenses, (str, bytes))
                and raw_licenses
                and isinstance(raw_licenses[0], Mapping)
            ):
                license_url = str(raw_licenses[0].get("URL") or "").strip() or None
            records.append(
                EvidenceRecord(
                    provider="crossref",
                    stable_id=stable_id,
                    title=title,
                    authors=_author_names(item.get("author")),
                    abstract=_strip_markup(item.get("abstract")),
                    doi=doi,
                    full_text_url=f"https://doi.org/{doi}" if doi else None,
                    access_status="metadata",
                    license=license_url,
                    retrieved_at=_utc_now(),
                )
            )
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _arxiv_adapter(session: Any) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        response = session.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "max_results": 20},
            headers={"User-Agent": "ChalkScience125/1.0"},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        text = str(getattr(response, "text", "") or "")
        if len(text.encode("utf-8")) > 5 * 1024 * 1024:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        records: list[EvidenceRecord] = []
        try:
            root = ET.fromstring(text)
        except (ET.ParseError, TypeError, ValueError):
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            raw_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            arxiv_id = raw_id.rsplit("/", 1)[-1] if raw_id else ""
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id) if arxiv_id else ""
            title = _strip_markup(entry.findtext("atom:title", default="", namespaces=ns))
            if not title or not arxiv_id:
                continue
            records.append(
                EvidenceRecord(
                    provider="arxiv",
                    stable_id=f"arxiv:{arxiv_id}",
                    title=title,
                    authors=tuple(
                        _strip_markup(name.text)
                        for name in entry.findall("atom:author/atom:name", ns)
                        if _strip_markup(name.text)
                    ),
                    abstract=_strip_markup(entry.findtext("atom:summary", default="", namespaces=ns)),
                    arxiv_id=arxiv_id,
                    full_text_url=f"https://arxiv.org/abs/{arxiv_id}",
                    access_status="open_full_text",
                    retrieved_at=_utc_now(),
                )
            )
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _ncbi_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": 20,
            "tool": "chalk_science125",
        }
        for env_name, param_name in (
            ("SCIENCE125_NCBI_API_KEY", "api_key"),
            ("SCIENCE125_NCBI_TOOL_EMAIL", "email"),
        ):
            value = str(environ.get(env_name, "")).strip()
            if value:
                params[param_name] = value
        response = session.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        result = _response_json(response).get("esearchresult", {})
        ids = result.get("idlist", []) if isinstance(result, Mapping) else []
        records = tuple(
            EvidenceRecord(
                provider="ncbi",
                stable_id=f"pmid:{str(pmid).strip()}",
                title=f"PubMed record {str(pmid).strip()}",
                pmid=str(pmid).strip(),
                full_text_url=f"https://pubmed.ncbi.nlm.nih.gov/{str(pmid).strip()}/",
                access_status="metadata",
                retrieved_at=_utc_now(),
            )
            for pmid in ids
            if str(pmid).strip()
        )
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=records)

    return adapter


def _normalize_doi(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    return text or None


def _openalex_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        params = {"search": query, "per-page": 20}
        mailto = str(environ.get("SCIENCE125_OPENALEX_MAILTO", "")).strip()
        if mailto:
            params["mailto"] = mailto
        response = session.get("https://api.openalex.org/works", params=params, timeout=(10, 30))
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        items = payload.get("results", [])
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            title = _strip_markup(item.get("display_name") or item.get("title"))
            if not title:
                continue
            doi = _normalize_doi(item.get("doi"))
            authorships = item.get("authorships", [])
            authors = tuple(
                str((authorship.get("author") or {}).get("display_name") or "").strip()
                for authorship in authorships
                if isinstance(authorship, Mapping)
                and isinstance(authorship.get("author"), Mapping)
                and str((authorship.get("author") or {}).get("display_name") or "").strip()
            ) if isinstance(authorships, Sequence) and not isinstance(authorships, (str, bytes)) else ()
            inverted = item.get("abstract_inverted_index")
            abstract = ""
            if isinstance(inverted, Mapping):
                positions: list[tuple[int, str]] = []
                for word, indexes in inverted.items():
                    if isinstance(indexes, Sequence) and not isinstance(indexes, (str, bytes)):
                        positions.extend((int(index), str(word)) for index in indexes if _safe_int(index) is not None)
                abstract = " ".join(word for _, word in sorted(positions))
            record_id = str(item.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
            stable_id = f"doi:{doi}" if doi else f"openalex:{record_id or _hash_text(title)[:24]}"
            open_access = item.get("open_access") if isinstance(item.get("open_access"), Mapping) else {}
            locations = tuple(
                location
                for location in (item.get("best_oa_location"), item.get("primary_location"))
                if isinstance(location, Mapping)
            )
            pdf_url = next(
                (
                    str(location.get("pdf_url") or "").strip()
                    for location in locations
                    if str(location.get("pdf_url") or "").strip()
                ),
                "",
            )
            oa_url = str(open_access.get("oa_url") or "").strip()
            landing_url = next(
                (
                    str(location.get("landing_page_url") or "").strip()
                    for location in locations
                    if str(location.get("landing_page_url") or "").strip()
                ),
                "",
            )
            has_open_full_text = bool(open_access.get("is_oa") and (pdf_url or oa_url))
            records.append(EvidenceRecord(
                provider="openalex",
                stable_id=stable_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                full_text_url=pdf_url or oa_url or landing_url or None,
                access_status="open_full_text" if has_open_full_text else "metadata",
                license=next(
                    (
                        str(location.get("license") or "").strip()
                        for location in locations
                        if str(location.get("license") or "").strip()
                    ),
                    None,
                ),
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _europe_pmc_adapter(session: Any) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        response = session.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "pageSize": 20, "resultType": "core"},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        result_list = payload.get("resultList", {})
        items = result_list.get("result", []) if isinstance(result_list, Mapping) else []
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            title = _strip_markup(item.get("title"))
            if not title:
                continue
            pmid = str(item.get("pmid") or "").strip() or None
            doi = _normalize_doi(item.get("doi"))
            stable_id = f"pmid:{pmid}" if pmid else f"doi:{doi}" if doi else f"europe_pmc:{item.get('id') or _hash_text(title)[:24]}"
            author_string = str(item.get("authorString") or "")
            authors = tuple(part.strip() for part in author_string.split(",") if part.strip())
            records.append(EvidenceRecord(
                provider="europe_pmc",
                stable_id=stable_id,
                title=title,
                authors=authors,
                abstract=_strip_markup(item.get("abstractText")),
                doi=doi,
                pmid=pmid,
                full_text_url=f"https://europepmc.org/article/MED/{pmid}" if pmid else None,
                access_status="open_full_text" if str(item.get("isOpenAccess") or "").upper() == "Y" else "metadata",
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _semantic_scholar_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        headers = {"Accept": "application/json"}
        key = str(environ.get("SCIENCE125_SEMANTIC_SCHOLAR_API_KEY", "")).strip()
        if key:
            headers["x-api-key"] = key
        response = session.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": 20, "fields": "title,abstract,authors,externalIds,openAccessPdf"},
            headers=headers,
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        items = payload.get("data", [])
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            title = _strip_markup(item.get("title"))
            paper_id = str(item.get("paperId") or "").strip()
            if not title or not paper_id:
                continue
            external = item.get("externalIds") if isinstance(item.get("externalIds"), Mapping) else {}
            doi = _normalize_doi(external.get("DOI"))
            authors = _author_names(item.get("authors"))
            open_access = item.get("openAccessPdf")
            open_access_url = (
                str(open_access.get("url") or "").strip()
                if isinstance(open_access, Mapping)
                else ""
            )
            records.append(EvidenceRecord(
                provider="semantic_scholar",
                stable_id=f"semantic_scholar:{paper_id}",
                title=title,
                authors=authors,
                abstract=_strip_markup(item.get("abstract")),
                doi=doi,
                arxiv_id=str(external.get("ArXiv") or "").strip() or None,
                full_text_url=open_access_url or None,
                access_status="open_full_text" if open_access_url else "metadata",
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _clinical_trials_adapter(session: Any) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        response = session.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.term": query, "pageSize": 20, "format": "json"},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        studies = payload.get("studies", [])
        records: list[EvidenceRecord] = []
        for study in studies if isinstance(studies, Sequence) and not isinstance(studies, (str, bytes)) else ():
            if not isinstance(study, Mapping):
                continue
            protocol = study.get("protocolSection", {})
            identification = protocol.get("identificationModule", {}) if isinstance(protocol, Mapping) else {}
            description = protocol.get("descriptionModule", {}) if isinstance(protocol, Mapping) else {}
            nct_id = str(identification.get("nctId") or "").strip()
            title = _strip_markup(identification.get("briefTitle") or identification.get("officialTitle"))
            if not nct_id or not title:
                continue
            records.append(EvidenceRecord(
                provider="clinical_trials",
                stable_id=f"nct:{nct_id}",
                title=title,
                abstract=_strip_markup(description.get("briefSummary")) if isinstance(description, Mapping) else "",
                full_text_url=f"https://clinicaltrials.gov/study/{nct_id}",
                access_status="study_registry",
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _inspire_adapter(session: Any) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        response = session.get(
            "https://inspirehep.net/api/literature",
            params={"q": query, "size": 20},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        hits = payload.get("hits", {})
        items = hits.get("hits", []) if isinstance(hits, Mapping) else []
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            titles = metadata.get("titles", [])
            title = _strip_markup(titles[0].get("title") if isinstance(titles, Sequence) and titles and isinstance(titles[0], Mapping) else "")
            record_id = str(item.get("id") or "").strip()
            if not title or not record_id:
                continue
            dois = metadata.get("dois", [])
            doi = _normalize_doi(dois[0].get("value") if isinstance(dois, Sequence) and dois and isinstance(dois[0], Mapping) else None)
            arxiv = metadata.get("arxiv_eprints", [])
            arxiv_id = str(arxiv[0].get("value") or "").strip() if isinstance(arxiv, Sequence) and arxiv and isinstance(arxiv[0], Mapping) else None
            abstracts = metadata.get("abstracts", [])
            abstract = _strip_markup(abstracts[0].get("value") if isinstance(abstracts, Sequence) and abstracts and isinstance(abstracts[0], Mapping) else "")
            authors = tuple(
                str(author.get("full_name") or author.get("raw_affiliation") or "").strip()
                for author in metadata.get("authors", [])
                if isinstance(author, Mapping) and str(author.get("full_name") or author.get("raw_affiliation") or "").strip()
            ) if isinstance(metadata.get("authors"), Sequence) else ()
            records.append(EvidenceRecord(
                provider="inspire",
                stable_id=f"inspire:{record_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                arxiv_id=arxiv_id,
                full_text_url=f"https://inspirehep.net/literature/{record_id}",
                access_status="metadata",
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def _nasa_ads_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        token = str(environ.get("SCIENCE125_NASA_ADS_API_TOKEN", "")).strip()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = session.get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params={"q": query, "rows": 20, "fl": "bibcode,title,abstract,author,doi"},
            headers=headers,
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        body = payload.get("response", {})
        items = body.get("docs", []) if isinstance(body, Mapping) else []
        records: list[EvidenceRecord] = []
        for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else ():
            if not isinstance(item, Mapping):
                continue
            title_raw = item.get("title", "")
            title = _strip_markup(title_raw[0] if isinstance(title_raw, Sequence) and not isinstance(title_raw, (str, bytes)) and title_raw else title_raw)
            bibcode = str(item.get("bibcode") or "").strip()
            if not title or not bibcode:
                continue
            doi_raw = item.get("doi", "")
            doi = _normalize_doi(doi_raw[0] if isinstance(doi_raw, Sequence) and not isinstance(doi_raw, (str, bytes)) and doi_raw else doi_raw)
            records.append(EvidenceRecord(
                provider="nasa_ads",
                stable_id=f"ads:{bibcode}",
                title=title,
                authors=_author_names(item.get("author")),
                abstract=_strip_markup(item.get("abstract")),
                doi=doi,
                ads_id=bibcode,
                full_text_url=f"https://ui.adsabs.harvard.edu/abs/{bibcode}" if bibcode else None,
                access_status="metadata",
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


_ELEMENT_SYMBOLS = frozenset({
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra",
    "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db",
    "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
})


def _is_chemical_formula(value: str) -> bool:
    tokens = re.findall(r"([A-Z][a-z]?)(?:\d+(?:\.\d+)?)?", value)
    rebuilt = "".join(re.findall(r"[A-Z][a-z]?\d*(?:\.\d+)?", value))
    return bool(tokens) and rebuilt == value and all(symbol in _ELEMENT_SYMBOLS for symbol in tokens)


def _materials_project_formula(query: str) -> str | None:
    normalized = " ".join(str(query).split())
    if re.fullmatch(r"(?:[A-Z][a-z]?\d*){1,12}", normalized) and _is_chemical_formula(normalized):
        return normalized
    candidates = re.findall(r"\b(?:[A-Z][a-z]?\d*){1,12}\b", normalized)
    for candidate in candidates:
        element_count = len(re.findall(r"[A-Z][a-z]?", candidate))
        if _is_chemical_formula(candidate) and (
            any(character.isdigit() for character in candidate) or element_count >= 2
        ):
            return candidate
    return None


def _materials_project_adapter(session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        api_key = str(environ.get("MATERIALS_PROJECT_API_KEY") or environ.get("MP_API_KEY") or "").strip()
        formula = _materials_project_formula(query)
        if not api_key or not formula:
            return ProviderSearchResponse(status_code=200)
        response = session.get(
            "https://api.materialsproject.org/materials/summary/",
            params={
                "formula": formula,
                "_fields": (
                    "material_id,formula_pretty,band_gap,formation_energy_per_atom,"
                    "energy_above_hull,total_magnetization,volume,density"
                ),
                "_limit": 20,
            },
            headers={"Accept": "application/json", "X-API-KEY": api_key},
            timeout=(10, 30),
        )
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        items = payload.get("data", [])
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            items = []
        records: list[EvidenceRecord] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            material_id = str(item.get("material_id") or "").strip()
            formula_pretty = str(item.get("formula_pretty") or formula).strip()
            if not material_id or not formula_pretty:
                continue
            property_parts = [
                f"band_gap={item.get('band_gap')}",
                f"formation_energy_per_atom={item.get('formation_energy_per_atom')}",
                f"energy_above_hull={item.get('energy_above_hull')}",
                f"density={item.get('density')}",
            ]
            records.append(EvidenceRecord(
                provider="materials_project",
                stable_id=f"materials_project:{material_id}",
                title=f"Materials Project {material_id}: {formula_pretty}",
                abstract="; ".join(property_parts),
                full_text_url=f"https://materialsproject.org/materials/{material_id}",
                access_status="metadata",
                license="Materials Project terms apply",
                warning=(
                    "Materials Project structured enrichment is not a peer-reviewed full-text "
                    "literature source and does not count toward the Science 125 evidence gate."
                ),
                retrieved_at=_utc_now(),
            ))
        return ProviderSearchResponse(
            status_code=status,
            headers=_response_headers(response),
            records=tuple(records),
        )

    return adapter


def _generic_json_adapter(provider_id: str, session: Any, environ: Mapping[str, str]) -> ProviderAdapter:
    definition = get_provider(provider_id)

    def adapter(_provider: ProviderDefinition, query: str) -> ProviderSearchResponse:
        headers: dict[str, str] = {"Accept": "application/json"}
        params: dict[str, Any] = {"q": query, "query": query, "per-page": 20, "limit": 20}
        if provider_id == "semantic_scholar":
            key = str(environ.get("SCIENCE125_SEMANTIC_SCHOLAR_API_KEY", "")).strip()
            if key:
                headers["x-api-key"] = key
            params = {"query": query, "limit": 20, "fields": "title,abstract,authors,externalIds,openAccessPdf"}
        elif provider_id == "nasa_ads":
            token = str(environ.get("SCIENCE125_NASA_ADS_API_TOKEN", "")).strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            params = {"q": query, "rows": 20, "fl": "bibcode,title,abstract,author,doi"}
        response = session.get(definition.base_url, params=params, headers=headers, timeout=(10, 30))
        status = _response_status(response)
        if status >= 400:
            return ProviderSearchResponse(status_code=status, headers=_response_headers(response))
        payload = _response_json(response)
        raw_items: Any = payload.get("results") or payload.get("data") or payload.get("items") or payload.get("docs")
        if provider_id == "semantic_scholar":
            raw_items = payload.get("data", [])
        if provider_id == "nasa_ads":
            raw_items = payload.get("response", {}).get("docs", []) if isinstance(payload.get("response"), Mapping) else []
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raw_items = []
        records: list[EvidenceRecord] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            title = _strip_markup(item.get("title") or item.get("name") or item.get("display_name"))
            if isinstance(item.get("title"), Sequence) and not isinstance(item.get("title"), (str, bytes)):
                title = _strip_markup(item["title"][0] if item["title"] else "")
            if not title:
                continue
            external = item.get("externalIds") if isinstance(item.get("externalIds"), Mapping) else {}
            doi = str(item.get("doi") or external.get("DOI") or "").strip() or None
            stable = doi or str(item.get("id") or item.get("bibcode") or _hash_text(title)[:24])
            records.append(
                EvidenceRecord(
                    provider=provider_id,
                    stable_id=f"{provider_id}:{stable}",
                    title=title,
                    authors=_author_names(item.get("author") or item.get("authors")),
                    abstract=_strip_markup(item.get("abstract") or item.get("summary")),
                    doi=doi,
                    arxiv_id=str(external.get("ArXiv") or "").strip() or None,
                    ads_id=str(item.get("bibcode") or "").strip() or None,
                    full_text_url=str((item.get("openAccessPdf") or {}).get("url") or "") or None
                    if isinstance(item.get("openAccessPdf"), Mapping)
                    else None,
                    access_status="metadata",
                    retrieved_at=_utc_now(),
                )
            )
        return ProviderSearchResponse(status_code=status, headers=_response_headers(response), records=tuple(records))

    return adapter


def default_provider_adapters(
    *,
    environ: Mapping[str, str] | None = None,
    session: Any | None = None,
) -> dict[str, ProviderAdapter]:
    """Build approved API adapters without performing a request at construction."""
    env = dict(os.environ if environ is None else environ)
    client = session or requests.Session()
    adapters: dict[str, ProviderAdapter] = {
        "crossref": _crossref_adapter(client, env),
        "arxiv": _arxiv_adapter(client),
        "ncbi": _ncbi_adapter(client, env),
        "openalex": _openalex_adapter(client, env),
        "europe_pmc": _europe_pmc_adapter(client),
        "semantic_scholar": _semantic_scholar_adapter(client, env),
        "clinical_trials": _clinical_trials_adapter(client),
        "inspire": _inspire_adapter(client),
        "nasa_ads": _nasa_ads_adapter(client, env),
        "materials_project": _materials_project_adapter(client, env),
    }
    return adapters


def search_science125(
    profile_id: str,
    query: str,
    *,
    adapters: Mapping[str, ProviderAdapter],
    store: ProviderRateStateStore,
    environ: Mapping[str, str] | None = None,
    parameters: Mapping[str, Any] | None = None,
    provider_ids: Sequence[str] | None = None,
    window: str = "default",
    max_attempts: int = MAX_TOTAL_ATTEMPTS,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    random_uniform: Callable[[float, float], float] = random.uniform,
    user_scope: str | int = "anonymous",
    user_id: str | int | None = None,
    credential_scope: str | None = None,
    use_cache: bool = True,
) -> Science125SearchResult:
    """Search approved sources sequentially through injected adapters.

    Adapters perform one provider request and return normalized records; this
    function owns ordering, readiness, retries, rate state, de-duplication and
    diagnostics.  It intentionally has no default network transport.
    """
    profile = get_retrieval_profile(profile_id)
    if user_id is not None:
        user_scope = user_id
    normalized_query = " ".join(str(query).split())
    if not normalized_query:
        raise ValueError("Science 125 retrieval query must not be empty.")
    q_hash = query_hash(normalized_query)
    cache_key = retrieval_cache_key(profile_id, normalized_query, parameters, window=window)
    selected = tuple(provider_ids or profile.provider_ids)
    # Caller may narrow providers for a reviewed run, but cannot add an
    # unregistered provider or exceed the profile's configured maximum.
    allowed = set(profile.provider_ids)
    if any(provider_id not in allowed for provider_id in selected):
        raise ValueError("Requested provider is not allowed by the retrieval profile.")
    selected = tuple(dict.fromkeys(selected))[: profile.max_providers]
    env = dict(os.environ if environ is None else environ)
    cache_user_scope = _cache_scope("user", user_scope)
    cache_credential_scope = _combined_credential_scope(selected, env, credential_scope)
    cache_window_key = _cache_window_key(window, parameters)
    if use_cache:
        cached = store.get_cached(
            profile_id=profile_id,
            query_hash=q_hash,
            window_key=cache_window_key,
            user_scope=cache_user_scope,
            credential_scope=cache_credential_scope,
            now=now(),
        )
        if cached is not None:
            try:
                restored = _search_result_from_payload(cached.payload)
                if restored.profile_id == profile_id and restored.query_hash == q_hash:
                    return restored
            except (TypeError, ValueError, KeyError):
                store.delete_cached(
                    profile_id=profile_id,
                    query_hash=q_hash,
                    window_key=cache_window_key,
                    user_scope=cache_user_scope,
                    credential_scope=cache_credential_scope,
                )
    client = Science125RetrievalClient(
        store=store,
        environ=env,
        now=now,
        sleep=sleep,
        random_uniform=random_uniform,
    )
    all_records: list[EvidenceRecord] = []
    diagnostics: list[ProviderDiagnostic] = []
    for provider_id in selected:
        readiness = provider_readiness(provider_id, environ=env)
        if not readiness.ready:
            diagnostics.append(
                ProviderDiagnostic(
                    provider=provider_id,
                    status="not_ready",
                    message="Provider credentials/configuration are unavailable.",
                    missing_configuration_codes=readiness.missing_configuration_codes,
                )
            )
            continue
        adapter = adapters.get(provider_id)
        if adapter is None:
            diagnostics.append(ProviderDiagnostic(provider=provider_id, status="adapter_missing"))
            continue
        try:
            request_result = client.request_result(
                provider_id,
                lambda adapter=adapter, provider_id=provider_id: adapter(get_provider(provider_id), normalized_query),
                max_attempts=max_attempts,
            )
            response = request_result.response
            if not isinstance(response, ProviderSearchResponse):
                raise TypeError("Provider adapter must return ProviderSearchResponse.")
            records = tuple(
                replace(record, provider=provider_id, query_hash=q_hash)
                for record in response.records
            )
            all_records.extend(records)
            diagnostics.append(
                ProviderDiagnostic(
                    provider=provider_id,
                    status=(
                        "succeeded"
                        if 200 <= response.status_code < 300
                        else "credential_rejected"
                        if response.status_code in {401, 403}
                        else "http_error"
                    ),
                    status_code=response.status_code,
                    attempts=request_result.attempts,
                    message=(
                        None
                        if 200 <= response.status_code < 300
                        else "Provider rejected the configured credential or denied this API operation."
                        if response.status_code in {401, 403}
                        else "Provider returned a non-success status."
                    ),
                )
            )
        except ProviderNotReadyError as exc:
            diagnostics.append(
                ProviderDiagnostic(provider=provider_id, status="not_ready", message=str(exc), missing_configuration_codes=exc.codes)
            )
        except RetrievalRequestError as exc:
            diagnostics.append(
                ProviderDiagnostic(provider=provider_id, status="transport_error", attempts=exc.attempts, message="Provider request failed after retries.")
            )
        except Exception:
            # Adapter details may include private provider responses; expose a
            # stable diagnostic without leaking them to the API.
            diagnostics.append(ProviderDiagnostic(provider=provider_id, status="adapter_error", message="Provider adapter returned invalid data."))
    result = Science125SearchResult(
        profile_id=profile_id,
        query_hash=q_hash,
        cache_key=cache_key,
        evidence=_deduplicate_records(all_records),
        diagnostics=tuple(diagnostics),
    )
    # Cache only a complete successful provider set with usable evidence.
    # Partial, failed, or empty responses must be retried on the next run.
    cacheable = bool(result.evidence) and len(result.diagnostics) == len(selected) and all(
        diagnostic.status == "succeeded" for diagnostic in result.diagnostics
    )
    if use_cache and cacheable:
        store.put_cached(
            profile_id=profile_id,
            query_hash=q_hash,
            window_key=cache_window_key,
            user_scope=cache_user_scope,
            credential_scope=cache_credential_scope,
            payload=result.to_dict(),
            now=now(),
            ttl_seconds=profile.cache_ttl_seconds,
        )
    return result


__all__ = [
    "EvidenceRecord",
    "MAX_TOTAL_ATTEMPTS",
    "ProviderDefinition",
    "ProviderDiagnostic",
    "ProviderNotReadyError",
    "ProviderRateLimiter",
    "ProviderRatePolicy",
    "ProviderRateState",
    "ProviderRateStateStore",
    "ProviderReadiness",
    "ProviderSearchResponse",
    "RETRIEVAL_PROFILE_REGISTRY",
    "PROVIDER_REGISTRY",
    "ProfileReadiness",
    "RetrievalProfile",
    "RetrievalRequestError",
    "Science125RetrievalClient",
    "Science125SearchResult",
    "StoredRetrievalCache",
    "get_provider",
    "get_retrieval_profile",
    "get_science125_retrieval_profile",
    "default_provider_adapters",
    "profile_readiness",
    "provider_readiness",
    "query_hash",
    "resolve_provider",
    "retrieval_cache_key",
    "search_science125",
]
