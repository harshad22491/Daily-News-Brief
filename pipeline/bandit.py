from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Iterable

from .common import Store, load_config, now_ist


def update_weights(store: Store, recipient: dict[str, Any] | str) -> None:
    recipient_id = recipient["id"] if isinstance(recipient, dict) else recipient
    cfg = load_config()
    _seed(store, recipient_id, cfg)
    target_date = (now_ist().date() - timedelta(days=1)).isoformat()
    ratings = store.select("ratings", {"recipient": recipient_id, "date": target_date})
    for rating in ratings:
        _apply_rating(store, recipient_id, rating)
    _apply_global_negative_decay(store, target_date, recipient_id)


def _seed(store: Store, recipient: str, cfg: dict[str, Any]) -> None:
    if store.select("keyword_weights", {"recipient": recipient}, limit=1):
        return
    updated = now_ist().isoformat()
    rows: list[dict[str, Any]] = []
    for topic, keywords in cfg.get("keywords_seed", {}).items():
        rows.extend(
            _new_weight(recipient, topic, str(keyword), updated) for keyword in keywords
        )
    for source in cfg.get("feeds", {}).get("sources", {}):
        rows.append(_new_weight(recipient, "__source__", source, updated))
    for topic in cfg.get("topics", {}).get("topics", []):
        rows.append(_new_weight(recipient, "__topic__", topic, updated))
    store.upsert("keyword_weights", rows, on=("recipient", "topic", "keyword"))


def _new_weight(recipient: str, topic: str, keyword: str, updated: str) -> dict[str, Any]:
    return {
        "recipient": recipient,
        "topic": topic,
        "keyword": keyword,
        "weight": 1.0,
        "observations": 0,
        "status": "active",
        "changed_by": "seed",
        "updated_at": updated,
    }


def _apply_rating(store: Store, recipient: str, rating: dict[str, Any]) -> None:
    score = int(rating.get("score", 3))
    reward = (score - 3) / 2
    targets = _targets_for_rating(store, recipient, rating)
    for topic, keyword in targets:
        _update_one(store, recipient, topic, keyword, reward)


def _targets_for_rating(
    store: Store, recipient: str, rating: dict[str, Any]
) -> set[tuple[str, str]]:
    kind = rating.get("kind")
    item = str(rating.get("item", ""))
    targets: set[tuple[str, str]] = set()
    articles: list[dict[str, Any]] = []
    if kind == "article":
        articles = store.select("articles", {"id": item}, limit=1)
    elif kind == "topic":
        targets.add(("__topic__", item))
        issue_id = f"{rating.get('date')}:{recipient}"
        issue_items = store.select(
            "issue_items", {"issue_id": issue_id, "topic": item}
        )
        article_ids = [row.get("article_id") for row in issue_items]
        if article_ids:
            articles = store.select("articles", {"id": article_ids})
    for article in articles:
        topic = str(article.get("topic") or "")
        for keyword in article.get("keywords") or []:
            keyword_rows = store.select(
                "keyword_weights",
                {
                    "recipient": recipient,
                    "keyword": str(keyword),
                    "status": "active",
                },
            )
            keyword_topics = {
                str(row.get("topic"))
                for row in keyword_rows
                if row.get("topic") not in {"__source__", "__topic__"}
            }
            if keyword_topics:
                targets.update((keyword_topic, str(keyword)) for keyword_topic in keyword_topics)
            elif topic:
                targets.add((topic, str(keyword)))
        source = str(article.get("source") or "")
        if source:
            targets.add(("__source__", source))
        if topic:
            targets.add(("__topic__", topic))
    return targets


def _update_one(
    store: Store, recipient: str, topic: str, keyword: str, reward: float
) -> None:
    filters = {"recipient": recipient, "topic": topic, "keyword": keyword}
    rows = store.select("keyword_weights", filters, limit=1)
    row = rows[0] if rows else _new_weight(recipient, topic, keyword, now_ist().isoformat())
    current = float(row.get("weight", 1.0))
    row.update(
        {
            "weight": _clamp(0.8 * current + 0.2 * (1 + reward)),
            "observations": int(row.get("observations", 0)) + 1,
            "status": "active",
            "changed_by": "bandit",
            "updated_at": now_ist().isoformat(),
        }
    )
    store.upsert("keyword_weights", row, on=("recipient", "topic", "keyword"))


def _apply_global_negative_decay(
    store: Store, target_date: str, current_recipient: str
) -> None:
    ratings = store.select("ratings", {"date": target_date})
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rating in ratings:
        grouped[(str(rating.get("kind")), str(rating.get("item")))].append(rating)
    for group in grouped.values():
        recipients = {str(row.get("recipient")) for row in group}
        if len(recipients) < 2 or any(int(row.get("score", 3)) > 2 for row in group):
            continue
        if current_recipient not in recipients:
            continue
        sample = group[0]
        for topic, keyword in _targets_for_rating(store, current_recipient, sample):
            if topic in {"__source__", "__topic__"}:
                continue
            filters = {
                "recipient": current_recipient,
                "topic": topic,
                "keyword": keyword,
            }
            rows = store.select("keyword_weights", filters, limit=1)
            if rows:
                store.update(
                    "keyword_weights",
                    filters,
                    {
                        "weight": _clamp(float(rows[0]["weight"]) * 0.9),
                        "changed_by": "bandit",
                        "updated_at": now_ist().isoformat(),
                    },
                )


def _clamp(value: float) -> float:
    return max(0.2, min(3.0, value))
