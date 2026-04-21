"""Google Search Console tool for Hermes.

Registers two LLM-callable tools:
- ``gsc_performance`` -- query search performance (clicks, impressions, CTR, position)
- ``gsc_inspect_url`` -- inspect a URL's indexing status

Authentication uses a service account JSON key file.
Path is read from ``GOOGLE_APPLICATION_CREDENTIALS`` env var
(default: /opt/data/gsc-service-account.json).
The site URL is read from ``GSC_SITE_URL`` env var
(default: sc-domain:noriven.com).
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GSC_BASE = "https://searchconsole.googleapis.com/webmasters/v3"
_INSPECT_BASE = "https://searchconsole.googleapis.com/v1"

_cached_token: Dict[str, Any] = {}


def _key_path() -> str:
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/opt/data/gsc-service-account.json")


def _site_url() -> str:
    return os.getenv("GSC_SITE_URL", "sc-domain:noriven.com")


def _check_gsc_available() -> bool:
    return os.path.isfile(_key_path())


def _get_access_token() -> str:
    """Return a cached or fresh OAuth2 access token via JWT assertion."""
    global _cached_token
    now = time.time()
    if _cached_token.get("expires_at", 0) - 60 > now:
        return _cached_token["access_token"]

    try:
        import jwt
        import requests as _requests
    except ImportError as exc:
        raise RuntimeError(f"Missing dependency: {exc}") from exc

    with open(_key_path()) as f:
        key_data = json.load(f)

    claim = {
        "iss": key_data["client_email"],
        "scope": _SCOPES,
        "aud": _TOKEN_URI,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    assertion = jwt.encode(claim, key_data["private_key"], algorithm="RS256")

    resp = _requests.post(_TOKEN_URI, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _cached_token = {
        "access_token": data["access_token"],
        "expires_at": now + data.get("expires_in", 3600),
    }
    return _cached_token["access_token"]


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_get_access_token()}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_gsc_performance(
    start_date: str,
    end_date: str,
    dimensions: Optional[List[str]] = None,
    row_limit: int = 25,
    filter_query: Optional[str] = None,
    site: Optional[str] = None,
    **_,
) -> str:
    try:
        import requests as _requests
    except ImportError as exc:
        return f"error: missing dependency {exc}"

    dims = dimensions or ["query"]
    body: Dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dims,
        "rowLimit": min(row_limit, 100),
    }
    if filter_query:
        body["dimensionFilterGroups"] = [{
            "filters": [{
                "dimension": "query",
                "operator": "contains",
                "expression": filter_query,
            }]
        }]

    site = site or _site_url()
    url = f"{_GSC_BASE}/sites/{site}/searchAnalytics/query"
    try:
        resp = _requests.post(url, headers=_headers(), json=body, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return f"error: {exc}"

    rows = data.get("rows", [])
    if not rows:
        return json.dumps({"site": site, "period": f"{start_date} → {end_date}", "rows": []})

    results = []
    for r in rows:
        entry = dict(zip(dims, r.get("keys", [])))
        entry.update({
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        })
        results.append(entry)

    return json.dumps({
        "site": site,
        "period": f"{start_date} → {end_date}",
        "dimensions": dims,
        "rows": results,
    }, ensure_ascii=False)


def _handle_gsc_inspect_url(page_url: str, site: Optional[str] = None, **_) -> str:
    try:
        import requests as _requests
    except ImportError as exc:
        return f"error: missing dependency {exc}"

    body = {"inspectionUrl": page_url, "siteUrl": site or _site_url()}
    url = f"{_INSPECT_BASE}/urlInspection/index:inspect"
    try:
        resp = _requests.post(url, headers=_headers(), json=body, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return f"error: {exc}"

    result = data.get("inspectionResult", {})
    index = result.get("indexStatusResult", {})
    return json.dumps({
        "url": page_url,
        "verdict": index.get("verdict"),
        "coverage_state": index.get("coverageState"),
        "last_crawl": index.get("lastCrawlTime"),
        "robots_txt": index.get("robotsTxtState"),
        "indexing_state": index.get("indexingState"),
        "page_fetch": index.get("pageFetchState"),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SITE_DESCRIPTION = (
    "GSC site identifier. Available: 'sc-domain:noriven.com' (default) or "
    "'sc-domain:glutax.ca'. Use the sc-domain: prefix, not https://."
)

GSC_PERFORMANCE_SCHEMA = {
    "name": "gsc_performance",
    "description": (
        "Query Google Search Console performance data (clicks, impressions, CTR, position) "
        "for noriven.com or glutax.ca. Use dimensions=['query'] for keywords, "
        "['page'] for URLs, or ['date'] for time series."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start_date": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format.",
            },
            "end_date": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format. GSC has a ~2 day lag.",
            },
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": ["query", "page", "country", "device", "date"]},
                "description": "Breakdown dimensions. Default: ['query'].",
            },
            "row_limit": {
                "type": "integer",
                "description": "Max rows to return (1–100). Default: 25.",
            },
            "filter_query": {
                "type": "string",
                "description": "Optional keyword filter (contains match on query dimension).",
            },
            "site": {
                "type": "string",
                "description": _SITE_DESCRIPTION,
            },
        },
        "required": ["start_date", "end_date"],
    },
}

GSC_INSPECT_URL_SCHEMA = {
    "name": "gsc_inspect_url",
    "description": (
        "Inspect the Google indexing status of a specific URL. "
        "Returns verdict (PASS/FAIL/NEUTRAL), coverage state, last crawl time, robots.txt status. "
        "Works for noriven.com and glutax.ca."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "page_url": {
                "type": "string",
                "description": "Full URL to inspect (e.g. 'https://noriven.com/blog/article').",
            },
            "site": {
                "type": "string",
                "description": _SITE_DESCRIPTION,
            },
        },
        "required": ["page_url"],
    },
}

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from tools.registry import registry, tool_error  # noqa: E402

registry.register(
    name="gsc_performance",
    toolset="search_console",
    schema=GSC_PERFORMANCE_SCHEMA,
    handler=_handle_gsc_performance,
    check_fn=_check_gsc_available,
    emoji="📊",
)

registry.register(
    name="gsc_inspect_url",
    toolset="search_console",
    schema=GSC_INSPECT_URL_SCHEMA,
    handler=_handle_gsc_inspect_url,
    check_fn=_check_gsc_available,
    emoji="🔍",
)
