# Video Editing Skill V3 — Xiaohongshu-Optimized Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the video-editing skill from a generic auto-cutter into a Xiaohongshu-tuned content engine: better story (hooks/structure/CTA), platform-safe (taboos lint), richer output (B-roll/title cards/beat sync), with audience-aware defaults and multi-platform export.

**Architecture:** Layer new capabilities as composable scripts in `scripts/`, driven by YAML profiles in `scripts/profiles/`, gated by a `content_guard.py` lint, and exposed via existing CLI entrypoints. Keep all current behavior backward-compatible by defaulting new flags to off (Phase 2+); make P0 fixes (Phase 1) the new default since they're regressions caught in day58 production.

**Tech Stack:** Python 3 + ffmpeg + Whisper (mlx/faster/openai) + Pillow + PyYAML + librosa (Phase 2) + spaCy/WhisperNER (Phase 2) + zxing-cpp (Phase 2). All existing dependencies stay; new ones added per-phase.

---

## Phase Index

| Phase | Theme | Status | Files |
|---|---|---|---|
| **Phase 1 (V3.0)** | Day58 production regressions | **Execute now** | 5 tasks |
| Phase 2 (V3.1) | Content Guard + Story Engine | Outlined | ~10 tasks |
| Phase 3 (V3.2) | Auto-Enrich (B-roll/beat/cards) | Outlined | ~12 tasks |
| Phase 4 (V3.3) | Cover + Multi-platform + Caption | Outlined | ~8 tasks |
| Phase 5 (V3.4) | Docs | Outlined | ~5 tasks |

---

## Phase 1 (V3.0) — Production Regressions

Five fixes that day58 surfaced. All have working code already living in `~/.codex/skills/video-editing/` (the codex local install) but never made it back to the repo.

### Task 1: Port MLX-whisper backend from codex local install

**Why:** Apple Silicon users (the author included) want mlx-whisper, not faster-whisper. The local codex install at `~/.codex/skills/video-editing/scripts/{transcribe,utils}.py` already has the implementation, validated in day58. The repo version is one generation behind.

**Files:**
- Modify: `scripts/transcribe.py` (add mlx-whisper engine branch + model-name mapping)
- Modify: `scripts/utils.py` (Apple Silicon auto-detect prefers mlx-whisper)
- Modify: `SKILL.md` (M1/M2/M3/M4 install instructions → `pip install mlx-whisper`)
- Test: `tests/test_transcribe_mlx.py` (verify engine arg parse + auto-detect)

**Step 1: Diff local vs repo for transcribe.py**

Run:
```bash
diff ~/.codex/skills/video-editing/scripts/transcribe.py scripts/transcribe.py | head -100
```
Goal: identify exactly which blocks to port.

**Step 2: Port transcribe.py changes**

Copy the relevant sections (MLX_MODEL_MAP, resolve_mlx_model, transcribe_mlx_whisper, `--engine` choices includes `mlx-whisper`, engine dispatch branch).

**Step 3: Port utils.py changes**

Auto-detect order: mlx-whisper (if darwin/arm64 + import succeeds) → faster-whisper → openai-whisper. Device map: mlx-whisper → `("mlx", "float16")`.

**Step 4: Update SKILL.md install section**

Replace the macOS Apple Silicon install line: `pip install mlx-whisper  # (replaces faster-whisper on M1/M2/M3/M4)`.

**Step 5: Write smoke test**

`tests/test_transcribe_mlx.py`:
```python
import subprocess, sys
def test_engine_choices_include_mlx():
    out = subprocess.run([sys.executable, "scripts/transcribe.py", "--help"],
                        capture_output=True, text=True)
    assert "mlx-whisper" in out.stdout
def test_utils_diagnostics_runs():
    out = subprocess.run([sys.executable, "scripts/utils.py"], capture_output=True, text=True)
    assert out.returncode == 0
```

**Step 6: Run tests & commit**

```bash
python3 -m pytest tests/test_transcribe_mlx.py -v
git add scripts/transcribe.py scripts/utils.py SKILL.md tests/test_transcribe_mlx.py
git commit -m "feat(transcribe): port mlx-whisper backend from codex local install"
```

---

### Task 2: Forbid pipeline-internal text on output frames

**Why:** Day58 leaked `1.25x` to the top of the rendered video. Jay's rule: never display speed/model/engine names. Need a hard guard in render so these can never be in `drawtext`, ASS overlays, or cover text.

**Files:**
- Create: `scripts/_internal_text_guard.py` (regex check + raise on violation)
- Modify: `scripts/render_final.py` (call guard before each drawtext/ASS write)
- Modify: `scripts/generate_cover.py` (call guard on cover title/subtitle)
- Test: `tests/test_internal_text_guard.py`

**Step 1: Write the failing test**

`tests/test_internal_text_guard.py`:
```python
import pytest
from scripts._internal_text_guard import check_visible_text, InternalTextLeak

def test_speed_label_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("加速 1.25x 播放")
def test_model_name_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("whisper-large-v3-turbo")
def test_normal_title_ok():
    check_visible_text("DAY 58 — AI 失业焦虑")
def test_engine_name_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("使用 mlx-whisper 转写")
```

**Step 2: Verify it fails**

```bash
python3 -m pytest tests/test_internal_text_guard.py -v
# Expected: ModuleNotFoundError
```

**Step 3: Implement the guard**

`scripts/_internal_text_guard.py`:
```python
import re

class InternalTextLeak(Exception):
    pass

_FORBIDDEN_PATTERNS = [
    r"\d+\.?\d*\s*[xX]\b",                    # 1.25x, 2X
    r"\b(mlx|faster|openai)[-_]?whisper\b",   # engine names
    r"\bwhisper[-_]?(large|medium|small|base|tiny|turbo)",  # model names
    r"\bDEBUG\b|\bTODO\b|\bFIXME\b",
    r"_temp\b|_tmp\b|\.tmp\b",
    r"\batempo\b|\bdynaudnorm\b|\bloudnorm\b",
]

def check_visible_text(text: str) -> None:
    if not isinstance(text, str):
        return
    for pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            raise InternalTextLeak(
                f"Refusing to burn pipeline-internal token into frame: "
                f"text={text!r} matched={pat!r}"
            )
```

**Step 4: Verify it passes**

```bash
python3 -m pytest tests/test_internal_text_guard.py -v
```

**Step 5: Wire guard into render_final.py**

Find every spot that writes title/subtitle/badge text into ASS or drawtext. Add `check_visible_text(text)` call before write.

Locations (approx):
- `build_merged_ass` segment text iteration
- `build_karaoke_ass` segment text iteration
- Cover title/subtitle assignment

**Step 6: Wire into generate_cover.py**

Same pattern — guard `args.title` and `args.subtitle`.

**Step 7: Commit**

```bash
git add scripts/_internal_text_guard.py scripts/render_final.py scripts/generate_cover.py tests/test_internal_text_guard.py
git commit -m "feat(guard): refuse pipeline-internal tokens on output frames"
```

---

### Task 3: Default subtitle font to a Heavy weight

**Why:** Day58 used `Hiragino Sans GB W3` by default — too thin for short video. Jay had to manually switch to STHeiti Medium / Source Han Sans Heavy. Make Heavy the default.

**Files:**
- Modify: `scripts/utils.py::find_chinese_font` (preference order)
- Modify: `SKILL.md` (mention bold default)
- Test: `tests/test_default_font.py`

**Step 1: Inspect current preference order**

```bash
grep -n "Hiragino\|STHeiti\|PingFang\|Source Han\|Smiley" scripts/utils.py
```

**Step 2: Reorder preferences**

The function should try in this order: `SourceHanSansSC-Heavy` → `Smiley Sans` → `STHeitiSC-Medium` → `PingFangSC-Semibold` → `Hiragino Sans GB W6` → existing fallbacks. Keep Hiragino W3 only as last resort.

**Step 3: Write test**

`tests/test_default_font.py`:
```python
import os, sys
sys.path.insert(0, "scripts")
from utils import find_chinese_font

def test_returns_a_font():
    path, name = find_chinese_font(None)
    assert path is not None or name is not None

def test_prefers_heavier_weight():
    # On a Mac with multiple fonts available, the chosen font name should
    # contain "Heavy", "Medium", "Semibold", or "Bold" — NOT plain "W3" or "Regular".
    path, name = find_chinese_font(None)
    if name:
        assert not name.endswith("W3"), f"Default fell back to a thin font: {name}"
```

**Step 4: Verify**

```bash
python3 -m pytest tests/test_default_font.py -v
```

**Step 5: Commit**

```bash
git add scripts/utils.py SKILL.md tests/test_default_font.py
git commit -m "feat(font): default to Heavy/Medium-weight CJK fonts"
```

---

### Task 4: Default audio loudness normalization in render

**Why:** Day58 mid-section was too quiet after 1.25x speed change. Jay had to add `dynaudnorm + acompressor + loudnorm` manually. Make this default for speech rendering.

**Files:**
- Modify: `scripts/render_final.py` (af_parts chain in main render path)
- Test: `tests/test_audio_chain.py` (parse generated ffmpeg command)

**Step 1: Identify the af_parts construction**

It's around line 665-680 of `scripts/render_final.py`. Currently only adds `atempo` and `adelay`.

**Step 2: Add CLI flag**

```python
parser.add_argument("--no-loudnorm", action="store_true",
                    help="Disable default dynaudnorm+compressor+loudnorm chain")
```

**Step 3: Inject the filters after atempo**

```python
if not args.no_loudnorm:
    af_parts.append("dynaudnorm=f=250:g=15")
    af_parts.append("acompressor=threshold=-18dB:ratio=3:attack=20:release=200")
    af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
```

Insert *after* atempo, *before* adelay (so the cover-period silence stays silent).

**Step 4: Write integration smoke test**

`tests/test_audio_chain.py`:
```python
import subprocess, sys
def test_help_mentions_no_loudnorm():
    out = subprocess.run([sys.executable, "scripts/render_final.py", "--help"],
                        capture_output=True, text=True)
    assert "--no-loudnorm" in out.stdout
```

(Full integration test would need a fixture audio file; smoke test is enough for now.)

**Step 5: Manual verification note**

Add a comment in render_final.py explaining the chain and pointing to docs/prompts/13-tips.md.

**Step 6: Commit**

```bash
git add scripts/render_final.py tests/test_audio_chain.py
git commit -m "feat(audio): default dynaudnorm+compressor+loudnorm for speech"
```

---

### Task 5: Promote `--speed` to a first-class primary-output flag

**Why:** Current `--speed 1.25 1.5` renders *additional variants* alongside a 1.0 base. Day58 wanted 1.25x to be *the* output. Add `--primary-speed 1.25` flag.

**Files:**
- Modify: `scripts/render_final.py` (parse arg, set `all_speeds` accordingly)
- Test: `tests/test_primary_speed.py`

**Step 1: Add CLI flag**

```python
parser.add_argument("--primary-speed", type=float, default=1.0,
                    help="Primary output speed (default 1.0). Renders only this speed unless --speed adds variants.")
```

**Step 2: Adjust all_speeds composition**

```python
primary = args.primary_speed
extras = [s for s in args.speed if s != primary]
all_speeds = [primary] + extras
```

**Step 3: Ensure subtitle/audio timelines scale together**

Audit the loop. The existing scaling code at line ~601 (`if speed == 1.0: ...`) might assume 1.0 is always the base. Make sure the primary scale propagates correctly to the ASS subtitle and concat lists.

**Step 4: Test**

`tests/test_primary_speed.py`:
```python
import subprocess, sys
def test_help_mentions_primary_speed():
    out = subprocess.run([sys.executable, "scripts/render_final.py", "--help"],
                        capture_output=True, text=True)
    assert "--primary-speed" in out.stdout
```

**Step 5: Commit**

```bash
git add scripts/render_final.py tests/test_primary_speed.py
git commit -m "feat(render): --primary-speed makes the speed-adjusted version the main output"
```

---

## Phase 2 (V3.1) — Content Guard + Story Engine

Outline only — to be expanded into TDD tasks at the start of Phase 2 execution.

- **Task 6** Create `scripts/content_guard.py` with HARD-BLOCK regexes (极限词 / 导流 / 医美 / 财富诱导 / 政治-色情)
- **Task 7** Add SOFT-WARN regexes (标题长度 / 标点连用 / emoji 占比 / 单镜头时长)
- **Task 8** Add zxing-cpp QR-code scan over sampled frames
- **Task 9** Add external-platform watermark OCR (抖音/快手/视频号/TikTok)
- **Task 10** Create `scripts/prompts/hook_templates.yaml` (8 hook templates)
- **Task 11** Create `scripts/prompts/cta_templates.yaml` (5 CTA templates)
- **Task 12** Create `scripts/rewrite_script.py` (LLM-driven 5-field JSON output: hook/pain/turn/value/cta)
- **Task 13** Create `scripts/profiles/tech_pro.yaml` + `lifestyle.yaml` + profile loader in utils
- **Task 14** Wire `--profile tech_pro` flag into render_final.py
- **Task 15** Add `--guard strict` flag that blocks export on hard violations

---

## Phase 3 (V3.2) — Auto-Enrich Pipeline

Outline only.

- **Task 16** `scripts/auto_broll.py` — language-driven B-roll scheduling (single-shot >5s → forced cut)
- **Task 17** Add NER-based B-roll matching (WhisperNER or spaCy + zh model)
- **Task 18** CLIP-embedding match between口播 entities and media_library
- **Task 19** `scripts/auto_chapter_cards.py` — 3 design templates, silence-based + LLM-based triggers
- **Task 20** `scripts/beat_sync.py` — librosa.beat_track + cut-point snap to nearest beat (±200ms)
- **Task 21** Audio FX library `assets/sfx/{whoosh,ding,pop,swoosh}.wav` + mixer
- **Task 22** `scripts/auto_stickers.py` — emotion→sticker mapping + overlay
- **Task 23** Auto-enrich orchestrator `scripts/auto_enrich.py` that calls 19→20→22 in order
- **Task 24** Integrate `minimax-image` CLI for abstract-concept B-roll generation
- **Task 25** Add film-grain + LUT pass for AI-generated images
- **Task 26** Unit tests for each enrichment trigger
- **Task 27** End-to-end fixture test on a 30-second sample

---

## Phase 4 (V3.3) — Cover, Multi-Platform, Caption

Outline only.

- **Task 28** `scripts/profiles/fonts.yaml` + `ensure_font()` auto-downloader (Smiley Sans, Source Han Sans, Alimama family)
- **Task 29** Three cover templates (`three_layer`, `tag_title`, `magazine`) in `generate_cover.py`
- **Task 30** `scripts/multi_export.py` — three platform presets (xhs 3:4 / douyin 9:16 / wxch 9:16 ≤60s)
- **Task 31** `scripts/generate_caption.py` — title (≤18 chars, TF-IDF keywords) + body + tags + publish-time
- **Task 32** Caption-vs-script CTA differentiation rule (caption末尾CTA ≠ video末尾CTA)
- **Task 33** Integrate caption generator with Content Guard (caption must pass lint)
- **Task 34** Hashtag suggestion via TF-IDF over the cleaned script
- **Task 35** Publish-time hint per audience profile (AI/创业 → 7:30/21:00)

---

## Phase 5 (V3.4) — Documentation

Outline only.

- **Task 36** `docs/prompts/15-xhs-daily-tech-video.md` — full daily-video prompt template using new V3 flags
- **Task 37** `docs/prompts/16-content-guard.md` — lint workflow + override semantics
- **Task 38** `docs/prompts/17-multi-platform.md` — three-platform export workflow
- **Task 39** `docs/prompts/18-auto-enrich.md` — when to enable B-roll/cards/beat-sync
- **Task 40** README — add "Xiaohongshu defaults" section with profile chart

---

## Execution Notes

- **Branch:** `v3-improvements` (already created)
- **Commit cadence:** one commit per task; each task has 4-7 steps and should complete in 5-15 minutes
- **Tests dir:** `tests/` (created fresh — no pytest config yet, plain `python3 -m pytest tests/` will work as long as files start with `test_`)
- **Backward compat:** every new flag defaults to opt-out so existing day-NN scripts keep working. Exceptions explicitly noted (Task 2 guard is mandatory; Task 3 font default change is intentional regression of bad behavior).
- **Sources:** the four research reports synthesized into 2026-05-17 conversation (small-red-book hooks, taboos, audience, enrichment).

---

## Done Definition (V3.0)

Phase 1 complete when:
- [ ] `python3 -m pytest tests/` green
- [ ] `python3 scripts/transcribe.py --help` mentions `mlx-whisper`
- [ ] `python3 scripts/render_final.py --help` mentions both `--primary-speed` and `--no-loudnorm`
- [ ] `python3 scripts/utils.py` prints diagnostics without crashing
- [ ] A trial render attempt with the string `"1.25x"` in cover title is *rejected* by the guard
- [ ] Default font on this Mac mini M1 reports Heavy/Medium weight, not W3
- [ ] 5 commits land on `v3-improvements`
- [ ] All changes pushed and merged to `main`
