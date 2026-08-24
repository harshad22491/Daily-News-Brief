from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from dateutil import parser as date_parser

from .common import Article


def assign(
    articles: Iterable[Article], weights: Iterable[dict[str, Any]] | dict[str, Any]
) -> list[Article]:
    keyword_rows = _weight_rows(weights)
    by_topic: dict[str, list[dict[str, Any]]] = {}
    source_adaptive: dict[str, float] = {}
    topic_adaptive: dict[str, float] = {}
    for row in keyword_rows:
        topic = str(row.get("topic", ""))
        if row.get("status", "active") != "active":
            continue
        if topic == "__source__":
            source_adaptive[str(row.get("keyword", ""))] = float(
                row.get("weight", 1.0)
            )
        elif topic == "__topic__":
            topic_adaptive[str(row.get("keyword", ""))] = float(
                row.get("weight", 1.0)
            )
        else:
            by_topic.setdefault(topic, []).append(row)

    output: list[Article] = []
    now = datetime.now(timezone.utc)
    for original in articles:
        article = copy.deepcopy(original)
        haystack = f"{article.title} {article.snippet}"
        topic_scores: dict[str, float] = {}
        matches: list[tuple[str, str, float, int]] = []
        for topic, rows in by_topic.items():
            subtotal = 0.0
            for row in rows:
                keyword = str(row.get("keyword", "")).strip()
                if keyword and _matches_keyword(haystack, keyword):
                    weight = float(row.get("weight", 1.0))
                    observations = int(row.get("observations", 0))
                    matches.append((topic, keyword, weight, observations))
                    subtotal += weight
            if subtotal:
                topic_scores[topic] = subtotal

        if topic_scores:
            maximum = max(topic_scores.values())
            tied = [topic for topic, score in topic_scores.items() if score == maximum]
            article.topic = (
                article.topic_hint
                if len(tied) > 1 and article.topic_hint
                else sorted(tied)[0]
            )
        elif article.topic_hint:
            article.topic = article.topic_hint
        else:
            continue

        article.keywords = list(dict.fromkeys(keyword for _, keyword, _, _ in matches))
        article.keyword_observations = {
            keyword: observations for _, keyword, _, observations in matches
        }
        raw_score = sum(weight for _, _, weight, _ in matches)
        published = _published(article.published_at)
        age_hours = max(0.0, (now - published).total_seconds() / 3600)
        recency = max(0.6, 1.0 - (0.4 * min(age_hours, 24.0) / 24.0))
        source_factor = article.source_weight * source_adaptive.get(article.source, 1.0)
        topic_factor = topic_adaptive.get(article.topic or "", 1.0)
        article.score = raw_score * source_factor * topic_factor * recency
        output.append(article)
    return output


def _weight_rows(
    weights: Iterable[dict[str, Any]] | dict[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(weights, dict):
        return list(weights)
    rows: list[dict[str, Any]] = []
    for topic, values in weights.items():
        if isinstance(values, dict):
            for keyword, value in values.items():
                if isinstance(value, dict):
                    rows.append({"topic": topic, "keyword": keyword, **value})
                else:
                    rows.append(
                        {"topic": topic, "keyword": keyword, "weight": value}
                    )
        elif isinstance(values, list):
            rows.extend(
                {"topic": topic, "keyword": keyword, "weight": 1.0}
                for keyword in values
            )
    return rows


def _matches_keyword(text: str, keyword: str) -> bool:
    pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _published(raw: str) -> datetime:
    try:
        value = date_parser.parse(raw)
    except (ValueError, TypeError, OverflowError):
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
