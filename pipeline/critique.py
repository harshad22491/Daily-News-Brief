from __future__ import annotations

import argparse
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from .common import Store, now_ist
from .generate import call_prompt
from .keywords_html import generate as generate_keywords_html


logger = logging.getLogger(__name__)
PROMPT_FILES = {"prompts/edition.md", "prompts/style-guide.md"}


def run_critique() -> dict[str, Any]:
    store = Store()
    data = _weekly_data(store)
    prompt = Path("prompts/critique.md").read_text(encoding="utf-8")
    prompt += "\n\n## INPUT JSON\n" + json.dumps(data, ensure_ascii=False)
    result = call_prompt(prompt)
    _apply_previous_verdict(store, result.get("previous_change_verdict") or {})
    _apply_keyword_changes(store, result.get("keyword_changes") or [])
    _apply_prompt_change(store, result.get("prompt_change"))
    generate_keywords_html(store)
    logger.info("weekly critique: %s", result.get("diagnosis", "no diagnosis"))
    return result


def _weekly_data(store: Store) -> dict[str, Any]:
    cutoff = (now_ist().date() - timedelta(days=6)).isoformat()
    ratings = store.select("ratings", {"date__gte": cutoff}, order="date.asc")
    annotated_ratings: list[dict[str, Any]] = []
    for rating in ratings:
        row = dict(rating)
        if rating.get("kind") == "article":
            articles = store.select("articles", {"id": rating.get("item")}, limit=1)
            if articles:
                row["article"] = {
                    key: articles[0].get(key)
                    for key in ("keywords", "source", "topic", "title")
                }
        annotated_ratings.append(row)
    issues = store.select("issues", {"date__gte": cutoff}, order="date.asc")
    issue_exports: list[dict[str, Any]] = []
    for issue in issues:
        exported = dict(issue)
        path = Path("work") / str(issue.get("date")) / (
            _safe_path_component(str(issue.get("recipient"))) + ".html"
        )
        if path.exists():
            exported["html"] = path.read_text(encoding="utf-8")
        issue_exports.append(exported)
    prompts = {
        path.as_posix(): path.read_text(encoding="utf-8")
        for path in Path("prompts").glob("*.md")
    }
    return {
        "week_ratings": annotated_ratings,
        "issues": issue_exports,
        "prompts": prompts,
        "keyword_weights": store.select(
            "keyword_weights", order="recipient.asc,topic.asc,weight.desc"
        ),
        "prompt_versions": store.select(
            "prompt_versions", order="created_at.desc", limit=10
        ),
    }


def _apply_keyword_changes(store: Store, changes: list[dict[str, Any]]) -> None:
    for change in changes:
        recipient = str(change.get("recipient", ""))
        topic = str(change.get("topic", ""))
        keyword = str(change.get("keyword", "")).strip()
        action = change.get("action")
        if not recipient or not topic or not keyword or action not in {"add", "retire"}:
            logger.warning("ignoring invalid keyword change: %r", change)
            continue
        key = {"recipient": recipient, "topic": topic, "keyword": keyword}
        existing = store.select("keyword_weights", key, limit=1)
        row = existing[0] if existing else {
            **key,
            "weight": 1.0,
            "observations": 0,
        }
        row.update(
            {
                "status": "active" if action == "add" else "retired",
                "changed_by": "critique",
                "updated_at": now_ist().isoformat(),
            }
        )
        store.upsert(
            "keyword_weights", row, on=("recipient", "topic", "keyword")
        )


def _apply_prompt_change(store: Store, change: dict[str, Any] | None) -> None:
    if not change:
        return
    filename = str(change.get("file", ""))
    version = now_ist().strftime("%Y%m%dT%H%M%S%z")
    record: dict[str, Any] = {
        "version": version,
        "file": filename,
        "diff": "",
        "rationale": str(change.get("rationale", "")),
        "created_at": now_ist().isoformat(),
        "verdict": "NOT_APPLIED",
    }
    if filename not in PROMPT_FILES:
        record["diff"] = json.dumps({"error": "file is not editable"})
        store.insert("prompt_versions", record)
        return
    path = Path(filename)
    before = path.read_text(encoding="utf-8")
    find = str(change.get("find", ""))
    replace = str(change.get("replace", ""))
    if find and find in before:
        after = before.replace(find, replace, 1)
        path.write_text(after, encoding="utf-8")
        record["verdict"] = "APPLIED"
        record["diff"] = json.dumps(
            {"before": before, "after": after}, ensure_ascii=False
        )
    else:
        record["diff"] = json.dumps({"error": "exact match not found"})
    store.insert("prompt_versions", record)


def _apply_previous_verdict(store: Store, verdict: dict[str, Any]) -> None:
    version = str(verdict.get("version", ""))
    decision = verdict.get("verdict")
    if not version or decision not in {"KEEP", "REVERT", "NO_DATA"}:
        return
    rows = store.select("prompt_versions", {"version": version}, limit=1)
    if not rows:
        logger.warning("prompt version for verdict not found: %s", version)
        return
    row = rows[0]
    if decision == "REVERT":
        try:
            saved = json.loads(row.get("diff") or "{}")
            before = saved["before"]
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("prompt version cannot be reverted: %s", version)
        else:
            filename = str(row.get("file", ""))
            if filename in PROMPT_FILES:
                Path(filename).write_text(str(before), encoding="utf-8")
    store.update("prompt_versions", {"version": version}, {"verdict": decision})


def _safe_path_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "@._+-" else "_"
        for character in value
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the weekly editorial critique")
    parser.parse_args()
    run_critique()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
