#!/usr/bin/env python3
"""Editable transcript review round trip for Whisper JSON.

The tool keeps this pipeline CLI-first: export a human-editable review file,
let the user fix ASR mistakes, then apply those edits back into transcript JSON
while preserving segment timings and optionally redistributing word timings.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "transcript_review.v1"
REVIEW_LINE = re.compile(
    r"^\[seg:(?P<id>[^\s\]]+)\s+start:(?P<start>[0-9:.]+)\s+end:(?P<end>[0-9:.]+)\]\s*(?P<text>.*?)\s*$"
)
TIME_ONLY_LINE = re.compile(r"^\[(?P<start>[0-9:.]+)\]\s*(?P<text>.*?)\s*$")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?|[^\s]"
)


class TranscriptReviewError(ValueError):
    """Raised for user-fixable transcript review errors."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def parse_time(value: str) -> float:
    raw = str(value).strip()
    if not raw:
        raise TranscriptReviewError("empty timecode")
    if ":" not in raw:
        return float(raw)
    parts = raw.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise TranscriptReviewError(f"bad timecode: {value!r}")


def load_transcript(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    data = _read_json(path)
    if isinstance(data, list):
        wrapper: Dict[str, Any] = {"segments": data}
    elif isinstance(data, dict):
        wrapper = data
    else:
        raise TranscriptReviewError("transcript must be a JSON object or segment list")
    segments = wrapper.get("segments")
    if not isinstance(segments, list):
        raise TranscriptReviewError("transcript must contain a segments list")
    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(segments, start=1):
        if not isinstance(raw, dict):
            continue
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"), start)
        if end <= start:
            continue
        segment = copy.deepcopy(raw)
        segment.setdefault("id", idx)
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
        segment["text"] = _clean_text(segment.get("text", ""))
        normalized.append(segment)
    if not normalized:
        raise TranscriptReviewError("transcript has no valid timed segments")
    wrapper = copy.deepcopy(wrapper)
    wrapper["segments"] = normalized
    return wrapper, normalized


def _parse_text_correction_line(line: str) -> Optional[Tuple[str, str]]:
    for sep in ("=>", "->", "="):
        if sep in line:
            left, right = line.split(sep, 1)
            wrong = left.strip()
            correct = right.strip()
            if wrong:
                return wrong, correct
    return None


def load_corrections(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    raw = _read_json(path) if path.lower().endswith(".json") else None
    corrections: Dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            wrong = str(key).strip()
            if wrong:
                corrections[wrong] = str(value).strip()
        return corrections
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            wrong = str(item.get("wrong") or item.get("from") or "").strip()
            right = str(item.get("right") or item.get("to") or "").strip()
            if wrong:
                corrections[wrong] = right
        return corrections
    if raw is not None:
        raise TranscriptReviewError("corrections JSON must be an object or a list of wrong/right objects")

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = _parse_text_correction_line(line)
            if parsed:
                wrong, right = parsed
                corrections[wrong] = right
    return corrections


def _needs_word_boundary(pattern: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_ -]*", pattern))


def apply_text_corrections(text: str, corrections: Mapping[str, str]) -> Tuple[str, Dict[str, int]]:
    result = str(text)
    applied: Dict[str, int] = {}
    for wrong, right in corrections.items():
        if not wrong:
            continue
        if _needs_word_boundary(wrong):
            pattern = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)")
            result, count = pattern.subn(right, result)
        else:
            count = result.count(wrong)
            result = result.replace(wrong, right)
        if count:
            applied[wrong] = applied.get(wrong, 0) + count
    return result, applied


def _merge_counts(base: Dict[str, int], extra: Mapping[str, int]) -> None:
    for key, count in extra.items():
        base[key] = base.get(key, 0) + int(count)


def _default_review_path(transcript_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(transcript_path)), "transcript_review.txt")


def _default_html_path(transcript_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(transcript_path)), "transcript_review.html")


def build_review_lines(
    transcript_path: str,
    segments: Sequence[Mapping[str, Any]],
    corrections: Mapping[str, str],
) -> Tuple[List[str], Dict[str, int]]:
    applied_total: Dict[str, int] = {}
    lines = [
        "# Transcript Review",
        "# Edit only the text after the prefix. Keep [seg:<id> start:<time> end:<time>] unchanged.",
        "# After review, run: python3 scripts/transcript_review.py apply --transcript <json> --review <this-file> --output <reviewed.json>",
        f"# Source: {os.path.abspath(transcript_path)}",
        f"# Generated: {_now_iso()}",
        f"# Version: {VERSION}",
        "",
    ]
    for segment in segments:
        text, applied = apply_text_corrections(_clean_text(segment.get("text", "")), corrections)
        _merge_counts(applied_total, applied)
        lines.append(
            "[seg:{id} start:{start} end:{end}] {text}".format(
                id=segment.get("id"),
                start=format_time(_as_float(segment.get("start"))),
                end=format_time(_as_float(segment.get("end"))),
                text=text,
            )
        )
    lines.extend(["", "# === CORRECTIONS APPLIED ==="])
    if applied_total:
        for wrong, count in sorted(applied_total.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"# {wrong} => {corrections[wrong]} (x{count})")
    else:
        lines.append("# (none)")
    return lines, applied_total


def write_review(path: str, lines: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def build_html_payload(
    transcript_path: str,
    segments: Sequence[Mapping[str, Any]],
    corrections: Mapping[str, str],
    *,
    video_path: Optional[str] = None,
    max_cps: float = 20.0,
    review_name: str = "transcript_review.txt",
) -> Dict[str, Any]:
    """Build the local-only payload used by the interactive review page."""
    if max_cps <= 0 or not math.isfinite(max_cps):
        raise TranscriptReviewError("max_cps must be a positive finite number")

    transcript_abs = os.path.abspath(transcript_path)
    video_abs = os.path.abspath(video_path) if video_path else ""
    applied_total: Dict[str, int] = {}
    items: List[Dict[str, Any]] = []
    for segment in segments:
        text, applied = apply_text_corrections(_clean_text(segment.get("text", "")), corrections)
        _merge_counts(applied_total, applied)
        items.append(
            {
                "id": segment.get("id"),
                "start": round(_as_float(segment.get("start")), 3),
                "end": round(_as_float(segment.get("end")), 3),
                "text": text,
            }
        )

    safe_review_name = os.path.basename(str(review_name or "").strip().replace("\\", "/")) or "transcript_review.txt"
    signature = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "version": VERSION,
        "generated_at": _now_iso(),
        "transcript": transcript_abs,
        "title": os.path.basename(transcript_abs),
        "video": {
            "path": video_abs,
            "uri": Path(video_abs).resolve().as_uri() if video_abs else "",
        },
        "max_cps": float(max_cps),
        "review_name": safe_review_name,
        "transcript_signature": signature,
        "storage_key": f"video-editing-skill:transcript-review:{transcript_abs}:{signature}",
        "segments": items,
        "summary": {
            "segments": len(items),
            "corrections_applied": sum(applied_total.values()),
            "corrections": applied_total,
        },
    }


def emit_review_html(payload: Mapping[str, Any]) -> str:
    """Emit a dependency-free transcript editor with synchronized local playback."""
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload_html = html.escape(payload_json, quote=False)
    title = html.escape(str(payload.get("title") or "Transcript Review"), quote=True)
    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Transcript Review · __TITLE__</title>
  <style>
    :root { color-scheme: dark; --bg:#0b0d12; --panel:#151922; --line:#2a3140; --text:#f6f7fb; --muted:#9ba6ba; --accent:#69d2ff; --warn:#ffbe55; --dirty:#ffe082; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:15px/1.45 ui-sans-serif,system-ui,-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif; }
    header { position:sticky; top:0; z-index:20; display:flex; gap:18px; align-items:center; justify-content:space-between; padding:14px 22px; border-bottom:1px solid var(--line); background:rgba(11,13,18,.94); backdrop-filter:blur(14px); }
    h1 { margin:0; font-size:20px; }
    #stats { margin:3px 0 0; color:var(--muted); font-size:13px; }
    button,.file-label,input { font:inherit; }
    button,.file-label { border:1px solid var(--line); border-radius:9px; background:#202633; color:var(--text); padding:8px 11px; cursor:pointer; }
    button:hover,.file-label:hover { border-color:var(--accent); }
    button.primary { background:var(--accent); border-color:var(--accent); color:#061018; font-weight:750; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }
    main { display:grid; grid-template-columns:minmax(290px,36vw) minmax(0,1fr); min-height:calc(100vh - 69px); }
    aside { position:sticky; top:69px; align-self:start; max-height:calc(100vh - 69px); overflow:auto; padding:18px; border-right:1px solid var(--line); background:#10141b; }
    video { width:100%; max-height:42vh; border-radius:12px; background:#000; }
    .video-tools { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:10px; color:var(--muted); font-size:12px; }
    .file-label input { display:none; }
    .toolbox { margin-top:18px; padding:14px; border:1px solid var(--line); border-radius:12px; background:var(--panel); }
    .toolbox h2 { margin:0 0 10px; font-size:14px; }
    .replace-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    input { min-width:0; width:100%; border:1px solid var(--line); border-radius:8px; background:#0d1118; color:var(--text); padding:9px; }
    #replace-all { width:100%; margin-top:8px; }
    .legend { margin:14px 2px 0; color:var(--muted); font-size:12px; }
    .legend strong { color:var(--warn); }
    #segments { padding:18px; }
    .segment { display:grid; grid-template-columns:125px minmax(0,1fr) 92px; gap:12px; align-items:start; padding:12px; border:1px solid transparent; border-bottom-color:var(--line); border-radius:10px; }
    .segment:hover { background:var(--panel); }
    .segment.active { border-color:var(--accent); background:#14212a; }
    .segment.dirty .time { color:var(--dirty); }
    .time { border:0; background:transparent; color:var(--accent); padding:7px 2px; text-align:left; font:600 12px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
    textarea { width:100%; min-height:52px; resize:vertical; border:1px solid var(--line); border-radius:8px; background:#0d1118; color:var(--text); padding:9px 10px; font:16px/1.55 inherit; }
    textarea:focus { outline:2px solid color-mix(in srgb,var(--accent) 55%,transparent); border-color:var(--accent); }
    .meta { padding-top:7px; color:var(--muted); text-align:right; font-size:12px; }
    .meta.warn { color:var(--warn); font-weight:700; }
    .empty { padding:60px 20px; color:var(--muted); text-align:center; }
    @media (max-width:820px) { main { grid-template-columns:1fr; } aside { position:static; max-height:none; border-right:0; border-bottom:1px solid var(--line); } .segment { grid-template-columns:105px minmax(0,1fr); } .meta { grid-column:2; text-align:left; padding-top:0; } }
  </style>
</head>
<body>
  <header>
    <div><h1>Transcript Review</h1><p id="stats">载入中…</p></div>
    <div class="actions">
      <button id="copy-review">复制 review</button>
      <button id="reset-review">重置</button>
      <button id="save-review" class="primary">保存 review.txt</button>
    </div>
  </header>
  <main>
    <aside>
      <video id="video" controls preload="metadata"></video>
      <div class="video-tools">
        <span id="video-name">未绑定视频</span>
        <label class="file-label">选择本地视频<input id="video-picker" type="file" accept="video/*,audio/*"></label>
      </div>
      <div class="toolbox">
        <h2>全文查找 / 替换</h2>
        <div class="replace-grid"><input id="find" placeholder="查找"><input id="replace" placeholder="替换为"></div>
        <button id="replace-all">全部替换</button>
      </div>
      <p class="legend">点击时间码跳到该段；播放时自动高亮。编辑会保存在当前浏览器。<strong>CPS 超过阈值时会标黄</strong>，仅提示阅读压力，不自动改字。</p>
    </aside>
    <section id="segments"></section>
  </main>
  <script id="review-data" type="application/json">__PAYLOAD__</script>
  <script>
  (() => {
    "use strict";
    const data = JSON.parse(document.getElementById("review-data").textContent);
    const original = data.segments.map((segment) => String(segment.text || ""));
    const segments = data.segments.map((segment) => ({...segment, text:String(segment.text || "")}));
    const root = document.getElementById("segments");
    const video = document.getElementById("video");
    let activeIndex = -1;
    let pickedVideoUrl = "";

    const clean = (value) => String(value || "").replace(/[\r\n]+/g, " ").replace(/\s+/g, " ").trim();
    const formatTime = (value) => {
      const total = Math.max(0, Number(value) || 0);
      const minutes = Math.floor(total / 60);
      const seconds = total - minutes * 60;
      return String(minutes).padStart(2, "0") + ":" + seconds.toFixed(3).padStart(6, "0");
    };
    const cps = (segment) => {
      const duration = Math.max(.001, Number(segment.end) - Number(segment.start));
      return Array.from(clean(segment.text).replace(/\s/g, "")).length / duration;
    };
    const isDirty = (index) => clean(segments[index].text) !== clean(original[index]);

    function restoreDraft() {
      try {
        const saved = JSON.parse(localStorage.getItem(data.storage_key) || "null");
        if (!saved || saved.transcript !== data.transcript || !Array.isArray(saved.texts)) return;
        saved.texts.forEach((text, index) => { if (index < segments.length) segments[index].text = String(text); });
      } catch (_) {}
    }

    function saveDraft() {
      try {
        localStorage.setItem(data.storage_key, JSON.stringify({
          transcript:data.transcript,
          saved_at:new Date().toISOString(),
          texts:segments.map((segment) => segment.text)
        }));
      } catch (_) {}
    }

    function updateStats() {
      const dirty = segments.filter((_, index) => isDirty(index)).length;
      const warnings = segments.filter((segment) => cps(segment) > Number(data.max_cps)).length;
      document.getElementById("stats").textContent = `${segments.length} 段 · ${dirty} 处修改 · ${warnings} 个 CPS 提示 · 自动本地保存`;
    }

    function refreshRow(index, refreshStats = true) {
      const row = root.querySelector(`[data-index="${index}"]`);
      if (!row) return;
      row.classList.toggle("dirty", isDirty(index));
      const value = cps(segments[index]);
      const meta = row.querySelector(".meta");
      meta.textContent = `${value.toFixed(1)} CPS · ${(Number(segments[index].end) - Number(segments[index].start)).toFixed(2)}s`;
      meta.classList.toggle("warn", value > Number(data.max_cps));
      if (refreshStats) updateStats();
    }

    function seek(index, play) {
      if (!Number.isFinite(Number(segments[index].start))) return;
      setActive(index, true);
      if (!video.currentSrc && !video.getAttribute("src")) return;
      try { video.currentTime = Number(segments[index].start); } catch (_) {}
      if (play) video.play().catch(() => {});
    }

    function setActive(index, scroll) {
      if (activeIndex === index) return;
      const previous = root.querySelector(".segment.active");
      if (previous) previous.classList.remove("active");
      activeIndex = index;
      const next = root.querySelector(`[data-index="${index}"]`);
      if (next) {
        next.classList.add("active");
        if (scroll) next.scrollIntoView({block:"nearest", behavior:"smooth"});
      }
    }

    function render() {
      root.textContent = "";
      if (!segments.length) {
        root.innerHTML = '<div class="empty">没有可复核的 transcript segment。</div>';
        updateStats();
        return;
      }
      segments.forEach((segment, index) => {
        const row = document.createElement("article");
        row.className = "segment";
        row.dataset.index = String(index);

        const time = document.createElement("button");
        time.className = "time";
        time.type = "button";
        time.textContent = `${formatTime(segment.start)}\n${formatTime(segment.end)}`;
        time.title = "跳到这一段并播放";
        time.addEventListener("click", () => seek(index, true));

        const textarea = document.createElement("textarea");
        textarea.dir = "auto";
        textarea.value = segment.text;
        textarea.setAttribute("aria-label", `Segment ${segment.id}`);
        textarea.addEventListener("focus", () => seek(index, false));
        textarea.addEventListener("input", () => {
          segment.text = textarea.value;
          saveDraft();
          refreshRow(index);
        });

        const meta = document.createElement("div");
        meta.className = "meta";
        row.append(time, textarea, meta);
        root.appendChild(row);
        refreshRow(index, false);
      });
      updateStats();
    }

    function reviewText() {
      const lines = [
        "# Transcript Review",
        "# Exported from the local interactive reviewer. Edit only text after each prefix.",
        `# Source: ${data.transcript}`,
        `# Generated: ${new Date().toISOString()}`,
        `# Version: ${data.version}`,
        ""
      ];
      segments.forEach((segment) => {
        lines.push(`[seg:${segment.id} start:${formatTime(segment.start)} end:${formatTime(segment.end)}] ${clean(segment.text)}`);
      });
      lines.push("");
      return lines.join("\n");
    }

    function downloadFallback(content) {
      const blob = new Blob([content], {type:"text/plain;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = data.review_name || "transcript_review.txt";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function saveReview() {
      const content = reviewText();
      if (window.showSaveFilePicker) {
        try {
          const handle = await window.showSaveFilePicker({
            suggestedName:data.review_name || "transcript_review.txt",
            types:[{description:"Transcript review", accept:{"text/plain":[".txt"]}}]
          });
          const writable = await handle.createWritable();
          await writable.write(content);
          await writable.close();
          return;
        } catch (error) {
          if (error && error.name === "AbortError") return;
        }
      }
      downloadFallback(content);
    }

    restoreDraft();
    render();
    if (data.video && data.video.uri) {
      video.src = data.video.uri;
      document.getElementById("video-name").textContent = data.video.path.split(/[\\/]/).pop();
    }

    video.addEventListener("timeupdate", () => {
      const now = Number(video.currentTime);
      const index = segments.findIndex((segment) => now >= Number(segment.start) && now < Number(segment.end));
      setActive(index, index >= 0 && !video.paused);
    });
    document.getElementById("video-picker").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      if (pickedVideoUrl) URL.revokeObjectURL(pickedVideoUrl);
      pickedVideoUrl = URL.createObjectURL(file);
      video.src = pickedVideoUrl;
      document.getElementById("video-name").textContent = file.name;
    });
    document.getElementById("replace-all").addEventListener("click", () => {
      const find = document.getElementById("find").value;
      const replacement = document.getElementById("replace").value;
      if (!find) return;
      segments.forEach((segment) => { segment.text = segment.text.split(find).join(replacement); });
      saveDraft();
      render();
    });
    document.getElementById("reset-review").addEventListener("click", () => {
      if (!confirm("重置全部浏览器内修改？")) return;
      segments.forEach((segment, index) => { segment.text = original[index]; });
      try { localStorage.removeItem(data.storage_key); } catch (_) {}
      render();
    });
    document.getElementById("copy-review").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(reviewText()); } catch (_) { downloadFallback(reviewText()); }
    });
    document.getElementById("save-review").addEventListener("click", saveReview);
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveReview();
      } else if (event.code === "Space" && !/^(INPUT|TEXTAREA|BUTTON)$/.test(document.activeElement.tagName)) {
        event.preventDefault();
        video.paused ? video.play().catch(() => {}) : video.pause();
      }
    });
  })();
  </script>
</body>
</html>
"""
    return template.replace("__TITLE__", title).replace("__PAYLOAD__", payload_html)


def parse_review(path: str) -> List[Dict[str, Any]]:
    edits: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            raw = line.rstrip("\n")
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = REVIEW_LINE.match(stripped)
            if match:
                edits.append({
                    "line": lineno,
                    "id": match.group("id"),
                    "start": parse_time(match.group("start")),
                    "end": parse_time(match.group("end")),
                    "text": _clean_text(match.group("text")),
                    "format": "seg",
                })
                continue
            match = TIME_ONLY_LINE.match(stripped)
            if match:
                edits.append({
                    "line": lineno,
                    "id": None,
                    "start": parse_time(match.group("start")),
                    "end": None,
                    "text": _clean_text(match.group("text")),
                    "format": "time",
                })
                continue
            raise TranscriptReviewError(f"review line {lineno} is not a recognized transcript line: {raw!r}")
    if not edits:
        raise TranscriptReviewError("review file contains no transcript lines")
    return edits


def tokenize_text(text: str) -> List[str]:
    return TOKEN_RE.findall(_clean_text(text))


def _timed_word_span(segment: Mapping[str, Any]) -> Tuple[float, float]:
    words = segment.get("words")
    if isinstance(words, list):
        timed = [
            word for word in words
            if isinstance(word, dict) and "start" in word and "end" in word
        ]
        if timed:
            start = _as_float(timed[0].get("start"), _as_float(segment.get("start")))
            end = _as_float(timed[-1].get("end"), _as_float(segment.get("end"), start))
            if end > start:
                return start, end
    return _as_float(segment.get("start")), _as_float(segment.get("end"))


def redistribute_words(text: str, segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    tokens = tokenize_text(text)
    if not tokens:
        return []
    start, end = _timed_word_span(segment)
    if end <= start:
        end = start + 0.001
    span = end - start
    weights = [max(1, len(token)) for token in tokens]
    total_weight = float(sum(weights)) or 1.0
    out: List[Dict[str, Any]] = []
    cursor = start
    for token, weight in zip(tokens, weights):
        duration = span * weight / total_weight
        next_time = cursor + duration
        out.append({
            "word": token,
            "start": round(cursor, 3),
            "end": round(next_time, 3),
        })
        cursor = next_time
    out[-1]["end"] = round(end, 3)
    return out


def _segment_lookup(segments: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    by_id = {str(segment.get("id")): segment for segment in segments}
    sorted_segments = sorted(segments, key=lambda seg: (_as_float(seg.get("start")), _as_float(seg.get("end"))))
    return by_id, sorted_segments


def _match_segment(
    edit: Mapping[str, Any],
    by_id: Mapping[str, Dict[str, Any]],
    sorted_segments: Sequence[Dict[str, Any]],
    tolerance: float,
) -> Optional[Dict[str, Any]]:
    edit_id = edit.get("id")
    if edit_id is not None and str(edit_id) in by_id:
        return by_id[str(edit_id)]
    start = _as_float(edit.get("start"))
    if not sorted_segments:
        return None
    best = min(sorted_segments, key=lambda seg: abs(_as_float(seg.get("start")) - start))
    if abs(_as_float(best.get("start")) - start) <= tolerance:
        return best
    return None


def apply_review_edits(
    transcript: Dict[str, Any],
    edits: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 0.75,
    redistribute: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    updated = copy.deepcopy(transcript)
    segments: List[Dict[str, Any]] = updated["segments"]
    by_id, sorted_segments = _segment_lookup(segments)
    changes: List[Dict[str, Any]] = []
    matched_ids = set()
    for edit in edits:
        segment = _match_segment(edit, by_id, sorted_segments, tolerance)
        if segment is None:
            raise TranscriptReviewError(
                f"review line {edit.get('line')} did not match any segment within {tolerance:.2f}s"
            )
        seg_key = str(segment.get("id"))
        if seg_key in matched_ids:
            raise TranscriptReviewError(f"review line {edit.get('line')} duplicates segment {seg_key}")
        matched_ids.add(seg_key)
        before = _clean_text(segment.get("text", ""))
        after = _clean_text(edit.get("text", ""))
        if before != after:
            changes.append({
                "id": segment.get("id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "before": before,
                "after": after,
            })
        segment["text"] = after
        if redistribute:
            segment["words"] = redistribute_words(after, segment)

    summary = {
        "version": VERSION,
        "applied_at": _now_iso(),
        "segments_in_review": len(edits),
        "changed_segments": len(changes),
        "word_timing": "redistributed" if redistribute else "unchanged",
        "changes": changes,
    }
    updated["review"] = summary
    return updated, summary


def cmd_export(args: argparse.Namespace) -> int:
    _transcript, segments = load_transcript(args.transcript)
    corrections = load_corrections(args.corrections)
    lines, applied = build_review_lines(args.transcript, segments, corrections)
    review_path = args.review or _default_review_path(args.transcript)
    write_review(review_path, lines)
    print(f"review file: {review_path}")
    print(f"segments: {len(segments)}")
    print(f"corrections applied: {sum(applied.values())}")
    return 0


def cmd_html(args: argparse.Namespace) -> int:
    _transcript, segments = load_transcript(args.transcript)
    corrections = load_corrections(args.corrections)
    if args.video and not os.path.isfile(args.video):
        raise TranscriptReviewError(f"video not found: {args.video}")
    payload = build_html_payload(
        args.transcript,
        segments,
        corrections,
        video_path=args.video,
        max_cps=args.max_cps,
        review_name=args.review_name,
    )
    output = args.output or _default_html_path(args.transcript)
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(emit_review_html(payload))
    print(f"interactive review: {output}")
    print(f"segments: {len(segments)}")
    print(f"corrections applied: {payload['summary']['corrections_applied']}")
    print("Open the HTML locally, review against the video, then save transcript_review.txt.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    transcript, _segments = load_transcript(args.transcript)
    edits = parse_review(args.review)
    output = args.transcript if args.in_place else args.output
    if not output:
        base, ext = os.path.splitext(args.transcript)
        output = f"{base}_reviewed{ext or '.json'}"
    updated, summary = apply_review_edits(
        transcript,
        edits,
        tolerance=args.tolerance,
        redistribute=not args.keep_words,
    )
    _write_json(output, updated)
    print(f"reviewed transcript: {output}")
    print(f"review lines: {summary['segments_in_review']}")
    print(f"changed segments: {summary['changed_segments']}")
    print(f"word timing: {summary['word_timing']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export/apply editable transcript review files.")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Write transcript_review.txt from transcript JSON.")
    export.add_argument("--transcript", required=True, help="Whisper transcript JSON with segments.")
    export.add_argument("--review", help="Output review text path. Defaults to transcript_review.txt next to transcript.")
    export.add_argument("--corrections", help="Optional corrections JSON/text file: wrong => right.")
    export.set_defaults(func=cmd_export)

    html_cmd = sub.add_parser("html", help="Write a local interactive transcript review page.")
    html_cmd.add_argument("--transcript", required=True, help="Whisper transcript JSON with segments.")
    html_cmd.add_argument("--video", help="Optional local source video/audio to preload in the page.")
    html_cmd.add_argument("--output", help="Output HTML. Defaults to transcript_review.html next to transcript.")
    html_cmd.add_argument("--corrections", help="Optional corrections JSON/text file: wrong => right.")
    html_cmd.add_argument("--max-cps", type=float, default=20.0,
                          help="Highlight segments above this characters-per-second threshold.")
    html_cmd.add_argument("--review-name", default="transcript_review.txt",
                          help="Suggested filename when saving the reviewed text from the browser.")
    html_cmd.set_defaults(func=cmd_html)

    apply = sub.add_parser("apply", help="Apply transcript_review.txt edits back to transcript JSON.")
    apply.add_argument("--transcript", required=True, help="Original transcript JSON.")
    apply.add_argument("--review", required=True, help="Edited review text file.")
    apply.add_argument("--output", help="Reviewed transcript JSON. Defaults to <transcript>_reviewed.json.")
    apply.add_argument("--in-place", action="store_true", help="Overwrite --transcript instead of writing a reviewed copy.")
    apply.add_argument("--tolerance", type=float, default=0.75,
                       help="Fallback start-time matching tolerance in seconds when no segment id is present.")
    apply.add_argument("--keep-words", action="store_true",
                       help="Keep existing words arrays unchanged instead of redistributing timings.")
    apply.set_defaults(func=cmd_apply)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TranscriptReviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
