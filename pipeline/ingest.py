from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

from dateutil import parser as date_parser

try:
    import feedparser
except ModuleNotFoundError:  # permits a snippet-only degraded run before deps install
    feedparser = None  # type: ignore[assignment]

from .common import Article, http_get, now_ist


logger = logging.getLogger(__name__)


class IngestResult(list[Article]):
    def __init__(
        self,
        articles: list[Article],
        feed_health: list[tuple[str, bool, int, str]],
    ) -> None:
        super().__init__(articles)
        self.feed_health = feed_health


def ingest(cfg: dict[str, Any]) -> IngestResult:
    articles: dict[str, Article] = {}
    health: list[tuple[str, bool, int, str]] = []
    window_hours = int(cfg.get("topics", {}).get("window_hours", 24))
    cutoff = now_ist().astimezone(timezone.utc) - timedelta(hours=window_hours)

    for source, source_cfg in cfg.get("feeds", {}).get("sources", {}).items():
        for feed_cfg in source_cfg.get("feeds", []):
            feed_name = f"{source}/{feed_cfg.get('name', 'feed')}"
            try:
                response = http_get(str(feed_cfg["url"]))
                if feed_cfg.get("type", "rss") == "news_sitemap":
                    parsed = _parse_sitemap(
                        response.content, source, source_cfg, feed_cfg, cutoff
                    )
                else:
                    parsed = _parse_rss(
                        response.content, source, source_cfg, feed_cfg, cutoff
                    )
                for article in parsed:
                    articles.setdefault(article.id, article)
                health.append((feed_name, True, len(parsed), ""))
            except Exception as exc:  # a failed feed must not abort an edition
                error = str(exc).replace("\n", " ")[:240]
                logger.warning("feed failed: %s: %s", feed_name, error)
                health.append((feed_name, False, 0, error))
    return IngestResult(list(articles.values()), health)


def _parse_rss(
    content: bytes,
    source: str,
    source_cfg: dict[str, Any],
    feed_cfg: dict[str, Any],
    cutoff: datetime,
) -> list[Article]:
    document = feedparser.parse(content) if feedparser else _stdlib_feed(content)
    if document.bozo:
        raise ValueError(f"invalid feed XML: {document.bozo_exception}")
    if not document.entries:
        raise ValueError("feed contains no entries")
    output: list[Article] = []
    for entry in document.entries:
        published = _entry_datetime(entry)
        if published is None or published < cutoff:
            continue
        url = str(entry.get("link", "")).strip()
        title = _plain_text(str(entry.get("title", "")))
        if not url or not title:
            continue
        snippet = _plain_text(
            str(entry.get("summary") or entry.get("description") or "")
        )
        output.append(
            _article(
                url=url,
                title=title,
                snippet=snippet,
                published=published,
                source=source,
                source_cfg=source_cfg,
                feed_cfg=feed_cfg,
            )
        )
    return output


def _parse_sitemap(
    content: bytes,
    source: str,
    source_cfg: dict[str, Any],
    feed_cfg: dict[str, Any],
    cutoff: datetime,
) -> list[Article]:
    root = ElementTree.fromstring(content)
    output: list[Article] = []
    for url_node in root.findall("{*}url"):
        loc = url_node.findtext("{*}loc", default="").strip()
        news_node = url_node.find("{*}news")
        if news_node is None:
            continue
        title = (news_node.findtext("{*}title", default="") or "").strip()
        raw_date = news_node.findtext("{*}publication_date", default="") or ""
        published = _parse_datetime(raw_date)
        if not loc or not title or published is None or published < cutoff:
            continue
        article = _article(
            url=loc,
            title=_plain_text(title),
            snippet="",
            published=published,
            source=source,
            source_cfg=source_cfg,
            feed_cfg=feed_cfg,
        )
        article.premium = False
        output.append(article)
    if not list(root.findall("{*}url")):
        raise ValueError("sitemap contains no url entries")
    return output


def _article(
    *,
    url: str,
    title: str,
    snippet: str,
    published: datetime,
    source: str,
    source_cfg: dict[str, Any],
    feed_cfg: dict[str, Any],
) -> Article:
    now = now_ist().isoformat()
    premium_text = f"{title} {snippet}".lower()
    premium = any(
        marker in premium_text
        for marker in ("et prime", "mint premium", "toi+")
    ) or "/prime/" in url.lower()
    return Article(
        id=hashlib.sha1(url.encode("utf-8")).hexdigest(),
        url=url,
        source=source,
        source_label=str(source_cfg.get("label", source)),
        feed=str(feed_cfg.get("name", "")),
        title=title,
        snippet=snippet,
        published_at=published.astimezone(timezone.utc).isoformat(),
        premium=premium,
        first_seen=now,
        topic_hint=feed_cfg.get("topic_hint"),
        source_weight=float(source_cfg.get("weight", 1.0)),
        headline_only=bool(source_cfg.get("headline_only", False)),
    )


def _entry_datetime(entry: Any) -> datetime | None:
    structured = entry.get("published_parsed") or entry.get("updated_parsed")
    if structured:
        try:
            return datetime(*structured[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    raw = entry.get("published") or entry.get("updated") or entry.get("pubDate")
    return _parse_datetime(str(raw)) if raw else None


def _parse_datetime(raw: str) -> datetime | None:
    try:
        value = date_parser.parse(raw)
    except (ValueError, TypeError, OverflowError):
        try:
            value = parsedate_to_datetime(raw)
        except (ValueError, TypeError, OverflowError):
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stdlib_feed(content: bytes) -> SimpleNamespace:
    """Minimal RSS/Atom reader used only when feedparser is unavailable."""
    root = ElementTree.fromstring(content)
    nodes = root.findall(".//{*}item") or root.findall(".//{*}entry")
    entries: list[dict[str, str]] = []
    for node in nodes:
        link_node = node.find("{*}link")
        link = ""
        if link_node is not None:
            link = (link_node.text or link_node.get("href") or "").strip()
        entries.append(
            {
                "link": link,
                "title": node.findtext("{*}title", default="") or "",
                "summary": (
                    node.findtext("{*}description", default="")
                    or node.findtext("{*}summary", default="")
                    or node.findtext("{*}content", default="")
                    or ""
                ),
                "published": (
                    node.findtext("{*}pubDate", default="")
                    or node.findtext("{*}published", default="")
                    or node.findtext("{*}updated", default="")
                    or node.findtext("{*}date", default="")
                    or ""
                ),
            }
        )
    return SimpleNamespace(bozo=False, bozo_exception=None, entries=entries)


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain_text(value: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()
