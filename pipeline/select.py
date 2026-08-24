from __future__ import annotations

import random
from typing import Any, Iterable

from .common import Article


def select(
    articles: Iterable[Article], cfg: dict[str, Any], rng_seed: str | int
) -> dict[str, Any]:
    topic_cfg = cfg.get("topics", cfg)
    topics = list(topic_cfg.get("topics", []))
    selection_cfg = topic_cfg.get("selection", {})
    per_topic = int(selection_cfg.get("per_topic", 5))
    noted_min = int(selection_cfg.get("briefly_noted_min", 3))
    noted_max = int(selection_cfg.get("briefly_noted_max", 5))
    exploration_share = float(selection_cfg.get("exploration_share", 0.1))
    rng = random.Random(str(rng_seed))

    selected: dict[str, list[Article]] = {topic: [] for topic in topics}
    noted: dict[str, list[Article]] = {topic: [] for topic in topics}
    primaries = [article for article in articles if article.is_primary]
    for topic in topics:
        candidates = sorted(
            (article for article in primaries if article.topic == topic),
            key=lambda article: (article.score, article.published_at, article.id),
            reverse=True,
        )
        exploration_slots = int(per_topic * exploration_share)
        fraction = per_topic * exploration_share - exploration_slots
        if rng.random() < fraction:
            exploration_slots += 1
        exploration_candidates = [
            article
            for article in candidates
            if article.keywords
            and any(value < 3 for value in article.keyword_observations.values())
        ]
        explored: list[Article] = []
        pool = exploration_candidates[:]
        while pool and len(explored) < exploration_slots:
            choice = rng.choice(pool)
            explored.append(choice)
            pool.remove(choice)
        chosen_ids = {article.id for article in explored}
        exploitation = [article for article in candidates if article.id not in chosen_ids]
        topic_selected = exploitation[: max(0, per_topic - len(explored))] + explored
        topic_selected.sort(
            key=lambda article: (article.score, article.published_at, article.id),
            reverse=True,
        )
        selected[topic] = topic_selected
        selected_ids = {article.id for article in topic_selected}
        remaining = [article for article in candidates if article.id not in selected_ids]
        count = min(noted_max, len(remaining))
        if count < noted_min:
            count = len(remaining)
        noted[topic] = remaining[:count]

    all_selected = [article for values in selected.values() for article in values]
    lead_candidates = sorted(
        all_selected,
        key=lambda article: (article.score, article.published_at, article.id),
        reverse=True,
    )[:3]
    return {
        "topics": selected,
        "briefly_noted": noted,
        "lead_candidates": lead_candidates,
    }
