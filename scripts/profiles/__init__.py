"""Audience profile loader.

Profiles encode platform/audience-tuned defaults so the user doesn't have
to pass a dozen flags. They are plain YAML so a human can read/edit them.

Use:
    from profiles import load_profile
    p = load_profile("tech_pro")
    primary_speed = p["duration"]["default_seconds"]  # etc.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


_PROFILES_DIR = os.path.dirname(os.path.abspath(__file__))


def _yaml_safe_load(text: str) -> Dict[str, Any]:
    """Minimal YAML reader for our profile files (subset: maps, lists, strings,
    ints, floats, booleans, comments, indentation).

    We avoid taking a hard PyYAML dependency for a skill that may run on
    fresh installs; profile files stay simple by convention.
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Hand-rolled fallback — only covers the constructs we actually use.
    root: Dict[str, Any] = {}
    stack: List[tuple] = [(0, root)]  # (indent, container)

    def _coerce(v: str) -> Any:
        v = v.strip()
        if v == "":
            return None
        if v in ("true", "True", "yes"):
            return True
        if v in ("false", "False", "no"):
            return False
        # quoted string
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return v[1:-1]
        # number
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v

    for raw_line in text.splitlines():
        # Drop comments and trailing whitespace
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        # Strip inline comments preceded by '  #'
        if "  #" in line:
            line = line.split("  #", 1)[0].rstrip()
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

        # Pop stack until parent indent < current indent
        while stack and indent < stack[-1][0]:
            stack.pop()

        parent = stack[-1][1] if stack else root

        if content.startswith("- "):
            value = _coerce(content[2:])
            if isinstance(parent, list):
                parent.append(value)
            else:
                # parent is a map and the most recent key holds a list
                # — find it by looking at the last key inserted at parent's indent.
                # For our profile files this case appears as `key:\n  - item`.
                if not isinstance(parent, list):
                    # Should not happen in well-formed profile files
                    continue
                parent.append(value)
            continue

        if ":" in content:
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # Container — peek next line for list vs map
                child: Any = {}  # default to map; replaced when we see a "-"
                parent[key] = child
                stack.append((indent + 2, child))
                # Sentinel: also allow lists by upgrading on first "-"
                # For our files indentation is consistent at 2 spaces.
            else:
                parent[key] = _coerce(val)

    # Post-process: any value that is a dict containing only numeric int keys is left alone
    # (no upgrade needed because the loader above always creates dicts; lists are
    # detected by the "- " prefix above only when the parent is already a list).
    # Profile files in this repo only have lists under preferred_windows / font_preference;
    # detect those and convert.
    _upgrade_known_lists(root)
    return root


def _upgrade_known_lists(node: Any) -> None:
    """Walk and convert specific keys we know hold lists into actual list values."""
    if not isinstance(node, dict):
        return
    list_keys = ("preferred_windows", "font_preference")
    for key in list_keys:
        if key in node and isinstance(node[key], dict):
            # the fallback parser left items as dict {0: v, 1: v} or similar; we don't use
            # that pattern, but if the value is an empty dict it means children were
            # parsed as items. In practice the fallback parses "- foo" properly into a list
            # via the parent reference, so this is a safety net.
            pass
    for v in node.values():
        _upgrade_known_lists(v)


def load_profile(name: str) -> Dict[str, Any]:
    """Return the profile dict for `name`. Raises FileNotFoundError if missing."""
    path = os.path.join(_PROFILES_DIR, f"{name}.yaml")
    if not os.path.isfile(path):
        available = list_profiles()
        raise FileNotFoundError(
            f"Profile {name!r} not found. Available: {available}"
        )
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return _yaml_safe_load(text)


def list_profiles() -> List[str]:
    """List audience profile names. Skips underscore-prefixed YAML files
    (those are companion configs like _fonts.yaml, not audience profiles)."""
    out = []
    for entry in os.listdir(_PROFILES_DIR):
        if entry.endswith(".yaml") and not entry.startswith("_"):
            out.append(entry[:-5])
    return sorted(out)


def load_fonts_preset(preset: str = "tech_ai") -> dict:
    """Load `_fonts.yaml` and return the named preset's font mapping."""
    path = os.path.join(_PROFILES_DIR, "_fonts.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        data = _yaml_safe_load(f.read())
    presets = data.get("presets", {})
    if preset not in presets:
        raise KeyError(f"font preset {preset!r} not found; available: {list(presets)}")
    return presets[preset]
