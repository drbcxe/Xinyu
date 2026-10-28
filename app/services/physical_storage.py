"""Bound owned runtime files; never treat content quotas as filesystem quotas."""

import ctypes
import os
import re
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from filelock import FileLock, Timeout

from app.config import PROJECT_ROOT, get_settings

LOG_NAME = re.compile(r"app-[a-z][a-z0-9-]{0,40}-(\d{1,10})\.jsonl(?:\.\d{1,2})?")
MAX_SCAN_ENTRIES = 100000


class FileBudgetError(RuntimeError):
    pass


def is_link(path):
    return path.is_symlink() or path.is_junction()


def checked_folder(path):
    path = Path(path).absolute()
    if is_link(path):
        raise FileBudgetError("Runtime root must not be a link")
    resolved = path.resolve()
    if os.name == "nt" and resolved.drive.lower() == "c:":
        raise FileBudgetError("Runtime files must stay off C drive")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def runtime_folder(name):
    if name not in {"logs", "backups", "restore-tmp"}:
        raise ValueError("Unknown runtime directory")
    path = checked_folder(PROJECT_ROOT / ".runtime" / name)
    if not path.is_relative_to(PROJECT_ROOT.resolve()):
        raise FileBudgetError("Runtime directory escaped workspace")
    return path


def directory_bytes(root):
    root = Path(root).resolve()
    if not root.exists():
        return 0
    total, count, pending = 0, 0, [root]
    while pending:
        for entry in pending.pop().iterdir():
            count += 1
            if (
                count > MAX_SCAN_ENTRIES
                or is_link(entry)
                or not entry.resolve().is_relative_to(root)
            ):
                raise FileBudgetError("Directory cannot be safely measured")
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                total += entry.stat().st_size
            else:
                raise FileBudgetError("Unexpected runtime entry")
    return total


def storage_lock(root, name, timeout=2):
    path = Path(root) / name
    if is_link(path):
        raise FileBudgetError("Runtime lock must not be a link")
    return FileLock(
        path, timeout=timeout, fallback_to_soft=False, preserve_lock_file=True
    )


class FileBudget:
    def __init__(self, root, limit):
        self.root, self.limit = Path(root).resolve(), limit
        self.used = directory_bytes(self.root)
        self.reserve(0)

    def reserve(self, amount):
        if amount < 0 or self.used + amount > self.limit:
            raise FileBudgetError("Runtime file budget exhausted")
        if (
            shutil.disk_usage(self.root).free - amount
            < get_settings().runtime_free_bytes
        ):
            raise FileBudgetError("Insufficient free space for runtime write")
        self.used += amount


def process_alive(pid):
    if pid <= 0 or pid > 0xFFFFFFFF:
        return True  # Unknown ownership is protected.
    if os.name != "nt":
        try:
            os.kill(pid, 0)  # POSIX probe only; this must never run on Windows.
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # Access denied/unknown remains protected.
    try:
        code = wintypes.DWORD()
        return (
            not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            or code.value == 259
        )
    finally:
        kernel.CloseHandle(handle)


def log_files(root):
    found = []
    for path in root.iterdir():
        match = LOG_NAME.fullmatch(path.name)
        if (
            match
            and not is_link(path)
            and path.is_file()
            and path.resolve().parent == root
        ):
            info = path.stat()
            found.append((path, info.st_size, info.st_mtime, int(match[1])))
    return found


def prune_logs_locked(root, needed=0):
    settings = get_settings()
    rows = log_files(root)
    used = sum(row[1] for row in rows)
    deleted = 0
    alive = {}
    for path, size, modified, pid in sorted(rows, key=lambda row: row[2]):
        if (
            time.time() - modified < settings.log_retention_days * 86400
            and used + needed <= settings.log_total_bytes
        ):
            continue
        if pid not in alive:
            alive[pid] = process_alive(pid)
        if alive[pid]:
            continue
        # Revalidate the exact owned path immediately before deletion; never recurse here.
        if (
            is_link(path)
            or path.resolve().parent != root
            or not LOG_NAME.fullmatch(path.name)
        ):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        used -= size
        deleted += 1
    return used, deleted


def maintain_logs(root=None):
    root = checked_folder(root) if root else runtime_folder("logs")
    with storage_lock(root, ".log-budget.lock"):
        used, deleted = prune_logs_locked(root)
    return {"managed_bytes": used, "deleted_files": deleted}


class BudgetedJsonHandler(RotatingFileHandler):
    dropped = 0

    def __init__(self, path):
        self.root = checked_folder(Path(path).parent)
        if not LOG_NAME.fullmatch(Path(path).name):
            raise ValueError("Invalid application log identity")
        settings = get_settings()
        super().__init__(
            self.root / Path(path).name,
            maxBytes=settings.log_file_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf8",
            delay=True,
        )

    def emit(self, record):
        try:
            encoded = self.format(record)
            needed = (
                len(encoded.encode("utf8")) + 2
            )  # Includes Windows CRLF conservatively.
            settings = get_settings()
            if needed > self.maxBytes:
                type(self).dropped += 1
                return
            with storage_lock(self.root, ".log-budget.lock", timeout=0.1):
                if any(
                    is_link(Path(self.baseFilename + suffix))
                    for suffix in [
                        "",
                        *(f".{i}" for i in range(1, self.backupCount + 1)),
                    ]
                ):
                    raise FileBudgetError("Log target must not be a link")
                if (
                    self.stream
                    and self.stream.tell() + needed > self.maxBytes
                    or self.stream is None
                    and Path(self.baseFilename).exists()
                    and Path(self.baseFilename).stat().st_size + needed > self.maxBytes
                ):
                    self.doRollover()
                used, _ = prune_logs_locked(self.root, needed)
                if (
                    used + needed > settings.log_total_bytes
                    or shutil.disk_usage(self.root).free - needed
                    < settings.runtime_free_bytes
                ):
                    type(self).dropped += 1
                    return
                if self.stream is None:
                    self.stream = self._open()
                self.stream.write(encoded + self.terminator)
                self.flush()
        except (OSError, Timeout, RuntimeError):
            # Do not call logging.handleError: it prints the original (possibly private) record.
            type(self).dropped += 1


def runtime_file_status():
    settings = get_settings()
    logs = runtime_folder("logs")
    rows = log_files(logs)
    total_log_bytes = sum(
        path.stat().st_size
        for path in logs.iterdir()
        if path.is_file() and not is_link(path) and path.name != ".log-budget.lock"
    )
    backup_root = runtime_folder("backups")
    backup_dirs = [
        path
        for path in backup_root.iterdir()
        if path.is_dir()
        and re.fullmatch(r"xinyu_backup_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{16}", path.name)
    ]
    return {
        "logs": {
            "managed_bytes": sum(row[1] for row in rows),
            "file_bytes": total_log_bytes,
            "limit_bytes": settings.log_total_bytes,
            "files": len(rows),
            "dropped_in_calling_process": BudgetedJsonHandler.dropped,
        },
        "backups": {
            "file_bytes": directory_bytes(backup_root),
            "limit_bytes": settings.backup_total_bytes,
            "count": len(backup_dirs),
            "incomplete": sum(
                not (path / "COMPLETE").is_file() for path in backup_dirs
            ),
        },
        "restore_temp": {
            "file_bytes": directory_bytes(runtime_folder("restore-tmp")),
            "limit_bytes": settings.restore_temp_bytes,
        },
        "minimum_free_bytes": settings.runtime_free_bytes,
        "note": "文件字节按逻辑长度计数，不等同于文件系统已分配簇；日志丢弃计数只属于当前进程。",
    }
