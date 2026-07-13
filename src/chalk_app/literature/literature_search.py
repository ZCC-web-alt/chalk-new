"""
OA 文献搜索引擎 — 从合规开源平台检索相关论文支撑假设。

支持平台：
  - arXiv（预印本，化学/材料/物理交叉）
  - Crossref（DOI 元数据，覆盖面最广）
  - Semantic Scholar（AI 增强，相关论文推荐）
  - DOAJ（OA 期刊目录）
  - PMC Entrez（生物医学+化工交叉）

合规要求：
  - 仅爬取 Open Access 内容
  - 请求间隔 >= 3 秒
  - 保留元数据与版权信息
  - 禁止商用与传播
"""

import json
import logging
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional, Dict

import requests

logger = logging.getLogger(__name__)

# 平台限速（秒/请求），按各 API 官方限制设置。
# arXiv: 1 request / 3 seconds.
# Crossref polite pool: 10 requests / second, single-process caller.
# Semantic Scholar: use a stable per-client 1 RPS default; unauthenticated traffic is a shared pool.
# NCBI E-utilities/PMC: 3 RPS without API key, 10 RPS with API key.
# PubChem: 5 RPS. DOAJ docs do not publish an explicit limit, so keep a modest 2 RPS.
PLATFORM_MIN_INTERVALS = {
    "arxiv": 3.0,
    "crossref": 0.1,
    "semantic_scholar": 1.0,
    "doaj": 0.5,
    "pmc": 1.0 / 3.0,
    "ncbi": 1.0 / 3.0,
    "pubchem": 0.2,
}
_last_request_time_by_platform: Dict[str, float] = {}
_platform_cooldowns: Dict[str, Dict[str, Any]] = {}
DEFAULT_RATE_LIMIT_COOLDOWN_MINUTES = 10
DEFAULT_PLATFORMS = {
    "arxiv": True,
    "crossref": True,
    "semantic_scholar": None,
    "doaj": True,
    "pmc": True,
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _cooldown_minutes() -> float:
    return max(
        0.1,
        _safe_float(
            os.getenv("CHALK_LITERATURE_RATE_LIMIT_COOLDOWN_MINUTES"),
            DEFAULT_RATE_LIMIT_COOLDOWN_MINUTES,
        ),
    )


def _rate_limit_platform(platform: str, interval: Optional[float] = None):
    """Per-platform throttle; avoids the previous global 4-second bottleneck."""
    platform_key = (platform or "default").lower()
    min_interval = PLATFORM_MIN_INTERVALS.get(platform_key, 1.0) if interval is None else interval
    if min_interval <= 0:
        _last_request_time_by_platform[platform_key] = time.time()
        return

    last_request_time = _last_request_time_by_platform.get(platform_key, 0.0)
    elapsed = time.time() - last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time_by_platform[platform_key] = time.time()


def rate_limit_for_platform(platform: str):
    """Shared lightweight limiter for adjacent modules such as PubChem lookup."""
    _rate_limit_platform(platform)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, max_len: Optional[int] = None) -> str:
    """Normalize API text fields without trusting response shapes."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(_clean_text(item) for item in value)
    elif isinstance(value, dict):
        value = " ".join(_clean_text(item) for item in value.values())
    else:
        value = str(value)

    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len] if max_len else value


def _first_list_text(value: Any) -> str:
    """Return the first non-empty text item from list-shaped API fields."""
    for item in _as_list(value):
        text = _clean_text(item)
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class SearchResult:
    """单条文献搜索结果"""
    title: str = ""
    authors: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    abstract: str = ""
    source_platform: str = ""  # arxiv / crossref / semantic_scholar / doaj / pmc
    url: str = ""
    is_open_access: bool = True
    relevance_score: float = 0.0  # 0-1 相关度
    access_status: str = "open_access"  # open_access / metadata_only / needs_verification
    needs_fulltext: bool = False
    warning: str = ""

    def to_reference_dict(self) -> dict:
        """转换为比赛 References 格式"""
        return {
            "authors": self.authors,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "doi": self.doi,
            "abstract": self.abstract,
            "source_platform": self.source_platform,
            "url": self.url,
            "is_open_access": self.is_open_access,
            "search_relevance_score": self.relevance_score,
            "access_status": self.access_status,
            "needs_fulltext": self.needs_fulltext,
            "warning": self.warning,
        }


@dataclass
class SearchQuery:
    """搜索查询"""
    keywords: List[str] = field(default_factory=list)
    domain: str = ""  # catalysis / materials / energy / ...
    max_results: int = 5  # 每平台最大返回数
    year_from: str = ""  # 限定起始年份
    year_to: str = ""


@dataclass
class SearchDiagnostics:
    """Search results plus platform health for UI/report diagnostics."""

    results: List[SearchResult] = field(default_factory=list)
    platform_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    query: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "results": [r.to_reference_dict() for r in self.results],
            "platform_status": self.platform_status,
            "warnings": self.warnings,
            "query": self.query,
        }


class PlatformRateLimitedError(RuntimeError):
    """Raised when a literature provider returns 429 and should cool down."""

    def __init__(self, platform: str, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.platform = platform
        self.retry_after = retry_after


class LiteratureSearchEngine:
    """
    OA 文献搜索引擎。

    用法：
        engine = LiteratureSearchEngine()
        results = engine.search(SearchQuery(keywords=["d-band center", "ORR", "catalyst"]))
        refs = [r.to_reference_dict() for r in results]
    """

    # 各平台开关默认值由 __init__ 根据 API key 和环境变量解析。
    PLATFORMS = DEFAULT_PLATFORMS.copy()

    def __init__(
        self,
        platforms: Optional[Dict[str, bool]] = None,
        timeout: int = 15,
        retry_attempts: int = 3,
        retry_backoff: float = 1.0,
    ):
        """
        Args:
            platforms: 各平台开关，默认全部启用
            timeout: 单次 HTTP 请求超时（秒）
            retry_attempts: 临时网络失败的最大尝试次数
            retry_backoff: 重试退避基数（秒）
        """
        self.timeout = timeout
        self.retry_attempts = max(1, _safe_int(retry_attempts, 3))
        self.retry_backoff = max(0.0, _safe_float(retry_backoff, 1.0))
        self.semantic_scholar_api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        self.crossref_mailto = os.getenv("CROSSREF_MAILTO", "chalk@example.com").strip()
        self.ncbi_api_key = os.getenv("NCBI_API_KEY", "").strip()
        self.rate_limit_cooldown_minutes = _cooldown_minutes()
        self.PLATFORMS = self._resolve_platforms(platforms)
        self.platform_status: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.last_diagnostics = SearchDiagnostics()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ChalkLab/2.6 (Academic Research; mailto:chalk@example.com)",
            "Accept": "application/json",
        })

    def _resolve_platforms(self, platforms: Optional[Dict[str, bool]]) -> Dict[str, bool]:
        resolved: Dict[str, bool] = {}
        for platform, default in DEFAULT_PLATFORMS.items():
            if default is None and platform == "semantic_scholar":
                value = bool(self.semantic_scholar_api_key) and _env_bool(
                    "CHALK_LITERATURE_ENABLE_SEMANTIC_SCHOLAR",
                    True,
                )
            else:
                env_name = f"CHALK_LITERATURE_ENABLE_{platform.upper()}"
                value = _env_bool(env_name, bool(default))
            resolved[platform] = value
        if platforms:
            resolved.update({k: bool(v) for k, v in platforms.items()})
        return resolved

    @staticmethod
    def _platform_key(platform: str) -> str:
        platform_lower = (platform or "").lower().replace("-", "_").replace(" ", "_")
        if platform_lower.startswith("semantic"):
            return "semantic_scholar"
        if platform_lower.startswith("pmc") or platform_lower.startswith("ncbi"):
            return "pmc"
        if platform_lower.startswith("crossref"):
            return "crossref"
        if platform_lower.startswith("arxiv"):
            return "arxiv"
        if platform_lower.startswith("doaj"):
            return "doaj"
        if platform_lower.startswith("pubchem"):
            return "pubchem"
        return platform_lower or "default"

    def _rate_limit_interval(self, platform: str) -> float:
        platform_key = self._platform_key(platform)
        if platform_key == "pmc" and self.ncbi_api_key:
            return 0.1
        return PLATFORM_MIN_INTERVALS.get(platform_key, 1.0)

    def _throttle(self, platform: str):
        platform_key = self._platform_key(platform)
        _rate_limit_platform(platform_key, self._rate_limit_interval(platform_key))

    def _retry_delay(self, attempt: int, response: Optional[requests.Response] = None) -> float:
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            retry_after = _safe_float(getattr(headers, "get", lambda *_: 0)("Retry-After", 0))
            if retry_after > 0:
                return min(retry_after, 30.0)
        return min(self.retry_backoff * (2 ** attempt), 12.0)

    def _get_with_retries(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[int] = None,
        platform: str = "literature source",
    ) -> requests.Response:
        retriable_statuses = {500, 502, 503, 504}
        attempts = self.retry_attempts
        platform_key = self._platform_key(platform)

        for attempt in range(attempts):
            try:
                self._throttle(platform)
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout if timeout is None else timeout,
                )
                if getattr(resp, "status_code", 0) == 429:
                    retry_after = self._retry_delay(attempt, resp)
                    raise PlatformRateLimitedError(
                        platform_key,
                        f"{platform} returned 429 rate limit",
                        retry_after=retry_after,
                    )
                if getattr(resp, "status_code", 0) in retriable_statuses and attempt < attempts - 1:
                    delay = self._retry_delay(attempt, resp)
                    logger.info(
                        f"{platform} 返回 {resp.status_code}，等待 {delay:.1f}s 后重试 "
                        f"({attempt + 1}/{attempts})"
                    )
                    if delay > 0:
                        time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError as e:
                response = getattr(e, "response", None)
                if getattr(response, "status_code", 0) == 429:
                    retry_after = self._retry_delay(attempt, response)
                    raise PlatformRateLimitedError(
                        platform_key,
                        f"{platform} returned 429 rate limit",
                        retry_after=retry_after,
                    ) from e
                raise
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt >= attempts - 1:
                    raise
                delay = self._retry_delay(attempt)
                logger.info(
                    f"{platform} 网络请求失败，等待 {delay:.1f}s 后重试 "
                    f"({attempt + 1}/{attempts}): {e}"
                )
                if delay > 0:
                    time.sleep(delay)

        raise RuntimeError(f"{platform} 请求失败且未返回响应")

    def _cooldown_info(self, platform: str) -> Optional[Dict[str, Any]]:
        platform_key = self._platform_key(platform)
        info = _platform_cooldowns.get(platform_key)
        if not info:
            return None
        until = _safe_float(info.get("until"), 0.0)
        if until <= time.time():
            _platform_cooldowns.pop(platform_key, None)
            return None
        remaining = max(0.0, until - time.time())
        return {**info, "remaining_seconds": remaining}

    def _set_cooldown(
        self,
        platform: str,
        reason: str,
        retry_after: float = 0.0,
    ) -> Dict[str, Any]:
        platform_key = self._platform_key(platform)
        duration = max(_safe_float(retry_after, 0.0), self.rate_limit_cooldown_minutes * 60)
        info = {
            "status": "rate_limited",
            "reason": reason,
            "until": time.time() + duration,
            "remaining_seconds": duration,
            "cooldown_minutes": round(duration / 60, 2),
        }
        _platform_cooldowns[platform_key] = info
        return info

    @staticmethod
    def _platform_display_name(platform: str) -> str:
        return {
            "arxiv": "arXiv",
            "crossref": "Crossref",
            "semantic_scholar": "Semantic Scholar",
            "doaj": "DOAJ",
            "pmc": "PMC",
        }.get(platform, platform)

    def _search_platform(self, platform: str, query: SearchQuery) -> List[SearchResult]:
        if platform == "arxiv":
            return self._search_arxiv(query)
        if platform == "crossref":
            return self._search_crossref(query)
        if platform == "semantic_scholar":
            return self._search_semantic_scholar(query)
        if platform == "doaj":
            return self._search_doaj(query)
        if platform == "pmc":
            return self._search_pmc(query)
        raise ValueError(f"Unsupported literature platform: {platform}")

    def search_with_diagnostics(self, query: SearchQuery) -> SearchDiagnostics:
        """
        Search enabled platforms and retain platform-level health diagnostics.
        The legacy search() method stays list-only for existing callers.
        """
        all_results: List[SearchResult] = []
        seen_titles = set()
        platform_status: Dict[str, Dict[str, Any]] = {}
        warnings: List[str] = []

        search_terms = query.keywords if query.keywords else []
        if not search_terms:
            diagnostics = SearchDiagnostics(
                results=[],
                platform_status={},
                warnings=["No search keywords were provided."],
                query=asdict(query),
            )
            self.platform_status = diagnostics.platform_status
            self.warnings = diagnostics.warnings
            self.last_diagnostics = diagnostics
            return diagnostics

        for platform in ["crossref", "arxiv", "semantic_scholar", "doaj", "pmc"]:
            display_name = self._platform_display_name(platform)
            if not self.PLATFORMS.get(platform):
                platform_status[platform] = {
                    "status": "disabled",
                    "count": 0,
                    "reason": "Platform disabled by configuration.",
                }
                continue

            cooldown = self._cooldown_info(platform)
            if cooldown:
                remaining = int(cooldown.get("remaining_seconds", 0))
                warning = (
                    f"{display_name} is rate limited and cooling down for about "
                    f"{remaining} seconds; search continued with other platforms."
                )
                platform_status[platform] = {
                    "status": "rate_limited",
                    "count": 0,
                    "reason": cooldown.get("reason", "rate limited"),
                    "cooldown_remaining_seconds": remaining,
                }
                warnings.append(warning)
                continue

            try:
                results = self._search_platform(platform, query)
                before = len(all_results)
                self._merge_dedup(all_results, results, seen_titles)
                added = len(all_results) - before
                platform_status[platform] = {
                    "status": "ok" if results else "empty",
                    "count": len(results),
                    "added_count": added,
                }
            except PlatformRateLimitedError as exc:
                cooldown = self._set_cooldown(
                    exc.platform or platform,
                    str(exc),
                    retry_after=exc.retry_after,
                )
                remaining = int(cooldown.get("remaining_seconds", 0))
                warning = (
                    f"{display_name} returned 429 and was skipped for this search; "
                    f"cooldown is about {remaining} seconds."
                )
                platform_status[platform] = {
                    "status": "rate_limited",
                    "count": 0,
                    "reason": str(exc),
                    "cooldown_remaining_seconds": remaining,
                }
                warnings.append(warning)
                logger.warning(warning)
            except Exception as exc:
                warning = f"{display_name} search failed: {exc}"
                platform_status[platform] = {
                    "status": "failed",
                    "count": 0,
                    "reason": str(exc),
                }
                warnings.append(warning)
                logger.warning(warning)

        all_results.sort(key=lambda r: r.relevance_score, reverse=True)
        diagnostics = SearchDiagnostics(
            results=all_results,
            platform_status=platform_status,
            warnings=warnings,
            query=asdict(query),
        )
        self.platform_status = platform_status
        self.warnings = warnings
        self.last_diagnostics = diagnostics
        return diagnostics

    def search(self, query: SearchQuery) -> List[SearchResult]:
        """
        多平台并行搜索，合并去重。

        Args:
            query: 搜索查询

        Returns:
            去重后的搜索结果列表，按 relevance_score 降序
        """
        return self.search_with_diagnostics(query).results

    # ─── arXiv ───────────────────────────────────────────────

    def _search_arxiv(self, query: SearchQuery) -> List[SearchResult]:
        """搜索 arXiv 预印本"""
        # 构建 arXiv 查询
        terms = query.keywords
        if len(terms) == 1:
            query_str = f'all:"{terms[0]}"'
        else:
            query_str = " AND ".join(f'all:"{t}"' for t in terms[:3])

        # 限定分类（化工相关）
        domain_cat = {
            "catalysis": "cat:cond-mat.mtrl-sci OR cat:physics.chem-ph",
            "materials": "cat:cond-mat.mtrl-sci",
            "energy": "cat:physics.chem-ph OR cat:cond-mat.mtrl-sci",
            "chemistry": "cat:physics.chem-ph OR cat:cond-mat.mtrl-sci",
        }.get(query.domain, "cat:cond-mat.mtrl-sci OR cat:physics.chem-ph")

        full_query = f"({query_str}) AND ({domain_cat})"

        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": full_query,
            "start": 0,
            "max_results": query.max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        resp = self._get_with_retries(url, params=params, timeout=25, platform="arxiv")

        results = []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning(f"arXiv XML 解析失败: {e}")
            return []
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            title = _clean_text(entry.findtext("atom:title", "", ns))
            if not title:
                continue
            summary = _clean_text(entry.findtext("atom:summary", "", ns), 500)

            authors = []
            for author_elem in entry.findall("atom:author/atom:name", ns):
                author_name = _clean_text(author_elem.text)
                if author_name:
                    authors.append(author_name)
            authors_str = ", ".join(authors[:5])
            if len(authors) > 5:
                authors_str += " et al."

            published = _clean_text(entry.findtext("atom:published", "", ns))
            year = published[:4] if published else ""

            doi = ""
            for link in entry.findall("atom:link", ns):
                link_title = link.get("title", "")
                if link_title == "doi":
                    doi = link.get("href", "").replace("http://dx.doi.org/", "")
                    break

            pdf_url = ""
            page_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                elif link.get("type") == "text/html":
                    page_url = link.get("href", "")
            if not page_url:
                page_url = entry.findtext("atom:id", "", ns)

            results.append(SearchResult(
                title=title,
                authors=authors_str,
                journal="arXiv preprint",
                year=year,
                doi=doi,
                abstract=summary,
                source_platform="arxiv",
                url=page_url,
                is_open_access=True,
                relevance_score=0.7,
                access_status="open_access",
                needs_fulltext=False,
            ))

        logger.info(f"arXiv 返回 {len(results)} 条结果")
        return results

    # ─── Crossref ────────────────────────────────────────────

    def _search_crossref(self, query: SearchQuery) -> List[SearchResult]:
        """搜索 Crossref 元数据"""
        terms = " ".join(query.keywords[:4])
        url = "https://api.crossref.org/works"
        params = {
            "query": terms,
            "rows": query.max_results,
            "sort": "relevance",
            "filter": "type:journal-article",
        }
        if self.crossref_mailto:
            params["mailto"] = self.crossref_mailto
        if query.year_from:
            params["filter"] += f",from-pub-date:{query.year_from}"
        if query.year_to:
            params["filter"] += f",until-pub-date:{query.year_to}"

        resp = self._get_with_retries(url, params=params, platform="Crossref")

        data = _as_dict(resp.json())
        message = _as_dict(data.get("message"))
        items = _as_list(message.get("items"))

        results = []
        for item in items:
            item = _as_dict(item)
            title = _first_list_text(item.get("title"))
            if not title:
                continue

            authors_list = _as_list(item.get("author"))
            author_names = []
            for a in authors_list[:5]:
                a = _as_dict(a)
                family = a.get("family", "")
                given = a.get("given", "")
                author_name = _clean_text(f"{family} {given}")
                if author_name:
                    author_names.append(author_name)
            authors_str = ", ".join(author_names)
            if len(authors_list) > 5:
                authors_str += " et al."

            journal = _first_list_text(item.get("container-title"))

            year = ""
            pub_date = _as_dict(item.get("published-print") or item.get("published-online"))
            date_parts = _as_list(pub_date.get("date-parts"))
            if date_parts and date_parts[0]:
                first_date = _as_list(date_parts[0])
                year = _clean_text(first_date[0]) if first_date else ""

            doi = _clean_text(item.get("DOI"))
            abstract = _clean_text(item.get("abstract"), 500)

            results.append(SearchResult(
                title=title,
                authors=authors_str,
                journal=journal,
                year=year,
                doi=doi,
                abstract=abstract,
                source_platform="crossref",
                url=f"https://doi.org/{doi}" if doi else "",
                is_open_access=False,
                relevance_score=0.6,
                access_status="metadata_only",
                needs_fulltext=True,
                warning="Crossref only provides metadata; upload authorized full text before treating this as full-text evidence.",
            ))

        logger.info(f"Crossref 返回 {len(results)} 条结果")
        return results

    # ─── Semantic Scholar ────────────────────────────────────

    def _search_semantic_scholar(self, query: SearchQuery) -> List[SearchResult]:
        """搜索 Semantic Scholar（AI 增强相关论文推荐）"""
        terms = " ".join(query.keywords[:4])
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": terms,
            "limit": query.max_results,
            "fields": "title,authors,year,abstract,journal,isOpenAccess,externalIds,url",
        }

        # Semantic Scholar API Key（可选，有 Key 可大幅提高限额）
        api_key = self.semantic_scholar_api_key
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key

        resp = self._get_with_retries(
            url,
            params=params,
            headers=headers,
            platform="Semantic Scholar",
        )

        data = _as_dict(resp.json())
        papers = _as_list(data.get("data"))

        results = []
        for paper in papers:
            paper = _as_dict(paper)
            title = _clean_text(paper.get("title"))
            if not title:
                continue

            authors_list = _as_list(paper.get("authors"))
            author_names = [
                name
                for a in authors_list[:5]
                for name in [_clean_text(_as_dict(a).get("name"))]
                if name
            ]
            authors_str = ", ".join(author_names)
            if len(authors_list) > 5:
                authors_str += " et al."

            journal_info = _as_dict(paper.get("journal"))
            journal = _clean_text(journal_info.get("name"))

            year = _clean_text(paper.get("year"))

            ext_ids = _as_dict(paper.get("externalIds"))
            doi = _clean_text(ext_ids.get("DOI"))

            abstract = _clean_text(paper.get("abstract"), 500)
            is_oa = bool(paper.get("isOpenAccess", False))
            paper_url = _clean_text(paper.get("url"))
            access_status = "open_access" if is_oa else "metadata_only"

            results.append(SearchResult(
                title=title,
                authors=authors_str,
                journal=journal,
                year=year,
                doi=doi,
                abstract=abstract,
                source_platform="semantic_scholar",
                url=paper_url,
                is_open_access=is_oa,
                relevance_score=0.8,  # Semantic Scholar 相关性通常较好
                access_status=access_status,
                needs_fulltext=not is_oa,
                warning="" if is_oa else "Semantic Scholar metadata does not indicate open access full text.",
            ))

        logger.info(f"Semantic Scholar 返回 {len(results)} 条结果")
        return results

    # ─── DOAJ ────────────────────────────────────────────────

    def _search_doaj(self, query: SearchQuery) -> List[SearchResult]:
        """搜索 DOAJ（OA 期刊目录）"""
        terms = " ".join(query.keywords[:4])
        url = "https://doaj.org/api/search/articles/" + urllib.parse.quote(terms)
        params = {
            "page": 1,
            "pageSize": query.max_results,
        }

        resp = self._get_with_retries(url, params=params, platform="DOAJ")

        data = _as_dict(resp.json())
        articles = _as_list(data.get("results"))

        results = []
        for article in articles:
            article = _as_dict(article)
            bibjson = _as_dict(article.get("bibjson"))

            title = _clean_text(bibjson.get("title"))
            if not title:
                continue

            authors_list = _as_list(bibjson.get("author"))
            author_names = [
                name
                for a in authors_list[:5]
                for name in [_clean_text(_as_dict(a).get("name"))]
                if name
            ]
            authors_str = ", ".join(author_names)

            journal = bibjson.get("journal", {})
            if isinstance(journal, list):
                journal_name = _clean_text(_as_dict(journal[0]).get("title")) if journal else ""
            else:
                journal_name = _clean_text(_as_dict(journal).get("title"))

            year = _clean_text(bibjson.get("year"))

            identifiers = bibjson.get("identifier", {})
            if isinstance(identifiers, list):
                doi = ""
                for id_item in identifiers:
                    if isinstance(id_item, dict) and id_item.get("type") == "doi":
                        doi = _clean_text(id_item.get("id"))
                        break
            else:
                doi = _clean_text(_as_dict(identifiers).get("doi"))

            abstract = _clean_text(bibjson.get("abstract"), 500)

            link_url = ""
            for link in _as_list(bibjson.get("link")):
                link = _as_dict(link)
                if link.get("type") == "fulltext":
                    link_url = _clean_text(link.get("url"))
                    break

            results.append(SearchResult(
                title=title,
                authors=authors_str,
                journal=journal_name,
                year=year,
                doi=doi,
                abstract=abstract,
                source_platform="doaj",
                url=link_url,
                is_open_access=True,
                relevance_score=0.6,
                access_status="open_access",
                needs_fulltext=False,
            ))

        logger.info(f"DOAJ 返回 {len(results)} 条结果")
        return results

    # ─── PMC Entrez ─────────────────────────────────────────

    def _search_pmc(self, query: SearchQuery) -> List[SearchResult]:
        """搜索 PubMed Central（生物医学+化工交叉）"""
        terms = " AND ".join(query.keywords[:3])
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "pmc",
            "term": terms,
            "retmax": query.max_results,
            "sort": "relevance",
            "retmode": "json",
        }
        if self.ncbi_api_key:
            params["api_key"] = self.ncbi_api_key

        resp = self._get_with_retries(url, params=params, platform="PMC esearch")

        data = _as_dict(resp.json())
        id_list = _as_list(_as_dict(data.get("esearchresult")).get("idlist"))

        if not id_list:
            return []

        # 获取摘要
        fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        fetch_params = {
            "db": "pmc",
            "id": ",".join(id_list[:query.max_results]),
            "rettype": "xml",
            "retmode": "xml",
        }
        if self.ncbi_api_key:
            fetch_params["api_key"] = self.ncbi_api_key

        fetch_resp = self._get_with_retries(
            fetch_url,
            params=fetch_params,
            platform="PMC efetch",
        )

        results = []
        try:
            root = ET.fromstring(fetch_resp.text)
            for article in root.findall(".//article"):
                title_elem = article.find(".//article-title")
                title = _clean_text(title_elem.text) if title_elem is not None else ""
                if not title:
                    continue

                authors = []
                for contrib in article.findall(".//contrib[@contrib-type='author']"):
                    name = contrib.find("name")
                    if name is not None:
                        surname = name.findtext("surname", "")
                        given = name.findtext("given-names", "")
                        author_name = _clean_text(f"{surname} {given}")
                        if author_name:
                            authors.append(author_name)
                authors_str = ", ".join(authors[:5])
                if len(authors) > 5:
                    authors_str += " et al."

                journal_elem = article.find(".//journal-title")
                journal = _clean_text(journal_elem.text) if journal_elem is not None else ""

                year_elem = article.find(".//pub-date/year")
                year = _clean_text(year_elem.text) if year_elem is not None else ""

                abstract_parts = []
                for sec in article.findall(".//abstract//p"):
                    if sec.text:
                        text = _clean_text(sec.text)
                        if text:
                            abstract_parts.append(text)
                abstract = _clean_text(" ".join(abstract_parts), 500)

                doi = ""
                for article_id in article.findall(".//article-id"):
                    if article_id.get("pub-id-type") == "doi":
                        doi = article_id.text or ""

                pmc_id = ""
                for article_id in article.findall(".//article-id"):
                    if article_id.get("pub-id-type") == "pmc":
                        pmc_id = article_id.text or ""

                results.append(SearchResult(
                    title=title,
                    authors=authors_str,
                    journal=journal,
                    year=year,
                    doi=doi,
                    abstract=abstract,
                    source_platform="pmc",
                    url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/" if pmc_id else "",
                    is_open_access=True,
                    relevance_score=0.7,
                    access_status="open_access",
                    needs_fulltext=False,
                ))
        except ET.ParseError as e:
            logger.warning(f"PMC XML 解析失败: {e}")

        logger.info(f"PMC 返回 {len(results)} 条结果")
        return results

    # ─── 去重合并 ───────────────────────────────────────────

    @staticmethod
    def _merge_dedup(
        all_results: List[SearchResult],
        new_results: List[SearchResult],
        seen_titles: set,
    ):
        """基于 DOI 和标题去重合并"""
        seen_dois = {r.doi.lower() for r in all_results if r.doi}

        for r in new_results:
            if not isinstance(r, SearchResult):
                continue
            r.title = _clean_text(r.title)
            r.doi = _clean_text(r.doi)
            if not r.title:
                continue
            # DOI 去重
            if r.doi and r.doi.lower() in seen_dois:
                continue
            # 标题去重（归一化）
            norm_title = re.sub(r'\s+', ' ', r.title.lower().strip())
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)
            if r.doi:
                seen_dois.add(r.doi.lower())
            all_results.append(r)


# ─── 便捷函数 ────────────────────────────────────────────────

def search_literature_for_hypothesis(
    hypothesis_data: dict,
    domain: str = "",
    max_results_per_platform: int = 3,
    return_diagnostics: bool = False,
    platforms: Optional[Dict[str, bool]] = None,
):
    """
    从假设数据中提取关键词，搜索 OA 文献。

    Args:
        hypothesis_data: 假设结果 JSON（包含 problem_statement, rationale 等）
        domain: 研究领域标识
        max_results_per_platform: 每平台最大返回数

    Returns:
        搜索结果列表；return_diagnostics=True 时返回 (results, diagnostics_dict)
    """
    # 从假设各字段中提取关键词
    keywords = _extract_search_keywords(hypothesis_data)

    if not keywords:
        logger.warning("未能从假设中提取搜索关键词")
        if return_diagnostics:
            diagnostics = SearchDiagnostics(
                results=[],
                platform_status={},
                warnings=["未能从假设中提取搜索关键词"],
                query={},
            )
            return [], diagnostics.to_dict()
        return []

    query = SearchQuery(
        keywords=keywords,
        domain=domain,
        max_results=max_results_per_platform,
        year_from="2015",  # 优先近10年文献
    )

    engine = LiteratureSearchEngine(platforms=platforms)
    diagnostics = engine.search_with_diagnostics(query)
    results = diagnostics.results

    logger.info(f"文献搜索完成: {len(results)} 条结果（关键词: {keywords}）")
    if return_diagnostics:
        return results, diagnostics.to_dict()
    return results


def _extract_search_keywords(hypothesis_data: dict) -> List[str]:
    """
    从假设数据中提取适合搜索的关键词。

    优先级：problem_statement > rationale > technical_details > methods
    """
    keywords = []

    # 从 problem_statement 提取核心名词短语
    problem = hypothesis_data.get("problem_statement", "")
    if problem:
        kws = _extract_key_phrases(problem)
        keywords.extend(kws[:3])

    # 从 rationale 补充
    rationale = hypothesis_data.get("rationale", "")
    if rationale and len(keywords) < 5:
        kws = _extract_key_phrases(rationale)
        for kw in kws:
            if kw not in keywords:
                keywords.append(kw)
            if len(keywords) >= 5:
                break

    # 从 technical_details / methods 补充
    for field_name in ["technical_details", "methods"]:
        text = hypothesis_data.get(field_name, "")
        if text and len(keywords) < 6:
            kws = _extract_key_phrases(str(text))
            for kw in kws:
                if kw not in keywords:
                    keywords.append(kw)
                if len(keywords) >= 6:
                    break

    return keywords[:6]


def _extract_key_phrases(text: str) -> List[str]:
    """
    从文本中提取关键词/短语。

    策略：
    1. 提取已知的化学/材料术语
    2. 提取英文复合词（含连字符/空格的 2-3 词短语）
    3. 提取单个英文技术名词
    """
    phrases = []

    # 已知化工/材料术语库（匹配优先级最高）
    known_terms = [
        # 催化
        "ORR", "OER", "HER", "CO2RR", "NRR", "oxygen reduction reaction",
        "hydrogen evolution reaction", "oxygen evolution reaction",
        "d-band center", "d-band theory", "single-atom catalyst",
        "single atom catalyst", "SAC", "scaling relation",
        "volcano plot", "adsorption energy", "activation barrier",
        "Brønsted-Evans-Polanyi", "Nørskov", "overpotential",
        # 材料
        "perovskite", "graphene", "MOF", "COF", "MXene",
        "heterostructure", "nanocomposite", "2D material",
        "transition metal", "noble metal", "alloy catalyst",
        # 能源
        "fuel cell", "lithium-ion battery", "supercapacitor",
        "solar cell", "photocatalysis", "electrocatalysis",
        # 方法
        "DFT", "density functional theory", "VASP", "molecular dynamics",
        "machine learning", "deep learning", "neural network",
        "random forest", "gradient boosting", "GNN",
        "graph neural network", "transfer learning",
        # 化工
        "chemical engineering", "reactor design", "process optimization",
        "catalytic reactor", "fluidized bed", "mass transfer",
        "heat transfer", "reaction kinetics", "thermodynamics",
    ]

    text_lower = text.lower()
    for term in known_terms:
        if term.lower() in text_lower and term not in phrases:
            phrases.append(term)

    # 英文复合短语提取（2-3 个词，含连字符）
    compound_pattern = re.findall(
        r'\b([a-z]+(?:[-][a-z]+)+(?:\s+[a-z]+)?)\b',
        text.lower(),
    )
    for cp in compound_pattern:
        if len(cp) > 4 and cp not in phrases:
            phrases.append(cp)

    # 英文技术名词提取（3+ 字母的独立词）
    words = re.findall(r'\b([A-Za-z]{3,})\b', text)
    stop_words = {
        "the", "and", "for", "are", "but", "not", "you", "all",
        "can", "had", "her", "was", "one", "our", "out", "has",
        "been", "from", "have", "this", "that", "with", "they",
        "will", "each", "make", "like", "been", "long", "very",
        "after", "also", "just", "than", "more", "other", "into",
        "could", "would", "should", "which", "their", "about",
        "these", "those", "being", "based", "using", "such",
        "both", "through", "between", "however", "therefore",
        "furthermore", "moreover", "addition", "specifically",
        "include", "includes", "including", "provide", "provides",
        "approach", "method", "result", "results", "study",
        "studies", "research", "investigation", "analysis",
        "proposed", "propose", "present", "presented", "show",
        "shown", "demonstrate", "demonstrated", "suggest",
        "suggested", "indicate", "indicated", "observed",
        "significant", "important", "novel", "new", "high",
        "low", "different", "various", "several", "many",
    }
    for w in words:
        wl = w.lower()
        if wl not in stop_words and wl not in [p.lower() for p in phrases]:
            # 只取首字母大写的可能是专有名词，或全小写的常见术语
            if w[0].isupper() or len(wl) > 5:
                if wl not in phrases:
                    phrases.append(wl)

    return phrases[:8]


def get_user_document_references(user_id: int, source_doc_id: int = 0) -> List[dict]:
    """
    从数据库获取用户导入的文档，转为 References 格式。

    Args:
        user_id: 用户 ID
        source_doc_id: 假设页面当前加载的文档 ID（>0 时只取该篇，=0 时不取任何文献）

    Returns:
        参考文献字典列表
    """
    refs = []
    if not source_doc_id:
        return refs  # 没有指定文档，不取
    try:
        from db import SessionLocal, Document
        with SessionLocal() as session:
            doc = (
                session.query(Document)
                .filter(Document.id == source_doc_id, Document.user_id == user_id)
                .first()
            )
            if doc:
                ref = {
                    "authors": "",
                    "title": doc.title,
                    "journal": doc.source_type,  # pdf/web/other
                    "year": doc.created_at.strftime("%Y") if doc.created_at else "",
                    "doi": "",
                    "source_platform": "user_imported",
                    "is_user_document": True,
                }
                refs.append(ref)
    except Exception as e:
        logger.warning(f"获取用户文档失败: {e}")

    return refs


def merge_references(
    user_refs: List[dict],
    search_refs: List[dict],
    llm_refs: List[dict],
    max_total: int = 15,
) -> List[dict]:
    """
    合并三类参考文献，去重并限制总数。

    优先级：用户导入 > LLM 生成 > 搜索结果

    Args:
        user_refs: 用户导入的文档
        search_refs: OA 文献搜索结果
        llm_refs: LLM OutputAgent 生成的引用
        max_total: 最大参考文献总数

    Returns:
        合并去重后的参考文献列表
    """
    merged = []
    seen_titles = set()
    seen_dois = set()

    def _add_ref(ref: dict) -> bool:
        """添加单条引用，去重返回是否新增"""
        title = ref.get("title", "").lower().strip()
        doi = ref.get("doi", "").lower().strip()

        # DOI 去重
        if doi and doi in seen_dois:
            return False
        # 标题去重
        norm_title = re.sub(r'\s+', ' ', title)
        if norm_title in seen_titles:
            return False

        seen_titles.add(norm_title)
        if doi:
            seen_dois.add(doi)
        merged.append(ref)
        return True

    # 1. 用户导入文档（最高优先级）
    for ref in user_refs:
        _add_ref(ref)

    # 2. LLM 生成的引用
    for ref in llm_refs:
        if len(merged) >= max_total:
            break
        _add_ref(ref)

    # 3. OA 搜索结果
    for ref in search_refs:
        if len(merged) >= max_total:
            break
        _add_ref(ref)

    # 为每条引用编号
    for i, ref in enumerate(merged, 1):
        ref["ref_number"] = i

    return merged
