from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
import yaml


IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


@dataclass
class Article:
    id: str
    url: str
    source: str
    source_label: str
    feed: str
    title: str
    snippet: str
    topic: str | None = None
    keywords: list[str] = field(default_factory=list)
    published_at: str = ""
    premium: bool = False
    cluster_id: str | None = None
    first_seen: str = ""
    topic_hint: str | None = None
    source_weight: float = 1.0
    headline_only: bool = False
    score: float = 0.0
    keyword_observations: dict[str, int] = field(default_factory=dict)
    cluster_links: list[dict[str, str]] = field(default_factory=list)
    full_text: str = ""
    is_primary: bool = True

    def to_record(self) -> dict[str, Any]:
        """Return only fields represented by the articles table."""
        data = asdict(self)
        allowed = {
            "id",
            "url",
            "source",
            "source_label",
            "feed",
            "title",
            "snippet",
            "topic",
            "keywords",
            "published_at",
            "premium",
            "cluster_id",
            "first_seen",
        }
        return {key: value for key, value in data.items() if key in allowed}

    def prompt_record(self, include_text: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source_label,
            "snippet": self.snippet,
            "premium": self.premium,
            "cluster_links": self.cluster_links,
        }
        if include_text and self.full_text:
            data["full_text"] = self.full_text
        return data

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "Article":
        names = cls.__dataclass_fields__
        return cls(**{key: value for key, value in row.items() if key in names})


def _config_root() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "config"))


def load_config() -> dict[str, Any]:
    root = _config_root()

    def read(name: str) -> Any:
        with (root / name).open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    return {
        "feeds": read("feeds.yaml"),
        "keywords_seed": read("keywords-seed.yaml"),
        "topics": read("topics.yaml"),
    }


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def today_ist() -> date:
    return now_ist().date()


def http_get(url: str, **kwargs: Any) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}) or {})
    timeout = kwargs.pop("timeout", 20.0)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


class Store:
    """Small PostgREST/local-JSON state adapter."""

    def __init__(self, state_dir: str | Path = "state") -> None:
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        self.remote = bool(self.url and self.key)
        self.state_dir = Path(state_dir)
        if not self.remote:
            self.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    def _path(self, table: str) -> Path:
        if not table.replace("_", "").isalnum():
            raise ValueError(f"invalid table name: {table}")
        return self.state_dir / f"{table}.json"

    def _read(self, table: str) -> list[dict[str, Any]]:
        path = self._path(table)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"cannot read local table {table}: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError(f"local table {table} is not a JSON list")
        return data

    def _write(self, table: str, rows: list[dict[str, Any]]) -> None:
        path = self._path(table)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        temp.replace(path)

    def insert(
        self, table: str, rows: dict[str, Any] | Iterable[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        payload = _rows(rows)
        if not payload:
            return []
        if self.remote:
            response = httpx.post(
                self._endpoint(table),
                headers={**self._headers, "Prefer": "return=representation"},
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            return response.json() if response.content else payload
        current = self._read(table)
        current.extend(copy.deepcopy(payload))
        self._write(table, current)
        return payload

    def upsert(
        self,
        table: str,
        rows: dict[str, Any] | Iterable[dict[str, Any]],
        on: str | Iterable[str],
    ) -> list[dict[str, Any]]:
        payload = _rows(rows)
        if not payload:
            return []
        keys = [on] if isinstance(on, str) else list(on)
        if self.remote:
            response = httpx.post(
                self._endpoint(table),
                params={"on_conflict": ",".join(keys)},
                headers={
                    **self._headers,
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            return response.json() if response.content else payload
        current = self._read(table)
        for incoming in payload:
            found = next(
                (
                    row
                    for row in current
                    if all(row.get(key) == incoming.get(key) for key in keys)
                ),
                None,
            )
            if found is None:
                current.append(copy.deepcopy(incoming))
            else:
                found.update(copy.deepcopy(incoming))
        self._write(table, current)
        return payload

    def select(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        if self.remote:
            params: dict[str, str | int] = {"select": "*"}
            for raw_key, value in filters.items():
                key, op = _split_filter(raw_key)
                if isinstance(value, (list, tuple, set)):
                    encoded = ",".join(str(item) for item in value)
                    params[key] = f"in.({encoded})"
                elif value is None:
                    params[key] = "is.null"
                else:
                    params[key] = f"{op}.{_postgrest_value(value)}"
            if order:
                params["order"] = order
            if limit is not None:
                params["limit"] = limit
            response = httpx.get(
                self._endpoint(table),
                params=params,
                headers=self._headers,
                timeout=20,
            )
            response.raise_for_status()
            return response.json()
        rows = [
            copy.deepcopy(row)
            for row in self._read(table)
            if _matches(row, filters)
        ]
        if order:
            for term in reversed([part.strip() for part in order.split(",")]):
                bits = term.split(".")
                field = bits[0]
                descending = len(bits) > 1 and bits[1].lower() == "desc"
                rows.sort(
                    key=lambda row: (row.get(field) is None, row.get(field)),
                    reverse=descending,
                )
        return rows[:limit] if limit is not None else rows

    def update(
        self, table: str, filters: dict[str, Any], patch: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if self.remote:
            params: dict[str, str] = {}
            for raw_key, value in filters.items():
                key, op = _split_filter(raw_key)
                params[key] = f"{op}.{_postgrest_value(value)}"
            response = httpx.patch(
                self._endpoint(table),
                params=params,
                headers={**self._headers, "Prefer": "return=representation"},
                json=patch,
                timeout=20,
            )
            response.raise_for_status()
            return response.json() if response.content else []
        rows = self._read(table)
        changed: list[dict[str, Any]] = []
        for row in rows:
            if _matches(row, filters):
                row.update(copy.deepcopy(patch))
                changed.append(copy.deepcopy(row))
        self._write(table, rows)
        return changed


def recipients(store: Store | None = None) -> list[dict[str, Any]]:
    store = store or Store()
    rows = store.select("recipients", {"active": True})
    if rows:
        return rows
    all_rows = store.select("recipients")
    if all_rows:
        return []
    emails = [
        email.strip()
        for email in os.environ.get("RECIPIENTS", "").split(",")
        if email.strip()
    ]
    created = [
        {"id": email, "token": secrets.token_urlsafe(16), "active": True}
        for email in dict.fromkeys(emails)
    ]
    if created:
        store.upsert("recipients", created, on="id")
    return created


def _rows(
    rows: dict[str, Any] | Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(rows, dict):
        return [copy.deepcopy(rows)]
    return [copy.deepcopy(row) for row in rows]


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _split_filter(key: str) -> tuple[str, str]:
    if "__" not in key:
        return key, "eq"
    name, suffix = key.rsplit("__", 1)
    operators = {"eq", "neq", "gt", "gte", "lt", "lte"}
    return (name, suffix) if suffix in operators else (key, "eq")


def _postgrest_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    for raw_key, expected in filters.items():
        key, op = _split_filter(raw_key)
        actual = row.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
            continue
        if op == "eq" and actual != expected:
            return False
        if op == "neq" and actual == expected:
            return False
        if op == "gt" and not (actual is not None and actual > expected):
            return False
        if op == "gte" and not (actual is not None and actual >= expected):
            return False
        if op == "lt" and not (actual is not None and actual < expected):
            return False
        if op == "lte" and not (actual is not None and actual <= expected):
            return False
    return True
