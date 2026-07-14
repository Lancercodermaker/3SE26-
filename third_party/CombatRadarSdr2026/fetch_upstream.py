"""Explicitly fetch the pinned, allowlisted upstream files into a separate checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


if __package__:
    from . import ALLOWED_UPSTREAM_FILES, UPSTREAM_COMMIT, UPSTREAM_REPOSITORY
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from third_party.CombatRadarSdr2026 import (  # type: ignore[no-redef]
        ALLOWED_UPSTREAM_FILES,
        UPSTREAM_COMMIT,
        UPSTREAM_REPOSITORY,
    )


def build_fetch_plan(destination: Path) -> dict[str, object]:
    destination = destination.resolve()
    git_at_destination = ["git", "-C", str(destination)]
    return {
        "repository": UPSTREAM_REPOSITORY,
        "commit": UPSTREAM_COMMIT,
        "files": list(ALLOWED_UPSTREAM_FILES),
        "commands": [
            ["git", "init", str(destination)],
            git_at_destination + ["remote", "add", "origin", UPSTREAM_REPOSITORY],
            git_at_destination + ["sparse-checkout", "init", "--no-cone"],
            git_at_destination
            + ["sparse-checkout", "set", "--no-cone"]
            + [f"/{path}" for path in ALLOWED_UPSTREAM_FILES],
            git_at_destination
            + ["fetch", "--depth=1", "origin", UPSTREAM_COMMIT],
            git_at_destination + ["checkout", "--detach", UPSTREAM_COMMIT],
        ],
    }


def fetch(destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    if shutil.which("git") is None:
        raise RuntimeError("git is required for the explicit upstream fetch")

    plan = build_fetch_plan(destination)
    for command in plan["commands"]:
        subprocess.run(command, check=True)

    completed = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    resolved_commit = completed.stdout.strip()
    if resolved_commit != UPSTREAM_COMMIT:
        raise RuntimeError(
            f"fetched commit {resolved_commit!r} does not match {UPSTREAM_COMMIT!r}"
        )

    checked_out_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(destination).parts
    }
    if checked_out_files != set(ALLOWED_UPSTREAM_FILES):
        raise RuntimeError(f"unexpected checked-out files: {sorted(checked_out_files)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the pinned allowlist into an isolated sparse checkout."
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print the pinned plan as JSON without filesystem or network access",
    )
    parser.add_argument(
        "--acknowledge-no-license",
        action="store_true",
        help="confirm that upstream has no explicit license and redistribution is forbidden",
    )
    arguments = parser.parse_args()

    if arguments.print_plan:
        print(json.dumps(build_fetch_plan(arguments.destination), indent=2))
        return 0
    if not arguments.acknowledge_no_license:
        parser.error("fetch requires --acknowledge-no-license")

    try:
        fetch(arguments.destination)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"fetch failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
