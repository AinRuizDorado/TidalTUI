from __future__ import annotations

import glob
import json
import logging
import os
import shutil

from textual.theme import Theme

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_matugen_available() -> bool:
    return shutil.which("matugen") is not None


# ---------------------------------------------------------------------------
# Palette sources (try them in priority order)
# ---------------------------------------------------------------------------

def _color_dirs() -> list[str]:
    """Directories that may contain matugen-generated JSON palettes."""
    dirs: list[str] = []
    home = os.path.expanduser("~")
    xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache"))
    xdg_state = os.environ.get("XDG_STATE_HOME", os.path.join(home, ".local", "state"))
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))

    # quickshell (most common matugen consumer on Arch/Hyprland)
    dirs.append(os.path.join(xdg_state, "quickshell", "generated"))

    # cache images
    dirs.append(os.path.join(xdg_cache, "matugen", "images"))

    # explicit colour files
    dirs.append(os.path.join(xdg_config, "matugen"))

    return dirs


def _find_matugen_json() -> str | None:
    """Return the newest JSON file that looks like a matugen colour palette.

    Checks in standard matugen output directories and also respects the
    optional ``matugen_colors_file`` key in low-tide's ``config.json``.
    """
    candidates: list[str] = []

    for d in _color_dirs():
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(d, fname)
            if not os.path.isfile(path):
                continue
            candidates.append(path)

    # user-specified path from low-tide config
    try:
        import lowtide.tidal_client as tc
        cfg_path = os.path.join(tc.CONF_DIR, "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path) as fh:
                cfg = json.load(fh)
            explicit = cfg.get("matugen_colors_file")
            if explicit:
                expanded = os.path.expanduser(explicit)
                if os.path.isfile(expanded):
                    candidates.append(expanded)
    except Exception:
        pass

    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# Colour extraction helpers
# ---------------------------------------------------------------------------

def _hex_val(obj: object) -> str | None:
    """Return a hex colour string from any common matugen representation."""
    if isinstance(obj, str) and obj.startswith("#"):
        return obj
    if isinstance(obj, dict):
        return obj.get("hex") or obj.get("color")
    return None


def _extract_colors(raw: dict, *, dark: bool) -> dict[str, str] | None:
    """Extract a flat ``{name: hex}`` map from a matugen colour dictionary.

    Handles both the raw ``matugen --json`` output::

        {"colors": {"primary": {"dark": {"color": "#..."}, ...}, ...}}

    and the flattened quickshell-style template output::

        {"md3": {"primary": "#...", ...}}
    """
    # raw matugen JSON  (``matugen … --json hex``)
    section = raw.get("colors")
    if isinstance(section, dict):
        result: dict[str, str] = {}
        mode = "dark" if dark else "light"
        for name, entry in section.items():
            if not isinstance(entry, dict):
                continue
            pick = entry.get(mode) or entry.get("default")
            val = _hex_val(pick)
            if val is not None:
                result[name] = val
        if result:
            return result

    # quickshell / flat template format
    for key in ("md3", "colors", "palette"):
        section = raw.get(key)
        if isinstance(section, dict):
            result = {}
            for name, val in section.items():
                hex_str = _hex_val(val)
                if hex_str is not None:
                    result[name] = hex_str
            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# Theme builder
# ---------------------------------------------------------------------------

_MAPPING: dict[str, str | None] = {
    "primary":                  "primary",
    "secondary":                "secondary",
    "tertiary":                 "accent",
    "error":                    "error",
    "background":               "background",
    "surface":                  "surface",
    "surface_container":        "panel",
    "surface_container_high":   "boost",
    "on_surface":               "foreground",
}


def build_matugen_theme(flat: dict[str, str], *, dark: bool = True) -> Theme:
    """Build a Textual ``Theme`` from a flat ``{name: "#hex"}`` dict."""
    kwargs: dict = {"name": "matugen", "dark": dark}
    for matugen_key, theme_attr in _MAPPING.items():
        if theme_attr is None:
            continue
        val = flat.get(matugen_key)
        if val is not None:
            kwargs[theme_attr] = val
    return Theme(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _dark_mode() -> bool:
    return os.environ.get("LOWTIDE_MATUGEN_LIGHT", "").lower() not in (
        "1", "true", "yes",
    )


def load_theme_from_file(path: str, *, dark: bool | None = None) -> Theme | None:
    """Build a Textual ``Theme`` from a specific matugen JSON file.

    Args:
        path: Absolute path to the JSON palette file.
        dark: If ``None`` (default), the ``LOWTIDE_MATUGEN_LIGHT`` env var
            is respected; otherwise force dark or light.

    Returns:
        A ``Theme`` instance or ``None`` on failure (logs at debug level).
    """
    if dark is None:
        dark = _dark_mode()

    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception:
        log.debug("Failed to read %s", path, exc_info=True)
        return None

    flat = _extract_colors(raw, dark=dark)
    if flat is None:
        log.debug("Unrecognised JSON format in %s", path)
        return None

    try:
        return build_matugen_theme(flat, dark=dark)
    except Exception:
        log.debug("Failed to build matugen theme from %s", path, exc_info=True)
        return None


def try_load_matugen_theme() -> Theme | None:
    """Return a Textual ``Theme`` derived from matugen colours, or ``None``.

    * Scans common matugen output directories for a generated JSON palette.
    * Supports both raw ``matugen --json`` output and the flattened format
      produced by quickshell-style templates.
    * Falls back gracefully when no palette is found (the original Textual
      theme is kept).

    Logs at debug level on failure.
    """
    if not _is_matugen_available():
        log.debug("matugen not found in PATH – skipping theme integration")
        return None

    path = _find_matugen_json()
    if path is None:
        log.debug("no matugen JSON palette found")
        return None

    return load_theme_from_file(path)
