"""Append-only, size-bounded audit log — the shared trail ingestion and actions write to."""
import os

from agent_access.audit import AuditLog


def test_append_writes_timestamped_entries(tmp_path):
    log = AuditLog(tmp_path / "audit.log", clock=lambda: 100.0)

    log.append("message_received", chat_id=42)
    log.append("answered", chat_id=42, latency=0.5)

    entries = log.entries()
    assert [e["event"] for e in entries] == ["message_received", "answered"]
    assert entries[0]["ts"] == 100.0
    assert entries[0]["chat_id"] == 42
    assert entries[1]["latency"] == 0.5


def test_rotates_when_exceeding_max_bytes(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path, clock=lambda: 1.0, max_bytes=200)

    for i in range(100):
        log.append("event", i=i, filler="x" * 20)

    assert os.path.getsize(path) <= 200
    assert (tmp_path / "audit.log.1").exists()
