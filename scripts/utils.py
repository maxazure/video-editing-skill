#!/usr/bin/env python3
"""
Shared utilities for the video-editing skill.

Provides platform detection, GPU/encoder detection, font finding,
mirror configuration, and dependency checking.
"""

import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform():
    """Return one of: 'macos', 'linux', 'wsl', 'windows'."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    if system == "Linux":
        # WSL detection
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    return "wsl"
        except (IOError, OSError):
            pass
        return "linux"
    return "linux"  # fallback


def is_apple_silicon():
    """Check if running on Apple Silicon (arm64 Mac)."""
    return detect_platform() == "macos" and platform.machine() == "arm64"


def is_china_locale():
    """Heuristic: detect if the user is likely in China."""
    import locale
    import time
    # Check timezone
    try:
        tz = time.tzname
        if any("CST" in t or "Asia/Shanghai" in t or "Asia/Chongqing" in t for t in tz):
            return True
    except Exception:
        pass
    # Check TZ env
    tz_env = os.environ.get("TZ", "")
    if "Asia/Shanghai" in tz_env or "Asia/Chongqing" in tz_env:
        return True
    # Check locale
    try:
        loc = locale.getlocale()[0] or ""
        if loc.startswith("zh_CN") or loc.startswith("zh_Hans"):
            return True
    except Exception:
        pass
    # Check LANG env
    lang = os.environ.get("LANG", "") + os.environ.get("LANGUAGE", "")
    if "zh_CN" in lang:
        return True
    return False


# ---------------------------------------------------------------------------
# GPU & encoder detection
# ---------------------------------------------------------------------------

def _run_quiet(cmd):
    """Run a command and return stdout, or empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def _detect_nvidia_gpu_name():
    """Query the GPU product name via nvidia-smi. Returns name string or ''."""
    out = _run_quiet([
        "nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"
    ])
    return out.strip().split("\n")[0].strip() if out.strip() else ""


def _detect_nvidia_arch_generation(gpu_name):
    """Detect NVIDIA architecture generation from GPU name.

    Returns one of: 'blackwell', 'ada', 'hopper', 'ampere', 'older', 'unknown'.
    """
    name = gpu_name.upper()
    # RTX 50xx = Blackwell (Compute Capability 12.0, sm_120)
    if any(x in name for x in ["RTX 50", "RTX50"]):
        return "blackwell"
    # RTX 40xx = Ada Lovelace (Compute Capability 8.9, sm_89)
    if any(x in name for x in ["RTX 40", "RTX40"]):
        return "ada"
    # RTX 30xx = Ampere (Compute Capability 8.6)
    if any(x in name for x in ["RTX 30", "RTX30", "RTX A", "A100", "A6000"]):
        return "ampere"
    # H100 / H200 = Hopper
    if any(x in name for x in ["H100", "H200"]):
        return "hopper"
    # RTX 20xx / GTX 16xx = Turing
    if any(x in name for x in ["RTX 20", "RTX20", "GTX 16", "GTX16"]):
        return "older"
    return "unknown"


def detect_gpu():
    """Detect available GPU type.

    Returns dict with keys:
        type: 'nvidia' | 'apple' | 'intel' | 'amd' | 'none'
        cuda: bool
        mps: bool  (Apple Metal Performance Shaders)
        qsv: bool  (Intel Quick Sync)
        amf: bool  (AMD AMF)
        videotoolbox: bool
        nvidia_gpu_name: str  (GPU product name, e.g. 'NVIDIA GeForce RTX 5060 Ti')
        nvidia_arch: str  ('blackwell' | 'ada' | 'ampere' | 'hopper' | 'older' | 'unknown')
    """
    info = {
        "type": "none",
        "cuda": False,
        "mps": False,
        "qsv": False,
        "amf": False,
        "videotoolbox": False,
        "nvidia_gpu_name": "",
        "nvidia_arch": "",
    }

    plat = detect_platform()

    # NVIDIA CUDA
    if _run_quiet(["nvidia-smi"]).strip():
        info["type"] = "nvidia"
        info["cuda"] = True
        gpu_name = _detect_nvidia_gpu_name()
        info["nvidia_gpu_name"] = gpu_name
        info["nvidia_arch"] = _detect_nvidia_arch_generation(gpu_name)
        return info

    # Apple Silicon MPS
    if plat == "macos" and is_apple_silicon():
        info["type"] = "apple"
        info["mps"] = True
        info["videotoolbox"] = True
        return info

    # macOS Intel — still has VideoToolbox
    if plat == "macos":
        info["type"] = "intel"
        info["videotoolbox"] = True
        return info

    # Check ffmpeg encoders for QSV / AMF
    enc_out = _run_quiet(["ffmpeg", "-hide_banner", "-encoders"])
    if "h264_qsv" in enc_out:
        info["type"] = "intel"
        info["qsv"] = True
    if "h264_amf" in enc_out:
        info["type"] = "amd"
        info["amf"] = True

    # Linux VAAPI
    if plat in ("linux", "wsl") and os.path.exists("/dev/dri/renderD128"):
        if info["type"] == "none":
            info["type"] = "intel"  # most likely

    return info


def get_ffmpeg_encoder(gpu_info=None):
    """Choose the best available H.264 encoder.

    Returns (encoder_name, extra_args_list).
    """
    if gpu_info is None:
        gpu_info = detect_gpu()

    # NVIDIA
    if gpu_info.get("cuda"):
        return "h264_nvenc", ["-preset", "p4", "-cq", "20"]

    # Apple VideoToolbox
    if gpu_info.get("videotoolbox"):
        return "h264_videotoolbox", ["-b:v", "12M"]

    # Intel QSV
    if gpu_info.get("qsv"):
        return "h264_qsv", ["-global_quality", "23"]

    # AMD AMF
    if gpu_info.get("amf"):
        return "h264_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"]

    # CPU fallback — preset medium balances speed and quality well
    return "libx264", ["-preset", "medium", "-crf", "18"]


def get_ffmpeg_encode_args(gpu_info=None):
    """Return full ffmpeg video encode arguments list: ['-c:v', encoder, ...extra]."""
    encoder, extra = get_ffmpeg_encoder(gpu_info)
    return ["-c:v", encoder] + extra


# ---------------------------------------------------------------------------
# Whisper engine detection
# ---------------------------------------------------------------------------

def detect_whisper_engine():
    """Detect the best available Whisper engine.

    Returns one of: 'faster-whisper', 'openai-whisper', 'none'.
    """
    # Try faster-whisper first
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        pass

    # Try openai whisper
    try:
        import whisper  # noqa: F401
        return "openai-whisper"
    except ImportError:
        pass

    return "none"


def recommend_whisper_model(gpu_info=None):
    """Recommend a Whisper model size based on available hardware.

    Returns (model_name, reason).
    """
    if gpu_info is None:
        gpu_info = detect_gpu()

    if gpu_info.get("cuda"):
        return "large-v3", "NVIDIA GPU detected, large model runs fast on CUDA"

    if gpu_info.get("mps"):
        # Apple Silicon — large-v3-turbo is ideal
        return "large-v3-turbo", "Apple Silicon detected, turbo model balances speed and quality"

    if gpu_info.get("qsv"):
        return "medium", "Intel iGPU detected, medium model is the best balance for CPU+iGPU"

    if gpu_info.get("amf"):
        return "medium", "AMD iGPU detected, medium model is the best balance for CPU+iGPU"

    # CPU only
    return "small", "No GPU detected, small model offers best speed/quality on CPU"


def get_whisper_device(gpu_info=None):
    """Return the device string for Whisper inference.

    Returns (device, compute_type) tuple.

    For NVIDIA Blackwell (RTX 50xx) GPUs, always uses float16 to avoid
    cuBLAS CUBLAS_STATUS_NOT_SUPPORTED errors with INT8 on sm_120.
    See: https://github.com/OpenNMT/CTranslate2/issues/1865
    """
    if gpu_info is None:
        gpu_info = detect_gpu()

    if gpu_info.get("cuda"):
        arch = gpu_info.get("nvidia_arch", "")
        if arch == "blackwell":
            # Blackwell (RTX 50xx, sm_120): INT8 Tensor Cores require
            # matrix dimensions padded to multiples of 16. CTranslate2 >= 4.7.1
            # has this fix, but float16 is the safest choice.
            print("[gpu] Blackwell GPU detected — using float16 (INT8 may cause cuBLAS errors)")
            return "cuda", "float16"
        return "cuda", "float16"

    if gpu_info.get("mps"):
        # faster-whisper doesn't support MPS yet; use CPU with int8
        engine = detect_whisper_engine()
        if engine == "openai-whisper":
            return "cpu", "float32"  # openai-whisper on Mac
        return "cpu", "int8"  # faster-whisper CPU

    # CPU (including Intel/AMD iGPU — Whisper can't use QSV/AMF)
    engine = detect_whisper_engine()
    if engine == "faster-whisper":
        return "cpu", "int8"
    return "cpu", "float32"


# ---------------------------------------------------------------------------
# Mirror / China support
# ---------------------------------------------------------------------------

PIP_MIRRORS = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple/",
}

HF_MIRROR = "https://hf-mirror.com"


def get_pip_mirror_args():
    """Return pip install args for China mirror, or empty list if not needed."""
    if is_china_locale() or os.environ.get("USE_CN_MIRROR"):
        return ["-i", PIP_MIRRORS["tsinghua"],
                "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]
    return []


def get_hf_endpoint():
    """Return HuggingFace endpoint — mirror for China, default otherwise."""
    if os.environ.get("HF_ENDPOINT"):
        return os.environ["HF_ENDPOINT"]
    if is_china_locale() or os.environ.get("USE_CN_MIRROR"):
        return HF_MIRROR
    return "https://huggingface.co"


def setup_china_env():
    """Set environment variables for China mirror if applicable."""
    if is_china_locale() or os.environ.get("USE_CN_MIRROR"):
        os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)
        print(f"[mirror] HuggingFace mirror: {HF_MIRROR}")
        return True
    return False


# ---------------------------------------------------------------------------
# Font finding (consolidated)
# ---------------------------------------------------------------------------

GOOGLE_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
GOOGLE_FONT_URL_CN = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
GOOGLE_FONT_FILENAME = "NotoSansSC[wght].ttf"

# ---------------------------------------------------------------------------
# Font catalog — popular free fonts for video production
# ---------------------------------------------------------------------------
# Each entry: (font_id, display_name, filename, urls, license, cjk, use_case)
# urls: list of download URLs (first working one wins), China CDN first if applicable
# cjk: True if the font supports CJK characters
# use_case: "title" | "body" | "caption" | "display" | "all"

FONT_CATALOG = {
    # ── Chinese fonts ──────────────────────────────────────────────────
    "noto-sans-sc": {
        "name": "Noto Sans SC",
        "filename": "NotoSansSC[wght].ttf",
        "urls": [GOOGLE_FONT_URL],
        "urls_cn": [GOOGLE_FONT_URL_CN],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "all",
        "description": "Google 思源黑体简体，万能中文字体，支持多字重",
    },
    "lxgw-wenkai": {
        "name": "LXGW WenKai",
        "filename": "LXGWWenKai-Regular.ttf",
        "urls": [
            "https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/lxgw/LxgwWenKai@v1.522/fonts/TTF/LXGWWenKai-Regular.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "all",
        "description": "霞鹜文楷，手写楷体风格，适合文艺/文化/情感类视频（~24MB）",
    },
    "lxgw-wenkai-lite": {
        "name": "LXGW WenKai Lite",
        "filename": "LXGWWenKaiLite-Regular.ttf",
        "urls": [
            "https://github.com/lxgw/LxgwWenKai-Lite/releases/download/v1.522/LXGWWenKaiLite-Regular.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/lxgw/LxgwWenKai-Lite@v1.522/fonts/TTF/LXGWWenKaiLite-Regular.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "all",
        "description": "霞鹜文楷轻便版，字符较少但体积更小（~13MB），适合嵌入式场景",
    },
    "lxgw-wenkai-bold": {
        "name": "LXGW WenKai",
        "filename": "LXGWWenKai-Medium.ttf",
        "urls": [
            "https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Medium.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/lxgw/LxgwWenKai@v1.522/fonts/TTF/LXGWWenKai-Medium.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "title",
        "description": "霞鹜文楷中粗体，适合标题（~24MB）",
    },
    "zcool-kuaile": {
        "name": "ZCOOL KuaiLe",
        "filename": "ZCOOLKuaiLe-Regular.ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "display",
        "description": "站酷快乐体，圆润可爱风格，适合轻松/娱乐类标题",
    },
    "zcool-qingke-huangyou": {
        "name": "ZCOOL QingKe HuangYou",
        "filename": "ZCOOLQingKeHuangYou-Regular.ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/zcoolqingkehuangyou/ZCOOLQingKeHuangYou-Regular.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/zcoolqingkehuangyou/ZCOOLQingKeHuangYou-Regular.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "display",
        "description": "站酷庆科黄油体，手写潮流风格，适合年轻/时尚类标题",
    },
    "zcool-xiaowei": {
        "name": "ZCOOL XiaoWei",
        "filename": "ZCOOLXiaoWei-Regular.ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "body",
        "description": "站酷小薇体，清秀端正风格，适合正文和字幕",
    },
    "noto-serif-sc": {
        "name": "Noto Serif SC",
        "filename": "NotoSerifSC[wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": True,
        "use_case": "all",
        "description": "Google 思源宋体简体，适合文化/古典/深度内容",
    },
    # ── English fonts ──────────────────────────────────────────────────
    "inter": {
        "name": "Inter",
        "filename": "Inter[opsz,wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": False,
        "use_case": "all",
        "description": "现代无衬线字体，专为屏幕设计，极佳可读性",
    },
    "montserrat": {
        "name": "Montserrat",
        "filename": "Montserrat[wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": False,
        "use_case": "title",
        "description": "几何无衬线字体，粗体适合视频标题",
    },
    "poppins": {
        "name": "Poppins",
        "filename": "Poppins-Bold.ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/poppins/Poppins-Bold.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": False,
        "use_case": "title",
        "description": "几何圆润无衬线字体，适合现代感标题",
    },
    "roboto": {
        "name": "Roboto",
        "filename": "Roboto[wdth,wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
        ],
        "license": "Apache 2.0",
        "cjk": False,
        "use_case": "all",
        "description": "Google 默认字体，中性现代风格",
    },
    "oswald": {
        "name": "Oswald",
        "filename": "Oswald[wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/oswald/Oswald%5Bwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/oswald/Oswald%5Bwght%5D.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": False,
        "use_case": "title",
        "description": "窄体无衬线字体，适合新闻/头条风格标题",
    },
    "playfair-display": {
        "name": "Playfair Display",
        "filename": "PlayfairDisplay[wght].ttf",
        "urls": [
            "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
        ],
        "urls_cn": [
            "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
        ],
        "license": "SIL OFL 1.1",
        "cjk": False,
        "use_case": "title",
        "description": "优雅衬线字体，适合文艺/高端/品牌类标题",
    },
}


def get_fonts_dir():
    """Return the fonts cache directory path."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), "fonts")


def list_available_fonts(cjk_only=False):
    """List all fonts in the catalog. Returns list of dicts."""
    fonts_dir = get_fonts_dir()
    result = []
    for font_id, info in FONT_CATALOG.items():
        if cjk_only and not info["cjk"]:
            continue
        cached = os.path.isfile(os.path.join(fonts_dir, info["filename"]))
        result.append({
            "id": font_id,
            "name": info["name"],
            "cached": cached,
            "cjk": info["cjk"],
            "use_case": info["use_case"],
            "description": info["description"],
            "license": info["license"],
        })
    return result


def download_font(font_id):
    """Download a font by its catalog ID. Returns (font_path, font_name) or (None, None)."""
    if font_id not in FONT_CATALOG:
        print(f"[font] Unknown font ID: {font_id}", file=sys.stderr)
        print(f"[font] Available fonts: {', '.join(FONT_CATALOG.keys())}")
        return None, None

    info = FONT_CATALOG[font_id]
    fonts_dir = get_fonts_dir()
    target_path = os.path.join(fonts_dir, info["filename"])

    # Return cached if exists
    if os.path.isfile(target_path):
        print(f"[font] Already cached: {info['name']} → {target_path}")
        return target_path, info["name"]

    # Build URL list (China CDN first if applicable)
    urls = []
    if is_china_locale() or os.environ.get("USE_CN_MIRROR"):
        urls.extend(info.get("urls_cn", []))
    urls.extend(info["urls"])

    return _download_font_from_urls(fonts_dir, target_path, info["name"], urls)


def _download_font_from_urls(fonts_dir, target_path, display_name, urls):
    """Download a font file from a list of URLs. Returns (path, name) or (None, None)."""
    for url in urls:
        try:
            os.makedirs(fonts_dir, exist_ok=True)
            print(f"[font] Downloading {display_name}...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(target_path, "wb") as f:
                    f.write(resp.read())
            size_mb = os.path.getsize(target_path) / (1024 * 1024)
            print(f"[font] Downloaded: {target_path} ({size_mb:.1f} MB)")
            return target_path, display_name
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            print(f"[font] Download failed from {url}: {e}")
            continue
    print(f"[font] ERROR: Could not download {display_name}", file=sys.stderr)
    return None, None


def find_chinese_font(custom_font_path=None):
    """Find a suitable Chinese font. Returns (font_path, font_name) or (None, fallback_name)."""
    if custom_font_path and os.path.isfile(custom_font_path):
        name = _guess_font_name(custom_font_path)
        return custom_font_path, name

    # Check cached fonts (try Noto Sans SC first, then others)
    fonts_dir = get_fonts_dir()
    for font_id in ["noto-sans-sc", "lxgw-wenkai", "lxgw-wenkai-lite",
                     "lxgw-wenkai-bold", "zcool-xiaowei", "noto-serif-sc"]:
        info = FONT_CATALOG[font_id]
        cached_path = os.path.join(fonts_dir, info["filename"])
        if os.path.isfile(cached_path):
            return cached_path, info["name"]

    # Try downloading Noto Sans SC (default)
    path, name = download_font("noto-sans-sc")
    if path:
        return path, name

    # System font fallback
    plat = detect_platform()
    return _find_system_font(plat)


def _find_system_font(plat):
    """Find system Chinese font by platform. Returns (path, name) or (None, fallback_name)."""
    candidates = []
    if plat == "macos":
        import glob
        candidates = [
            ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
            ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC"),
            ("/Library/Fonts/PingFang.ttc", "PingFang SC"),
        ]
        # AssetsV2
        for p in glob.glob("/System/Library/AssetsV2/**/PingFang.ttc", recursive=True):
            candidates.append((p, "PingFang SC"))
    elif plat == "windows":
        windir = os.environ.get("WINDIR", os.environ.get("SystemRoot", r"C:\Windows"))
        candidates = [
            (os.path.join(windir, "Fonts", "msyhbd.ttc"), "Microsoft YaHei"),
            (os.path.join(windir, "Fonts", "msyh.ttc"), "Microsoft YaHei"),
            (os.path.join(windir, "Fonts", "simhei.ttf"), "SimHei"),
        ]
    elif plat == "wsl":
        # WSL: check Linux fonts first, then Windows fonts via /mnt/c
        candidates = [
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/mnt/c/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
            ("/mnt/c/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
            ("/mnt/c/Windows/Fonts/simhei.ttf", "SimHei"),
        ]
    else:  # linux
        candidates = [
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
            ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "Droid Sans Fallback"),
        ]

    for path, name in candidates:
        if os.path.isfile(path):
            print(f"[font] Using system font: {path}")
            return path, name

    # fc-match fallback (Linux/WSL)
    if plat in ("linux", "wsl"):
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", ":lang=zh"],
                capture_output=True, text=True, check=True, timeout=5
            )
            fp = result.stdout.strip()
            if fp and os.path.isfile(fp):
                print(f"[font] Using fc-match font: {fp}")
                return fp, _guess_font_name(fp)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    # Fallback name (no file found)
    fallback = {
        "macos": "PingFang SC",
        "windows": "Microsoft YaHei",
        "wsl": "Microsoft YaHei",
        "linux": "Noto Sans CJK SC",
    }
    print("[font] WARNING: No Chinese font file found. Subtitles may show as boxes.", file=sys.stderr)
    return None, fallback.get(plat, "Noto Sans CJK SC")


def _guess_font_name(font_path):
    """Guess ASS font name from file path."""
    base = os.path.basename(font_path).lower()
    if "smiley" in base or "得意" in base:
        return "Smiley Sans"
    if "pingfang" in base:
        return "PingFang SC"
    if "notosanssc" in base or "notosanscjk" in base:
        return "Noto Sans SC"
    if "notoserifsc" in base:
        return "Noto Serif SC"
    if "msyh" in base:
        return "Microsoft YaHei"
    if "simhei" in base:
        return "SimHei"
    if "heiti" in base:
        return "Heiti SC"
    if "wenkailite" in base:
        return "LXGW WenKai Lite"
    if "lxgw" in base or "wenkai" in base:
        return "LXGW WenKai"
    if "zcoolkuaile" in base:
        return "ZCOOL KuaiLe"
    if "zcoolqingke" in base:
        return "ZCOOL QingKe HuangYou"
    if "zcoolxiaowei" in base:
        return "ZCOOL XiaoWei"
    if "inter" in base:
        return "Inter"
    if "montserrat" in base:
        return "Montserrat"
    if "poppins" in base:
        return "Poppins"
    if "roboto" in base:
        return "Roboto"
    if "oswald" in base:
        return "Oswald"
    if "playfair" in base:
        return "Playfair Display"
    return "Noto Sans SC"


# ---------------------------------------------------------------------------
# FFmpeg feature validation
# ---------------------------------------------------------------------------

def check_ffmpeg():
    """Check ffmpeg availability and required features.

    Returns dict with 'available', 'version', 'has_libass', 'has_drawtext'.
    """
    info = {"available": False, "version": "", "has_libass": False, "has_drawtext": False}
    # Version
    out = _run_quiet(["ffmpeg", "-version"])
    if not out:
        return info
    info["available"] = True
    m = re.search(r"ffmpeg version (\S+)", out)
    if m:
        info["version"] = m.group(1)
    # Build config
    cfg = _run_quiet(["ffmpeg", "-hide_banner", "-buildconf"])
    info["has_libass"] = "--enable-libass" in cfg
    info["has_drawtext"] = "--enable-libfreetype" in cfg
    return info


def check_dependencies():
    """Check all required dependencies. Returns list of (name, status, message)."""
    results = []
    # ffmpeg
    ff = check_ffmpeg()
    if ff["available"]:
        results.append(("ffmpeg", "ok", f"v{ff['version']}"))
        if not ff["has_libass"]:
            results.append(("ffmpeg-libass", "warn", "libass not found, subtitle burning may fail"))
        if not ff["has_drawtext"]:
            results.append(("ffmpeg-drawtext", "warn", "libfreetype not found, cover text may fail"))
    else:
        results.append(("ffmpeg", "missing", "Install: brew install ffmpeg (macOS) / apt install ffmpeg (Linux)"))

    # ffprobe
    if _run_quiet(["ffprobe", "-version"]):
        results.append(("ffprobe", "ok", ""))
    else:
        results.append(("ffprobe", "missing", "Usually bundled with ffmpeg"))

    # Python whisper
    engine = detect_whisper_engine()
    if engine == "faster-whisper":
        results.append(("whisper", "ok", "faster-whisper (recommended)"))
    elif engine == "openai-whisper":
        results.append(("whisper", "ok", "openai-whisper (consider upgrading to faster-whisper)"))
    else:
        results.append(("whisper", "missing", "Install: pip install faster-whisper (recommended) or pip install openai-whisper"))

    # GPU
    gpu = detect_gpu()
    encoder, _ = get_ffmpeg_encoder(gpu)
    results.append(("gpu", "info", f"type={gpu['type']}, encoder={encoder}"))

    return results


def print_env_report():
    """Print a diagnostic environment report."""
    plat = detect_platform()
    gpu = detect_gpu()
    engine = detect_whisper_engine()
    model, reason = recommend_whisper_model(gpu)
    encoder, _ = get_ffmpeg_encoder(gpu)

    print("=" * 50)
    print("Video Editing Skill — Environment Report")
    print("=" * 50)
    print(f"Platform      : {plat}" + (" (Apple Silicon)" if is_apple_silicon() else ""))
    print(f"China locale  : {is_china_locale()}")
    print(f"GPU type      : {gpu['type']}")
    if gpu.get("nvidia_gpu_name"):
        print(f"GPU name      : {gpu['nvidia_gpu_name']}")
        print(f"GPU arch      : {gpu.get('nvidia_arch', 'unknown')}")
    print(f"FFmpeg encoder: {encoder}")
    print(f"Whisper engine: {engine or 'not installed'}")
    if engine == "faster-whisper":
        device, compute = get_whisper_device(gpu)
        print(f"Whisper device: {device} (compute_type={compute})")
    print(f"Recommended model: {model} ({reason})")
    print()

    deps = check_dependencies()
    for name, status, msg in deps:
        icon = {"ok": "+", "warn": "!", "missing": "X", "info": "i"}[status]
        print(f"  [{icon}] {name}: {msg}")

    # Font status
    print()
    print("Cached fonts:")
    fonts = list_available_fonts()
    cached = [f for f in fonts if f["cached"]]
    if cached:
        for f in cached:
            tag = "CJK" if f["cjk"] else "EN"
            print(f"  [+] {f['name']} ({tag}, {f['use_case']})")
    else:
        print("  (none — run download_font('noto-sans-sc') to cache)")
    print(f"  Total available in catalog: {len(fonts)} fonts")
    print("=" * 50)


# ---------------------------------------------------------------------------
# Path utilities (cross-platform)
# ---------------------------------------------------------------------------

def get_video_info(video_path):
    """Get video duration, width, height, fps, and rotation.

    Detects rotation metadata (e.g. iPhone vertical video recorded as
    1920x1080 with rotation=-90) and swaps width/height accordingly so
    callers always get the *display* dimensions.

    Returns (duration, width, height, fps, rotation).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-show_entries", "stream_side_data=rotation",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.check_output(cmd, text=True)
        info = json.loads(result)
    except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
        return 5.0, 1920, 1080, 30.0, 0

    fmt_duration = float(info.get("format", {}).get("duration", 0))
    streams = info.get("streams", [{}])
    s = streams[0] if streams else {}

    w = s.get("width", 1920)
    h = s.get("height", 1080)

    # Parse frame rate like "30/1" or "29.97"
    r_str = s.get("r_frame_rate", "30/1")
    if "/" in r_str:
        num, den = r_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(r_str)

    stream_duration = float(s.get("duration", 0))
    duration = stream_duration if stream_duration > 0 else fmt_duration

    # Detect rotation from side_data or via separate ffprobe call
    rotation = 0
    side_data = s.get("side_data_list", [])
    for sd in side_data:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
            break

    if rotation == 0:
        # Fallback: check via stream tags or displaymatrix
        rot_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream_tags=rotate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            rot_out = subprocess.check_output(rot_cmd, text=True).strip()
            if rot_out:
                rotation = int(float(rot_out))
        except (subprocess.CalledProcessError, ValueError):
            pass

    # Swap width/height for 90/270 degree rotations
    if abs(rotation) in (90, 270):
        w, h = h, w

    return duration, w, h, fps, rotation


def escape_ffmpeg_path(path):
    """Escape a file path for use inside ffmpeg filter strings."""
    # FFmpeg filter syntax uses : and \ as special chars
    path = path.replace("\\", "/")  # normalize to forward slashes
    path = path.replace(":", "\\:")
    path = path.replace("'", "'\\''")
    return path


def sanitize_title(text):
    """Remove special characters that may cause rendering issues."""
    text = re.sub(r'[{}\\\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = re.sub(r'[<>|@#$%^&*~`]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ---------------------------------------------------------------------------
# Main (diagnostic)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print_env_report()
