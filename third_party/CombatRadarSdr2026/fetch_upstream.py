"""Explicitly fetch pinned upstream blobs into an isolated checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


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


def _validate_destination(destination: Path) -> Path:
    destination = destination.resolve()
    if not destination.name:
        raise ValueError("destination must not be a filesystem root")
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"destination parent does not exist: {destination.parent}"
        )
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    return destination


def _remove_staging(staging: Path) -> None:
    def remove_readonly(remove, path, _error) -> None:
        os.chmod(path, stat.S_IWRITE)
        remove(path)

    shutil.rmtree(staging, onerror=remove_readonly)


def fetch(destination: Path) -> None:
    destination = _validate_destination(destination)
    if shutil.which("git") is None:
        raise RuntimeError("git is required for the explicit upstream fetch")

    lock_path = destination.parent / f".{destination.name}.fetch.lock"
    staging: Path | None = None
    lock_owned = False
    try:
        try:
            lock_descriptor = os.open(
                lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError as error:
            raise FileExistsError(
                f"concurrent fetch lock exists: {lock_path}"
            ) from error
        lock_owned = True
        with os.fdopen(lock_descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")

        if destination.exists() or destination.is_symlink():
            raise FileExistsError(
                f"destination appeared during fetch: {destination}"
            )
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-", dir=destination.parent
            )
        )
        _materialize_checkout(staging)
        _verify_checkout(staging)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(
                f"destination appeared during fetch: {destination}"
            )
        os.rename(staging, destination)
        staging = None
    finally:
        if staging is not None and staging.exists():
            _remove_staging(staging)
        if lock_owned:
            lock_path.unlink()


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
