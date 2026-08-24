from __future__ import annotations

import argparse
import html as html_module
import logging
import re
from pathlib import Path
from typing import Any

from . import bandit
from .common import Article, Store, load_config, now_ist, recipients, today_ist
from .dedup import cluster
from .fetch import enrich
from .filter import assign
from .generate import GenerationError, generate
from .ingest import ingest
from .render import render
from .select import select
from .send import send


logger = logging.getLogger(__name__)


def run_daily(*, dryrun: bool = False) -> int:
    cfg = load_config()
    store = Store()
    active = recipients(store)
    if not active:
        logger.info("no active recipients configured")
        return 0
    date_text = today_ist().isoformat()
    sent = {
        row.get("recipient")
        for row in store.select("send_log", {"date": date_text})
        if row.get("status") in {"sent", "scheduled"}
    }
    if all(recipient["id"] in sent for recipient in active):
        logger.info("all active recipients already sent for %s", date_text)
        return 0

    ingestion = ingest(cfg)
    feed_missing = [name for name, ok, _, _ in ingestion.feed_health if not ok]
    failures = 0
    for recipient in active:
        if recipient["id"] in sent:
            logger.info("already sent; skipping %s", recipient["id"])
            continue
        try:
            _run_recipient(
                store,
                cfg,
                list(ingestion),
                recipient,
                feed_missing,
                dryrun=dryrun,
            )
        except Exception:
            failures += 1
            logger.exception("recipient run failed: %s", recipient["id"])
    return 1 if failures else 0


def _run_recipient(
    store: Store,
    cfg: dict[str, Any],
    raw_articles: list[Article],
    recipient: dict[str, Any],
    feed_missing: list[str],
    *,
    dryrun: bool,
) -> None:
    bandit.update_weights(store, recipient)
    weights = store.select("keyword_weights", {"recipient": recipient["id"]})
    assigned = assign(raw_articles, weights)
    clustered = cluster(
        assigned,
        float(cfg.get("topics", {}).get("dedup", {}).get("jaccard_threshold", 0.5)),
    )
    selection = select(
        clustered,
        cfg,
        f"{today_ist().isoformat()}:{recipient['id']}",
    )
    enrich(selection)
    edition_input = _edition_input(selection, recipient)
    partial = list(feed_missing)
    try:
        edition = generate(edition_input, "prompts")
        edition["date_ist"] = today_ist().isoformat()
    except Exception as exc:  # any generation failure degrades, never kills the day
        logger.warning("using degraded edition for %s: %s", recipient["id"], exc)
        partial.append("AI-written edition unavailable; showing headline summaries")
        edition = _degraded_edition(selection)
    articles_by_id = {article.id: article for article in clustered}
    subject, rendered = render(edition, articles_by_id, recipient, cfg, partial)
    output_dir = Path("work") / today_ist().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_filename(recipient['id'])}.html"
    output_path.write_text(rendered, encoding="utf-8")

    if clustered:
        store.upsert("articles", [article.to_record() for article in clustered], on="id")
    issue_id = f"{today_ist().isoformat()}:{recipient['id']}"
    issue = {
        "id": issue_id,
        "date": today_ist().isoformat(),
        "recipient": recipient["id"],
        "sent_at": None,
        "partial": bool(partial),
        "subject": subject,
    }
    store.upsert("issues", issue, on="id")
    issue_items = _issue_items(issue_id, edition)
    if issue_items:
        store.upsert(
            "issue_items", issue_items, on=("issue_id", "article_id", "slot")
        )
    if not dryrun:
        send(recipient, subject, rendered, now_ist())
        store.update("issues", {"id": issue_id}, {"sent_at": now_ist().isoformat()})
    print(
        f"recipient={recipient['id']} articles={len(clustered)} "
        f"partial={str(bool(partial)).lower()} dryrun={str(dryrun).lower()} "
        f"output={output_path}"
    )


def _edition_input(
    selection: dict[str, Any], recipient: dict[str, Any]
) -> dict[str, Any]:
    return {
        "date_ist": today_ist().isoformat(),
        "recipient": recipient["id"],
        "topics": {
            topic: [article.prompt_record() for article in articles]
            for topic, articles in selection["topics"].items()
        },
        "briefly_noted": {
            topic: [article.prompt_record(include_text=False) for article in articles]
            for topic, articles in selection["briefly_noted"].items()
        },
        "lead_candidates": [
            article.prompt_record() for article in selection["lead_candidates"]
        ],
    }


def _degraded_edition(selection: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, list[dict[str, str]]] = {}
    noted: dict[str, list[dict[str, str]]] = {}
    for topic, articles in selection["topics"].items():
        sections[topic] = []
        for article in articles:
            title = html_module.escape(article.title)
            url = html_module.escape(article.url, quote=True)
            snippet = html_module.escape(article.snippet)
            premium = " [Premium]" if article.premium else ""
            body = f'<p><a href="{url}">{title}</a>{premium}'
            if snippet:
                body += f" — {snippet}"
            body += "</p>"
            sections[topic].append({"article_id": article.id, "html": body})
        noted[topic] = [
            {
                "article_id": article.id,
                "line": (
                    f'<a href="{html_module.escape(article.url, quote=True)}">'
                    f"{html_module.escape(article.title)}</a>"
                ),
            }
            for article in selection["briefly_noted"].get(topic, [])
        ]
    final_article = next(
        iter(selection.get("lead_candidates") or []),
        None,
    )
    return {
        "date_ist": today_ist().isoformat(),
        "subject_suffix": "Headlines at a glance",
        "masthead": f"Your briefing for {today_ist().strftime('%A, %d %B %Y')}.",
        "lead": None,
        "sections": sections,
        "briefly_noted": noted,
        "number_of_day": None,
        "and_finally": (
            {
                "article_id": final_article.id,
                "html": "<p>That is the latest from today’s available feeds.</p>",
            }
            if final_article
            else None
        ),
    }


def _issue_items(issue_id: str, edition: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lead = edition.get("lead") or {}
    if lead.get("article_id"):
        rows.append(
            {
                "issue_id": issue_id,
                "article_id": str(lead["article_id"]),
                "topic": "",
                "slot": "lead",
            }
        )
    for topic, briefs in (edition.get("sections") or {}).items():
        rows.extend(
            {
                "issue_id": issue_id,
                "article_id": str(brief["article_id"]),
                "topic": topic,
                "slot": "brief",
            }
            for brief in briefs
            if brief.get("article_id")
        )
    for topic, items in (edition.get("briefly_noted") or {}).items():
        rows.extend(
            {
                "issue_id": issue_id,
                "article_id": str(item["article_id"]),
                "topic": topic,
                "slot": "noted",
            }
            for item in items
            if item.get("article_id")
        )
    number = edition.get("number_of_day") or {}
    if number.get("source_article_id"):
        rows.append(
            {
                "issue_id": issue_id,
                "article_id": str(number["source_article_id"]),
                "topic": "",
                "slot": "number",
            }
        )
    final = edition.get("and_finally") or {}
    if final.get("article_id"):
        rows.append(
            {
                "issue_id": issue_id,
                "article_id": str(final["article_id"]),
                "topic": "",
                "slot": "finally",
            }
        )
    unique = {
        (row["issue_id"], row["article_id"], row["slot"]): row for row in rows
    }
    return list(unique.values())


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9@._+-]", "_", value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Daily News Briefing pipeline")
    parser.add_argument("--mode", choices=["daily"], default="daily")
    parser.add_argument("--dryrun", action="store_true")
    args = parser.parse_args()
    return run_daily(dryrun=args.dryrun)


if __name__ == "__main__":
    raise SystemExit(main())
