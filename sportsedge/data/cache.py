"""Tiny on-disk JSON cache so repeated scans don't re-download box scores."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: str | os.PathLike | None = None):
        root = root or os.environ.get("SPORTSEDGE_CACHE") or Path.home() / ".cache" / "sportsedge"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")

    def get(self, key: str, ttl: float | None) -> Any | None:
        """Return the cached value, or None if missing / older than ttl seconds."""
        path = self._path(key)
        if not path.exists():
            return None
        if ttl is not None and time.time() - path.stat().st_mtime > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(path)
