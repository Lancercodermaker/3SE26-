"""Explicitly fetch pinned upstream blobs into an isolated checkout."""

from __future__ import annotations

import argparse
import ctypes
import errno
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
        ["git", "init", str(checkout)],
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
    for command in _fetch_commands(staging):
        _run(command)


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


def _windows_handle_identity(handle: int) -> tuple[int, int, int]:
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
    return (
        information.volume_serial_number,
        information.file_index_high,
        information.file_index_low,
    )


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


def _rename_no_replace_windows(source: Path, destination: Path) -> None:
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    if move_file(str(source), str(destination), 0):
        return
    error_number = ctypes.get_last_error()
    if error_number in (80, 183):  # FILE_EXISTS, ALREADY_EXISTS
        raise FileExistsError(error_number, "destination exists", destination)
    raise ctypes.WinError(error_number)


def _publish_no_replace(
    anchor: _ParentAnchor, staging: Path, destination: Path
) -> None:
    anchor.verify_identity()
    if os.name == "nt":
        _rename_no_replace_windows(staging, destination)
    else:
        _rename_no_replace_posix(anchor, staging.name, destination.name)
        try:
            anchor.verify_identity()
        except Exception as identity_error:
            try:
                _remove_staging(anchor.child_path(destination.name))
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{identity_error}; rollback failed: {rollback_error}"
                ) from identity_error
            raise


def _remove_staging(staging: Path) -> None:
    def remove_readonly(remove, path, _error) -> None:
        os.chmod(path, stat.S_IWRITE)
        remove(path)

    shutil.rmtree(staging, onerror=remove_readonly)


def fetch(destination: Path) -> None:
    destination, parent_identity = _validate_destination(destination)
    if shutil.which("git") is None:
        raise RuntimeError("git is required for the explicit upstream fetch")

    lock_name = f".{destination.name}.fetch.lock"
    with _open_parent_anchor(destination.parent, parent_identity) as anchor:
        staging_name: str | None = None
        staging: Path | None = None
        lock_owned = False
        try:
            try:
                lock_descriptor = anchor.create_lock(lock_name)
            except FileExistsError as error:
                raise FileExistsError(
                    "concurrent fetch lock exists: "
                    f"{anchor.child_path(lock_name)}"
                ) from error
            lock_owned = True
            with os.fdopen(
                lock_descriptor, "w", encoding="utf-8"
            ) as lock_file:
                lock_file.write(f"pid={os.getpid()}\n")

            if anchor.entry_exists(destination.name):
                raise FileExistsError(
                    f"destination appeared during fetch: {destination}"
                )
            staging_name, staging = anchor.create_staging(
                f".{destination.name}.staging-"
            )
            _materialize_checkout(staging)
            _verify_checkout(staging)
            if anchor.entry_exists(destination.name):
                raise FileExistsError(
                    f"destination appeared during fetch: {destination}"
                )
            _publish_no_replace(anchor, staging, destination)
            staging_name = None
            staging = None
        finally:
            if staging_name is not None and anchor.entry_exists(staging_name):
                _remove_staging(anchor.child_path(staging_name))
            if lock_owned:
                anchor.unlink(lock_name)


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
