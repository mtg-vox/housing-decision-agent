"""Atomic JSON storage for candidates, plus the append-only ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path

from .models import ValidationError, validate_version

_LOCK = threading.RLock()
_HELD = threading.local()


def transactional(method):
    """Serialize the entire adapter read/check/modify/write and ledger event."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        store = self if isinstance(self, Store) else self.store
        with store.transaction():
            return method(self, *args, **kwargs)
    return wrapped

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")


class ConflictError(RuntimeError):
    """Raised when a write is based on a stale version."""


class ReadOnlyProfileError(PermissionError):
    """Raised when writing to the bundled example profile."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    return "-".join(p for p in cleaned.split("-") if p)[:72] or "candidate"


def valid_id(candidate_id: str) -> str:
    if not isinstance(candidate_id, str) or not _ID_RE.match(candidate_id):
        raise ValidationError([f"invalid candidate id '{candidate_id}'"])
    return candidate_id


def content_hash(payload) -> str | None:
    if payload is None:
        return None
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, root: Path, read_only: bool = False):
        self.root = Path(root)
        self.read_only = read_only
        self.candidates_dir = self.root / "candidates"
        self.ledger_path = self.root / "ledger.jsonl"

    @contextmanager
    def transaction(self):
        # Never lock a replaced data-file inode: use one stable sidecar per root.
        # RLock handles threads; the OS lock handles independent CLI/server processes.
        with _LOCK:
            key = str(self.root.resolve())
            held = getattr(_HELD, "roots", None)
            if held is None:
                held = _HELD.roots = set()
            if key in held or self.read_only:
                yield
                return
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".housing.lock").open("a+b") as lock:
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0, os.SEEK_END)
                    if lock.tell() == 0:
                        lock.write(b"\0")
                        lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                held.add(key)
                try:
                    yield
                finally:
                    held.remove(key)
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # --- candidates -------------------------------------------------------
    def _path(self, candidate_id: str) -> Path:
        return self.candidates_dir / f"{valid_id(candidate_id)}.json"

    @transactional
    def list(self) -> list[dict]:
        if not self.candidates_dir.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.candidates_dir.glob("*.json"))]

    @transactional
    def get(self, candidate_id: str) -> dict | None:
        path = self._path(candidate_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    @transactional
    def put(self, candidate: dict, expected_version: int | None = None) -> dict:
        self._guard()
        validate_version(expected_version)
        with _LOCK:
            current = self.get(candidate["id"])
            current_version = int(current.get("version", 0)) if current else 0
            if expected_version is not None and expected_version != current_version:
                raise ConflictError(
                    f"{candidate['id']}: expected version {expected_version}, found {current_version}"
                )
            saved = dict(candidate)
            saved["version"] = current_version + 1
            atomic_write_json(self._path(saved["id"]), saved)
            return saved

    @transactional
    def delete(self, candidate_id: str) -> bool:
        self._guard()
        with _LOCK:
            path = self._path(candidate_id)
            if not path.is_file():
                return False
            path.unlink()
            return True

    # --- ledger -------------------------------------------------------------
    @transactional
    def append_event(self, actor: str, action: str, target: str, reason: str = "",
                     before=None, after=None, extra: dict | None = None) -> dict:
        self._guard()
        event = {
            "ts": utc_now(), "actor": actor, "action": action, "target": target, "reason": reason,
            "before_hash": content_hash(before), "after_hash": content_hash(after),
        }
        if extra:
            event.update(extra)
        with _LOCK:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    @transactional
    def events(self) -> list[dict]:
        if not self.ledger_path.is_file():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- profile ------------------------------------------------------------
    @transactional
    def write_profile(self, profile: dict) -> None:
        self._guard()
        with _LOCK:
            atomic_write_json(self.root / "profile.json", profile)

    def _guard(self) -> None:
        if self.read_only:
            raise ReadOnlyProfileError(
                "The example profile is read-only. Create ./profile.local or set HOUSING_PROFILE_DIR."
            )
