"""Append-only, size-bounded audit log. One JSON object per line. The shared
trail that turn events, ingestion, and the action-gate all write to."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List


class AuditLog:
    def __init__(self, path, clock: Callable[[], float] = time.time, max_bytes: int = 5_000_000):
        self.path = Path(path)
        self.clock = clock
        self.max_bytes = max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **details) -> None:
        self._rotate_if_needed()
        record = {"ts": self.clock(), "event": event, **details}
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def entries(self) -> List[Dict]:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _rotate_if_needed(self) -> None:
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            backup = self.path.with_suffix(self.path.suffix + ".1")
            os.replace(self.path, backup)
