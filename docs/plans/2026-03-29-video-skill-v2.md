# Video Editing Skill V2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical bugs, add media library with dual-backend index (JSON/SQLite), filler word detection, AI smart clip selection guidance, animated caption presets, multi-platform export, and improve code quality.

**Architecture:** Dual-backend media index (JSON for small projects, SQLite for large) with auto-migration. New `media_library.py` manages index CRUD. Filler word detection integrated into `transcribe.py`. AI clip selection and virality scoring are SKILL.md workflow instructions for the AI agent. Overlay index bug fixed via explicit input tracking dict. Multi-platform export via `--formats` flag in `render_final.py`.

**Tech Stack:** Python 3.8+, FFmpeg, faster-whisper, SQLite3 (stdlib), JSON, Remotion (React/TypeScript)

---

### Task 1: Fix Overlay Input Index Bug in render_final.py

**Files:**
- Modify: `scripts/render_final.py:590-630`

The current code calculates overlay input indices using `len(input_files) + extra_offset` which is fragile. Replace with an explicit `extra_inputs` list that tracks inputs in order.

**Step 1: Fix the index calculation**

Replace the fragile index calculation (lines 590-630) with a tracking dict:

```python
# --- Extra inputs tracking (order matters: must match -i order in cmd) ---
extra_inputs = []  # list of (type, idx, path) — tracks order of extra -i args

if bgm_path:
    bgm_input_idx = len(input_files) + len(extra_inputs)
    extra_inputs.append(("bgm", bgm_input_idx, bgm_path))
    bgm_total = effective_duration + cover_duration + end_cards_duration
else:
    bgm_input_idx = None

cover_input_idx = None
if cover_png_path and cover_duration > 0:
    cover_input_idx = len(input_files) + len(extra_inputs)
    extra_inputs.append(("cover", cover_input_idx, cover_png_path))
    vf_parts.append(
        f"[cover_img]overlay=0:0:enable='lte(t,{cover_duration:.4f})'")

overlay_input_idx = None
overlay_path = config.get("video_overlay")
if overlay_path and os.path.isfile(overlay_path):
    overlay_input_idx = len(input_files) + len(extra_inputs)
    extra_inputs.append(("overlay", overlay_input_idx, os.path.abspath(overlay_path)))
    vf_parts.append(
        f"[overlay_img]overlay=0:0:enable='gt(t,{cover_duration:.4f})'")

rec_blink = config.get("rec_blink")
rec_dot_input_idx = None
if rec_blink:
    dot_path = rec_blink.get("dot_image")
    if dot_path and os.path.isfile(dot_path):
        rec_dot_input_idx = len(input_files) + len(extra_inputs)
        extra_inputs.append(("rec_dot", rec_dot_input_idx, os.path.abspath(dot_path)))
        bx = rec_blink.get("x", 62)
        by = rec_blink.get("y", 55)
        period = rec_blink.get("period", 1.0)
        half = period / 2
        vf_parts.append(
            f"[rec_dot]overlay={bx}:{by}:enable='if(gt(t,{cover_duration:.1f}),gte(mod(t,{period:.2f}),{half:.2f}),0)'"
        )
```

Then update the ffmpeg cmd builder (lines 731-745) to use `extra_inputs`:

```python
cmd = ["ffmpeg", "-y"]
for inp in input_files:
    cmd.extend(["-i", inp])
# Add extra inputs in tracked order
for etype, eidx, epath in extra_inputs:
    cmd.extend(["-i", epath])
```

Also fix the dead code at line 637: `fps_val = fps if speed == 1.0 else fps` → just `fps_val = fps`.

**Step 2: Add JSON load error handling**

Wrap `load_config` with try/except:

```python
def load_config(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
```

**Step 3: Commit**

```bash
git add scripts/render_final.py
git commit -m "fix: overlay input index calculation and JSON error handling in render_final.py"
```

---

### Task 2: Add Filler Word Detection to transcribe.py

**Files:**
- Modify: `scripts/transcribe.py`

Add filler word detection that identifies Chinese and English filler words within transcript segments and marks them.

**Step 1: Add filler word detection function**

Add after `detect_silences()`:

```python
# Common filler words/phrases by language
FILLER_PATTERNS = {
    "zh": [
        "嗯", "呃", "额", "啊", "哦", "唔",
        "那个", "就是", "就是说", "然后呢", "怎么说呢",
        "对吧", "你知道吗", "我觉得吧", "基本上",
    ],
    "en": [
        "um", "uh", "erm", "ah", "oh",
        "like", "you know", "i mean", "basically",
        "actually", "literally", "right", "so yeah",
        "kind of", "sort of",
    ],
}


def detect_filler_words(segments, language="zh"):
    """Detect filler words/phrases in transcript segments.

    Args:
        segments: List of {"id", "start", "end", "text"} dicts.
        language: Language code ("zh", "en", etc.)

    Returns:
        List of {"segment_id", "text", "fillers_found", "is_filler_only"} dicts.
    """
    lang_key = "zh" if language and language.startswith("zh") else "en"
    patterns = FILLER_PATTERNS.get(lang_key, FILLER_PATTERNS["en"])

    results = []
    for seg in segments:
        text = seg["text"].strip()
        text_lower = text.lower()
        found = []
        for filler in patterns:
            if lang_key == "zh":
                if filler in text:
                    found.append(filler)
            else:
                # English: word boundary check
                import re
                if re.search(r'\b' + re.escape(filler) + r'\b', text_lower):
                    found.append(filler)

        if found:
            # Check if segment is ONLY filler (short + all filler)
            clean = text_lower
            for f in patterns:
                clean = clean.replace(f, "").strip()
            # Remove punctuation for emptiness check
            import re
            clean = re.sub(r'[^\w]', '', clean)
            is_filler_only = len(clean) == 0 or (len(text) <= 6 and len(found) > 0)

            results.append({
                "segment_id": seg["id"],
                "text": text,
                "fillers_found": found,
                "is_filler_only": is_filler_only,
            })

    return results
```

**Step 2: Integrate into main() and add CLI flag**

Add argument:
```python
parser.add_argument("--detect-fillers", action="store_true",
                    help="Detect filler words (um, uh, 嗯, 呃, etc.) and mark segments")
```

Add to output_data after silences:
```python
if args.detect_fillers:
    fillers = detect_filler_words(segments, detected_lang)
    if fillers:
        output_data["filler_words"] = fillers
```

Add reporting:
```python
if args.detect_fillers and fillers:
    filler_only = [f for f in fillers if f["is_filler_only"]]
    print(f"\nDetected filler words in {len(fillers)} segments ({len(filler_only)} filler-only):")
    for f in fillers[:10]:
        marker = " ← SKIP" if f["is_filler_only"] else ""
        print(f"  #{f['segment_id']:3d} [{', '.join(f['fillers_found'])}] \"{f['text']}\"{marker}")
    if len(fillers) > 10:
        print(f"  ... and {len(fillers) - 10} more")
```

**Step 3: Commit**

```bash
git add scripts/transcribe.py
git commit -m "feat: add filler word detection (Chinese + English) to transcribe.py"
```

---

### Task 3: Create Media Library System (media_library.py)

**Files:**
- Create: `scripts/media_library.py`

Dual-backend index: JSON for small projects (< 200 items), auto-upgrade to SQLite for large. Provides CLI for init, scan, search, and status.

**Step 1: Create media_library.py**

```python
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

AUTO_UPGRADE_THRESHOLD = 200  # Switch to SQLite when items exceed this


# ============================================================
# JSON Backend
# ============================================================

class JSONIndex:
    """JSON file-based media index."""

    def __init__(self, index_path):
        self.path = index_path
        self.data = {"version": 1, "created_at": "", "media": []}
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add(self, item):
        # Deduplicate by path
        existing = [m for m in self.data["media"] if m["path"] == item["path"]]
        if existing:
            existing[0].update(item)
        else:
            self.data["media"].append(item)
        self.save()

    def remove(self, path):
        self.data["media"] = [m for m in self.data["media"] if m["path"] != path]
        self.save()

    def search(self, query):
        query_lower = query.lower()
        return [m for m in self.data["media"]
                if query_lower in m.get("path", "").lower()
                or query_lower in m.get("type", "").lower()
                or any(query_lower in t.lower() for t in m.get("tags", []))]

    def get_all(self):
        return self.data["media"]

    def count(self):
        return len(self.data["media"])

    def get_by_type(self, media_type):
        return [m for m in self.data["media"] if m.get("type") == media_type]


# ============================================================
# SQLite Backend
# ============================================================

class SQLiteIndex:
    """SQLite-based media index for large libraries."""

    SCHEMA = """
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
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(self, db_path):
        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(self.SCHEMA)
        # Store version
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('version', '1')")
        self.conn.commit()

    def save(self):
        self.conn.commit()

    def add(self, item):
        now = datetime.now().isoformat()
        self.conn.execute("""
            INSERT INTO media (id, path, type, category, duration, width, height,
                              fps, file_size, transcript_path, tags, metadata, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                type=excluded.type, category=excluded.category,
                duration=excluded.duration, width=excluded.width, height=excluded.height,
                fps=excluded.fps, file_size=excluded.file_size,
                transcript_path=excluded.transcript_path, tags=excluded.tags,
                metadata=excluded.metadata, updated_at=excluded.updated_at
        """, (
            item.get("id", str(uuid.uuid4())),
            item["path"],
            item["type"],
            item.get("category", ""),
            item.get("duration"),
            item.get("width"),
            item.get("height"),
            item.get("fps"),
            item.get("file_size"),
            item.get("transcript_path"),
            json.dumps(item.get("tags", []), ensure_ascii=False),
            json.dumps(item.get("metadata", {}), ensure_ascii=False),
            item.get("added_at", now),
            now,
        ))
        self.conn.commit()

    def remove(self, path):
        self.conn.execute("DELETE FROM media WHERE path = ?", (path,))
        self.conn.commit()

    def search(self, query):
        query_like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM media WHERE path LIKE ? OR type LIKE ? OR tags LIKE ?",
            (query_like, query_like, query_like)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_all(self):
        rows = self.conn.execute("SELECT * FROM media ORDER BY added_at DESC").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    def get_by_type(self, media_type):
        rows = self.conn.execute(
            "SELECT * FROM media WHERE type = ?", (media_type,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row):
        d = dict(row)
        if d.get("tags"):
            d["tags"] = json.loads(d["tags"])
        if d.get("metadata"):
            d["metadata"] = json.loads(d["metadata"])
        return d

    def close(self):
        self.conn.close()


# ============================================================
# Unified Interface
# ============================================================

def open_index(project_dir, force_backend=None):
    """Open or create the media index with appropriate backend.

    Args:
        project_dir: Project root directory.
        force_backend: "json" or "sqlite" to force a backend. None = auto-detect.

    Returns:
        (index, backend_name) tuple.
    """
    json_path = os.path.join(project_dir, "media_index.json")
    db_path = os.path.join(project_dir, "media_index.db")

    if force_backend == "sqlite":
        return SQLiteIndex(db_path), "sqlite"
    if force_backend == "json":
        return JSONIndex(json_path), "json"

    # Auto-detect: prefer existing backend
    if os.path.isfile(db_path):
        return SQLiteIndex(db_path), "sqlite"
    if os.path.isfile(json_path):
        idx = JSONIndex(json_path)
        # Auto-upgrade if over threshold
        if idx.count() >= AUTO_UPGRADE_THRESHOLD:
            print(f"Index has {idx.count()} items (>= {AUTO_UPGRADE_THRESHOLD}), upgrading to SQLite...")
            return upgrade_to_sqlite(project_dir), "sqlite"
        return idx, "json"

    # New project: start with JSON
    return JSONIndex(json_path), "json"


def upgrade_to_sqlite(project_dir):
    """Migrate JSON index to SQLite."""
    json_path = os.path.join(project_dir, "media_index.json")
    db_path = os.path.join(project_dir, "media_index.db")

    json_idx = JSONIndex(json_path)
    sqlite_idx = SQLiteIndex(db_path)

    for item in json_idx.get_all():
        sqlite_idx.add(item)
    sqlite_idx.save()

    # Rename old JSON as backup
    backup = json_path + ".bak"
    os.rename(json_path, backup)
    print(f"Migrated {json_idx.count()} items to SQLite. JSON backed up to {backup}")
    return sqlite_idx


# ============================================================
# Scanning & Indexing
# ============================================================

def classify_file(filepath, project_dir):
    """Classify a media file by its extension and location."""
    ext = os.path.splitext(filepath)[1].lower()
    rel = os.path.relpath(filepath, project_dir)
    parts = rel.split(os.sep)

    # Determine category from directory name
    category = ""
    for part in parts:
        part_lower = part.lower()
        if part_lower in MEDIA_DIRS:
            category = part_lower
            break
        if part_lower in ("raw", "原始素材", "original", "source"):
            category = "raw"
            break
        if part_lower in ("broll", "b-roll", "cutaway", "补充素材"):
            category = "broll"
            break
        if part_lower in ("bgm", "music", "音乐", "背景音乐"):
            category = "bgm"
            break
        if part_lower in ("assets", "overlay", "素材", "水印"):
            category = "assets"
            break

    if ext in SUPPORTED_VIDEO_EXTS:
        return "video", category or "raw"
    elif ext in SUPPORTED_AUDIO_EXTS:
        return "audio", category or "bgm"
    elif ext in SUPPORTED_IMAGE_EXTS:
        return "image", category or "assets"
    return None, None


def scan_directory(project_dir, index):
    """Scan project directory and index all media files."""
    scanned = 0
    new_items = 0

    for root, dirs, files in os.walk(project_dir):
        # Skip hidden dirs, node_modules, .venv, output
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and d not in ("node_modules", ".venv", "__pycache__", "output")]

        for fname in files:
            filepath = os.path.join(root, fname)
            media_type, category = classify_file(filepath, project_dir)
            if not media_type:
                continue

            scanned += 1
            rel_path = os.path.relpath(filepath, project_dir)

            item = {
                "id": str(uuid.uuid4()),
                "path": rel_path,
                "type": media_type,
                "category": category,
                "file_size": os.path.getsize(filepath),
                "added_at": datetime.now().isoformat(),
                "tags": [],
                "metadata": {},
            }

            # Extract video/audio metadata
            if media_type == "video":
                try:
                    info = get_video_info(filepath)
                    item["duration"] = info.get("duration")
                    item["width"] = info.get("width")
                    item["height"] = info.get("height")
                    item["fps"] = info.get("fps")
                except Exception:
                    pass

            # Check for existing transcript
            if media_type == "video":
                base = os.path.splitext(filepath)[0]
                for suffix in ("_transcript.json", "_audio_transcript.json"):
                    t_path = base + suffix
                    if os.path.isfile(t_path):
                        item["transcript_path"] = os.path.relpath(t_path, project_dir)
                        break

            index.add(item)
            new_items += 1

    return scanned, new_items


# ============================================================
# CLI Commands
# ============================================================

def cmd_init(project_dir):
    """Initialize media library directory structure."""
    print(f"Initializing media library in: {project_dir}\n")

    for dirname, desc in MEDIA_DIRS.items():
        dirpath = os.path.join(project_dir, "media", dirname)
        os.makedirs(dirpath, exist_ok=True)
        # Create .gitkeep
        gitkeep = os.path.join(dirpath, ".gitkeep")
        if not os.path.isfile(gitkeep):
            open(gitkeep, "w").close()
        print(f"  media/{dirname}/  — {desc}")

    # Create index
    index, backend = open_index(project_dir)
    if hasattr(index, "data"):
        index.data["created_at"] = datetime.now().isoformat()
        index.save()

    print(f"\nIndex backend: {backend}")
    print(f"Index file: media_index.{'json' if backend == 'json' else 'db'}")
    print(f"\n✓ Media library initialized.")
    print(f"\nNext steps:")
    print(f"  1. Put your raw videos in media/raw/")
    print(f"  2. Put B-roll clips in media/broll/")
    print(f"  3. Put background music in media/bgm/")
    print(f"  4. Run: python3 scripts/media_library.py scan")


def cmd_scan(project_dir, backend=None):
    """Scan and index all media files."""
    index, bk = open_index(project_dir, force_backend=backend)
    print(f"Scanning: {project_dir} (backend: {bk})")

    scanned, new_items = scan_directory(project_dir, index)
    index.save()

    # Auto-upgrade check for JSON backend
    if bk == "json" and index.count() >= AUTO_UPGRADE_THRESHOLD:
        print(f"\nAuto-upgrading to SQLite ({index.count()} items)...")
        upgrade_to_sqlite(project_dir)

    print(f"\nScan complete: {scanned} files found, {new_items} indexed")
    _print_status(index)


def cmd_status(project_dir):
    """Show library status."""
    index, bk = open_index(project_dir)
    print(f"Media Library Status (backend: {bk})")
    print(f"{'=' * 50}")
    _print_status(index)


def cmd_search(project_dir, query):
    """Search indexed media."""
    index, _ = open_index(project_dir)
    results = index.search(query)
    if not results:
        print(f"No results for: {query}")
        return
    print(f"Found {len(results)} items matching '{query}':\n")
    for item in results:
        dur = f" ({item['duration']:.1f}s)" if item.get("duration") else ""
        res = f" {item.get('width', '?')}x{item.get('height', '?')}" if item.get("width") else ""
        trans = " [has transcript]" if item.get("transcript_path") else ""
        print(f"  [{item['type']:5s}] {item['path']}{dur}{res}{trans}")


def cmd_upgrade(project_dir):
    """Upgrade JSON index to SQLite."""
    json_path = os.path.join(project_dir, "media_index.json")
    if not os.path.isfile(json_path):
        print("No JSON index found to upgrade.")
        return
    upgrade_to_sqlite(project_dir)


def _print_status(index):
    """Print index status summary."""
    all_items = index.get_all()
    by_type = {}
    by_category = {}
    total_duration = 0
    total_size = 0
    with_transcript = 0

    for item in all_items:
        t = item.get("type", "unknown")
        c = item.get("category", "other")
        by_type[t] = by_type.get(t, 0) + 1
        by_category[c] = by_category.get(c, 0) + 1
        if item.get("duration"):
            total_duration += item["duration"]
        if item.get("file_size"):
            total_size += item["file_size"]
        if item.get("transcript_path"):
            with_transcript += 1

    print(f"\nTotal items: {len(all_items)}")
    if by_type:
        print(f"\nBy type:")
        for t, count in sorted(by_type.items()):
            print(f"  {t:10s}: {count}")
    if by_category:
        print(f"\nBy category:")
        for c, count in sorted(by_category.items()):
            print(f"  {c:10s}: {count}")
    if total_duration > 0:
        mins = total_duration / 60
        print(f"\nTotal duration: {mins:.1f} min")
    if total_size > 0:
        mb = total_size / 1024 / 1024
        print(f"Total size: {mb:.0f} MB")
    if with_transcript > 0:
        print(f"With transcript: {with_transcript}")


def main():
    parser = argparse.ArgumentParser(description="Media library index manager")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialize library structure")
    p_init.add_argument("project_dir", nargs="?", default=".")

    p_scan = sub.add_parser("scan", help="Scan and index media files")
    p_scan.add_argument("project_dir", nargs="?", default=".")
    p_scan.add_argument("--backend", choices=["json", "sqlite"], default=None)

    p_status = sub.add_parser("status", help="Show library status")
    p_status.add_argument("project_dir", nargs="?", default=".")

    p_search = sub.add_parser("search", help="Search indexed media")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("project_dir", nargs="?", default=".")

    p_upgrade = sub.add_parser("upgrade", help="Upgrade JSON → SQLite")
    p_upgrade.add_argument("project_dir", nargs="?", default=".")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.project_dir)
    elif args.command == "scan":
        cmd_scan(args.project_dir, backend=args.backend)
    elif args.command == "status":
        cmd_status(args.project_dir)
    elif args.command == "search":
        cmd_search(args.project_dir, args.query)
    elif args.command == "upgrade":
        cmd_upgrade(args.project_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

**Step 2: Update .gitignore**

Add:
```
media_index.json
media_index.db
media_index.json.bak
media/
```

**Step 3: Commit**

```bash
git add scripts/media_library.py .gitignore
git commit -m "feat: add media library system with JSON/SQLite dual backend"
```

---

### Task 4: Add Multi-Platform Export to render_final.py

**Files:**
- Modify: `scripts/render_final.py`

Add `--formats` flag to simultaneously output multiple aspect ratios.

**Step 1: Add format definitions and CLI argument**

Add format definitions near the top of the file:

```python
OUTPUT_FORMATS = {
    "vertical":   {"width": 1080, "height": 1920, "label": "9:16 (抖音/小红书/TikTok)"},
    "square":     {"width": 1080, "height": 1080, "label": "1:1 (Instagram)"},
    "horizontal": {"width": 1920, "height": 1080, "label": "16:9 (YouTube/B站)"},
}
```

Add argument to parser:
```python
parser.add_argument("--formats", nargs="*", choices=list(OUTPUT_FORMATS.keys()),
                    help="Additional output formats: vertical, square, horizontal")
```

**Step 2: Add crop/pad function**

```python
def build_reformat_filter(src_w, src_h, dst_w, dst_h):
    """Build ffmpeg filter to reformat video from src to dst dimensions.
    Uses center-crop (no black bars) for aspect ratio changes."""
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h

    if abs(src_ratio - dst_ratio) < 0.01:
        # Same aspect ratio, just scale
        return f"scale={dst_w}:{dst_h}"
    elif src_ratio > dst_ratio:
        # Source is wider: scale to match height, crop width
        return f"scale=-1:{dst_h},crop={dst_w}:{dst_h}"
    else:
        # Source is taller: scale to match width, crop height
        return f"scale={dst_w}:-1,crop={dst_w}:{dst_h}"
```

**Step 3: After main render loop, add format variants**

After the speed variants loop, add logic to render additional formats from the 1x output (this is acceptable as a second encode since the 1x is already the "master"):

```python
if args.formats and os.path.isfile(base_output):
    for fmt_name in args.formats:
        fmt = OUTPUT_FORMATS[fmt_name]
        fmt_output = base_output.replace(".mp4", f"_{fmt_name}.mp4")
        reformat = build_reformat_filter(width, height, fmt["width"], fmt["height"])
        fmt_cmd = [
            "ffmpeg", "-y", "-i", base_output,
            "-vf", reformat,
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-c:a", "copy",
            fmt_output,
        ]
        print(f"\nRendering {fmt['label']}...")
        try:
            subprocess.run(fmt_cmd, check=True, capture_output=True, text=True)
            size_mb = os.path.getsize(fmt_output) / 1024 / 1024
            print(f"Done: {fmt_output} ({size_mb:.1f}MB)")
        except subprocess.CalledProcessError as e:
            print(f"Format error ({fmt_name}):\n{e.stderr[-2000:]}", file=sys.stderr)
```

**Step 4: Commit**

```bash
git add scripts/render_final.py
git commit -m "feat: add multi-platform format export (vertical/square/horizontal)"
```

---

### Task 5: Add Animated Caption Style Presets

**Files:**
- Modify: `scripts/render_final.py` (add caption presets to ASS generation)

**Step 1: Add caption preset definitions**

Add near the top of the file:

```python
CAPTION_PRESETS = {
    "normal": {
        "desc": "Standard white subtitle with outline",
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 3,
        "shadow": 1,
        "bold": 0,
    },
    "karaoke": {
        "desc": "Word-by-word highlight (existing)",
    },
    "bold_pop": {
        "desc": "MrBeast/Hormozi style — thick outline, high contrast",
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 3,
        "bold": 1,
    },
    "neon": {
        "desc": "Neon glow effect — colored text with bright outline",
        "primary_color": "&H0000FFFF",  # Cyan
        "outline_color": "&H00FF00FF",  # Magenta
        "outline": 4,
        "shadow": 0,
        "bold": 1,
    },
    "minimal": {
        "desc": "Clean minimal — thin text, no outline",
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 0,
        "shadow": 2,
        "bold": 0,
    },
    "yellow_pop": {
        "desc": "Yellow text, black outline — high visibility",
        "primary_color": "&H0000FFFF",  # Yellow in ASS (BGR)
        "outline_color": "&H00000000",
        "outline": 4,
        "shadow": 1,
        "bold": 1,
    },
}
```

**Step 2: Apply presets in build_merged_ass()**

In the ASS Style line generation, check for preset and apply:

```python
preset_name = config.get("subtitle_style", "normal")
if preset_name in CAPTION_PRESETS and preset_name not in ("karaoke",):
    preset = CAPTION_PRESETS[preset_name]
    primary_color = preset["primary_color"]
    outline_color = preset["outline_color"]
    outline = preset["outline"]
    shadow = preset["shadow"]
    bold_flag = preset["bold"]
else:
    primary_color = "&H00FFFFFF"
    outline_color = "&H00000000"
    outline = 3
    shadow = 1
    bold_flag = 0
```

**Step 3: Update CLI help and config docs**

Add `--subtitle-style` choices to include new presets.

**Step 4: Commit**

```bash
git add scripts/render_final.py
git commit -m "feat: add animated caption presets (bold_pop, neon, minimal, yellow_pop)"
```

---

### Task 6: Update SKILL.md — Media Library + AI Workflow

**Files:**
- Modify: `SKILL.md`

**Step 1: Add Phase 0 (Media Library Setup) before Phase 1**

Insert new phase at the beginning of the Workflow section:

```markdown
### Phase 0: Media Library Setup（素材库初始化）

首次使用时，帮助用户建立素材目录结构：

```bash
python3 scripts/media_library.py init [project_dir]
```

这会创建以下目录结构：
```
media/
├── raw/      — 原始素材（摄像机/手机直出的视频）
├── broll/    — B-roll 素材（城市街景、产品特写等）
├── bgm/      — 背景音乐（MP3/WAV/M4A）
├── assets/   — 叠加素材（水印 PNG、Logo 等）
└── output/   — 输出目录
```

**询问素材来源**：
1. 询问用户的视频文件位置（本地路径或外部设备）
2. 建议将原始素材复制到 `media/raw/` 目录
3. 询问是否有 B-roll、BGM 等辅助素材

**扫描并建立索引**：
```bash
python3 scripts/media_library.py scan [project_dir]
```

索引系统会自动：
- 扫描所有视频/音频/图片文件
- 提取时长、分辨率、帧率等元数据
- 关联已有的 transcript 文件
- 小型项目（< 200 文件）使用 JSON 索引，大型项目自动升级为 SQLite

**查看素材库状态**：
```bash
python3 scripts/media_library.py status
```
```

**Step 2: Add AI Smart Clip Selection guidance to Phase 3**

Add to Phase 3 (User Interaction) section:

```markdown
#### AI 智能选片建议

在展示片段列表时，AI agent 应基于以下维度为每个片段提供推荐评分（1-5 分）：

**吸引力评分维度**：
1. **Hook 强度**（前 3 秒）：是否有吸引人的开头（提问、反直觉观点、情感触发）
2. **信息密度**：每秒传递的有效信息量（避免重复、废话）
3. **情感变化**：是否有情感起伏（幽默→严肃→惊喜）
4. **完整性**：片段是否构成一个完整的叙事单元（有开头、展开、收尾）
5. **视觉多样性**：画面是否有变化（vs 一直对着镜头说）

**自动跳过建议**：
- 标记为 `is_filler_only` 的片段（纯填充词）
- 静音间隙 > 2 秒的相邻片段（可能是卡壳后重说）
- 转录文字与前一片段高度重复的片段（重说/口误）

**长视频自动拆分**：
当视频 > 3 分钟时，AI agent 应：
1. 分析 transcript 识别话题转换点（语义断裂、过渡词如"接下来"、"另外"）
2. 将片段按话题分组为"潜在短视频"（每个 30-90 秒）
3. 为每组计算整体吸引力评分
4. 展示给用户选择：

```
推荐短视频拆分方案：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  方案 | 片段范围    | 时长  | 主题         | 推荐指数
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  A    | #1-#8       | 45s   | 痛点引入      | ★★★★★
  B    | #9-#18      | 62s   | 核心方法      | ★★★★☆
  C    | #19-#25     | 38s   | 实操演示      | ★★★☆☆
  D    | #1-#25      | 2m25s | 完整版       | ★★★★☆
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
```

**Step 3: Add filler word detection to Phase 2**

Update Phase 2 command:
```markdown
```bash
python3 scripts/transcribe.py "<audio_path>" --model auto --language zh --detect-fillers
```

新增 `--detect-fillers` 参数会自动检测填充词（中文：嗯/呃/那个/就是说；英文：um/uh/like/you know），并标记纯填充词片段为建议跳过。
```

**Step 4: Add multi-platform export to Phase 5**

Update Phase 5:
```markdown
**多平台格式导出**：
```bash
python3 scripts/render_final.py --config render_config.json --output final.mp4 \
  --formats vertical square horizontal
```

同时输出：
- `final.mp4`（原始比例）
- `final_vertical.mp4`（9:16 抖音/小红书/TikTok）
- `final_square.mp4`（1:1 Instagram）
- `final_horizontal.mp4`（16:9 YouTube/B站）
```

**Step 5: Add caption presets documentation**

Add to subtitle_style section:
```markdown
**字幕风格预设**：
| 风格 | 效果 | 适用场景 |
|------|------|---------|
| `normal` | 白字黑描边 | 默认，适合所有场景 |
| `karaoke` | 逐词高亮 | 音乐/节奏感内容 |
| `bold_pop` | 粗描边高对比 | MrBeast/Hormozi 风格 |
| `neon` | 霓虹灯效果 | 科技/潮流内容 |
| `minimal` | 极简无描边 | 文艺/安静内容 |
| `yellow_pop` | 黄字黑描边 | 高可见度，户外/嘈杂画面 |
```

**Step 6: Commit**

```bash
git add SKILL.md
git commit -m "feat: update SKILL.md with media library, AI clip selection, filler detection, multi-format"
```

---

### Task 7: Update README.md

**Files:**
- Modify: `README.md`

**Step 1: Add media library section and update feature list**

Add "Media Library" to features list and add a usage section covering:
- `media_library.py init` / `scan` / `status` / `search` / `upgrade`
- Dual backend explanation
- Filler word detection
- New caption presets
- Multi-platform export

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README with media library, filler detection, caption presets, multi-format export"
```

---

### Task 8: Update .gitignore

**Files:**
- Modify: `.gitignore`

**Step 1: Add media library entries**

```
# Media library
media/
media_index.json
media_index.db
media_index.json.bak
```

**Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: update .gitignore for media library files"
```

---

## Execution Order

Tasks 1-2 (bug fix + filler detection) are independent.
Task 3 (media library) is independent.
Tasks 4-5 (multi-format + caption presets) depend on Task 1 (same file).
Task 6 (SKILL.md) depends on Tasks 2-5.
Task 7 (README) depends on Task 6.
Task 8 (.gitignore) is independent.

**Recommended parallel batches:**
1. **Batch 1** (parallel): Task 1 + Task 2 + Task 3 + Task 8
2. **Batch 2** (parallel): Task 4 + Task 5
3. **Batch 3** (sequential): Task 6 → Task 7
