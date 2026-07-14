"""Explicitly fetch pinned upstream blobs into an isolated checkout."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid


if __package__:
    from . import (
        ALLOWED_UPSTREAM_FILES,
        UPSTREAM_BLOBS,
        UPSTREAM_COMMIT,
        UPSTREAM_REPOSITORY,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from third_party.CombatRadarSdr2026 import (  # type: ignore[no-redef]
        ALLOWED_UPSTREAM_FILES,
        UPSTREAM_BLOBS,
        UPSTREAM_COMMIT,
        UPSTREAM_REPOSITORY,
    )


def _git_at(checkout: Path) -> list[str]:
    return ["git", "-C", str(checkout)]


def _fetch_commands(checkout: Path) -> list[list[str]]:
    git_at_checkout = _git_at(checkout)
    return [
        [
            "git",
            "init",
            "--object-format=sha1",
            "--template=",
            str(checkout),
        ],
        git_at_checkout + ["config", "core.hooksPath", ".git/no-hooks"],
        git_at_checkout + ["remote", "add", "origin", UPSTREAM_REPOSITORY],
        git_at_checkout + ["config", "core.repositoryformatversion", "1"],
        git_at_checkout + ["config", "extensions.partialClone", "origin"],
        git_at_checkout + ["config", "remote.origin.promisor", "true"],
        git_at_checkout
        + ["config", "remote.origin.partialclonefilter", "blob:none"],
        git_at_checkout + ["sparse-checkout", "init", "--no-cone"],
        git_at_checkout
        + ["sparse-checkout", "set", "--no-cone"]
        + [f"/{path}" for path in ALLOWED_UPSTREAM_FILES],
        git_at_checkout
        + [
            "-c",
            "protocol.version=2",
            "fetch",
            "--no-tags",
            "--depth=1",
            "--filter=blob:none",
            "origin",
            UPSTREAM_COMMIT,
        ],
        git_at_checkout + ["checkout", "--detach", UPSTREAM_COMMIT],
    ]


def build_fetch_plan(destination: Path) -> dict[str, object]:
    destination = destination.resolve()
    staging = destination.parent / f".{destination.name}.staging-<unique>"
    return {
        "repository": UPSTREAM_REPOSITORY,
        "commit": UPSTREAM_COMMIT,
        "blobs": dict(UPSTREAM_BLOBS),
        "commands": _fetch_commands(staging),
        "lock": str(destination.parent / f".{destination.name}.fetch.lock"),
        "publish": {"from": str(staging), "to": str(destination)},
    }


def _run(
    command: list[str], *, check: bool = True, no_lazy_fetch: bool = False
) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_"):
            del environment[name]
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if no_lazy_fetch:
        environment["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env=environment,
    )


def _materialize_checkout(staging: Path) -> None:
    checkout = staging
    if os.name == "nt":
        checkout = staging / f"payload-{uuid.uuid4().hex}"
        os.mkdir(checkout, 0o700)
    commands = _fetch_commands(checkout)
    _run(commands[0])
    (checkout / ".git" / "info").mkdir(parents=True, exist_ok=True)
    for command in commands[1:]:
        _run(command)


def _active_checkout_path(staging: Path) -> Path:
    if os.name != "nt":
        return staging
    payloads = [
        path
        for path in staging.iterdir()
        if path.name.startswith("payload-")
    ]
    if not payloads:
        return staging
    if len(payloads) != 1:
        raise RuntimeError("staging payload identity changed")
    payload = payloads[0]
    if payload.is_symlink() or _path_is_reparse_point(payload):
        raise RuntimeError("staging payload is a reparse point")
    return payload


def _flatten_checkout(staging: Path, checkout: Path) -> None:
    if checkout == staging:
        return
    for child in checkout.iterdir():
        target = staging / child.name
        if _path_entry_exists(target):
            raise RuntimeError("staging payload target already exists")
        child.rename(target)
    checkout.rmdir()


def _tree_blobs(checkout: Path) -> dict[str, str]:
    completed = _run(
        _git_at(checkout) + ["ls-tree", "-r", "-z", "HEAD"],
        no_lazy_fetch=True,
    )
    blobs: dict[str, str] = {}
    for entry in completed.stdout.split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        _mode, object_type, object_id = metadata.split()
        if object_type == "blob":
            blobs[path] = object_id
    return blobs


def _local_blob_ids(checkout: Path) -> set[str]:
    completed = _run(
        _git_at(checkout)
        + [
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        no_lazy_fetch=True,
    )
    return {
        object_id
        for line in completed.stdout.splitlines()
        for object_id, object_type in [line.split()]
        if object_type == "blob"
    }


def _verify_checkout(checkout: Path) -> None:
    staging = checkout
    checkout = _active_checkout_path(staging)
    resolved_commit = _run(
        _git_at(checkout) + ["rev-parse", "HEAD"], no_lazy_fetch=True
    ).stdout.strip()
    if resolved_commit != UPSTREAM_COMMIT:
        raise RuntimeError(
            f"fetched commit {resolved_commit!r} does not match "
            f"{UPSTREAM_COMMIT!r}"
        )

    tree_blobs = _tree_blobs(checkout)
    for relative_path, expected_blob in UPSTREAM_BLOBS.items():
        resolved_blob = _run(
            _git_at(checkout) + ["rev-parse", f"HEAD:{relative_path}"],
            no_lazy_fetch=True,
        ).stdout.strip()
        if (
            resolved_blob != expected_blob
            or tree_blobs.get(relative_path) != expected_blob
        ):
            raise RuntimeError(
                f"blob pin mismatch for {relative_path}: "
                f"expected {expected_blob}, got {resolved_blob}"
            )

        materialized_blob = _run(
            _git_at(checkout) + ["hash-object", "--", relative_path],
            no_lazy_fetch=True,
        ).stdout.strip()
        if materialized_blob != expected_blob:
            raise RuntimeError(
                f"materialized blob mismatch for {relative_path}: "
                f"expected {expected_blob}, got {materialized_blob}"
            )

    checked_out_files = {
        path.relative_to(checkout).as_posix()
        for path in checkout.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(checkout).parts
    }
    if checked_out_files != set(UPSTREAM_BLOBS):
        raise RuntimeError(
            f"unexpected checked-out files: {sorted(checked_out_files)}"
        )

    local_blobs = _local_blob_ids(checkout)
    expected_blobs = set(UPSTREAM_BLOBS.values())
    if local_blobs != expected_blobs:
        raise RuntimeError(
            "local blob object allowlist mismatch: "
            f"expected {sorted(expected_blobs)}, got {sorted(local_blobs)}"
        )
    _flatten_checkout(staging, checkout)


def _path_entry_exists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _path_is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _path_identity(path: Path) -> tuple[int, int]:
    information = os.stat(path, follow_symlinks=False)
    return information.st_dev, information.st_ino


def _validate_destination(
    destination: Path,
) -> tuple[Path, tuple[int, int]]:
    requested = Path(destination).expanduser()
    if ".." in requested.parts:
        raise ValueError("destination must not contain parent traversal")
    destination = Path(os.path.abspath(os.fspath(requested)))
    if not destination.name:
        raise ValueError("destination must not be a filesystem root")
    if _path_entry_exists(destination):
        raise FileExistsError(f"destination already exists: {destination}")

    component = destination.parent
    while True:
        if component.is_symlink():
            raise ValueError(
                f"destination parent contains a symlink: {component}"
            )
        if _path_is_reparse_point(component):
            raise ValueError(
                f"destination parent contains a reparse point: {component}"
            )
        if component.parent == component:
            break
        component = component.parent

    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"destination parent does not exist: {destination.parent}"
        )
    return destination, _path_identity(destination.parent)


def _windows_open_directory(
    path: Path, *, prevent_rename: bool = False
) -> int:
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    desired_access = 0x0080  # FILE_READ_ATTRIBUTES
    share_mode = 0x0001 | 0x0002  # FILE_SHARE_READ | FILE_SHARE_WRITE
    if prevent_rename:
        desired_access |= 0x00010000  # DELETE
    else:
        share_mode |= 0x0004  # FILE_SHARE_DELETE
    handle = create_file(
        str(path),
        desired_access,
        share_mode,
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _windows_close_handle(handle: int) -> None:
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_handle_information(handle: int):
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    get_information.restype = wintypes.BOOL
    information = FileInformation()
    if not get_information(handle, ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    return information


def _windows_handle_identity(handle: int) -> tuple[int, int, int]:
    information = _windows_handle_information(handle)
    return (
        information.volume_serial_number,
        information.file_index_high,
        information.file_index_low,
    )


def _windows_regular_file_blob_id_no_follow(path: Path) -> str:
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x0001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        information = _windows_handle_information(handle)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        directory_flag = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)
        if information.file_attributes & (reparse_flag | directory_flag):
            raise RuntimeError(f"materialized entry is not a file: {path}")
        get_file_type = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).GetFileType
        get_file_type.argtypes = [wintypes.HANDLE]
        get_file_type.restype = wintypes.DWORD
        if get_file_type(handle) != 1:  # FILE_TYPE_DISK
            raise RuntimeError(
                f"materialized entry is not a disk file: {path}"
            )
        descriptor = msvcrt.open_osfhandle(
            handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
        transferred = True
        with os.fdopen(descriptor, "rb") as file:
            size = (
                information.file_size_high << 32
            ) | information.file_size_low
            return _stream_git_blob_id(file, size, path)
    finally:
        if not transferred:
            _windows_close_handle(handle)


class _ParentAnchor:
    def __init__(
        self,
        path: Path,
        descriptor: int,
        identity: tuple[int, ...],
        stable_root: Path | None = None,
    ) -> None:
        self.path = path
        self.descriptor = descriptor
        self.identity = identity
        self.stable_root = stable_root
        self.closed = False

    def __enter__(self) -> _ParentAnchor:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if os.name == "nt":
            _windows_close_handle(self.descriptor)
        else:
            os.close(self.descriptor)

    def child_path(self, name: str) -> Path:
        if os.name == "nt":
            return self.path / name
        if self.stable_root is None:
            raise RuntimeError("parent anchor has no stable filesystem path")
        return self.stable_root / name

    def entry_exists(self, name: str) -> bool:
        if os.name == "nt":
            return _path_entry_exists(self.child_path(name))
        try:
            os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def create_lock(self, name: str) -> int:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if os.name == "nt":
            return os.open(self.child_path(name), flags, 0o600)
        return os.open(name, flags, 0o600, dir_fd=self.descriptor)

    def unlink(self, name: str) -> None:
        if os.name == "nt":
            os.unlink(self.child_path(name))
        else:
            os.unlink(name, dir_fd=self.descriptor)

    def create_staging(self, prefix: str) -> tuple[str, Path]:
        for _attempt in range(100):
            name = f"{prefix}{uuid.uuid4().hex}"
            try:
                if os.name == "nt":
                    os.mkdir(self.child_path(name), 0o700)
                else:
                    os.mkdir(name, 0o700, dir_fd=self.descriptor)
            except FileExistsError:
                continue
            return name, self.child_path(name)
        raise RuntimeError("could not allocate a unique staging directory")

    def entry_stat(self, name: str):
        if os.name == "nt":
            return os.lstat(self.child_path(name))
        return os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)

    def entry_identity(self, name: str) -> tuple[int, ...]:
        path = self.child_path(name)
        if path.is_symlink() or _path_is_reparse_point(path):
            raise RuntimeError(f"anchored entry is a reparse point: {name}")
        if os.name == "nt":
            handle = _windows_open_directory(path)
            try:
                return _windows_handle_identity(handle)
            finally:
                _windows_close_handle(handle)
        information = self.entry_stat(name)
        return information.st_dev, information.st_ino

    def find_entry_by_identity(
        self, identity: tuple[int, ...]
    ) -> str | None:
        names = (
            os.listdir(self.path)
            if os.name == "nt"
            else os.listdir(self.descriptor)
        )
        for name in names:
            try:
                if self.entry_identity(name) == identity:
                    return name
            except (FileNotFoundError, NotADirectoryError, RuntimeError):
                continue
        return None

    def remove_entry_no_follow(self, name: str) -> None:
        path = self.child_path(name)
        information = self.entry_stat(name)
        if path.is_symlink() or _path_is_reparse_point(path):
            if os.name == "nt" and path.is_dir():
                os.rmdir(path)
            elif os.name == "nt":
                os.unlink(path)
            else:
                os.unlink(name, dir_fd=self.descriptor)
            return
        if stat.S_ISDIR(information.st_mode):
            if os.name == "nt":
                os.rmdir(path)
            else:
                os.rmdir(name, dir_fd=self.descriptor)
        elif os.name == "nt":
            os.unlink(path)
        else:
            os.unlink(name, dir_fd=self.descriptor)

    def verify_identity(self) -> None:
        if self.closed:
            raise RuntimeError("parent anchor is closed")
        if _path_is_reparse_point(self.path) or self.path.is_symlink():
            raise RuntimeError("destination parent identity changed")
        if os.name == "nt":
            current_handle = _windows_open_directory(self.path)
            try:
                current_identity = _windows_handle_identity(current_handle)
            finally:
                _windows_close_handle(current_handle)
        else:
            try:
                current = os.stat(self.path, follow_symlinks=False)
            except FileNotFoundError as error:
                raise RuntimeError(
                    "destination parent identity changed"
                ) from error
            current_identity = (current.st_dev, current.st_ino)
        if current_identity != self.identity:
            raise RuntimeError("destination parent identity changed")


class _StagingAnchor:
    def __init__(
        self,
        parent: _ParentAnchor,
        name: str,
        descriptor: int,
        identity: tuple[int, ...],
        path: Path,
    ) -> None:
        self.parent = parent
        self.name = name
        self.descriptor = descriptor
        self.identity = identity
        self.path = path
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if os.name == "nt":
            _windows_close_handle(self.descriptor)
        else:
            os.close(self.descriptor)

    def verify_parent_entry(self, name: str | None = None) -> None:
        entry_name = self.name if name is None else name
        try:
            current_identity = self.parent.entry_identity(entry_name)
        except (FileNotFoundError, NotADirectoryError) as error:
            raise RuntimeError("staging identity changed") from error
        if current_identity != self.identity:
            raise RuntimeError("staging identity changed")

    def verify_handle_identity(self) -> None:
        if self.closed:
            raise RuntimeError("staging anchor is closed")
        if os.name == "nt":
            current_identity = _windows_handle_identity(self.descriptor)
        else:
            information = os.fstat(self.descriptor)
            current_identity = information.st_dev, information.st_ino
        if current_identity != self.identity:
            raise RuntimeError("staging handle identity changed")


def _open_staging_anchor(
    parent: _ParentAnchor,
    name: str,
    expected_identity: tuple[int, ...] | None = None,
) -> _StagingAnchor:
    if os.name == "nt":
        path = parent.child_path(name)
        if path.is_symlink() or _path_is_reparse_point(path):
            raise RuntimeError("staging entry is a reparse point")
        descriptor = _windows_open_directory(path, prevent_rename=True)
        try:
            anchor = _StagingAnchor(
                parent,
                name,
                descriptor,
                _windows_handle_identity(descriptor),
                path,
            )
            if (
                expected_identity is not None
                and anchor.identity != expected_identity
            ):
                raise RuntimeError("created staging identity changed")
            anchor.verify_parent_entry()
        except BaseException:
            _windows_close_handle(descriptor)
            raise
        return anchor

    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise RuntimeError("safe staging anchoring is unsupported")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
    except OSError as error:
        raise RuntimeError(
            "staging entry is not an anchored directory"
        ) from error
    try:
        information = os.fstat(descriptor)
        path = Path(f"/proc/{os.getpid()}/fd/{descriptor}")
        if not path.is_dir():
            raise RuntimeError("safe staging anchoring is unsupported")
        anchor = _StagingAnchor(
            parent,
            name,
            descriptor,
            (information.st_dev, information.st_ino),
            path,
        )
        if (
            expected_identity is not None
            and anchor.identity != expected_identity
        ):
            raise RuntimeError("created staging identity changed")
        anchor.verify_parent_entry()
    except BaseException:
        os.close(descriptor)
        raise
    return anchor


def _remove_entry_if_expected(
    parent: _ParentAnchor, name: str, expected_identity: tuple[int, ...]
) -> bool:
    if not parent.entry_exists(name):
        return False
    try:
        current_identity = parent.entry_identity(name)
    except RuntimeError:
        parent.remove_entry_no_follow(name)
        return True
    if current_identity != expected_identity:
        return False
    parent.remove_entry_no_follow(name)
    return True


def _cleanup_created_staging(
    parent: _ParentAnchor, name: str, identity: tuple[int, ...]
) -> None:
    _remove_entry_if_expected(parent, name, identity)
    original_name = parent.find_entry_by_identity(identity)
    if original_name is not None:
        if not _remove_entry_if_expected(parent, original_name, identity):
            raise RuntimeError(
                "created staging identity changed during cleanup"
            )


def _create_staging_anchor(
    parent: _ParentAnchor, prefix: str
) -> _StagingAnchor:
    for _attempt in range(100):
        name = f"{prefix}{uuid.uuid4().hex}"
        try:
            if os.name == "nt":
                os.mkdir(parent.child_path(name), 0o700)
            else:
                os.mkdir(name, 0o700, dir_fd=parent.descriptor)
        except FileExistsError:
            continue
        try:
            created_identity = parent.entry_identity(name)
            try:
                return _open_staging_anchor(
                    parent, name, created_identity
                )
            except BaseException as primary_error:
                try:
                    _cleanup_created_staging(
                        parent, name, created_identity
                    )
                except BaseException as cleanup_error:
                    raise RuntimeError(
                        "staging anchor open failed; staging cleanup failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    ) from primary_error
                raise
        except BaseException:
            try:
                if parent.entry_exists(name):
                    path = parent.child_path(name)
                    if path.is_symlink() or _path_is_reparse_point(path):
                        parent.remove_entry_no_follow(name)
            except BaseException:
                pass
            raise
    raise RuntimeError("could not allocate a unique staging directory")


def _open_parent_anchor(
    parent: Path, expected_identity: tuple[int, int]
) -> _ParentAnchor:
    if os.name == "nt":
        descriptor = _windows_open_directory(parent, prevent_rename=True)
        try:
            if _path_identity(parent) != expected_identity:
                raise RuntimeError("destination parent identity changed")
            anchor = _ParentAnchor(
                parent, descriptor, _windows_handle_identity(descriptor)
            )
            anchor.verify_identity()
        except BaseException:
            _windows_close_handle(descriptor)
            raise
        return anchor

    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise RuntimeError("safe parent anchoring is unsupported")
    descriptor = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        information = os.fstat(descriptor)
        if (information.st_dev, information.st_ino) != expected_identity:
            raise RuntimeError("destination parent identity changed")
        stable_root = Path(f"/proc/{os.getpid()}/fd/{descriptor}")
        if not stable_root.is_dir():
            raise RuntimeError("safe parent anchoring is unsupported")
        anchor = _ParentAnchor(
            parent,
            descriptor,
            (information.st_dev, information.st_ino),
            stable_root,
        )
        anchor.verify_identity()
    except BaseException:
        os.close(descriptor)
        raise
    return anchor


def _rename_no_replace_posix(
    anchor: _ParentAnchor, source_name: str, destination_name: str
) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("atomic no-replace publication is unsupported")
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic no-replace publication is unsupported")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        anchor.descriptor,
        os.fsencode(source_name),
        anchor.descriptor,
        os.fsencode(destination_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            destination_name,
        )
    if error_number in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
        raise RuntimeError(
            "atomic no-replace publication is unsupported"
        )
    raise OSError(error_number, os.strerror(error_number), destination_name)


def _windows_rename_handle_no_replace(
    source_handle: int, destination: Path
) -> None:
    from ctypes import wintypes

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    destination_name = os.fspath(destination)
    encoded_name = destination_name.encode("utf-16-le")
    terminated_name = encoded_name + b"\x00\x00"
    file_name_offset = FileRenameInformation.file_name.offset
    buffer_size = max(
        ctypes.sizeof(FileRenameInformation),
        file_name_offset + len(terminated_name),
    )
    buffer = ctypes.create_string_buffer(buffer_size)
    information = ctypes.cast(
        buffer, ctypes.POINTER(FileRenameInformation)
    ).contents
    information.replace_if_exists = 0
    information.root_directory = None
    information.file_name_length = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(buffer) + file_name_offset,
        terminated_name,
        len(terminated_name),
    )

    set_information = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    if set_information(source_handle, 3, buffer, buffer_size):
        return
    error_number = ctypes.get_last_error()
    if error_number in (80, 183):
        raise FileExistsError(
            error_number, "destination exists", destination_name
        )
    raise ctypes.WinError(error_number)


def _remove_replaced_entry(
    parent: _ParentAnchor,
    name: str,
    expected_identity: tuple[int, ...],
    identity_error: BaseException,
) -> None:
    try:
        removed = _remove_entry_if_expected(
            parent, name, expected_identity
        )
    except BaseException as rollback_error:
        raise RuntimeError(
            f"{identity_error}; rollback failed: {rollback_error}"
        ) from identity_error
    if not removed and parent.entry_exists(name):
        raise RuntimeError(
            f"{identity_error}; rollback refused unknown directory identity"
        ) from identity_error


def _publish_no_replace(
    anchor: _ParentAnchor,
    staging: _StagingAnchor,
    destination: Path,
) -> None:
    anchor.verify_identity()
    try:
        staging.verify_parent_entry()
    except BaseException as identity_error:
        _remove_replaced_entry(
            anchor, staging.name, staging.identity, identity_error
        )
        raise
    if os.name == "nt":
        _windows_rename_handle_no_replace(
            staging.descriptor, destination
        )
    else:
        _rename_no_replace_posix(anchor, staging.name, destination.name)
    try:
        staging.verify_parent_entry(destination.name)
    except BaseException as identity_error:
        _remove_replaced_entry(
            anchor, destination.name, staging.identity, identity_error
        )
        raise
    staging.name = destination.name
    if os.name == "nt":
        staging.path = destination
    anchor.verify_identity()


def _remove_staging(staging: Path) -> None:
    def remove_readonly(remove, path, _error) -> None:
        os.chmod(path, stat.S_IWRITE)
        remove(path)

    shutil.rmtree(staging, onerror=remove_readonly)


MAX_MATERIALIZED_FILE_SIZE = 16 * 1024 * 1024
HASH_CHUNK_SIZE = 64 * 1024


def _stream_git_blob_id(file, size: int, path: object) -> str:
    if size > MAX_MATERIALIZED_FILE_SIZE:
        raise RuntimeError(
            f"final materialized file exceeds size limit: {path} ({size})"
        )
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    remaining = size
    while remaining:
        chunk = file.read(min(HASH_CHUNK_SIZE, remaining))
        if not chunk:
            raise RuntimeError(
                f"final materialized file ended early: {path}"
            )
        digest.update(chunk)
        remaining -= len(chunk)
    if file.read(1):
        raise RuntimeError(
            f"final materialized file grew during verification: {path}"
        )
    return digest.hexdigest()


def _collect_posix_materialized_blobs(
    descriptor: int, prefix: tuple[str, ...] = ()
) -> tuple[dict[str, str], set[str]]:
    blobs: dict[str, str] = {}
    directories: set[str] = set()
    for name in os.listdir(descriptor):
        relative_parts = prefix + (name,)
        information = os.stat(
            name, dir_fd=descriptor, follow_symlinks=False
        )
        if not prefix and name == ".git":
            if not stat.S_ISDIR(information.st_mode):
                raise RuntimeError("final .git entry is not a directory")
            continue
        relative_path = "/".join(relative_parts)
        if stat.S_ISLNK(information.st_mode):
            raise RuntimeError(
                f"final materialized tree contains a symlink: {relative_path}"
            )
        if stat.S_ISDIR(information.st_mode):
            directories.add(relative_path)
            child_descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            try:
                child_information = os.fstat(child_descriptor)
                if (
                    child_information.st_dev,
                    child_information.st_ino,
                ) != (information.st_dev, information.st_ino):
                    raise RuntimeError(
                        "final materialized directory identity changed: "
                        f"{relative_path}"
                    )
                child_blobs, child_directories = (
                    _collect_posix_materialized_blobs(
                        child_descriptor, relative_parts
                    )
                )
                blobs.update(child_blobs)
                directories.update(child_directories)
            finally:
                os.close(child_descriptor)
            continue
        if not stat.S_ISREG(information.st_mode):
            raise RuntimeError(
                f"final materialized tree contains a special entry: "
                f"{relative_path}"
            )
        file_descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        try:
            opened_information = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened_information.st_mode):
                raise RuntimeError(
                    "final materialized entry changed to a non-file: "
                    f"{relative_path}"
                )
            if (
                opened_information.st_dev,
                opened_information.st_ino,
            ) != (information.st_dev, information.st_ino):
                raise RuntimeError(
                    "final materialized file identity changed: "
                    f"{relative_path}"
                )
            with os.fdopen(file_descriptor, "rb", closefd=False) as file:
                blob_id = _stream_git_blob_id(
                    file, opened_information.st_size, relative_path
                )
        finally:
            os.close(file_descriptor)
        blobs[relative_path] = blob_id
    return blobs, directories


def _collect_windows_materialized_blobs(
    root: Path,
    prefix: tuple[str, ...] = (),
) -> tuple[dict[str, str], set[str]]:
    blobs: dict[str, str] = {}
    directories: set[str] = set()
    with os.scandir(root) as entries:
        for entry in entries:
            path = Path(entry.path)
            relative_parts = prefix + (entry.name,)
            relative_path = "/".join(relative_parts)
            if entry.is_symlink() or _path_is_reparse_point(path):
                raise RuntimeError(
                    "final materialized tree contains a reparse point: "
                    f"{relative_path}"
                )
            if not prefix and entry.name == ".git":
                if not entry.is_dir(follow_symlinks=False):
                    raise RuntimeError("final .git entry is not a directory")
                continue
            if entry.is_dir(follow_symlinks=False):
                directories.add(relative_path)
                child_handle = _windows_open_directory(
                    path, prevent_rename=True
                )
                try:
                    information = _windows_handle_information(child_handle)
                    reparse_flag = getattr(
                        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                    )
                    if information.file_attributes & reparse_flag:
                        raise RuntimeError(
                            "final materialized tree contains a reparse "
                            "point: "
                            f"{relative_path}"
                        )
                    child_blobs, child_directories = (
                        _collect_windows_materialized_blobs(
                            path, relative_parts
                        )
                    )
                    blobs.update(child_blobs)
                    directories.update(child_directories)
                finally:
                    _windows_close_handle(child_handle)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise RuntimeError(
                    f"final materialized tree contains a special entry: "
                    f"{relative_path}"
                )
            blobs[relative_path] = (
                _windows_regular_file_blob_id_no_follow(path)
            )
    return blobs, directories


def _verify_final_materialized_tree(staging: _StagingAnchor) -> None:
    staging.verify_handle_identity()
    if os.name == "nt":
        actual_blobs, actual_directories = (
            _collect_windows_materialized_blobs(staging.path)
        )
    else:
        actual_blobs, actual_directories = (
            _collect_posix_materialized_blobs(staging.descriptor)
        )
    expected_directories = {
        "/".join(parts[:index])
        for relative_path in UPSTREAM_BLOBS
        for parts in [relative_path.split("/")]
        for index in range(1, len(parts))
    }
    if actual_directories != expected_directories:
        raise RuntimeError(
            "unexpected final materialized directories: "
            f"{sorted(actual_directories)}"
        )
    if set(actual_blobs) != set(UPSTREAM_BLOBS):
        raise RuntimeError(
            "unexpected final materialized files: "
            f"{sorted(actual_blobs)}"
        )
    for relative_path, expected_blob in UPSTREAM_BLOBS.items():
        actual_blob = actual_blobs[relative_path]
        if actual_blob != expected_blob:
            raise RuntimeError(
                f"final materialized blob mismatch for {relative_path}: "
                f"expected {expected_blob}, got {actual_blob}"
            )
    staging.verify_handle_identity()


def _empty_staging_anchor(staging: _StagingAnchor) -> None:
    staging.verify_handle_identity()
    for child in staging.path.iterdir():
        if child.is_symlink() or _path_is_reparse_point(child):
            if os.name == "nt" and child.is_dir():
                os.rmdir(child)
            else:
                os.unlink(child)
        elif child.is_dir():
            _remove_staging(child)
        else:
            child.unlink()
    staging.verify_handle_identity()


def _cleanup_staging_anchor(staging: _StagingAnchor) -> None:
    parent = staging.parent
    if parent.entry_exists(staging.name):
        try:
            named_identity = parent.entry_identity(staging.name)
        except RuntimeError:
            parent.remove_entry_no_follow(staging.name)
        else:
            if named_identity == staging.identity:
                parent.remove_entry_no_follow(staging.name)

    original_name = parent.find_entry_by_identity(staging.identity)
    if original_name is None:
        return
    if parent.entry_identity(original_name) != staging.identity:
        raise RuntimeError("staging identity changed during cleanup")
    parent.remove_entry_no_follow(original_name)


def fetch(destination: Path) -> None:
    destination, parent_identity = _validate_destination(destination)
    if shutil.which("git") is None:
        raise RuntimeError("git is required for the explicit upstream fetch")

    lock_name = f".{destination.name}.fetch.lock"
    anchor = _open_parent_anchor(destination.parent, parent_identity)
    staging_anchor: _StagingAnchor | None = None
    lock_descriptor: int | None = None
    lock_stream = None
    published = False
    lock_owned = False
    primary_error: BaseException | None = None
    primary_traceback = None
    try:
        try:
            lock_descriptor = anchor.create_lock(lock_name)
        except FileExistsError as error:
            raise FileExistsError(
                "concurrent fetch lock exists: "
                f"{anchor.child_path(lock_name)}"
            ) from error
        lock_owned = True
        lock_stream = os.fdopen(
            lock_descriptor, "w", encoding="utf-8"
        )
        lock_descriptor = None
        lock_stream.write(f"pid={os.getpid()}\n")
        lock_stream.flush()

        if anchor.entry_exists(destination.name):
            raise FileExistsError(
                f"destination appeared during fetch: {destination}"
            )
        staging_anchor = _create_staging_anchor(
            anchor,
            f".{destination.name}.staging-"
        )
        _materialize_checkout(staging_anchor.path)
        _verify_checkout(staging_anchor.path)
        _verify_final_materialized_tree(staging_anchor)
        if anchor.entry_exists(destination.name):
            raise FileExistsError(
                f"destination appeared during fetch: {destination}"
            )
        _publish_no_replace(anchor, staging_anchor, destination)
        _verify_final_materialized_tree(staging_anchor)
        published = True
    except BaseException as error:
        primary_error = error
        primary_traceback = error.__traceback__

    cleanup_errors: list[tuple[str, BaseException]] = []
    if staging_anchor is not None:
        staging_cleanup_label = (
            "staging rollback"
            if staging_anchor.name == destination.name
            else "staging cleanup"
        )
        if not published:
            try:
                _empty_staging_anchor(staging_anchor)
            except BaseException as error:
                cleanup_errors.append((staging_cleanup_label, error))
        try:
            staging_anchor.close()
        except BaseException as error:
            cleanup_errors.append(("staging anchor close", error))
        if not published:
            try:
                _cleanup_staging_anchor(staging_anchor)
            except BaseException as error:
                cleanup_errors.append((staging_cleanup_label, error))
    if lock_stream is not None:
        try:
            lock_stream.close()
        except BaseException as error:
            cleanup_errors.append(("lock stream close", error))
    elif lock_descriptor is not None:
        try:
            os.close(lock_descriptor)
        except BaseException as error:
            cleanup_errors.append(("lock descriptor close", error))
    if lock_owned:
        try:
            anchor.unlink(lock_name)
        except BaseException as error:
            cleanup_errors.append(("lock cleanup", error))
    try:
        anchor.close()
    except BaseException as error:
        cleanup_errors.append(("parent anchor close", error))

    if cleanup_errors:
        failures = []
        if primary_error is not None:
            failures.append(
                "primary failure: "
                f"{type(primary_error).__name__}: {primary_error}"
            )
        failures.extend(
            f"{label}: {type(error).__name__}: {error}"
            for label, error in cleanup_errors
        )
        aggregate = RuntimeError("; ".join(failures))
        cause = primary_error or cleanup_errors[0][1]
        raise aggregate from cause
    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the pinned object allowlist into an isolated checkout."
        )
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help=(
            "print the pinned plan as JSON without filesystem or network "
            "access"
        ),
    )
    parser.add_argument(
        "--acknowledge-no-license",
        action="store_true",
        help=(
            "confirm that upstream has no explicit license and redistribution "
            "is forbidden"
        ),
    )
    arguments = parser.parse_args()

    if arguments.print_plan:
        print(json.dumps(build_fetch_plan(arguments.destination), indent=2))
        return 0
    if not arguments.acknowledge_no_license:
        parser.error("fetch requires --acknowledge-no-license")

    try:
        fetch(arguments.destination)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"fetch failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
