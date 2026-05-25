from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Re-use the same config directory as the rest of the app
from lowtide.tidal_client import CONF_DIR

_VOLUME_PATH = os.path.join(CONF_DIR, "volume.json")


def load_volume(default: int = 80) -> int:
    """Load the last persisted volume level (0–100). Returns *default* if none saved."""
    try:
        with open(_VOLUME_PATH) as f:
            data = json.load(f)
        vol = int(data.get("volume", default))
        return max(0, min(100, vol))
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning("volume store load failed: %s", e)
        return default


def save_volume(vol: int) -> None:
    """Persist the current volume level (0–100)."""
    vol = max(0, min(100, int(vol)))
    try:
        os.makedirs(CONF_DIR, exist_ok=True)
        with open(_VOLUME_PATH, "w") as f:
            json.dump({"volume": vol}, f)
    except Exception as e:
        log.warning("volume store save failed: %s", e)
