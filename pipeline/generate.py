from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


class GenerationError(RuntimeError):
    pass


def generate(edition_input: dict[str, Any], prompts_dir: str | Path) -> dict[str, Any]:
    prompt_path = Path(prompts_dir) / "edition.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt += "\n\n## INPUT JSON\n" + json.dumps(
        edition_input, ensure_ascii=False, default=str
    )
    allowed_ids = _article_ids(edition_input)
    correction = ""
    errors: list[str] = []
    for attempt in range(3):
        try:
            raw = _run_claude(prompt + correction)
            parsed = _parse_json(raw)
            _validate(parsed, allowed_ids)
            return parsed
        except (GenerationError, OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            correction = (
                "\n\nCORRECTION: Return strict JSON only. Cite only article_id values "
                "present in the input. The previous response failed validation: "
                + str(exc)[:400]
            )
            if attempt == 2:
                break
    raise GenerationError("Claude generation failed: " + " | ".join(errors))


def call_prompt(prompt: str, retries: int = 2) -> dict[str, Any]:
    errors: list[str] = []
    for _ in range(retries + 1):
        try:
            return _parse_json(_run_claude(prompt))
        except (GenerationError, OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
    raise GenerationError("Claude generation failed: " + " | ".join(errors))


def _run_claude(prompt: str) -> str:
    model = os.environ.get("MODEL", "sonnet")
    try:
        completed = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "text"],
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=600,
            check=False,
            shell=(os.name == "nt"),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise GenerationError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise GenerationError(
            f"claude exited {completed.returncode}: {detail[:500]}"
        )
    return completed.stdout.strip()


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    if not cleaned.startswith("{"):
        # tolerate preamble/postamble prose around the JSON object
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        snippet = raw.strip()[:300].replace("\n", " ")
        raise GenerationError(
            f"invalid JSON: {exc}; claude said: {snippet!r}"
        ) from exc
    if not isinstance(value, dict):
        raise GenerationError("generation output is not a JSON object")
    return value


def _article_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if "id" in value and isinstance(value["id"], str):
            found.add(value["id"])
        for child in value.values():
            found.update(_article_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_article_ids(child))
    return found


def _validate(value: dict[str, Any], allowed_ids: set[str]) -> None:
    cited: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in {"article_id", "source_article_id"} and isinstance(child, str):
                    cited.add(child)
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    unknown = cited - allowed_ids
    if unknown:
        raise GenerationError(f"unknown article ids: {sorted(unknown)}")
