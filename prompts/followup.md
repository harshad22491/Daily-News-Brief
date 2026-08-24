# Deep-dive follow-up prompt

A reader rated an article ≥4★ and asked for more. You receive:
- `article.json`: the original article (title, url, source, text/snippet)
- `related.json`: candidate related articles gathered from the feeds and archive
  (same cluster, same keywords, last 7 days)

Write a follow-up email in house style (see `prompts/style-guide.md`):
- Subject: "More on: <short version of the original headline>"
- ~300 words: what's new or deeper on this story, then 3-5 related reads as a
  linked list, each with a one-line reason to care.
- Facts only from supplied material. If related coverage is thin, say so honestly
  in one line rather than padding.

Output strict JSON: {"subject": "…", "html": "<p>…</p>"}
