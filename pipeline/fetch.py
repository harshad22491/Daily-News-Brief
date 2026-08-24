from __future__ import annotations

import logging
import os
from typing import Any, Iterable

try:
    import trafilatura
except ModuleNotFoundError:  # retain snippets in a dependency-light degraded run
    trafilatura = None  # type: ignore[assignment]

from .common import Article, Store, http_get, today_ist


logger = logging.getLogger(__name__)
SCRAPER_DAILY_CAP = 30


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


def _scraper(url: str, store: Store) -> str:
    counter_date = f"scraperapi:{today_ist().isoformat()}"
    filters = {"date": counter_date, "recipient": "__system__"}
    rows = store.select("send_log", filters)
    count = int(rows[0].get("detail", 0)) if rows else 0
    if count >= SCRAPER_DAILY_CAP:
        logger.warning("ScraperAPI daily cap reached")
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
        store.upsert(
            "send_log",
            {
                "date": counter_date,
                "recipient": "__system__",
                "message_id": "",
                "status": "counter",
                "detail": str(count + 1),
            },
            on=("date", "recipient"),
        )
    return trafilatura.extract(response.text) or "" if trafilatura else ""
