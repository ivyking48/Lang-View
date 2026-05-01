"""Tiny Flask dashboard for browsing the SQLite store.

Routes:
  GET  /                    HTML search page
  GET  /api/search?q=...    JSON FTS search results
  GET  /api/all             JSON, all rows (paginated)
  GET  /api/stats           JSON, frequency table

The dashboard is read-only. It opens a fresh `Storage` per request
so concurrent processes (the running watch loop) can keep writing
while you browse.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .storage import Storage

log = logging.getLogger(__name__)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Lang-View</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2em auto;
        max-width: 880px; color: #222; }}
 h1   {{ font-weight: 500; }}
 form {{ margin-bottom: 1.5em; }}
 input[type=text] {{ padding: .5em; width: 18em; font-size: 1em; }}
 select, button   {{ padding: .5em; font-size: 1em; }}
 table {{ border-collapse: collapse; width: 100%; }}
 td, th {{ padding: .35em .6em; border-bottom: 1px solid #eee;
          text-align: left; vertical-align: top; }}
 .lang {{ color: #888; font-size: .9em; }}
 .ts   {{ color: #aaa; font-size: .8em; white-space: nowrap; }}
</style>
</head>
<body>
<h1>Lang-View</h1>
<form action="/" method="get">
  <input type="text" name="q" value="{q}" placeholder="search…" autofocus>
  <select name="lang">
    <option value="">any</option>
    <option value="ja"{ja_sel}>ja</option>
    <option value="ko"{ko_sel}>ko</option>
  </select>
  <button type="submit">Search</button>
  <a href="/stats" style="margin-left:1em">stats</a>
</form>
{body}
</body>
</html>
"""


def _row_to_html(record):
    enrichment = record.get("enrichment") or {}
    extras = []
    if enrichment.get("furigana"):
        extras.append(enrichment["furigana"])
    if enrichment.get("romaji"):
        extras.append(enrichment["romaji"])
    if enrichment.get("romaja"):
        extras.append(enrichment["romaja"])
    translations = enrichment.get("translation") or {}
    if translations:
        extras.append(next(iter(translations.values())))
    extras_html = ("<br><small>" + " · ".join(_escape(e) for e in extras) + "</small>"
                   if extras else "")
    artifact_links = []
    frame = record.get("frame")
    if frame:
        artifact_links.append(
            f"<a href='/artifact?path={_escape(frame)}' target='_blank'>frame</a>"
        )
    snippet = record.get("snippet")
    if snippet:
        artifact_links.append(
            f"<a href='/artifact?path={_escape(snippet)}' target='_blank'>snippet</a>"
        )
    artifact_cell = " · ".join(artifact_links) if artifact_links else ""
    return (
        f"<tr>"
        f"<td class='ts'>{_escape(record.get('timestamp', ''))}</td>"
        f"<td class='lang'>{_escape(record.get('lang', ''))}</td>"
        f"<td>{_escape(record.get('text', ''))}{extras_html}</td>"
        f"<td class='lang'>{artifact_cell}</td>"
        f"</tr>"
    )


def _escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def create_app(db_path, artifact_roots=None):
    """Flask app. ``artifact_roots`` whitelists directories under which
    PNG snippet/frame paths may be served via ``/artifact``. Defaults to
    the parent of ``db_path`` plus its ``frames`` and ``snippets``
    subdirectories so the standard install layout works without config.
    """
    from flask import Flask, abort, jsonify, request, send_file

    db_path = Path(db_path)
    if artifact_roots is None:
        base = db_path.parent
        artifact_roots = [base, base / "frames", base / "snippets"]
    artifact_roots = [Path(r).resolve() for r in artifact_roots]

    app = Flask(__name__)
    app.config["DB_PATH"] = str(db_path)
    app.config["ARTIFACT_ROOTS"] = artifact_roots

    @app.route("/")
    def index():
        q = (request.args.get("q") or "").strip()
        lang = request.args.get("lang") or None
        body = ""
        if q:
            with Storage(app.config["DB_PATH"]) as s:
                results = s.search(q, lang=lang, limit=200)
            if results:
                rows = "\n".join(_row_to_html(r) for r in results)
                body = f"<table>{rows}</table>"
            else:
                body = "<p>No matches.</p>"
        return INDEX_HTML.format(
            q=_escape(q),
            ja_sel=" selected" if lang == "ja" else "",
            ko_sel=" selected" if lang == "ko" else "",
            body=body,
        )

    @app.route("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip()
        lang = request.args.get("lang") or None
        limit = int(request.args.get("limit", 50))
        if not q:
            return jsonify({"error": "missing q"}), 400
        with Storage(app.config["DB_PATH"]) as s:
            results = s.search(q, lang=lang, limit=limit)
        return jsonify(results)

    @app.route("/api/all")
    def api_all():
        lang = request.args.get("lang") or None
        limit = int(request.args.get("limit", 100))
        with Storage(app.config["DB_PATH"]) as s:
            results = s.all(lang=lang, limit=limit)
        return jsonify(results)

    @app.route("/api/stats")
    def api_stats():
        lang = request.args.get("lang") or None
        limit = int(request.args.get("limit", 100))
        with Storage(app.config["DB_PATH"]) as s:
            return jsonify(s.frequencies(lang=lang, limit=limit))

    @app.route("/artifact")
    def artifact():
        """Serve a snippet/frame PNG. The requested path must resolve under
        one of the configured artifact roots — anything else is rejected
        to prevent directory traversal."""
        raw = request.args.get("path") or ""
        if not raw:
            abort(400)
        try:
            target = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError):
            abort(404)
        for root in app.config["ARTIFACT_ROOTS"]:
            try:
                target.relative_to(root)
            except ValueError:
                continue
            return send_file(target)
        abort(403)

    @app.route("/stats")
    def stats_html():
        with Storage(app.config["DB_PATH"]) as s:
            rows = s.frequencies(limit=200)
        body = "<table><tr><th>n</th><th>lang</th><th>text</th></tr>"
        for r in rows:
            body += (f"<tr><td>{r['n']}</td><td class='lang'>{_escape(r['lang'])}</td>"
                     f"<td>{_escape(r['text'])}</td></tr>")
        body += "</table>"
        return INDEX_HTML.format(q="", ja_sel="", ko_sel="", body=body)

    return app
