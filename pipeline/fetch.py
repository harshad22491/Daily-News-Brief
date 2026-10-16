from __future__ import annotations

import logging
import os
from typing import Any, Iterable

try:
    import trafilatura
except ModuleNotFoundError:  # retain snippets in a dependency-light degraded run
    trafilatura = None  # type: ignore[assignment]

import httpx

from .common import Article, Store, http_get, today_ist


logger = logging.getLogger(__name__)

# ScraperAPI budget. The daily cap bounds a single bad edition; it does not bound
# a month, and it counts requests while ScraperAPI bills credits (a target behind
# Cloudflare/DataDome costs 11, not 1), so 30/day could be 330 credits/day rather
# than 30. Three brakes now, in cheapening order of trust:
#   1. SCRAPER_DAILY_CAP    - requests per IST day (unchanged);
#   2. SCRAPER_MONTHLY_CAP  - requests per IST month, same counter table;
#   3. SCRAPER_MIN_REMAINING - floor on the plan balance ScraperAPI itself
#      reports, which is the only number that sees real credit costs and the
#      spend of the other job sharing this key (the bulk-deals mailer).


def _env_int(name: str, default: int) -> int:
    """An unset GitHub Actions variable arrives as "", not as a missing key."""
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r, using %s", name, raw, default)
        return default


SCRAPER_DAILY_CAP = _env_int("SCRAPERAPI_DAILY_CAP", 30)
SCRAPER_MONTHLY_CAP = _env_int("SCRAPERAPI_MONTHLY_CAP", 400)
SCRAPER_MIN_REMAINING = _env_int("SCRAPERAPI_MIN_REMAINING", 100)
SCRAPER_ACCOUNT_API = "https://api.scraperapi.com/account"

_plan = {"checked": False, "remaining": None}


def enrich(selected: dict[str, Any] | Iterable[Article]) -> dict[str, Any] | list[Article]:
    articles = _selected_articles(selected)
    store = Store()
    for article in articles:
        if article.premium or article.headline_only:
            article.full_text = article.snippet[:2500]
            continue
        text = _direct(article.url)
        if len(text) < 400 and os.environ.get("SCRAPERAPI_KEY"):
            text = _scraper(article.url, store)
        article.full_text = (text if len(text) >= 400 else article.snippet)[:2500]
    return selected if isinstance(selected, dict) else articles


def _selected_articles(
    selected: dict[str, Any] | Iterable[Article],
) -> list[Article]:
    if not isinstance(selected, dict):
        return list(selected)
    unique: dict[str, Article] = {}
    for article in selected.get("lead_candidates", []):
        unique[article.id] = article
    for values in selected.get("topics", {}).values():
        for article in values:
            unique[article.id] = article
    return list(unique.values())


def _direct(url: str) -> str:
    if trafilatura is None:
        return ""
    try:
        response = http_get(url)
        return trafilatura.extract(response.text) or ""
    except Exception as exc:
        logger.info("direct article fetch failed for %s: %s", url, exc)
        return ""


def _plan_remaining() -> int | None:
    """Credits left in this billing cycle, or None if the check itself failed.

    requestCount and requestLimit both come from ScraperAPI in whatever unit it
    bills in, so their difference is the usable balance without us having to
    guess whether that unit counts requests or credits. Queried at most once per
    run, and only once a fallback is actually about to fire.
    """
    if _plan["checked"]:
        return _plan["remaining"]
    _plan["checked"] = True
    try:
        response = httpx.get(
            SCRAPER_ACCOUNT_API,
            params={"api_key": os.environ["SCRAPERAPI_KEY"]},
            timeout=15,
        )
        data = response.json()
        _plan["remaining"] = int(data["requestLimit"]) - int(data["requestCount"])
    except Exception as exc:
        # An unknown balance must not stall an edition; caps 1 and 2 still hold.
        logger.info("ScraperAPI account check failed: %s", exc)
        _plan["remaining"] = None
    return _plan["remaining"]


def _counter(store: Store, key: str) -> int:
    rows = store.select("send_log", {"date": key, "recipient": "__system__"})
    return int(rows[0].get("detail", 0)) if rows else 0


def _bump(store: Store, key: str, count: int) -> None:
    store.upsert(
        "send_log",
        {
            "date": key,
            "recipient": "__system__",
            "message_id": "",
            "status": "counter",
            "detail": str(count + 1),
        },
        on=("date", "recipient"),
    )


def _scraper(url: str, store: Store) -> str:
    today = today_ist()
    day_key = f"scraperapi:{today.isoformat()}"
    month_key = f"scraperapi:{today.strftime('%Y-%m')}"
    day_count = _counter(store, day_key)
    month_count = _counter(store, month_key)
    if day_count >= SCRAPER_DAILY_CAP:
        logger.warning("ScraperAPI daily cap reached (%s)", SCRAPER_DAILY_CAP)
        return ""
    if month_count >= SCRAPER_MONTHLY_CAP:
        logger.warning("ScraperAPI monthly cap reached (%s)", SCRAPER_MONTHLY_CAP)
        return ""
    remaining = _plan_remaining()
    if remaining is not None and remaining <= SCRAPER_MIN_REMAINING:
        logger.warning(
            "ScraperAPI plan balance %s at or below the %s reserve - not spending",
            remaining,
            SCRAPER_MIN_REMAINING,
        )
        return ""
    try:
        response = http_get(
            "http://api.scraperapi.com",
            params={"api_key": os.environ["SCRAPERAPI_KEY"], "url": url},
        )
    except Exception as exc:
        logger.info("ScraperAPI fetch failed for %s: %s", url, exc)
        return ""
    finally:
        # Counted on dispatch, not on success: a request that timed out may
        # still have been charged, so the counters have to assume it was.
        _bump(store, day_key, day_count)
        _bump(store, month_key, month_count)
    return trafilatura.extract(response.text) or "" if trafilatura else ""
