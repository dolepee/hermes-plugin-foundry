"""Plugin Foundry core: parse a request, emit a real Hermes plugin, test it.

Two narrow archetypes:
- SQLite folder watcher with one tool.
- SQLite API poller with one tool.

Generated code is human-reviewed before install or catalog submission. Nothing
is auto-merged.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from string import Template
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
FOUNDRY_DATA_DIRNAME = "plugin_foundry"
FOUNDRY_ROOT_ENV = "HERMES_PLUGIN_FOUNDRY_ROOT"
DEFAULT_FOLDER = "~/Downloads"
DEFAULT_INTERVAL = 15

# Only these variables reach a generated plugin's test subprocess. Provider keys
# and every other Hermes secret stay out of it.
TEST_ENV_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "WINDIR", "VIRTUAL_ENV",
)


class OutsideFoundryRoot(ValueError):
    """A path resolved outside the Foundry data root."""


def _hermes_home(hermes_home: str | Path | None = None) -> Path:
    """Resolve the Hermes home the way the host does (profile-aware)."""
    if hermes_home:
        return Path(hermes_home).expanduser()
    get_hermes_home = None
    with suppress(Exception):
        from hermes_constants import get_hermes_home as _get_hermes_home
        get_hermes_home = _get_hermes_home
    if get_hermes_home is not None:
        with suppress(Exception):
            return Path(get_hermes_home()).expanduser()
    env_home = os.environ.get("HERMES_HOME", "").strip()
    return Path(env_home).expanduser() if env_home else Path.home() / ".hermes"


def foundry_root(hermes_home: str | Path | None = None,
                 root: str | Path | None = None) -> Path:
    """The one directory Foundry may write into.

    ``HERMES_PLUGIN_FOUNDRY_ROOT`` is the user-configured override; otherwise
    it is ``<hermes home>/plugin-data/plugin_foundry/``.
    """
    if root is not None:
        base = Path(root).expanduser()
    else:
        override = os.environ.get(FOUNDRY_ROOT_ENV, "").strip()
        base = (Path(override).expanduser() if override
                else _hermes_home(hermes_home) / "plugin-data" / FOUNDRY_DATA_DIRNAME)
    return base.resolve()


def generated_root(hermes_home: str | Path | None = None,
                   root: str | Path | None = None) -> Path:
    return foundry_root(hermes_home, root) / "generated"


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_confined(raw_path: str | Path, root: str | Path) -> Path:
    """Resolve *raw_path* and require it to sit under *root*.

    Symlinks are followed, so a link inside the root that points outside it is
    rejected too.
    """
    root_resolved = Path(root).expanduser().resolve()
    try:
        resolved = Path(raw_path).expanduser().resolve()
    except OSError as exc:
        raise OutsideFoundryRoot(f"could not resolve path {raw_path!r}: {exc}") from exc
    if not _is_within(resolved, root_resolved):
        raise OutsideFoundryRoot(
            f"{resolved} is outside Foundry's generated root ({root_resolved}); "
            "set " + FOUNDRY_ROOT_ENV + " to change the root"
        )
    return resolved


def _check_tree_confined(target: Path, root: str | Path) -> None:
    """Reject any descendant that resolves outside *root*.

    Directory symlinks are traversed so descendants are checked too; a symlink
    loop is cut by tracking resolved directories. This is the gate before an
    unapproved unittest subprocess can follow a test or import symlink.
    """
    root_resolved = Path(root).expanduser().resolve()
    if not Path(target).exists():
        return
    pending = [Path(target)]
    seen = set()
    while pending:
        current = pending.pop()
        try:
            real = current.resolve()
        except OSError as exc:
            raise OutsideFoundryRoot(f"could not resolve {current}: {exc}") from exc
        if real in seen:
            continue
        seen.add(real)
        if not _is_within(real, root_resolved):
            raise OutsideFoundryRoot(
                f"{current} resolves outside Foundry's generated root ({root_resolved})")
        try:
            entries = list(current.iterdir())
        except OSError as exc:
            raise OutsideFoundryRoot(f"could not inspect {current}: {exc}") from exc
        for entry in entries:
            try:
                resolved = entry.resolve()
            except OSError as exc:
                raise OutsideFoundryRoot(f"could not resolve {entry}: {exc}") from exc
            if not _is_within(resolved, root_resolved):
                raise OutsideFoundryRoot(
                    f"{entry} resolves outside Foundry's generated root ({root_resolved})")
            if entry.is_dir():
                pending.append(entry)


def output_target(output_dir: str | Path | None, root: str | Path) -> Path:
    """Resolve ``output_dir`` to a directory under *root*.

    A bare name is joined under the root (``--output my-plugin``); anything else
    must already resolve inside it.
    """
    root_resolved = Path(root).expanduser().resolve()
    if output_dir is None or not str(output_dir).strip():
        raise ValueError("output_dir is empty")
    candidate = Path(str(output_dir).strip()).expanduser()
    if (not candidate.is_absolute() and len(candidate.parts) == 1
            and candidate.name not in {".", ".."}):
        candidate = root_resolved / candidate.name
    return resolve_confined(candidate, root_resolved)


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "file-digest"


_TYPE_ALIASES = {
    "pdf": ".pdf",
    "csv": ".csv",
    "txt": ".txt",
    "text": ".txt",
    "md": ".md",
    "markdown": ".md",
    "png": ".png",
    "image": ".png",
    "photo": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpeg",
    "docx": ".docx",
    "word": ".docx",
}
_DOTTED_TYPES = "pdf|csv|txt|md|png|jpg|jpeg|docx"
_UNSUPPORTED_TYPE_WORDS = {
    "xlsx", "xls", "doc", "ppt", "pptx", "json", "xml", "zip", "gz", "tar",
    "gif", "mp4", "mp3", "wav", "html", "htm",
}
_COMPOSITE_SUFFIX = re.compile(r"\.[a-z0-9]+(?:\.[a-z0-9]+)+")
_OUTPUT_VERB = re.compile(
    r"\b(?:and|to|then|with)\s+(?:also\s+)?"
    r"(?:produce|create|generate|write|output|make|summari[sz]e|return)\b"
)
_OUTPUT_NOUN = re.compile(
    r"\b(?:and|with)\s+(?:a|an)?\s*"
    r"(?:(?!(?:files?|and|or|then|of|from)\b)"
    r"(?:[a-z][a-z0-9]*|\.[a-z0-9][a-z0-9._-]*)"
    r"(?:\s+(?!(?:files?|and|or|then|of|from)\b)"
    r"(?:[a-z][a-z0-9]*|\.[a-z0-9][a-z0-9._-]*)){0,3}\s+)?"
    r"(?:summary|report|digest|output)\b"
)
_INPUT_CLAUSE = re.compile(
    r"\b(?:and|then|also|but|,\s*then)\s+(?:then\s+)?"
    r"(?:watch|monitor|scan|look\s+in|poll)\b"
)


def _extension(request: str) -> str:
    """Return the explicitly requested watcher type, or reject the request.

    A wrong but plausible default is worse than a clear refusal here: a watcher
    built for PDF will silently ignore a TXT inbox. Type words that qualify a
    file noun ("CSV report files") stay input even after an output-purpose
    clause, while a standalone output descriptor ("and a text report",
    "text summary") does not add a watched type. An explicit new input clause
    ("then watch ...") reopens the watched role. Only explicit known type
    words or single dotted suffixes are accepted; missing, unsupported,
    composite or conflicting types are errors.
    """
    lower = request.lower()
    # Paths often contain type-like words; remove path-shaped tokens before
    # interpreting the natural-language request.
    text = re.sub(r"(?:^|\s)(?:~|\.{0,2}/)\S+", " ", lower)
    text = re.sub(r"https?://\S+", " ", text)
    # Do not truncate here: a clause like "and generate a summary of new CSV
    # files" still declares CSV input. This boundary only keeps standalone
    # output descriptors like "text summary" out of the standalone scan.
    verb_starts = [match.start() for match in _OUTPUT_VERB.finditer(text)]
    input_starts = [match.start() for match in _INPUT_CLAUSE.finditer(text)]
    relation_starts = [match.start() for match in re.finditer(r"\b(?:of|from)\b", text)]
    noun_starts: list[int] = []
    for match in _OUTPUT_NOUN.finditer(text):
        tail = text[match.end():]
        # "CSV report files" and "CSV summary invoice files" qualify watched
        # files (plural) and stay input. "text summary file" and
        # "a .txt report" are standalone output descriptors. Do not let the
        # qualifier guard cross an of/from relation or a clause conjunction:
        # "summary of the files" is output, not a watched-file descriptor.
        qualifier = re.match(r"\s+(?:[a-z.]+(?:\s+[a-z.]+){0,2}\s+)?files\b", tail)
        if qualifier and not re.search(r"\b(?:of|from|and|or|then)\b", qualifier.group(0)):
            continue
        noun_starts.append(match.start())

    markers: list[tuple[int, str]] = []
    markers.extend((value, "verb") for value in verb_starts)
    markers.extend((value, "input") for value in input_starts)
    markers.extend((value, "relation") for value in relation_starts)
    markers.extend((value, "noun") for value in noun_starts)

    def candidate_is_output(start: int, end: int) -> bool:
        """One role decision for a word or dotted-suffix candidate.

        The nearest preceding clause marker defines the candidate's role:
        output verbs and standalone output nouns open generated-output roles;
        input verbs and of/from open watched-object roles. The nearest marker
        wins, so a later standalone output noun resets an earlier relation.
        """
        prior = [item for item in markers if item[0] <= start]
        if not prior:
            return False
        boundary, kind = max(prior, key=lambda item: item[0])
        if kind in {"input", "relation"}:
            return False
        if kind == "noun":
            return True
        # An output verb is output unless its own clause first introduced an
        # input object ("generate a summary of CSV files").
        prefix = text[boundary:start]
        return not re.search(r"\b(?:of|from)\b", prefix)

    found: list[str] = []

    def add_token(token: str) -> None:
        if token in _TYPE_ALIASES:
            found.append(_TYPE_ALIASES[token])
        elif token in _UNSUPPORTED_TYPE_WORDS:
            raise ValueError(
                f"unsupported file type {token!r}; name one of TXT, CSV, PDF, "
                "Markdown, PNG, JPEG or DOCX"
            )
        elif token.endswith("s") and token[:-1] in _TYPE_ALIASES:
            found.append(_TYPE_ALIASES[token[:-1]])
        elif token.endswith("s") and token[:-1] in _UNSUPPORTED_TYPE_WORDS:
            raise ValueError(
                f"unsupported file type {token!r}; name one of TXT, CSV, PDF, "
                "Markdown, PNG, JPEG or DOCX"
            )

    # Dotted candidates are complete suffix tokens, including composite and
    # digit-bearing forms. The same role decision as word candidates applies
    # before validation, so an output format is not treated as input.
    for match in re.finditer(r"\.[a-z0-9][a-z0-9._-]*", text):
        if candidate_is_output(match.start(), match.end()):
            continue
        suffix = match.group(0)
        # A single sentence-final period is punctuation, not a suffix segment.
        # Before a file noun, every trailing dot belongs to the token.
        if suffix.endswith(".") and not re.match(r"\s+files?\b", text[match.end():]):
            suffix = suffix[:-1]
        if _COMPOSITE_SUFFIX.fullmatch(suffix):
            raise ValueError(
                "unsupported composite file suffix; name one suffix such as .TXT or .CSV"
            )
        if suffix not in set(_TYPE_ALIASES.values()):
            raise ValueError(
                f"unsupported file suffix {suffix!r}; name one of .TXT, .CSV, "
                ".PDF, .MD, .PNG, .JPG, .JPEG or .DOCX"
            )
        found.append(suffix)

    for match in re.finditer(r"(?<![.a-z0-9])[a-z]+(?![.a-z0-9])", text):
        if candidate_is_output(match.start(), match.end()):
            continue
        add_token(match.group(0))

    unique = list(dict.fromkeys(found))
    if len(unique) > 1:
        raise ValueError(
            "ambiguous file type: name exactly one of "
            + ", ".join(sorted(set(_TYPE_ALIASES.values())))
        )
    if unique:
        return unique[0]
    raise ValueError(
        "request must name an explicit file type such as TXT, CSV, PDF, "
        "Markdown, PNG, JPEG or DOCX"
    )


def _interval_minutes(request: str) -> int:
    match = re.search(r"every\s+(\d+)\s*(minute|min|hour|day)", request, re.I)
    if not match:
        return DEFAULT_INTERVAL
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("hour"):
        return value * 60
    if unit.startswith("day"):
        return value * 1440
    return value


def _poller_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "github" in host:
        return "GitHub Poller"
    if "hn.algolia.com" in host or "news.ycombinator.com" in host:
        return "Hacker News Poller"
    if "reddit" in host:
        return "Reddit Poller"
    first = host.split(".")[0] if host else "API"
    return f"{first.title()} Poller"


def parse_request(request: str) -> dict:
    request = (request or "").strip()
    if not request:
        raise ValueError("request is required")

    url_match = re.search(r"https?://[^\s]+", request)
    if url_match:
        url = url_match.group(0).rstrip(".,;:)")
        name = _poller_name(url)
        slug = slugify(name)
        return {
            "request": request,
            "name": name,
            "slug": slug,
            "url": url,
            "interval_minutes": _interval_minutes(request),
            "tool": f"{slug.replace('-', '_')}_poll",
            "table": "seen_items",
            "archetype": "sqlite_api_poller",
        }

    folder = None
    for token in re.findall(r"(?:^|\s)((?:~|\.{0,2}/)[^\s,;]+)", request):
        folder = token.rstrip(".,;:)")
        break
    folder = folder or DEFAULT_FOLDER
    base = Path(folder).name or "downloads"
    name = " ".join(part.capitalize() for part in re.split(r"[-_]+", base) if part)
    if name.lower() in {"downloads", "documents", "files"}:
        name = "File Digest"
    name = f"{name} Digest"
    slug = slugify(name)
    return {
        "request": request,
        "name": name,
        "slug": slug,
        "folder": folder,
        "extension": _extension(request),
        "tool": f"{slug.replace('-', '_')}_scan",
        "table": "seen_files",
        "archetype": "sqlite_folder_watcher",
    }


WATCHER_PLUGIN = Template('''"""Generated by Plugin Foundry. Review before installing or publishing."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"
DEFAULT_FOLDER = Path(os.path.expanduser($folder_repr))
FOLDER = DEFAULT_FOLDER
EXTENSION = $extension_repr
TOOL_NAME = $tool_repr


def _connect():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS seen_files ("
        "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL, seen_at TEXT NOT NULL)"
    )
    return con


def _scan(args=None, **_kwargs):
    folder = Path(FOLDER).expanduser()
    if not folder.exists():
        return {"ok": False, "error": "folder not found", "folder": str(folder), "new": []}
    files = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() == EXTENSION.lower()
    )
    con = _connect()
    new = []
    try:
        for path in files:
            stat = path.stat()
            fingerprint = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
            row = con.execute("SELECT size, mtime FROM seen_files WHERE path=?", (fingerprint,)).fetchone()
            if row and row[0] == stat.st_size and row[1] == stat.st_mtime:
                continue
            new.append({"path": str(path), "size": stat.st_size})
            con.execute(
                "INSERT INTO seen_files(path,size,mtime,seen_at) VALUES(?,?,?,datetime('now')) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, seen_at=excluded.seen_at",
                (fingerprint, stat.st_size, stat.st_mtime),
            )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "folder": str(folder), "extension": EXTENSION, "new": new, "count": len(new)}


def _tool(args=None, **kwargs):
    return json.dumps(_scan(args, **kwargs), ensure_ascii=False, default=str)


def register(ctx):
    ctx.register_tool(
        name=TOOL_NAME,
        toolset=$slug_repr,
        schema={
            "name": TOOL_NAME,
            "description": "Scan the watched folder for new files and return only files not seen before.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        handler=_tool,
    )
''')

API_PLUGIN = Template('''"""Generated by Plugin Foundry. Review before installing or publishing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"
URL = $url_repr
INTERVAL_MINUTES = $interval_minutes
TOOL_NAME = $tool_repr


def _connect():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS seen_items ("
        "key TEXT PRIMARY KEY, title TEXT, url TEXT, seen_at TEXT NOT NULL)"
    )
    return con


def _fetch():
    request = urllib.request.Request(URL, headers={"User-Agent": "HermesPlugin/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "releases", "data", "stories", "hits", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _view(item):
    if not isinstance(item, dict):
        return {"key": hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest(),
                "title": str(item)[:200], "url": ""}
    key = None
    for candidate in ("id", "guid", "url", "html_url", "objectID", "number"):
        if item.get(candidate) is not None:
            key = str(item[candidate])
            break
    if key is None:
        key = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
    return {"key": key, "title": str(item.get("title") or item.get("name") or key)[:200],
            "url": str(item.get("url") or item.get("html_url") or "")}


def _poll(args=None, **_kwargs):
    try:
        payload = _fetch()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": URL, "new": []}
    items = [_view(item) for item in _extract_items(payload)]
    con = _connect()
    new = []
    try:
        for item in items:
            row = con.execute("SELECT key FROM seen_items WHERE key=?", (item["key"],)).fetchone()
            if row:
                continue
            new.append(item)
            con.execute(
                "INSERT INTO seen_items(key,title,url,seen_at) VALUES(?,?,?,datetime('now'))",
                (item["key"], item["title"], item["url"]),
            )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "url": URL, "interval_minutes": INTERVAL_MINUTES,
            "new": new, "count": len(new)}


def _tool(args=None, **kwargs):
    return json.dumps(_poll(args, **kwargs), ensure_ascii=False, default=str)


def register(ctx):
    ctx.register_tool(
        name=TOOL_NAME,
        toolset=$slug_repr,
        schema={
            "name": TOOL_NAME,
            "description": "Poll the configured public JSON endpoint and return only items not seen before.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        handler=_tool,
    )
''')

PLUGIN_INIT = '''"""Hermes entry point for the generated plugin."""
from .plugin import register

__all__ = ["register"]
'''

PLUGIN_YAML = Template('''name: $slug
label: $name
kind: standalone
manifest_version: 1
version: 0.1.0
description: >
  $description
author: Plugin Foundry
license: MIT
requires_hermes: ">=0.21.5"
provides_tools:
  - $tool
''')

WATCHER_README = Template('''# $name

Generated by Plugin Foundry from this request:

> $request

## What it does

Scans `$folder` for `$extension` files, stores a local SQLite fingerprint, and
returns only files that are new or changed.

## Tool

- `$tool` — no arguments; returns `{ok, folder, extension, new, count}`.

## Install

Copy this directory to `~/.hermes/plugins/$slug/`, then:

```bash
hermes plugins enable $slug
```

## Honest limits

- Reads local files only; no upload.
- A missing folder is reported as an error, not an empty success.
- The generated code is a starting point and must be reviewed before publishing.
- No catalog submission is automatic.
''')

API_README = Template('''# $name

Generated by Plugin Foundry from this request:

> $request

## What it does

Polls `$url` every $interval_minutes minutes, stores seen item keys in local
SQLite, and returns only items that have not been seen before.

## Tool

- `$tool` — no arguments; returns `{ok, url, interval_minutes, new, count}`.

## Install

Copy this directory to `~/.hermes/plugins/$slug/`, then:

```bash
hermes plugins enable $slug
```

## Honest limits

- Sends one GET request to the configured public URL; no other network calls.
- A failed request returns an error, not an empty success.
- The endpoint shape is inferred from common JSON keys; check it against the API.
- The generated code is a starting point and must be reviewed before publishing.
- No catalog submission is automatic.
''')

WATCHER_SKILL = Template('''---
name: $slug
description: Watch $folder for new $extension files and report unseen files.
---

# $name

Use `$tool` when the user asks what is new in the watched folder.

The result contains only files not seen before. An empty `new` list means there
are no new or changed files, not that the folder is empty. A missing folder is
an error. Do not upload file contents.
''')

API_SKILL = Template('''---
name: $slug
description: Poll $url and report items not seen before.
---

# $name

Use `$tool` when the user asks what is new from this source.

The result contains only items not seen before. An empty `new` list means there
is nothing new, not that the source is empty. A failed request is an error. Do
not treat an inferred field mapping as certainty; check the source.
''')

WATCHER_TEST = '''import json
import tempfile
import unittest
from pathlib import Path

import plugin


class Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class GeneratedPluginTests(unittest.TestCase):
    def test_registers_tool(self):
        ctx = Ctx()
        plugin.register(ctx)
        self.assertEqual(len(ctx.tools), 1)
        self.assertEqual(ctx.tools[0]["name"], plugin.TOOL_NAME)

    def test_scan_is_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin.FOLDER = Path(tmp)
            plugin.DB_PATH = Path(tmp) / "state.db"
            Path(tmp, "a" + plugin.EXTENSION).write_text("a")
            Path(tmp, "b" + plugin.EXTENSION).write_text("b")
            first = plugin._scan({})
            self.assertTrue(first["ok"])
            self.assertEqual(first["count"], 2)
            second = plugin._scan({})
            self.assertEqual(second["count"], 0)

    def test_handler_accepts_registry_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin.FOLDER = Path(tmp)
            plugin.DB_PATH = Path(tmp) / "state.db"
            result = json.loads(plugin._tool({}))
            self.assertTrue(result["ok"])

    def test_missing_folder_is_an_error(self):
        plugin.FOLDER = Path("/definitely/not/here")
        result = plugin._scan({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["new"], [])


if __name__ == "__main__":
    unittest.main()
'''

API_TEST = '''import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import plugin


class Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GeneratedPluginTests(unittest.TestCase):
    def test_registers_tool(self):
        ctx = Ctx()
        plugin.register(ctx)
        self.assertEqual(len(ctx.tools), 1)
        self.assertEqual(ctx.tools[0]["name"], plugin.TOOL_NAME)

    def test_poll_is_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin.DB_PATH = Path(tmp) / "state.db"
            payload = [{"id": 7, "title": "First", "url": "https://example.com/7"}]
            with patch("plugin.urllib.request.urlopen", return_value=FakeResponse(payload)):
                first = plugin._poll({})
                second = plugin._poll({})
            self.assertTrue(first["ok"])
            self.assertEqual(first["count"], 1)
            self.assertEqual(second["count"], 0)

    def test_handler_accepts_registry_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin.DB_PATH = Path(tmp) / "state.db"
            with patch("plugin.urllib.request.urlopen", return_value=FakeResponse([])):
                result = json.loads(plugin._tool({}))
            self.assertTrue(result["ok"])

    def test_poll_failure_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin.DB_PATH = Path(tmp) / "state.db"
            with patch("plugin.urllib.request.urlopen", side_effect=OSError("down")):
                result = plugin._poll({})
            self.assertFalse(result["ok"])
            self.assertEqual(result["new"], [])


if __name__ == "__main__":
    unittest.main()
'''

CATALOG = Template('''# Catalog submission draft

**Name:** $name  
**Slug:** $slug  
**Version:** 0.1.0  
**Archetype:** $archetype  
**Tool:** `$tool`  
**Manifest:** `plugin.yaml`  
**Tests:** `tests/test_plugin.py`

## What it does

$description

## Disclosure

- Reads or requests only the configured source.
- A failed read is reported as an error, not an empty success.
- Generated by Plugin Foundry and reviewed by a human before submission.
- Not auto-merged and not automatically submitted to the catalog.

## Evidence

- Generated tests must pass.
- Static scan must pass.
- A real Hermes load is required before this draft is treated as ready.
''')
CATALOG_YAML = Template("""name: $slug
repo: https://github.com/OWNER/REPO
sha: REPLACE_WITH_40_CHAR_COMMIT_SHA
description: $description_yaml
maintainer: OWNER
tier: community
category: automation
requires_hermes: ">=0.21.5"
version: "0.1.0"
capabilities:
  provides_tools:
    - $tool
  provides_hooks: []
  provides_middleware: []
  requires_env: []
""")

UNSAFE_PATTERNS = {
    "dynamic code": re.compile(r"\beval\s*\("),
    "dynamic code (exec)": re.compile(r"\bexec\s*\("),
    "shell command": re.compile(r"\bos\.system\s*\("),
    "subprocess": re.compile(r"\bsubprocess\b"),
    "pickle": re.compile(r"\bpickle\b"),
    "raw socket": re.compile(r"\bsocket\b"),
}


def _description(spec: dict) -> str:
    if spec["archetype"] == "sqlite_api_poller":
        return (f"Poll {spec['url']} every {spec['interval_minutes']} minutes, store seen item "
                "keys in local SQLite, and return only items not seen before.")
    return (f"Watch {spec['folder']} for new {spec['extension']} files and report only files "
            "that have not been seen before.")


def _render(spec: dict) -> dict[str, str]:
    spec = dict(spec)
    spec["description"] = _description(spec)
    spec["folder_repr"] = repr(spec.get("folder", ""))
    spec["extension_repr"] = repr(spec.get("extension", ""))
    spec["tool_repr"] = repr(spec["tool"])
    spec["slug_repr"] = repr(spec["slug"])
    spec["url_repr"] = repr(spec.get("url", ""))
    spec["description_yaml"] = json.dumps(spec["description"])
    if spec["archetype"] == "sqlite_api_poller":
        return {
            "plugin.yaml": PLUGIN_YAML.substitute(spec),
            "__init__.py": PLUGIN_INIT,
            "plugin.py": API_PLUGIN.substitute(spec),
            "README.md": API_README.substitute(spec),
            "SKILL.md": API_SKILL.substitute(spec),
            "tests/test_plugin.py": API_TEST,
            "CATALOG.md": CATALOG.substitute(spec),
            "CATALOG.yaml": CATALOG_YAML.substitute(spec),
        }
    return {
        "plugin.yaml": PLUGIN_YAML.substitute(spec),
        "__init__.py": PLUGIN_INIT,
        "plugin.py": WATCHER_PLUGIN.substitute(spec),
        "README.md": WATCHER_README.substitute(spec),
        "SKILL.md": WATCHER_SKILL.substitute(spec),
        "tests/test_plugin.py": WATCHER_TEST,
        "CATALOG.md": CATALOG.substitute(spec),
        "CATALOG.yaml": CATALOG_YAML.substitute(spec),
    }


def scan_code(plugin_dir: str | Path) -> dict:
    plugin_dir = Path(plugin_dir)
    findings = []
    for path in sorted(plugin_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in UNSAFE_PATTERNS.items():
            if pattern.search(text):
                findings.append({"file": path.relative_to(plugin_dir).as_posix(), "finding": label})
    return {"ok": not findings, "findings": findings}


def _sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approval_identity(tool_name: str, raw_path: str, hermes_home: str | None = None,
                      root: str | None = None) -> dict:
    """The exact object a human approval covers.

    Paths and source bytes are part of the identity. It intentionally does not
    enforce the root; the caller decides whether an outside target needs
    approval. Editing an outside test or catalog after approval changes this
    identity and invalidates the grant.
    """
    generated = generated_root(hermes_home, root)
    if tool_name == "foundry_build":
        try:
            target = output_target(raw_path, generated)
        except OutsideFoundryRoot:
            target = Path(raw_path).expanduser().resolve()
        return {"path": str(target)}
    target = Path(raw_path).expanduser().resolve()
    if tool_name == "foundry_catalog":
        catalog = target / "CATALOG.md"
        try:
            resolved = catalog.resolve()
        except OSError:
            resolved = catalog
        return {"path": str(resolved),
                "sha256": _sha256_file(resolved) if resolved.is_file() else None}
    if tool_name == "foundry_test":
        # Every regular file under the approved tree is part of the identity,
        # not just importable sources. A sourceless helper.pyc, a native
        # extension or a data file a test loads and executes can all change the
        # code that runs, so pinning only ``*.py`` would let a pending approval
        # be re-used for replacement bytes.
        sources = []
        directories = []
        pending = [target]
        seen = set()
        while pending:
            current = pending.pop()
            try:
                real = current.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    try:
                        resolved = entry.resolve()
                    except OSError:
                        continue
                    # Record every alias even when its destination has already
                    # been traversed. The file set alone does not capture which
                    # path resolution the approved execution will use.
                    directories.append({
                        "path": entry.relative_to(target).as_posix(),
                        "resolved": str(resolved),
                    })
                    if resolved not in seen:
                        pending.append(entry)
                    continue
                if not entry.is_file():
                    continue
                try:
                    resolved = entry.resolve()
                    sources.append({
                        "path": entry.relative_to(target).as_posix(),
                        "resolved": str(resolved),
                        "sha256": _sha256_file(resolved),
                    })
                except OSError:
                    continue
        directories.append({"path": ".", "resolved": str(target.resolve())})
        return {"path": str(target),
                "directories": sorted(directories, key=lambda row: row["path"]),
                "sources": sorted(sources, key=lambda row: row["path"])}
    return {"path": str(target)}


def check_tool_path(tool_name: str, raw_path: str, hermes_home: str | None = None,
                    root: str | None = None) -> Path:
    """Resolve a path-taking tool's argument exactly as its handler will."""
    generated = generated_root(hermes_home, root)
    if tool_name == "foundry_build":
        return output_target(raw_path, generated)
    target = resolve_confined(raw_path, generated)
    if tool_name == "foundry_test":
        _check_tree_confined(target, generated)
    elif tool_name == "foundry_catalog":
        return resolve_confined(target / "CATALOG.md", generated)
    return target


def build(request: str, output_dir: str | None = None, install: bool = False,
          hermes_home: str | None = None, root: str | None = None,
          allow_outside_root: bool = False) -> dict:
    spec = parse_request(request)
    root_path = foundry_root(hermes_home, root)
    generated = root_path / "generated"
    if output_dir and allow_outside_root:
        target = Path(str(output_dir).strip()).expanduser().resolve()
    elif output_dir:
        target = output_target(output_dir, generated)
    else:
        target = resolve_confined(generated / spec["slug"], generated)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=False)
    for rel, content in _render(spec).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    spec["path"] = str(target)
    result = {
        "spec": spec,
        "files": sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()),
    }
    if install:
        result["installed_to"] = install_plugin(target, spec["slug"], hermes_home)
    return result


def _test_env(scratch: str | Path | None = None) -> dict[str, str]:
    """A minimal environment for the generated plugin's test subprocess.

    Interpreter caches and ordinary temporary output go to a fresh directory
    outside the approved input tree. Existing bytecode/data under the tree is
    still hashed by ``approval_identity``; only newly generated artifacts are
    redirected so a normal run does not invalidate an untouched approval.
    """
    env = {key: os.environ[key] for key in TEST_ENV_ALLOWLIST if key in os.environ}
    env.setdefault("HOME", str(Path.home()))
    env["PYTHONIOENCODING"] = "utf-8"
    if scratch is not None:
        scratch = Path(scratch)
        cache = scratch / "pycache"
        temp = scratch / "tmp"
        cache.mkdir(parents=True, exist_ok=True)
        temp.mkdir(parents=True, exist_ok=True)
        env["PYTHONPYCACHEPREFIX"] = str(cache)
        env["TMPDIR"] = str(temp)
        env["TEMP"] = str(temp)
        env["TMP"] = str(temp)
    return env


def run_tests(plugin_dir: str | Path, timeout: int = 90,
              hermes_home: str | None = None, root: str | None = None,
              allow_outside_root: bool = False) -> dict:
    if allow_outside_root:
        target = Path(plugin_dir).expanduser().resolve()
    else:
        target = resolve_confined(plugin_dir, generated_root(hermes_home, root))
    if not target.is_dir():
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": f"not a directory: {target}"}
    if not allow_outside_root:
        _check_tree_confined(target, generated_root(hermes_home, root))
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="foundry-test-") as scratch:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=target, env=_test_env(scratch), capture_output=True, text=True, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": f"tests timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": f"could not start tests: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def install_plugin(plugin_dir: str | Path, slug: str, hermes_home: str | None = None) -> str:
    home = _hermes_home(hermes_home)
    target = home / "plugins" / slug
    if target.exists():
        raise FileExistsError(f"plugin already installed: {target}")
    shutil.copytree(plugin_dir, target, ignore=shutil.ignore_patterns("__pycache__", "data"))
    return str(target)


def list_generated(hermes_home: str | None = None, root: str | None = None) -> list[dict]:
    generated = generated_root(hermes_home, root)
    if not generated.exists():
        return []
    rows = []
    for path in sorted(generated.iterdir()):
        if path.is_dir() and (path / "plugin.yaml").exists():
            rows.append({"name": path.name, "path": str(path)})
    return rows


def read_catalog(path: str | Path, hermes_home: str | None = None, root: str | None = None,
                 allow_outside_root: bool = False) -> dict:
    if allow_outside_root:
        plugin_dir = Path(path).expanduser().resolve()
        catalog = plugin_dir / "CATALOG.md"
    else:
        plugin_dir = resolve_confined(path, generated_root(hermes_home, root))
        catalog = resolve_confined(plugin_dir / "CATALOG.md", generated_root(hermes_home, root))
    if not catalog.exists():
        return {"ok": False, "error": f"no catalog draft at {catalog}"}
    return {"ok": True, "path": str(catalog), "content": catalog.read_text(encoding="utf-8")}


def build_and_test(request: str, output_dir: str | None = None,
                   hermes_home: str | None = None, root: str | None = None,
                   allow_outside_root: bool = False) -> dict:
    built = build(request, output_dir=output_dir, hermes_home=hermes_home, root=root,
                  allow_outside_root=allow_outside_root)
    scanned = scan_code(built["spec"]["path"])
    tested = run_tests(built["spec"]["path"], hermes_home=hermes_home, root=root,
                       allow_outside_root=allow_outside_root) if scanned["ok"] else {
        "ok": False, "returncode": None, "stdout": "", "stderr": "skipped: static scan failed",
    }
    return {"build": built, "scan": scanned, "tests": tested, "ok": scanned["ok"] and tested["ok"]}
