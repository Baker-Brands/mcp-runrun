"""Content extraction & standardized tabulation for Runrun.it tasks.

A Runrun.it task stores the rich "content" of a card across several places:
  - the body/description: a separate endpoint, as HTML
  - tags: inline (`tags_data`)
  - custom fields: inline (`custom_fields`) but keyed by opaque ids (custom_30…)
    whose human labels live in the per-org field definitions (`/tasks/:id/fields`)
  - assignees: inline (`assignments`)
  - comments: a separate endpoint (mixes human + system messages)

This module turns all of that into one flat, spreadsheet-ready record.
"""

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

WEB_TASK_URL = "https://runrun.it/tasks/{id}"


# ── HTML → plain text ───────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Collapse Runrun's HTML description into readable plain text.

    - Links render as 'anchor text (https://url)' so the URL survives in a cell.
    - Block tags become line breaks; table cells are separated by a tab so a row
      stays on one line yet keeps cell boundaries; list items get a '- ' bullet.
    - <script>/<style>/<head> contents are dropped.
    """

    _BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "thead", "tbody", "blockquote", "section", "header", "footer"}
    _CELL = {"td", "th"}
    _SKIP = {"script", "style", "head", "title", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._href: str | None = None
        self._anchor_text = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br" or tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._CELL:
            self.parts.append("\t")
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = False

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._href:
            # only append the URL if it adds info beyond the anchor text
            self.parts.append(f" ({self._href}) " if self._anchor_text else f"{self._href} ")
            self._href = None
        elif tag == "tr":
            self.parts.append("\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href is not None and data.strip():
            self._anchor_text = True
        self.parts.append(data)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    text = unescape("".join(parser.parts))
    text = re.sub(r"[ \t\xa0]+(?=\t)", "", text)        # tidy whitespace before a tab
    text = re.sub(r"[^\S\n\t]+", " ", text)             # collapse runs of spaces (keep \n,\t)
    text = re.sub(r" *\n *", "\n", text)                 # trim spaces around newlines
    text = re.sub(r"\n\t+", "\n", text)                  # drop leading tab at start of a row
    text = re.sub(r"\n{3,}", "\n\n", text)               # cap blank lines
    return text.strip()


# ── Time formatting ──────────────────────────────────────────────────────────────

def seconds_to_hms(seconds: Any) -> str | None:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


# ── Custom field resolution ─────────────────────────────────────────────────────

def build_field_label_map(field_defs: list[dict[str, Any]]) -> dict[str, str]:
    """Map custom-field id -> human label, disambiguating duplicate labels.

    Two distinct org fields can share a label (e.g. custom_48 'PAÍS' and
    custom_72 'País'). To avoid silently collapsing them into one spreadsheet
    column, any label used by more than one id gets the id appended.
    """
    pairs: list[tuple[str, str]] = []
    for f in field_defs or []:
        fid = f.get("id")
        label = f.get("label")
        if isinstance(fid, str) and fid.startswith("custom_") and label:
            pairs.append((fid, str(label)))

    counts: dict[str, int] = {}
    for _, label in pairs:
        counts[label] = counts.get(label, 0) + 1

    out: dict[str, str] = {}
    for fid, label in pairs:
        out[fid] = f"{label} ({fid})" if counts[label] > 1 else label
    return out


def _label_of(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("label") or v.get("name") or v.get("title")
    return v


def cf_value(val: Any) -> Any:
    """Reduce a custom-field value to its label(s) or raw scalar. Never returns a dict."""
    if isinstance(val, dict):
        return _label_of(val)
    if isinstance(val, list):
        return [lbl for lbl in (_label_of(v) for v in val) if lbl is not None]
    return val


def resolve_custom_fields(custom_fields: dict[str, Any] | None,
                          label_map: dict[str, str]) -> dict[str, Any]:
    """Turn {'custom_30': {...,'label':'Growth'}} into {'Área': 'Growth'}.

    Unknown ids (e.g. a stale cache) are kept under their raw id so no data is lost.
    """
    out: dict[str, Any] = {}
    for key, val in (custom_fields or {}).items():
        label = label_map.get(key, key)  # fall back to raw id — lossless
        out[label] = cf_value(val)
    return out


# ── Record assembly ──────────────────────────────────────────────────────────────

def tags_of(task: dict[str, Any]) -> list[str]:
    data = task.get("tags_data")
    if isinstance(data, list) and data:
        return [t.get("name") for t in data if isinstance(t, dict) and t.get("name")]
    tags = task.get("tags")
    return tags if isinstance(tags, list) else []


def assignees_of(task: dict[str, Any]) -> list[str]:
    asg = task.get("assignments")
    if not isinstance(asg, list):
        return []
    names: list[str] = []
    for a in asg:
        if isinstance(a, dict):
            name = a.get("assignee_name") or a.get("assignee_id")
            if not name and isinstance(a.get("assignee"), dict):
                name = a["assignee"].get("name") or a["assignee"].get("email")
            if name:
                names.append(name)
    return names


def task_record(task: dict[str, Any], label_map: dict[str, str],
                description_html: str | None = None,
                comments: list[dict[str, Any]] | None = None,
                html_format: str = "text") -> dict[str, Any]:
    """Build one standardized, flat, spreadsheet-ready record from a task object."""
    rec: dict[str, Any] = {
        "id": task.get("id"),
        "title": task.get("title"),
        "url": WEB_TASK_URL.format(id=task.get("id")),
        "is_closed": task.get("is_closed"),
        "state": task.get("state"),
        "is_urgent": task.get("is_urgent"),
        "board": task.get("board_name"),
        "stage": task.get("board_stage_name"),
        "project": task.get("project_name"),
        "client": task.get("client_name"),
        "type": task.get("type_name"),
        "creator": task.get("user_name"),
        "creator_id": task.get("user_id"),
        "responsible": task.get("responsible_name"),
        "responsible_id": task.get("responsible_id"),
        "assignees": assignees_of(task),
        "points": task.get("points"),
        "created_at": task.get("created_at"),
        "desired_date": task.get("desired_date"),
        "start_date": task.get("start_date"),
        "close_date": task.get("close_date"),
        "time_worked_seconds": task.get("time_worked"),
        "time_worked_hms": seconds_to_hms(task.get("time_worked")),
        "time_estimated_seconds": task.get("time_total"),
        "time_estimated_hms": seconds_to_hms(task.get("time_total")),
        "subtasks_count": task.get("subtasks_count"),
        "attachments_count": task.get("attachments_count"),
        "tags": tags_of(task),
        "custom_fields": resolve_custom_fields(task.get("custom_fields"), label_map),
    }
    if description_html is not None:
        rec["description"] = description_html if html_format == "html" else html_to_text(description_html)
    if comments is not None:
        rec["comments"] = [
            {
                "author": c.get("commenter_name") or c.get("user_id"),
                "text": html_to_text(c.get("text")),
                "created_at": c.get("created_at"),
            }
            for c in comments
            if not c.get("is_system_message") and not c.get("is_automation_message")
        ]
    return rec


def join_cell(value: Any, sep: str = "; ") -> Any:
    """Render a list value as a single spreadsheet cell string; scalars pass through."""
    if isinstance(value, list):
        return sep.join(str(v) for v in value if v is not None)
    return value


def flatten_record(rec: dict[str, Any], cf_columns: list[str]) -> dict[str, Any]:
    """Flatten a record for a spreadsheet: lists joined, custom fields promoted to
    'cf: <label>' columns so every row has identical keys."""
    flat: dict[str, Any] = {}
    for k, v in rec.items():
        if k == "custom_fields":
            continue
        if k == "comments":
            flat[k] = " || ".join(f"{c['author']}: {c['text']}" for c in v) if isinstance(v, list) else v
        else:
            flat[k] = join_cell(v)
    cfs = rec.get("custom_fields", {}) or {}
    for col in cf_columns:
        flat[f"cf: {col}"] = join_cell(cfs.get(col))
    return flat
