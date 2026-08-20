"""Storage backends for TokenLedger with formal ABC/protocol."""

import abc
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)


class StorageBackend(abc.ABC):
    """Abstract storage backend that all stores must implement."""

    running_totals: dict[str, dict[str, Any]]
    budgets: dict[str, dict[str, Any]]

    @abc.abstractmethod
    def insert_record(self, record: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def get_records(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def get_running_totals(self, scope: str, scope_id: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def set_budget(self, scope: str, scope_id: str, budget_config: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def get_budget(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]: ...

    @abc.abstractmethod
    def get_all_budgets(self) -> dict[str, dict[str, Any]]: ...

    @abc.abstractmethod
    def clear(self) -> None: ...

    @abc.abstractmethod
    def compact(self, max_age_days: Optional[int] = None) -> dict[str, Any]: ...

    @abc.abstractmethod
    def get_record_count(self) -> int: ...

    @abc.abstractmethod
    def verify_immutability(self) -> list[str]: ...

    def get_windowed_spend(self, budget: dict[str, Any], window_start: str) -> Optional[float]:
        """Efficient windowed spend query. Return None to fall back to full scan.

        Backends that can push filtering into storage (e.g. SQLite) should
        override this to avoid O(n) scans.
        """
        return None

    def apply_retention(self, max_age_days: Optional[int] = None) -> None:
        """Prune records outside the retention window. Backends may override."""
        self.compact(max_age_days=max_age_days)


def _is_billable(record: dict[str, Any]) -> bool:
    """Ghost-mode and blocked/error records must not count toward spend."""
    if record.get("_ghost"):
        return False
    return record.get("status") not in ("blocked", "error")


_RETENTION_CHECK_INTERVAL_S = 60.0


class RetentionPolicy:
    def __init__(self, max_age_days: int = 90, max_records: int = 100_000, archive_on_trim: bool = True):
        self.max_age_days = max_age_days
        self.max_records = max_records
        self.archive_on_trim = archive_on_trim


def _checksum(record: dict[str, Any]) -> str:
    # Records are built in a deterministic key order, so sort_keys is
    # unnecessary — one dumps + hash pass is ~2x faster per record.
    raw = json.dumps(record, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _checksum_sorted(record: dict[str, Any]) -> str:
    """Legacy sort-keys checksum used by files written before v1.5.1."""
    raw = json.dumps(record, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


_MAGIC = b"TLDGR1\0"
_MAGIC_LEN = len(_MAGIC)
_MAC_LEN = 32  # sha256

_FERNET_CLS: Any = None  # lazy probe: None = unchecked, False = cryptography unavailable


def _fernet(key: bytes) -> Optional[Any]:
    """Return a Fernet (AES-128-CBC + HMAC) cipher, or None if `cryptography` is missing.

    Imported lazily to keep process startup fast and the core dependency-free.
    """
    global _FERNET_CLS
    if _FERNET_CLS is None:
        try:
            from cryptography.fernet import Fernet

            _FERNET_CLS = Fernet
        except ImportError:  # pragma: no cover
            _FERNET_CLS = False
    if _FERNET_CLS:
        return _FERNET_CLS(base64.urlsafe_b64encode(key))
    return None


def _normalize_key(key: "str | bytes") -> bytes:
    """Normalize a str or bytes encryption key to a fixed 32-byte SHA-256 digest."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hashlib.sha256(key).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, key * (len(data) // len(key) + 1)))


def _encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt bytes with Fernet (AES-128-CBC + HMAC) when `cryptography` is installed.

    Falls back to XOR + HMAC-SHA256 obfuscation when it is not — useful for
    casual privacy only, never for secrets or compliance-grade at-rest data.
    """
    cipher = _fernet(key)
    if cipher is not None:
        return cast(bytes, cipher.encrypt(data))
    blob = _xor(data, key)
    mac = hmac.new(key, blob, hashlib.sha256).digest()
    return _MAGIC + mac + blob


def _decrypt(raw: bytes, key: bytes) -> Optional[bytes]:
    """Reverse :func:`_encrypt`; returns None on wrong key or tampered data.

    Fernet tokens are tried first; legacy XOR blobs (magic-prefixed) and
    plaintext files are still readable for back-compatibility.
    """
    cipher = _fernet(key)
    if cipher is not None:
        try:
            return cast(bytes, cipher.decrypt(raw))
        except Exception:
            pass  # nosec B110: not a Fernet token — fall through to legacy XOR/plaintext
    if raw.startswith(_MAGIC):
        body = raw[_MAGIC_LEN:]
        if len(body) < _MAC_LEN:
            return None
        mac, blob = body[:_MAC_LEN], body[_MAC_LEN:]
        expected = hmac.new(key, blob, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            return None
        return _xor(blob, key)
    return raw  # legacy plaintext / old XOR-only blob — left as-is


class MemoryStore(StorageBackend):
    """Thread-safe in-memory store with ring buffer, retention, keyed obfuscation, and JSONL persistence."""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        max_records: int = 100_000,
        retention_days: int = 90,
        encryption_key: Optional["str | bytes"] = None,
    ):
        self.records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self.budgets: dict[str, dict[str, Any]] = {}
        self.running_totals: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.persist_path = persist_path
        self.encryption_key = _normalize_key(encryption_key) if encryption_key else None
        self.retention = RetentionPolicy(max_age_days=retention_days, max_records=max_records, archive_on_trim=True)
        self._last_retention_check: Optional[datetime] = None
        if persist_path and os.path.exists(persist_path):
            self._load_from_disk()

    def insert_record(self, record: dict[str, Any]) -> None:
        with self.lock:
            record["_checksum"] = _checksum(record)
            self.records.append(record)
            self._update_running_totals(record)
            self._apply_retention()
            if self.persist_path:
                self._append_to_disk()

    def _update_running_totals(self, record: dict[str, Any]) -> None:
        if not _is_billable(record):
            return
        dimensions = [
            ("global", "all"),
            ("provider", record.get("provider", "unknown")),
            ("model", record.get("model", "unknown")),
            ("user", record.get("user_id", "anonymous")),
            ("project", record.get("project_id", "default")),
            ("month", record.get("timestamp", "")[:7]),
        ]
        if record.get("conversation_id") and str(record["conversation_id"]).strip():
            dimensions.append(("conversation", record["conversation_id"]))
        if record.get("agent_id") and str(record["agent_id"]).strip():
            dimensions.append(("agent", record["agent_id"]))
        if record.get("tenant_id") and str(record["tenant_id"]).strip():
            dimensions.append(("tenant", record["tenant_id"]))
        for scope, scope_id in dimensions:
            key = f"{scope}:{scope_id}"
            agg = self.running_totals.setdefault(
                key, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
            )
            agg["requests"] += 1
            agg["input_tokens"] += record.get("input_tokens", 0)
            agg["output_tokens"] += record.get("output_tokens", 0)
            agg["total_tokens"] += record.get("total_tokens", 0)
            agg["cost_usd"] += record.get("cost_usd", 0.0)

    def _serialize_lines(self) -> bytes:
        """Serialize all in-memory records to verified JSONL bytes."""
        out = bytearray()
        for r in self.records:
            line = json.dumps(r, default=str)
            checksum = hashlib.sha256(line.encode()).hexdigest()
            out.extend(f"{checksum}:{line}\n".encode())
        return bytes(out)

    def _append_to_disk(self) -> None:
        """Persist records to disk.

        Encrypted files are written as a single authenticated blob so no
        plaintext boundary is exposed and wrong keys are detected on load.
        Plaintext files use a true append-only write for O(1) I/O per insert.
        """
        if self.persist_path is None:
            return
        try:
            if self.encryption_key:
                payload = self._serialize_lines()
                payload = _encrypt(payload, self.encryption_key)
                with open(self.persist_path, "wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
            else:
                # Append only the newest record — O(1) instead of rewriting N records
                record = self.records[-1] if self.records else None
                if record is None:
                    return
                line = json.dumps(record, default=str)
                checksum = hashlib.sha256(line.encode()).hexdigest()
                with open(self.persist_path, "ab") as f:
                    f.write(f"{checksum}:{line}\n".encode())
                    f.flush()
                    os.fsync(f.fileno())
        except OSError as e:
            logger.warning("Failed to persist record: %s", e)

    def _ingest_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            if ":" in line:
                stored_checksum, rest = line.split(":", 1)
                if len(stored_checksum) == 64:
                    actual = hashlib.sha256(rest.encode()).hexdigest()
                    if stored_checksum == actual:
                        record = json.loads(rest)
                        self.records.append(record)
                        self._update_running_totals(record)
                else:
                    record = json.loads(line)
                    self.records.append(record)
                    self._update_running_totals(record)
            else:
                record = json.loads(line)
                self.records.append(record)
                self._update_running_totals(record)
        except (json.JSONDecodeError, ValueError):
            pass

    def _load_from_disk(self) -> None:
        if self.persist_path is None:
            return
        try:
            with open(self.persist_path, "rb") as f:
                raw = f.read()
            if self.encryption_key:
                plain = _decrypt(raw, self.encryption_key)
                if plain is None:
                    logger.warning(
                        "Failed to decrypt %s — wrong encryption key or tampered file; no records loaded",
                        self.persist_path,
                    )
                    return
                text = plain.decode("utf-8", errors="replace")
            else:
                text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                self._ingest_line(line)
        except OSError:
            pass

    def get_records(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.records)

    def get_running_totals(self, scope: str, scope_id: str) -> dict[str, Any]:
        key = f"{scope}:{scope_id}"
        with self.lock:
            return dict(
                self.running_totals.get(key, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0})
            )

    def set_budget(self, scope: str, scope_id: str, budget_config: dict[str, Any]) -> None:
        with self.lock:
            self.budgets[f"{scope}:{scope_id}"] = budget_config

    def get_budget(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
        with self.lock:
            return self.budgets.get(f"{scope}:{scope_id}")

    def get_all_budgets(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return dict(self.budgets)

    def clear(self) -> None:
        with self.lock:
            self.records.clear()
            self.running_totals.clear()
            self.budgets.clear()
            if self.persist_path:
                try:
                    # Truncate persist file so records don't resurrect on restart
                    open(self.persist_path, "wb").close()
                    bak = self.persist_path + ".bak"
                    if os.path.exists(bak):
                        os.remove(bak)
                except OSError:
                    pass

    def get_windowed_spend(self, budget: dict[str, Any], window_start: str) -> Optional[float]:
        """MemoryStore windowed spend — filtered scan with window_start."""
        # Check for cached window? For now simple filtered scan but respects window.
        # This is still O(n) but avoids full history scan when window is recent.
        # For large histories, callers will hit this only for windowed budgets.
        total = 0.0
        # Use local copy to avoid holding lock during iteration? Keep lock for thread safety.
        with self.lock:
            for r in self.records:
                ts = r.get("timestamp", "")
                if ts and ts < window_start:
                    continue
                if r.get("status") in ("blocked", "error") or r.get("_ghost"):
                    continue
                # Scope check delegated to BudgetEnforcer? Duplicate logic here for speed.
                scope = budget.get("scope", "global")
                scope_id = budget.get("scope_id", "")
                if scope == "global":
                    pass
                elif scope == "project" and r.get("project_id", "default") != scope_id:
                    continue
                elif scope == "user" and r.get("user_id", "anonymous") != scope_id:
                    continue
                elif scope == "user_project":
                    parts = scope_id.split(":")
                    if len(parts) != 2 or r.get("user_id") != parts[0] or r.get("project_id") != parts[1]:
                        continue
                elif scope == "provider" and r.get("provider") != scope_id:
                    continue
                elif scope == "model" and r.get("model") != scope_id:
                    continue
                elif scope == "tenant" and r.get("tenant_id") != scope_id:
                    continue
                elif scope not in ("global", "project", "user", "user_project", "provider", "model", "tenant"):
                    continue
                total += float(r.get("cost_usd", 0) or 0)
        return total

    def _apply_retention(self, force: bool = False) -> None:
        """Prune records older than ``max_age_days`` (amortized).

        The age scan is throttled to once per ``_RETENTION_CHECK_INTERVAL_S``
        so inserts stay O(1) — the deque's maxlen already enforces the record
        ring buffer on every insert. Callers acting explicitly
        (``compact``/``apply_retention``) pass ``force=True``.
        """
        if self.retention.max_age_days < 0:
            return
        if not force:
            now = datetime.now(timezone.utc)
            last = self._last_retention_check
            if last is not None and (now - last).total_seconds() < _RETENTION_CHECK_INTERVAL_S:
                return
            self._last_retention_check = now
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention.max_age_days)
        pruned = []
        for r in self.records:
            ts = r.get("timestamp", "")
            try:
                parsed = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed is None or parsed >= cutoff:
                pruned.append(r)
        if len(pruned) < len(self.records):
            self.records = deque(pruned, maxlen=self.retention.max_records)
            self.running_totals.clear()
            for r in self.records:
                self._update_running_totals(r)
            if self.persist_path and self.retention.archive_on_trim:
                self._rewrite_disk()

    def _rewrite_disk(self) -> None:
        """Rewrite the on-disk JSONL to match current in-memory records (used after retention trim)."""
        if self.persist_path is None:
            return
        try:
            if os.path.exists(self.persist_path):
                backup = self.persist_path + ".bak"
                os.replace(self.persist_path, backup)
            payload = self._serialize_lines()
            if self.encryption_key:
                payload = _encrypt(payload, self.encryption_key)
            with open(self.persist_path, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.warning("Failed to rewrite disk after retention: %s", e)

    def compact(self, max_age_days: Optional[int] = None) -> dict[str, Any]:
        with self.lock:
            before = len(self.records)
            if max_age_days is not None:
                self.retention.max_age_days = max_age_days
            self._apply_retention(force=True)
            after = len(self.records)
            return {"removed": before - after, "remaining": after}

    def apply_retention(self, max_age_days: Optional[int] = None) -> None:
        with self.lock:
            if max_age_days is not None:
                self.retention.max_age_days = max_age_days
            self._apply_retention(force=True)

    def close(self) -> None:
        """No-op for symmetry with SqliteStore; enables polymorphic cleanup."""

    def get_record_count(self) -> int:
        with self.lock:
            return len(self.records)

    def verify_immutability(self) -> list[str]:
        """
        Check per-record SHA-256 checksums for tampering.

        .. note::
            This is **tamper-evident, not tamper-proof**. The checksum is stored
            alongside the record, so an attacker with write access can modify the
            record and recompute the checksum. For strong guarantees, use
            :meth:`tokenledger.TokenLedger.sign_ledger` with an external HMAC key
            kept outside the store, or export a signed audit bundle.
        """
        tampered = []
        for r in self.get_records():
            expected = r.get("_checksum", "")
            if not expected:
                continue
            actual = _checksum({k: v for k, v in r.items() if k != "_checksum"})
            if actual != expected:
                actual = _checksum_sorted({k: v for k, v in r.items() if k != "_checksum"})
            if expected != actual:
                tampered.append(r.get("record_id", "unknown"))
        return tampered

    async def async_insert_record(self, record: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.insert_record, record)

    async def async_get_records(self) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_records)

    async def async_compact(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.compact)
