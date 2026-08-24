from __future__ import annotations

import html
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import Store


def generate(store: Store, path: str | Path = "docs/keywords.html") -> None:
    rows = store.select(
        "keyword_weights", order="recipient.asc,topic.asc,weight.desc"
    )
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row.get("recipient", "unknown"))][
            str(row.get("topic", "unknown"))
        ].append(row)
    versions = store.select("prompt_versions", order="created_at.desc")
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<title>Briefing keyword weights</title>",
        "<style>body{font:14px/1.45 system-ui,sans-serif;color:#202020;max-width:1000px;margin:32px auto;padding:0 18px}",
        "h1,h2,h3{color:#172b4d}table{width:100%;border-collapse:collapse;margin:10px 0 28px}",
        "th,td{text-align:left;border-bottom:1px solid #ddd;padding:7px}.bar{height:10px;background:#d4dee8;border-radius:5px}",
        ".track{width:180px;background:#eee;border-radius:5px}.retired{color:#8a3d33}small{color:#666}</style></head><body>",
        "<h1>Briefing keyword weights</h1>",
    ]
    for recipient, topics in grouped.items():
        parts.append(f"<h2>{html.escape(recipient)}</h2>")
        for topic, weights in topics.items():
            parts.append(f"<h3>{html.escape(topic)}</h3>")
            parts.append(
                "<table><thead><tr><th>Keyword</th><th>Weight</th><th>Strength</th>"
                "<th>Observations</th><th>Status</th><th>Changed by</th><th>Updated</th>"
                "</tr></thead><tbody>"
            )
            for row in weights:
                weight = float(row.get("weight", 0))
                width = max(0, min(100, round(weight / 3 * 100)))
                status = str(row.get("status", ""))
                parts.append(
                    f'<tr class="{html.escape(status)}"><td>{html.escape(str(row.get("keyword", "")))}</td>'
                    f"<td>{weight:.2f}</td><td><div class=\"track\"><div class=\"bar\" style=\"width:{width}%\"></div></div></td>"
                    f"<td>{int(row.get('observations', 0))}</td><td>{html.escape(status)}</td>"
                    f"<td>{html.escape(str(row.get('changed_by', '')))}</td>"
                    f"<td><small>{html.escape(str(row.get('updated_at', '')))}</small></td></tr>"
                )
            parts.append("</tbody></table>")
    parts.append("<h2>Prompt changelog</h2>")
    if versions:
        parts.append(
            "<table><thead><tr><th>Version</th><th>File</th><th>Rationale</th>"
            "<th>Verdict</th><th>Created</th></tr></thead><tbody>"
        )
        for row in versions:
            parts.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('version', '')))}</td>"
                f"<td>{html.escape(str(row.get('file', '')))}</td>"
                f"<td>{html.escape(str(row.get('rationale', '')))}</td>"
                f"<td>{html.escape(str(row.get('verdict', '')))}</td>"
                f"<td>{html.escape(str(row.get('created_at', '')))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No prompt changes have been recorded.</p>")
    parts.append("</body></html>")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(parts), encoding="utf-8")
