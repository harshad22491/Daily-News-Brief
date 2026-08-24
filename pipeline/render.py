from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import Article, today_ist


def render(
    edition: dict[str, Any],
    articles_by_id: dict[str, Article],
    recipient: dict[str, Any],
    cfg: dict[str, Any],
    partial_missing: list[str],
) -> tuple[str, str]:
    issue_date = _issue_date(edition.get("date_ist"))
    suffix = str(edition.get("subject_suffix") or "Your daily briefing").strip()
    subject = f"Daily News Briefing – {issue_date.strftime('%a, %d %b %Y')}: {suffix}"
    topics_cfg = cfg.get("topics", cfg)
    topic_names = list(topics_cfg.get("topics", []))
    labels = topics_cfg.get("labels", {})
    base = os.environ.get("RATE_BASE", "").rstrip("/")
    token = str(recipient.get("token", ""))

    sections: list[dict[str, Any]] = []
    edition_sections = edition.get("sections", {}) or {}
    edition_noted = edition.get("briefly_noted", {}) or {}
    for topic in topic_names:
        briefs: list[dict[str, Any]] = []
        for brief in edition_sections.get(topic, []) or []:
            article = articles_by_id.get(str(brief.get("article_id", "")))
            if article is None:
                continue
            briefs.append(
                {
                    "article": article,
                    "html": str(brief.get("html", "")),
                    "rating": _rating(base, token, "article", article.id, issue_date),
                }
            )
        noted: list[dict[str, Any]] = []
        for item in edition_noted.get(topic, []) or []:
            article = articles_by_id.get(str(item.get("article_id", "")))
            if article is None:
                continue
            noted.append({"article": article, "line": str(item.get("line", ""))})
        sections.append(
            {
                "name": topic,
                "label": labels.get(topic, topic.title()),
                "briefs": briefs,
                "noted": noted,
                "rating": _rating(base, token, "topic", topic, issue_date),
            }
        )

    lead_data = edition.get("lead") or {}
    lead_article = articles_by_id.get(str(lead_data.get("article_id", "")))
    number_data = edition.get("number_of_day") or {}
    number_article = articles_by_id.get(
        str(number_data.get("source_article_id", ""))
    )
    final_data = edition.get("and_finally") or {}
    final_article = articles_by_id.get(str(final_data.get("article_id", "")))
    unsubscribe_email = os.environ.get("UNSUBSCRIBE_EMAIL") or os.environ.get(
        "SENDER_EMAIL", ""
    )
    unsubscribe_url = (
        "mailto:" + unsubscribe_email + "?" + urlencode({"subject": "unsubscribe"})
        if unsubscribe_email
        else ""
    )

    template_dir = Path(os.environ.get("TEMPLATES_DIR", "templates"))
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("email.html.j2")
    html = template.render(
        subject=subject,
        issue_date=issue_date,
        masthead=edition.get("masthead", ""),
        partial_missing=list(dict.fromkeys(partial_missing)),
        lead=lead_data,
        lead_article=lead_article,
        sections=sections,
        number=number_data,
        number_article=number_article,
        final=final_data,
        final_article=final_article,
        ratings_online=bool(base and token),
        unsubscribe_url=unsubscribe_url,
    )
    return subject, html


def _rating(
    base: str,
    token: str,
    kind: str,
    item: str,
    issue_date: date,
) -> list[dict[str, str | int]]:
    if not base or not token:
        return []
    return [
        {
            "score": score,
            "url": base
            + "/rate?"
            + urlencode(
                {
                    "t": token,
                    "kind": kind,
                    "item": item,
                    "s": score,
                    "d": issue_date.isoformat(),
                }
            ),
        }
        for score in range(1, 6)
    ]


def _issue_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return today_ist()
