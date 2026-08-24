from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable

from .common import Article


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "will",
    "with",
}


def cluster(articles: Iterable[Article], threshold: float = 0.5) -> list[Article]:
    items = list(articles)
    tokens = [_tokens(article.title) for article in items]
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    inverted: dict[str, list[int]] = defaultdict(list)
    candidates: set[tuple[int, int]] = set()
    for index, words in enumerate(tokens):
        for word in words:
            for earlier in inverted[word]:
                candidates.add((earlier, index))
            inverted[word].append(index)
    for left, right in candidates:
        union_size = len(tokens[left] | tokens[right])
        similarity = (
            len(tokens[left] & tokens[right]) / union_size if union_size else 0.0
        )
        if similarity >= threshold:
            union(left, right)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        groups[find(index)].append(index)
    for member_indexes in groups.values():
        primary_index = max(
            member_indexes,
            key=lambda index: (items[index].score, items[index].published_at, items[index].id),
        )
        primary = items[primary_index]
        stable_cluster_id = hashlib.sha1(
            "|".join(sorted(items[index].id for index in member_indexes)).encode()
        ).hexdigest()
        primary.cluster_links = [
            {"source": items[index].source_label, "url": items[index].url}
            for index in member_indexes
            if index != primary_index
        ]
        for index in member_indexes:
            items[index].cluster_id = stable_cluster_id
            items[index].is_primary = index == primary_index
            if index != primary_index:
                items[index].cluster_links = []
    return items


def _tokens(title: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", title.lower())
        if token not in STOP_WORDS
    }
