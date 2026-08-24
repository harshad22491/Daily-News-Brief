from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dateutil import parser as date_parser  # noqa: E402

try:  # noqa: E402
    import feedparser
except ModuleNotFoundError:  # noqa: E402
    feedparser = None  # type: ignore[assignment]

from pipeline.common import http_get, load_config  # noqa: E402


def check_feed(
    source: str, source_cfg: dict[str, Any], feed_cfg: dict[str, Any]
) -> tuple[str, str, str, int, str]:
    label = str(source_cfg.get("label", source))
    name = str(feed_cfg.get("name", "feed"))
    try:
        content = http_get(str(feed_cfg["url"]), timeout=10).content
        root = ElementTree.fromstring(content)
        if feed_cfg.get("type", "rss") == "news_sitemap":
            nodes = root.findall("{*}url")
            if not nodes:
                raise ValueError("no sitemap entries")
            dates = [
                _date(node.findtext(".//{*}publication_date", default=""))
                for node in nodes
            ]
            entries = len(nodes)
        else:
            if feedparser:
                parsed = feedparser.parse(content)
                if parsed.bozo:
                    raise ValueError(f"invalid feed XML: {parsed.bozo_exception}")
                entries_data = list(parsed.entries)
            else:
                entries_data = _stdlib_entries(root)
            if not entries_data:
                raise ValueError("no feed entries")
            dates = [
                _date(
                    str(
                        entry.get("published")
                        or entry.get("updated")
                        or entry.get("pubDate")
                        or ""
                    )
                )
                for entry in entries_data
            ]
            entries = len(entries_data)
        newest = max((value for value in dates if value), default=None)
        newest_text = newest.astimezone(timezone.utc).isoformat() if newest else "unknown"
        return label, name, "OK", entries, newest_text
    except Exception as exc:
        return label, name, f"FAIL: {str(exc)[:90]}", 0, "-"


def main() -> int:
    cfg = load_config()
    jobs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for source, source_cfg in cfg.get("feeds", {}).get("sources", {}).items():
        jobs.extend(
            (source, source_cfg, feed_cfg)
            for feed_cfg in source_cfg.get("feeds", [])
        )
    results: list[tuple[str, str, str, int, str]] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as executor:
        futures = [executor.submit(check_feed, *job) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: (row[0], row[1]))
    headers = ("SOURCE", "FEED", "STATUS", "ENTRIES", "NEWEST (UTC)")
    widths = [len(value) for value in headers]
    for row in results:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))
    print(_format_row(headers, widths))
    print(_format_row(tuple("-" * width for width in widths), widths))
    for row in results:
        print(_format_row(row, widths))
    failures = sum(1 for row in results if row[2].startswith("FAIL"))
    print(f"\n{len(results) - failures}/{len(results)} feeds healthy")
    return 1 if results and failures / len(results) > 0.30 else 0


def _date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = date_parser.parse(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _stdlib_entries(root: ElementTree.Element) -> list[dict[str, str]]:
    nodes = root.findall(".//{*}item") or root.findall(".//{*}entry")
    return [
        {
            "published": (
                node.findtext("{*}pubDate", default="")
                or node.findtext("{*}published", default="")
                or node.findtext("{*}updated", default="")
                or node.findtext("{*}date", default="")
                or ""
            )
        }
        for node in nodes
    ]


def _format_row(row: tuple[Any, ...], widths: list[int]) -> str:
    return " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))


if __name__ == "__main__":
    raise SystemExit(main())
