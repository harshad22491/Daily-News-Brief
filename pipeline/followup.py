from __future__ import annotations

import argparse
import json
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from .common import Article, Store, load_config, now_ist
from .dedup import cluster
from .filter import assign
from .generate import call_prompt
from .ingest import ingest
from .send import send


def run_followup(request_id: str) -> None:
    store = Store()
    requests = store.select("followup_requests", {"id": request_id}, limit=1)
    if not requests:
        raise RuntimeError(f"follow-up request not found: {request_id}")
    request = requests[0]
    article_rows = store.select(
        "articles", {"id": request.get("article_id")}, limit=1
    )
    if not article_rows:
        raise RuntimeError(f"article not found for follow-up request: {request_id}")
    original = Article.from_record(article_rows[0])
    recipient_rows = store.select(
        "recipients", {"id": request.get("recipient"), "active": True}, limit=1
    )
    if not recipient_rows:
        raise RuntimeError("follow-up recipient is missing or inactive")
    recipient = recipient_rows[0]

    related = _related_articles(store, original, str(request["recipient"]))
    prompt = Path("prompts/followup.md").read_text(encoding="utf-8")
    prompt += "\n\n## INPUT JSON\n" + json.dumps(
        {
            "article": original.prompt_record(),
            "related": [article.prompt_record() for article in related],
        },
        ensure_ascii=False,
    )
    result = call_prompt(prompt)
    subject = str(result.get("subject") or f"More on: {original.title[:70]}")
    body = str(result.get("html") or "")
    if not body:
        raise RuntimeError("follow-up generation returned empty HTML")
    send(recipient, subject, _wrap_email(body), now_ist(), force_immediate=True)
    store.update(
        "followup_requests",
        {"id": request_id},
        {"status": "sent"},
    )


def _related_articles(
    store: Store, original: Article, recipient: str
) -> list[Article]:
    cutoff = (now_ist().astimezone(timezone.utc) - timedelta(days=7)).isoformat()
    archived = [
        Article.from_record(row)
        for row in store.select("articles", {"published_at__gte": cutoff})
    ]
    cfg = load_config()
    fresh_result = ingest(cfg)
    weights = store.select("keyword_weights", {"recipient": recipient})
    fresh = cluster(assign(list(fresh_result), weights))
    candidates: dict[str, Article] = {
        article.id: article for article in [*archived, *fresh] if article.id != original.id
    }
    original_keywords = set(original.keywords)
    related = [
        article
        for article in candidates.values()
        if (
            original.cluster_id
            and article.cluster_id
            and article.cluster_id == original.cluster_id
        )
        or len(original_keywords & set(article.keywords)) >= 2
    ]
    related.sort(
        key=lambda article: (article.score, article.published_at, article.id),
        reverse=True,
    )
    return related[:12]


def _wrap_email(body: str) -> str:
    return (
        '<!doctype html><html><body style="margin:0;padding:24px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;"
        'color:#202020;background:#ffffff;"><div style="max-width:620px;'
        f'margin:0 auto;font-size:16px;line-height:25px;">{body}</div></body></html>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a requested deep dive")
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    run_followup(args.request_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
