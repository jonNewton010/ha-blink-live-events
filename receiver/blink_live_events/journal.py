"""Bound the private event queue while preserving pending deliveries."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from .state import ensure_private_dir

MAX_BYTES = 8 * 1024 * 1024
TARGET_BYTES = 4 * 1024 * 1024
_LOCK = threading.RLock()


def delivery_key(record: dict[str, Any]) -> str:
    fcm = record.get("fcm") or {}
    payload = record.get("payload") or {}
    identity = (
        fcm.get("persistent_id")
        or fcm.get("id")
        or payload.get("unique_id")
        or record.get("received_at")
        or json.dumps(record, sort_keys=True, separators=(",", ":"))
    )
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()


def load_records(path: Path) -> list[dict[str, Any]]:
    with _LOCK:
        if not path.exists():
            return []
        records = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records


def _encode(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _compact(records: list[dict[str, Any]]) -> bytes:
    delivered = {
        record.get("delivery_key")
        for record in records
        if record.get("kind") == "ha_webhook_sent"
    }
    initialized = False
    for record in records:
        if record.get("kind") == "ha_webhook_initialized":
            initialized = True
            delivered.update(record.get("existing_delivery_keys", []))

    # Remove each delivered message together with its checkpoint. A marker
    # preserves initialization so retained pending events survive a restart.
    marker = _encode({"kind": "ha_webhook_initialized", "existing_delivery_keys": []}) if initialized else b""
    pending = [
        record for record in records
        if record.get("kind") == "fcm_message" and delivery_key(record) not in delivered
    ]
    kept = []
    used = len(marker)
    for record in reversed(pending):
        data = _encode(record)
        if used + len(data) <= TARGET_BYTES:
            kept.append(data)
            used += len(data)
    dropped = len(pending) - len(kept)
    if dropped:
        print(f"Blink journal capacity reached; discarded {dropped} oldest/oversized pending event(s)", flush=True)
    return marker + b"".join(reversed(kept))


def append_private(path: Path, record: dict[str, Any]) -> None:
    with _LOCK:
        ensure_private_dir(path.parent)
        data = _encode(record)
        size = path.stat().st_size if path.exists() else 0
        if size + len(data) > MAX_BYTES:
            data = _compact(load_records(path) + [record])
            fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "ab", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
