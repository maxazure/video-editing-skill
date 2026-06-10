#!/usr/bin/env python3
"""
Media library index for video editing projects.

Manages a local index of video files, transcripts, B-roll, BGM and other assets.
Supports two backends:
  - JSON (default, for < 200 items): media_index.json
  - SQLite (auto or manual, for large libraries): media_index.db

Usage:
  python3 media_library.py init [project_dir]     # Initialize library structure
  python3 media_library.py scan [project_dir]      # Scan and index media files
  python3 media_library.py status [project_dir]    # Show library status
  python3 media_library.py search <query>           # Search indexed media
  python3 media_library.py upgrade                  # Upgrade JSON → SQLite
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info

# --- Constants ---

MEDIA_DIRS = {
    "raw":    "原始素材 — 摄像机/手机直出的原始视频",
    "broll":  "B-roll 素材 — 城市街景、产品特写等补充画面",
    "bgm":    "背景音乐 — MP3/WAV/M4A 格式",
    "assets": "叠加素材 — 水印 PNG、品牌 Logo、圆点图标等",
    "output": "输出目录 — 最终渲染的视频",
}

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv"}
SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

AUTO_UPGRADE_THRESHOLD = 200
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

# Directory name mappings for classification (English and Chinese)
_DIR_CATEGORY_MAP = {
    "raw": "raw",
    "原始素材": "raw",
    "broll": "broll",
    "b-roll": "broll",
    "bgm": "bgm",
    "背景音乐": "bgm",
    "music": "bgm",
    "assets": "assets",
    "叠加素材": "assets",
    "output": "output",
    "输出目录": "output",
    "输出": "output",
}


# ---------------------------------------------------------------------------
# JSONIndex backend
# ---------------------------------------------------------------------------

class JSONIndex:
    """JSON-file backed media index, suitable for small libraries (< 200 items)."""

    def __init__(self, index_path):
        self.index_path = index_path
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.items = data.get("items", [])
            self.meta = data.get("meta", {})
        else:
            self.items = []
            self.meta = {"created_at": datetime.now().isoformat(), "backend": "json"}

    def save(self):
        """Persist the index to disk."""
        self.meta["updated_at"] = datetime.now().isoformat()
        self.meta["count"] = len(self.items)
        data = {"meta": self.meta, "items": self.items}
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, item):
        """Add or update an item (matched by path)."""
        for i, existing in enumerate(self.items):
            if existing.get("path") == item.get("path"):
                item["updated_at"] = datetime.now().isoformat()
                self.items[i] = item
                return
        if "id" not in item:
            item["id"] = str(uuid.uuid4())
        if "added_at" not in item:
            item["added_at"] = datetime.now().isoformat()
        self.items.append(item)

    def remove(self, path):
        """Remove an item by file path."""
        self.items = [it for it in self.items if it.get("path") != path]

    def search(self, query):
        """Search items by substring match on path, type, category, and tags."""
        query_lower = query.lower()
        results = []
        for it in self.items:
            searchable = " ".join(
                str(it.get(k, ""))
                for k in ("path", "type", "category", "tags")
            ).lower()
            if query_lower in searchable:
                results.append(it)
        return results

    def get_all(self):
        """Return all indexed items."""
        return list(self.items)

    def count(self):
        """Return total number of indexed items."""
        return len(self.items)

    def get_by_type(self, media_type):
        """Return items matching a given media type (video/audio/image)."""
        return [it for it in self.items if it.get("type") == media_type]


# ---------------------------------------------------------------------------
# SQLiteIndex backend
# ---------------------------------------------------------------------------

class SQLiteIndex:
    """SQLite-backed media index for larger libraries."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS media (
        id TEXT PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL,
        category TEXT,
        duration REAL,
        width INTEGER,
        height INTEGER,
        fps REAL,
        file_size INTEGER,
        transcript_path TEXT,
        tags TEXT,
        metadata TEXT,
        added_at TEXT,
        updated_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_media_type ON media(type);
    CREATE INDEX IF NOT EXISTS idx_media_category ON media(category);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """

    _COLUMNS = [
        "id", "path", "type", "category", "duration", "width", "height",
        "fps", "file_size", "transcript_path", "tags", "metadata",
        "added_at", "updated_at",
    ]

    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript(self._SCHEMA)
        # Set backend meta if not present
        cur.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("backend", "sqlite"),
        )
        cur.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("created_at", datetime.now().isoformat()),
        )
        self.conn.commit()

    def _row_to_dict(self, row):
        """Convert a sqlite3.Row to a plain dict."""
        if row is None:
            return None
        d = dict(row)
        # Deserialize JSON fields
        for key in ("tags", "metadata"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def save(self):
        """Commit any pending changes."""
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("updated_at", datetime.now().isoformat()),
        )
        self.conn.commit()

    def add(self, item):
        """Add or update a media item (matched by path)."""
        item = dict(item)  # avoid mutating caller's dict
        if "id" not in item or not item["id"]:
            item["id"] = str(uuid.uuid4())
        now = datetime.now().isoformat()
        if "added_at" not in item or not item["added_at"]:
            item["added_at"] = now
        item["updated_at"] = now

        # Serialize list/dict fields to JSON strings
        for key in ("tags", "metadata"):
            val = item.get(key)
            if val is not None and not isinstance(val, str):
                item[key] = json.dumps(val, ensure_ascii=False)

        cols = self._COLUMNS
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        values = [item.get(c) for c in cols]

        sql = (
            f"INSERT INTO media ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(path) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, values)

    def remove(self, path):
        """Remove a media item by file path."""
        self.conn.execute("DELETE FROM media WHERE path = ?", (path,))

    def search(self, query):
        """Search media by substring match on path, type, category, tags."""
        pattern = f"%{query}%"
        cur = self.conn.execute(
            "SELECT * FROM media WHERE "
            "path LIKE ? OR type LIKE ? OR category LIKE ? OR tags LIKE ?",
            (pattern, pattern, pattern, pattern),
        )
        return [self._row_to_dict(row) for row in cur.fetchall()]

    def get_all(self):
        """Return all indexed media items."""
        cur = self.conn.execute("SELECT * FROM media")
        return [self._row_to_dict(row) for row in cur.fetchall()]

    def count(self):
        """Return total number of indexed media items."""
        cur = self.conn.execute("SELECT COUNT(*) FROM media")
        return cur.fetchone()[0]

    def get_by_type(self, media_type):
        """Return items matching a given media type."""
        cur = self.conn.execute("SELECT * FROM media WHERE type = ?", (media_type,))
        return [self._row_to_dict(row) for row in cur.fetchall()]

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def open_index(project_dir, force_backend=None):
    """Open the media index with auto-detection or forced backend.

    Auto-upgrade from JSON to SQLite when item count >= AUTO_UPGRADE_THRESHOLD.
    """
    json_path = os.path.join(project_dir, "media_index.json")
    db_path = os.path.join(project_dir, "media_index.db")

    if force_backend == "sqlite":
        return SQLiteIndex(db_path)
    if force_backend == "json":
        return JSONIndex(json_path)

    # Auto-detect: prefer SQLite if it exists
    if os.path.exists(db_path):
        return SQLiteIndex(db_path)

    # Use JSON, but check if we need to upgrade
    idx = JSONIndex(json_path)
    if idx.count() >= AUTO_UPGRADE_THRESHOLD:
        print(f"[auto-upgrade] Item count ({idx.count()}) >= {AUTO_UPGRADE_THRESHOLD}, "
              f"upgrading to SQLite...")
        upgrade_to_sqlite(project_dir)
        return SQLiteIndex(db_path)

    return idx


def upgrade_to_sqlite(project_dir):
    """Migrate JSON index to SQLite. Backs up the old JSON file."""
    json_path = os.path.join(project_dir, "media_index.json")
    db_path = os.path.join(project_dir, "media_index.db")
    backup_path = json_path + ".bak"

    if not os.path.exists(json_path):
        print(f"No JSON index found at {json_path}, nothing to migrate.")
        return False

    # Load existing JSON data
    json_idx = JSONIndex(json_path)
    items = json_idx.get_all()

    # Create SQLite index and insert all items
    sqlite_idx = SQLiteIndex(db_path)
    for item in items:
        sqlite_idx.add(item)
    sqlite_idx.save()
    sqlite_idx.close()

    # Backup old JSON
    os.rename(json_path, backup_path)
    print(f"Migrated {len(items)} items from JSON to SQLite.")
    print(f"  JSON backup: {backup_path}")
    print(f"  SQLite DB:   {db_path}")
    return True


def classify_file(filepath, project_dir):
    """Classify a file by its extension and parent directory.

    Returns a dict with keys: type, category.
    - type: "video", "audio", "image", or "other"
    - category: "raw", "broll", "bgm", "assets", "output", or None
    """
    ext = os.path.splitext(filepath)[1].lower()

    # Determine media type
    if ext in SUPPORTED_VIDEO_EXTS:
        media_type = "video"
    elif ext in SUPPORTED_AUDIO_EXTS:
        media_type = "audio"
    elif ext in SUPPORTED_IMAGE_EXTS:
        media_type = "image"
    else:
        media_type = "other"

    # Determine category from directory path
    category = None
    rel = os.path.relpath(filepath, project_dir)
    parts = rel.replace("\\", "/").split("/")
    for part in parts:
        part_lower = part.lower()
        if part_lower in _DIR_CATEGORY_MAP:
            category = _DIR_CATEGORY_MAP[part_lower]
            break

    return {"type": media_type, "category": category}


def _find_transcript(video_path):
    """Look for a transcript JSON file associated with a video.

    Checks for <basename>_transcript.json in the same directory.
    """
    base, _ = os.path.splitext(video_path)
    transcript = base + "_transcript.json"
    if os.path.exists(transcript):
        return transcript
    return None


def _resolve_item_path(project_dir, item_path):
    """Return an absolute path for an indexed item path."""
    if not item_path:
        return ""
    path = os.path.expanduser(str(item_path))
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(project_dir, path))


def _path_for_index(project_dir, file_path):
    """Store project-local paths relatively and external files absolutely."""
    abs_project = os.path.abspath(project_dir)
    abs_file = os.path.abspath(file_path)
    try:
        rel = os.path.relpath(abs_file, abs_project)
    except ValueError:
        return abs_file
    if rel == "." or rel.startswith(".."):
        return abs_file
    return rel


def _parse_tag_args(values):
    tags = []
    for value in values or []:
        for tag in re.split(r"[,，;；\n]+", str(value)):
            cleaned = tag.strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
    return tags


def _metadata_from_args(args):
    metadata = {}
    for key in ("provider", "source_url", "creator", "license", "license_url", "notes"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            metadata[key] = value
    return metadata or None


def _unique_destination(directory, filename):
    os.makedirs(directory, exist_ok=True)
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    idx = 2
    while True:
        candidate = os.path.join(directory, f"{base}-{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def build_media_item(project_dir, file_path, *, category=None, tags=None, metadata=None):
    """Build an index item for one registered media file."""
    classification = classify_file(file_path, project_dir)
    if category:
        classification["category"] = category

    item = {
        "path": _path_for_index(project_dir, file_path),
        "type": classification["type"],
        "category": classification["category"],
        "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else None,
        "tags": tags or None,
        "metadata": metadata or None,
    }

    if classification["type"] == "video" and os.path.exists(file_path):
        try:
            duration, width, height, fps, _rotation = get_video_info(file_path)
            item["duration"] = duration
            item["width"] = width
            item["height"] = height
            item["fps"] = fps
        except Exception:
            item["duration"] = None
            item["width"] = None
            item["height"] = None
            item["fps"] = None

        transcript = _find_transcript(file_path)
        if transcript:
            item["transcript_path"] = _path_for_index(project_dir, transcript)

    return item


def find_index_item(project_dir, index, item_path):
    """Find an indexed item by stored path or resolved absolute path."""
    wanted_abs = _resolve_item_path(project_dir, item_path)
    wanted_norm = os.path.normcase(os.path.abspath(wanted_abs))
    wanted_raw = str(item_path)
    for item in index.get_all():
        stored = str(item.get("path") or "")
        if stored == wanted_raw:
            return item
        stored_abs = _resolve_item_path(project_dir, stored)
        if os.path.normcase(os.path.abspath(stored_abs)) == wanted_norm:
            return item
    return None


def _flatten_text(value):
    """Convert nested tags/metadata into searchable lowercase text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, (int, float)):
        return str(value).lower()
    if isinstance(value, dict):
        return " ".join(
            f"{_flatten_text(k)} {_flatten_text(v)}" for k, v in value.items()
        ).lower()
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(v) for v in value).lower()
    return str(value).lower()


def _query_tokens(query):
    tokens = []
    for token in _TOKEN_RE.findall(str(query or "").lower()):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens


def _parse_aspect(value):
    """Parse an aspect value such as 9:16, 1.777, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    text = str(value).strip().lower()
    if ":" in text:
        left, right = text.split(":", 1)
        try:
            w = float(left)
            h = float(right)
            return w / h if w > 0 and h > 0 else None
        except ValueError:
            return None
    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def score_media_candidate(item, query, *, target_duration=None, target_aspect=None):
    """Score one indexed media item for a natural-language B-roll query.

    Returns (score, reasons). The scoring is intentionally transparent so an
    agent can show why a candidate was suggested before linking it into a render.
    """
    tokens = _query_tokens(query)
    full_query = str(query or "").strip().lower()
    path_text = _flatten_text(item.get("path"))
    filename_text = os.path.splitext(os.path.basename(path_text))[0].replace("-", " ")
    tags_text = _flatten_text(item.get("tags"))
    metadata_text = _flatten_text(item.get("metadata"))
    transcript_text = _flatten_text(item.get("transcript_path"))
    category_text = _flatten_text(item.get("category"))

    score = 0.0
    reasons = []
    matched_query = False

    for token in tokens:
        matched = False
        if token in tags_text:
            score += 5.0
            reasons.append(f"tag:{token}")
            matched = True
            matched_query = True
        if token in filename_text:
            score += 3.0
            reasons.append(f"filename:{token}")
            matched = True
            matched_query = True
        elif token in path_text:
            score += 2.0
            reasons.append(f"path:{token}")
            matched = True
            matched_query = True
        if token in metadata_text:
            score += 2.0
            reasons.append(f"metadata:{token}")
            matched = True
            matched_query = True
        if token in transcript_text:
            score += 1.5
            reasons.append(f"transcript:{token}")
            matched = True
            matched_query = True
        if not matched and token in category_text:
            score += 0.5
            reasons.append(f"category:{token}")
            matched_query = True

    if full_query and len(full_query) >= 3:
        if full_query in tags_text:
            score += 3.0
            reasons.append("exact-tag-query")
            matched_query = True
        if full_query in path_text:
            score += 2.0
            reasons.append("exact-path-query")
            matched_query = True

    if tokens and not matched_query:
        return 0.0, []

    if item.get("category") == "broll":
        score += 1.0
        reasons.append("category:broll")
    if item.get("type") == "video":
        score += 0.5
        reasons.append("type:video")

    duration = item.get("duration")
    if target_duration and duration:
        try:
            duration = float(duration)
            target = float(target_duration)
            if duration >= target:
                score += 1.0
                reasons.append("duration-covers-cue")
            if target * 0.5 <= duration <= target * 4:
                score += 0.5
                reasons.append("duration-close")
        except (TypeError, ValueError):
            pass

    aspect = _parse_aspect(target_aspect)
    width = item.get("width")
    height = item.get("height")
    if aspect and width and height:
        try:
            candidate_aspect = float(width) / float(height)
            if abs(candidate_aspect - aspect) <= 0.12:
                score += 1.0
                reasons.append("aspect-match")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    return score, reasons


def recommend_assets(
    project_dir,
    query,
    *,
    media_type=None,
    category=None,
    limit=5,
    min_duration=None,
    max_duration=None,
    target_duration=None,
    target_aspect=None,
    existing_only=True,
):
    """Return ranked media candidates from media_index.json/sqlite.

    The returned paths include both the stored project-relative path and an
    absolute path, making the result usable from storyboard manifests.
    """
    project_dir = os.path.abspath(project_dir)
    index = open_index(project_dir)
    try:
        items = index.get_all()
    finally:
        if hasattr(index, "close"):
            index.close()

    results = []
    for item in items:
        if media_type and item.get("type") != media_type:
            continue
        if category and item.get("category") != category:
            continue
        abs_path = _resolve_item_path(project_dir, item.get("path"))
        if existing_only and (not abs_path or not os.path.exists(abs_path)):
            continue

        duration = item.get("duration")
        if duration is not None:
            try:
                duration_float = float(duration)
            except (TypeError, ValueError):
                duration_float = None
            if min_duration is not None and duration_float is not None and duration_float < min_duration:
                continue
            if max_duration is not None and duration_float is not None and duration_float > max_duration:
                continue

        score, reasons = score_media_candidate(
            item,
            query,
            target_duration=target_duration,
            target_aspect=target_aspect,
        )
        if score <= 0:
            continue
        result = {
            "path": item.get("path"),
            "absolute_path": abs_path,
            "type": item.get("type"),
            "category": item.get("category"),
            "duration": item.get("duration"),
            "width": item.get("width"),
            "height": item.get("height"),
            "tags": item.get("tags") or [],
            "score": round(score, 3),
            "reasons": reasons,
            "transcript_path": item.get("transcript_path"),
        }
        results.append(result)

    def _sort_key(result):
        duration = result.get("duration")
        try:
            duration_value = float(duration) if duration is not None else 10**9
        except (TypeError, ValueError):
            duration_value = 10**9
        duration_delta = (
            abs(duration_value - float(target_duration))
            if target_duration and duration_value != 10**9
            else duration_value
        )
        return (-float(result.get("score", 0)), duration_delta, str(result.get("path") or ""))

    return sorted(results, key=_sort_key)[: max(0, int(limit))]


_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".DS_Store"}


def scan_directory(project_dir, index):
    """Walk project_dir, index all supported media files.

    Skips hidden directories and common non-media directories.
    Extracts video metadata via get_video_info().
    """
    media_root = os.path.join(project_dir, "media")
    scan_root = media_root if os.path.isdir(media_root) else project_dir

    all_exts = SUPPORTED_VIDEO_EXTS | SUPPORTED_AUDIO_EXTS | SUPPORTED_IMAGE_EXTS
    added = 0
    skipped = 0

    for dirpath, dirnames, filenames in os.walk(scan_root):
        # Skip hidden and non-media directories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _SKIP_DIRS
        ]

        for fname in filenames:
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in all_exts:
                skipped += 1
                continue

            filepath = os.path.join(dirpath, fname)
            classification = classify_file(filepath, project_dir)

            item = {
                "path": os.path.relpath(filepath, project_dir),
                "type": classification["type"],
                "category": classification["category"],
                "file_size": os.path.getsize(filepath),
                "tags": None,
                "metadata": None,
            }

            # Extract video metadata
            if classification["type"] == "video":
                try:
                    duration, width, height, fps, _rotation = get_video_info(filepath)
                    item["duration"] = duration
                    item["width"] = width
                    item["height"] = height
                    item["fps"] = fps
                except Exception:
                    item["duration"] = None
                    item["width"] = None
                    item["height"] = None
                    item["fps"] = None

                # Look for associated transcript
                transcript = _find_transcript(filepath)
                if transcript:
                    item["transcript_path"] = os.path.relpath(transcript, project_dir)

            index.add(item)
            added += 1

    index.save()
    return added, skipped


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    """Initialize the media library directory structure."""
    project_dir = os.path.abspath(args.project_dir)
    media_root = os.path.join(project_dir, "media")

    print(f"Initializing media library in: {project_dir}\n")

    for dirname in MEDIA_DIRS:
        dirpath = os.path.join(media_root, dirname)
        os.makedirs(dirpath, exist_ok=True)
        gitkeep = os.path.join(dirpath, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, "w") as f:
                pass

    print("Created directories:")
    print(f"  media/raw/      — 原始素材（摄像机/手机直出的视频）")
    print(f"  media/broll/    — B-roll 素材（城市街景、产品特写等）")
    print(f"  media/bgm/      — 背景音乐（MP3/WAV/M4A）")
    print(f"  media/assets/   — 叠加素材（水印 PNG、Logo 等）")
    print(f"  media/output/   — 输出目录")
    print()
    print("Next steps:")
    print("  1. Put your raw videos in media/raw/")
    print("  2. Put B-roll clips in media/broll/")
    print("  3. Put background music in media/bgm/")
    print("  4. Run: python3 scripts/media_library.py scan")


def cmd_scan(args):
    """Scan and index all media files in the project."""
    project_dir = os.path.abspath(args.project_dir)
    index = open_index(project_dir)

    print(f"Scanning: {project_dir}")
    added, skipped = scan_directory(project_dir, index)

    print(f"\nScan complete:")
    print(f"  Indexed: {added} files")
    print(f"  Skipped: {skipped} non-media files")
    print(f"  Total:   {index.count()} items in library")

    # Summary by type
    for media_type in ("video", "audio", "image"):
        items = index.get_by_type(media_type)
        if items:
            print(f"  {media_type}: {len(items)} files")

    if hasattr(index, "close"):
        index.close()


def cmd_status(args):
    """Show library statistics."""
    project_dir = os.path.abspath(args.project_dir)
    json_path = os.path.join(project_dir, "media_index.json")
    db_path = os.path.join(project_dir, "media_index.db")

    if not os.path.exists(json_path) and not os.path.exists(db_path):
        print("No media index found. Run 'init' and 'scan' first.")
        return

    index = open_index(project_dir)
    all_items = index.get_all()

    # Backend info
    backend = "SQLite" if isinstance(index, SQLiteIndex) else "JSON"
    print(f"Media Library Status ({backend} backend)")
    print(f"{'=' * 45}")
    print(f"Total items: {index.count()}")

    # By type
    type_counts = {}
    for it in all_items:
        t = it.get("type", "other")
        type_counts[t] = type_counts.get(t, 0) + 1
    if type_counts:
        print(f"\nBy type:")
        for t, c in sorted(type_counts.items()):
            print(f"  {t}: {c}")

    # By category
    cat_counts = {}
    for it in all_items:
        cat = it.get("category") or "uncategorized"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if cat_counts:
        print(f"\nBy category:")
        for cat, c in sorted(cat_counts.items()):
            print(f"  {cat}: {c}")

    # Total duration (video only)
    total_duration = 0.0
    for it in all_items:
        dur = it.get("duration")
        if dur:
            total_duration += float(dur)
    if total_duration > 0:
        minutes = int(total_duration // 60)
        seconds = total_duration % 60
        print(f"\nTotal video duration: {minutes}m {seconds:.1f}s")

    # Total file size
    total_size = sum(it.get("file_size", 0) or 0 for it in all_items)
    if total_size > 0:
        if total_size >= 1024 ** 3:
            size_str = f"{total_size / 1024 ** 3:.2f} GB"
        elif total_size >= 1024 ** 2:
            size_str = f"{total_size / 1024 ** 2:.1f} MB"
        else:
            size_str = f"{total_size / 1024:.1f} KB"
        print(f"Total file size: {size_str}")

    # Transcript count
    transcript_count = sum(
        1 for it in all_items if it.get("transcript_path")
    )
    if transcript_count > 0:
        print(f"Transcripts found: {transcript_count}")

    if hasattr(index, "close"):
        index.close()


def cmd_search(args):
    """Search indexed media by query string."""
    project_dir = os.path.abspath(args.project_dir)
    index = open_index(project_dir)

    results = index.search(args.query)
    if not results:
        print(f"No results for: {args.query}")
    else:
        print(f"Found {len(results)} result(s) for '{args.query}':\n")
        for it in results:
            path = it.get("path", "?")
            mtype = it.get("type", "?")
            cat = it.get("category") or "-"
            dur = it.get("duration")
            dur_str = f" ({dur:.1f}s)" if dur else ""
            print(f"  [{mtype}] {path}  (category: {cat}){dur_str}")

    if hasattr(index, "close"):
        index.close()


def cmd_recommend(args):
    """Recommend ranked assets for a storyboard/transcript query."""
    project_dir = os.path.abspath(args.project_dir)
    results = recommend_assets(
        project_dir,
        args.query,
        media_type=args.type,
        category=args.category,
        limit=args.limit,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        target_duration=args.target_duration,
        target_aspect=args.target_aspect,
        existing_only=not args.include_missing,
    )
    payload = {
        "query": args.query,
        "project_dir": project_dir,
        "filters": {
            "type": args.type,
            "category": args.category,
            "limit": args.limit,
            "min_duration": args.min_duration,
            "max_duration": args.max_duration,
            "target_duration": args.target_duration,
            "target_aspect": args.target_aspect,
            "existing_only": not args.include_missing,
        },
        "results": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not results:
        print(f"No ranked candidates for: {args.query}")
        return

    print(f"Recommended {len(results)} asset(s) for '{args.query}':\n")
    for result in results:
        duration = result.get("duration")
        duration_text = ""
        if duration not in (None, ""):
            try:
                duration_text = f" {float(duration):.1f}s"
            except (TypeError, ValueError):
                duration_text = ""
        print(
            f"  {result['score']:>5.1f}  {result.get('absolute_path')}"
            f"{duration_text}  [{', '.join(result.get('reasons') or [])}]"
        )


def cmd_import(args):
    """Register or copy a media asset into the index with provenance metadata."""
    project_dir = os.path.abspath(args.project_dir)
    source_path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isfile(source_path):
        print(f"media file not found: {args.path}", file=sys.stderr)
        return 1

    category = args.category
    target_path = source_path
    if args.copy:
        if not category:
            category = classify_file(source_path, project_dir).get("category") or "assets"
        destination_dir = os.path.join(project_dir, "media", category)
        target_path = _unique_destination(destination_dir, os.path.basename(source_path))
        shutil.copy2(source_path, target_path)

    tags = _parse_tag_args(args.tag)
    metadata = _metadata_from_args(args)
    item = build_media_item(
        project_dir,
        target_path,
        category=category,
        tags=tags,
        metadata=metadata,
    )

    index = open_index(project_dir)
    try:
        index.add(item)
        index.save()
    finally:
        if hasattr(index, "close"):
            index.close()

    payload = {
        "status": "imported",
        "project_dir": project_dir,
        "copied": bool(args.copy),
        "source_path": source_path,
        "item": item,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Imported: {item['path']}")
        if metadata:
            print(f"  metadata: {json.dumps(metadata, ensure_ascii=False)}")
        if tags:
            print(f"  tags: {', '.join(tags)}")
    return 0


def cmd_annotate(args):
    """Update tags/category/provenance metadata for an existing index item."""
    project_dir = os.path.abspath(args.project_dir)
    index = open_index(project_dir)
    try:
        item = find_index_item(project_dir, index, args.path)
        if not item:
            print(f"indexed media not found: {args.path}", file=sys.stderr)
            return 1

        if args.category:
            item["category"] = args.category

        new_tags = _parse_tag_args(args.tag)
        if new_tags:
            existing_tags = item.get("tags") or []
            if isinstance(existing_tags, str):
                existing_tags = _parse_tag_args([existing_tags])
            for tag in new_tags:
                if tag not in existing_tags:
                    existing_tags.append(tag)
            item["tags"] = existing_tags

        new_metadata = _metadata_from_args(args)
        if new_metadata:
            existing_metadata = item.get("metadata") or {}
            if not isinstance(existing_metadata, dict):
                existing_metadata = {"notes": str(existing_metadata)}
            existing_metadata.update(new_metadata)
            item["metadata"] = existing_metadata

        index.add(item)
        index.save()
    finally:
        if hasattr(index, "close"):
            index.close()

    payload = {"status": "annotated", "project_dir": project_dir, "item": item}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Annotated: {item.get('path')}")
    return 0


def cmd_upgrade(args):
    """Force upgrade from JSON to SQLite backend."""
    project_dir = os.path.abspath(args.project_dir)
    success = upgrade_to_sqlite(project_dir)
    if not success:
        print("Upgrade failed or nothing to migrate.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Media library index manager for video editing projects.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    p_init = subparsers.add_parser("init", help="Initialize library structure")
    p_init.add_argument("project_dir", nargs="?", default=".",
                        help="Project root directory (default: .)")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan and index media files")
    p_scan.add_argument("project_dir", nargs="?", default=".",
                        help="Project root directory (default: .)")

    # status
    p_status = subparsers.add_parser("status", help="Show library status")
    p_status.add_argument("project_dir", nargs="?", default=".",
                          help="Project root directory (default: .)")

    # search
    p_search = subparsers.add_parser("search", help="Search indexed media")
    p_search.add_argument("query", help="Search query string")
    p_search.add_argument("--project-dir", default=".",
                          help="Project root directory (default: .)")

    # recommend
    p_recommend = subparsers.add_parser(
        "recommend",
        help="Rank indexed media for a storyboard/transcript query",
    )
    p_recommend.add_argument("query", help="Natural-language query or storyboard B-roll cue")
    p_recommend.add_argument("--project-dir", default=".",
                             help="Project root directory (default: .)")
    p_recommend.add_argument("--type", choices=["video", "audio", "image", "other"],
                             default=None, help="Optional media type filter")
    p_recommend.add_argument("--category", default=None,
                             help="Optional category filter, e.g. broll")
    p_recommend.add_argument("--limit", type=int, default=5,
                             help="Maximum candidates to return")
    p_recommend.add_argument("--min-duration", type=float, default=None,
                             help="Skip shorter media when duration metadata exists")
    p_recommend.add_argument("--max-duration", type=float, default=None,
                             help="Skip longer media when duration metadata exists")
    p_recommend.add_argument("--target-duration", type=float, default=None,
                             help="Prefer media that covers this cue duration")
    p_recommend.add_argument("--target-aspect", default=None,
                             help="Prefer an aspect ratio such as 9:16 or 1.777")
    p_recommend.add_argument("--include-missing", action="store_true",
                             help="Include stale index entries whose files no longer exist")
    p_recommend.add_argument("--json", action="store_true",
                             help="Emit machine-readable recommendation JSON")

    # import
    p_import = subparsers.add_parser(
        "import",
        help="Register or copy one media asset with provenance metadata",
    )
    p_import.add_argument("path", help="Media file to register")
    p_import.add_argument("--project-dir", default=".",
                          help="Project root directory (default: .)")
    p_import.add_argument("--category", choices=sorted(MEDIA_DIRS),
                          default=None, help="Override category, e.g. broll")
    p_import.add_argument("--copy", action="store_true",
                          help="Copy the file into media/<category>/ before indexing")
    p_import.add_argument("--tag", action="append",
                          help="Tag(s) to attach. Repeatable or comma-separated")
    p_import.add_argument("--provider", help="Source provider, e.g. pexels, pixabay, coverr, owned")
    p_import.add_argument("--source-url", help="Original source URL for provenance")
    p_import.add_argument("--creator", help="Creator/author attribution")
    p_import.add_argument("--license", help="License name or policy")
    p_import.add_argument("--license-url", help="License URL")
    p_import.add_argument("--notes", help="Free-form provenance notes")
    p_import.add_argument("--json", action="store_true",
                          help="Emit machine-readable import result")

    # annotate
    p_annotate = subparsers.add_parser(
        "annotate",
        help="Update tags/category/provenance metadata for an indexed asset",
    )
    p_annotate.add_argument("path", help="Indexed path or absolute file path")
    p_annotate.add_argument("--project-dir", default=".",
                            help="Project root directory (default: .)")
    p_annotate.add_argument("--category", choices=sorted(MEDIA_DIRS),
                            default=None, help="Override category")
    p_annotate.add_argument("--tag", action="append",
                            help="Tag(s) to add. Repeatable or comma-separated")
    p_annotate.add_argument("--provider", help="Source provider")
    p_annotate.add_argument("--source-url", help="Original source URL for provenance")
    p_annotate.add_argument("--creator", help="Creator/author attribution")
    p_annotate.add_argument("--license", help="License name or policy")
    p_annotate.add_argument("--license-url", help="License URL")
    p_annotate.add_argument("--notes", help="Free-form provenance notes")
    p_annotate.add_argument("--json", action="store_true",
                            help="Emit machine-readable annotation result")

    # upgrade
    p_upgrade = subparsers.add_parser("upgrade", help="Upgrade JSON → SQLite")
    p_upgrade.add_argument("project_dir", nargs="?", default=".",
                           help="Project root directory (default: .)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "scan": cmd_scan,
        "status": cmd_status,
        "search": cmd_search,
        "recommend": cmd_recommend,
        "import": cmd_import,
        "annotate": cmd_annotate,
        "upgrade": cmd_upgrade,
    }

    result = commands[args.command](args)
    if isinstance(result, int):
        sys.exit(result)


if __name__ == "__main__":
    main()
