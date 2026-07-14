import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY = REPOSITORY_ROOT / "third_party" / "CombatRadarSdr2026"
UPSTREAM_URL = "https://github.com/qianchuan-wys/CombatRadarSdr2026.git"
UPSTREAM_COMMIT = "13b13a68b7111a15163aedc97f1cb17722f45ad2"
UPSTREAM_BLOBS = {
    "phy.py": "b842cc16cb4b2b04874268839ebf705603e5f182",
    "protocol.py": "5195c9a7183c2087184f9e5de9cbeff96d044b0f",
    "radio_profiles.py": "b189816d6802e31a23c0ee567d6e7d72cf00fd5f",
    "parser/gnuradio_frame_parser.py": (
        "ed1b4ec02ff147be7d9af98fe2fdf7f9ff01ff97"
    ),
}
ALLOWED_UPSTREAM_FILES = tuple(UPSTREAM_BLOBS)
EXPECTED_BOUNDARY_FILES = {"UPSTREAM.md", "__init__.py", "fetch_upstream.py"}


def _load_module(path: Path, name: str):
    assert path.is_file(), f"missing module: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_boundary_metadata():
    return _load_module(BOUNDARY / "__init__.py", "combat_radar_upstream")


def _load_fetch_module():
    return _load_module(BOUNDARY / "fetch_upstream.py", "combat_radar_fetch")


def _visible_boundary_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }


def _assert_exact_boundary_files(root: Path) -> None:
    assert _visible_boundary_files(root) == EXPECTED_BOUNDARY_FILES


def _git(
    cwd: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _create_local_upstream(tmp_path: Path):
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Boundary Test")
    _git(repository, "config", "user.email", "boundary@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "config", "uploadpack.allowFilter", "true")
    _git(repository, "config", "uploadpack.allowAnySHA1InWant", "true")

    source_files = {
        **{path: f"allowlisted {path}\n" for path in ALLOWED_UPSTREAM_FILES},
        "README.md": "not allowlisted\n",
        "images/通信格式.jpg": "not allowlisted non-ascii path\n",
        "server_" + "comm.py": "not allowlisted server transport\n",
    }
    for relative_path, content in source_files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "fixture")
    commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    blobs = {
        path: _git(repository, "rev-parse", f"HEAD:{path}").stdout.strip()
        for path in source_files
    }
    return repository, commit, blobs


def _has_local_blob(repository: Path, blob: str) -> bool:
    environment = os.environ.copy()
    environment["GIT_NO_LAZY_FETCH"] = "1"
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    local_blobs = {
        object_id
        for line in completed.stdout.splitlines()
        for object_id, object_type in [line.split()]
        if object_type == "blob"
    }
    return blob in local_blobs


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_upstream_metadata_and_notice_share_pinned_blob_map():
    metadata = _load_boundary_metadata()

    assert metadata.UPSTREAM_REPOSITORY == UPSTREAM_URL
    assert metadata.UPSTREAM_COMMIT == UPSTREAM_COMMIT
    assert hasattr(
        metadata, "UPSTREAM_BLOBS"
    ), "executable blob pins are missing"
    assert metadata.UPSTREAM_BLOBS == UPSTREAM_BLOBS
    assert metadata.ALLOWED_UPSTREAM_FILES == ALLOWED_UPSTREAM_FILES
    assert metadata.LICENSE_STATUS == "NO_EXPLICIT_LICENSE"

    upstream_notice = (BOUNDARY / "UPSTREAM.md").read_text(encoding="utf-8")
    assert UPSTREAM_URL in upstream_notice
    assert UPSTREAM_COMMIT in upstream_notice
    assert "server_" + "comm.py" in upstream_notice
    assert "written permission" in upstream_notice.lower()
    for path, blob in UPSTREAM_BLOBS.items():
        assert f"| `{path}` | `{blob}` |" in upstream_notice


def test_boundary_contains_only_project_authored_files():
    _assert_exact_boundary_files(BOUNDARY)


@pytest.mark.parametrize(
    "unexpected", ("server_" + "comm.py", "phy.py", "other.txt")
)
def test_boundary_guard_rejects_injected_files(
    tmp_path: Path, unexpected: str
):
    for relative_path in EXPECTED_BOUNDARY_FILES | {unexpected}:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_exact_boundary_files(tmp_path)


def test_complete_production_python_boundary_has_no_server_or_tcp_dependency():
    production_sources = list(
        (REPOSITORY_ROOT / "sdr_receiver_py_wrapper").rglob("*.py")
    ) + list(BOUNDARY.rglob("*.py"))
    production_sources = [
        path
        for path in production_sources
        if "__pycache__" not in path.parts
        and "test" not in path.relative_to(REPOSITORY_ROOT).parts
    ]
    forbidden_markers = (
        "RadarServer" + "Comm",
        "server_" + "comm",
        "socket." + "send",
    )

    assert production_sources
    for source_path in production_sources:
        source = source_path.read_text(encoding="utf-8")
        assert not any(
            marker in source for marker in forbidden_markers
        ), source_path

    requirements_path = (
        REPOSITORY_ROOT / "sdr_receiver_py_wrapper" / "requirements.txt"
    )
    active_requirements = [
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(
        token in requirement.lower()
        for requirement in active_requirements
        for token in ("git+", "github.com", "combatradarsdr2026")
    )


def test_fixed_fetch_plan_is_offline_parseable(tmp_path: Path):
    fetch_script = BOUNDARY / "fetch_upstream.py"
    destination = tmp_path / "upstream"
    environment = os.environ.copy()
    environment["PATH"] = ""

    completed = subprocess.run(
        [sys.executable, str(fetch_script), "--print-plan", str(destination)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["repository"] == UPSTREAM_URL
    assert plan["commit"] == UPSTREAM_COMMIT
    assert "blobs" in plan, "offline plan omits executable blob pins"
    assert plan["blobs"] == UPSTREAM_BLOBS
    assert any("--filter=blob:none" in command for command in plan["commands"])
    assert all(isinstance(command, list) for command in plan["commands"])
    assert not destination.exists()


def test_fetch_sanitizes_hostile_git_environment_and_preserves_victim(
    tmp_path: Path, monkeypatch
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    _git(victim, "init", "--quiet")
    _git(victim, "config", "user.name", "Victim Test")
    _git(victim, "config", "user.email", "victim@example.invalid")
    tracked = victim / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    _git(victim, "add", "tracked.txt")
    _git(victim, "commit", "--quiet", "-m", "victim baseline")
    tracked.write_text("dirty working tree\n", encoding="utf-8")
    staged = victim / "staged.txt"
    staged.write_text("staged state\n", encoding="utf-8")
    _git(victim, "add", "staged.txt")
    _git(victim, "config", "boundary.sentinel", "preserve")
    object_source = victim / "loose-object.txt"
    object_source.write_text("preserve object\n", encoding="utf-8")
    _git(victim, "hash-object", "-w", "loose-object.txt")
    victim_before = _snapshot_files(victim)

    hostile_global = tmp_path / "hostile-global.gitconfig"
    hostile_global.write_text(
        "[core]\n\thooksPath = hostile-hooks\n", encoding="utf-8"
    )
    hostile_template = tmp_path / "hostile-template"
    hostile_template.mkdir()
    hostile_hooks = tmp_path / "hostile-hooks"
    hostile_hooks.mkdir()
    hostile_environment = {
        "GIT_DIR": str(victim / ".git"),
        "GIT_WORK_TREE": str(victim),
        "GIT_INDEX_FILE": str(victim / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(victim / ".git" / "objects"),
        "GIT_COMMON_DIR": str(victim / ".git"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(victim / ".git" / "objects"),
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_TEMPLATE_DIR": str(hostile_template),
        "GIT_CONFIG_GLOBAL": str(hostile_global),
        "GIT_CONFIG_SYSTEM": str(hostile_global),
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hostile_hooks),
        "GIT_SSH_COMMAND": "hostile-ssh-command",
        "GIT_SSH_VARIANT": "ssh",
        "GIT_EXEC_PATH": str(tmp_path / "hostile-git-exec"),
        "GIT_TERMINAL_PROMPT": "1",
    }
    for name, value in hostile_environment.items():
        monkeypatch.setenv(name, value)

    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module,
        "UPSTREAM_BLOBS",
        {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES},
        raising=False,
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )
    real_run = fetch_module.subprocess.run
    git_environments = []

    def capture_git_environment(command, **kwargs):
        if command and command[0] == "git":
            git_environments.append(dict(kwargs["env"]))
        return real_run(command, **kwargs)

    monkeypatch.setattr(
        fetch_module.subprocess, "run", capture_git_environment
    )

    fetch_module.fetch(destination)

    safe_git_environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert git_environments
    for environment in git_environments:
        git_environment = {
            name: value
            for name, value in environment.items()
            if name.startswith("GIT_")
        }
        if "GIT_NO_LAZY_FETCH" in git_environment:
            assert git_environment.pop("GIT_NO_LAZY_FETCH") == "1"
        assert git_environment == safe_git_environment
    commands = fetch_module._fetch_commands(destination)
    assert "--object-format=sha1" in commands[0]
    assert "--template=" in commands[0]
    assert any("core.hooksPath" in command for command in commands)
    assert _snapshot_files(victim) == victim_before


def test_fetch_requires_explicit_license_acknowledgement(tmp_path: Path):
    destination = tmp_path / "upstream"
    environment = os.environ.copy()
    environment["PATH"] = ""

    completed = subprocess.run(
        [
            sys.executable,
            str(BOUNDARY / "fetch_upstream.py"),
            str(destination),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "--acknowledge-no-license" in completed.stderr
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_partial_fetch_materializes_only_allowlisted_blobs(
    tmp_path: Path, monkeypatch
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module,
        "UPSTREAM_BLOBS",
        {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES},
        raising=False,
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )

    fetch_module.fetch(destination)

    materialized = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(destination).parts
    }
    assert materialized == set(ALLOWED_UPSTREAM_FILES)
    assert all(
        _has_local_blob(destination, blobs[path])
        for path in ALLOWED_UPSTREAM_FILES
    )
    assert not _has_local_blob(destination, blobs["README.md"])
    assert not _has_local_blob(destination, blobs["server_" + "comm.py"])


@pytest.mark.parametrize("extra_kind", ["file", "empty-directory"])
def test_fetch_rejects_extra_entry_added_after_flatten(
    tmp_path: Path, monkeypatch, extra_kind: str
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module,
        "UPSTREAM_BLOBS",
        {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES},
        raising=False,
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )
    real_verify = fetch_module._verify_checkout

    def verify_then_add_extra(staging: Path) -> None:
        real_verify(staging)
        if extra_kind == "file":
            (staging / "unexpected.txt").write_text(
                "must not publish", encoding="utf-8"
            )
        else:
            (staging / "unexpected-empty-dir").mkdir()

    monkeypatch.setattr(
        fetch_module, "_verify_checkout", verify_then_add_extra
    )

    with pytest.raises(RuntimeError, match="unexpected|allowlist|final"):
        fetch_module.fetch(destination)

    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


@pytest.mark.parametrize("extra_kind", ["file", "empty-directory"])
def test_fetch_rolls_back_extra_entry_added_after_publish(
    tmp_path: Path, monkeypatch, extra_kind: str
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module,
        "UPSTREAM_BLOBS",
        {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES},
        raising=False,
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )
    real_publish = fetch_module._publish_no_replace

    def publish_then_add_extra(anchor, staging, target: Path) -> None:
        real_publish(anchor, staging, target)
        if extra_kind == "file":
            (target / "unexpected.txt").write_text(
                "must roll back", encoding="utf-8"
            )
        else:
            (target / "unexpected-empty-dir").mkdir()

    monkeypatch.setattr(
        fetch_module, "_publish_no_replace", publish_then_add_extra
    )

    with pytest.raises(RuntimeError, match="unexpected|allowlist|final"):
        fetch_module.fetch(destination)

    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO regression")
def test_final_tree_check_never_blocks_on_regular_file_replaced_by_fifo(
    tmp_path: Path, monkeypatch
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    candidate = checkout / "phy.py"
    candidate.write_text("regular", encoding="utf-8")
    stale_information = candidate.stat()
    candidate.unlink()
    os.mkfifo(candidate)
    descriptor = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    fetch_module = _load_fetch_module()
    real_stat = fetch_module.os.stat
    errors = []
    finished = threading.Event()

    def stale_stat(path, *arguments, **keywords):
        if path == candidate.name and keywords.get("dir_fd") == descriptor:
            return stale_information
        return real_stat(path, *arguments, **keywords)

    def collect() -> None:
        try:
            fetch_module._collect_posix_materialized_blobs(descriptor)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    monkeypatch.setattr(fetch_module.os, "stat", stale_stat)
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    completed_without_writer = finished.wait(0.5)
    if not completed_without_writer:
        writer = os.open(candidate, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer)
        assert finished.wait(2.0), (
            "collector remained blocked after FIFO release"
        )
    thread.join(timeout=2.0)
    os.close(descriptor)

    assert completed_without_writer, (
        "collector blocked opening a replaced FIFO"
    )
    assert errors
    assert isinstance(errors[0], RuntimeError)


def test_final_tree_check_rejects_oversized_sparse_file(
    tmp_path: Path
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    candidate = checkout / "phy.py"
    with candidate.open("wb") as file:
        file.truncate(17 * 1024 * 1024)
    fetch_module = _load_fetch_module()

    with pytest.raises(RuntimeError, match="large|size|limit"):
        if os.name == "nt":
            fetch_module._collect_windows_materialized_blobs(checkout)
        else:
            descriptor = os.open(
                checkout, os.O_RDONLY | os.O_DIRECTORY
            )
            try:
                fetch_module._collect_posix_materialized_blobs(descriptor)
            finally:
                os.close(descriptor)


def test_staging_entry_swap_never_touches_victim_or_publishes(
    tmp_path: Path, monkeypatch
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "owned.txt").write_text("preserve\n", encoding="utf-8")
    victim_before = _snapshot_files(victim)
    orphan = tmp_path / "orphaned-staging"
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module,
        "UPSTREAM_BLOBS",
        {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES},
        raising=False,
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )
    real_create_staging = fetch_module._create_staging_anchor
    real_materialize = fetch_module._materialize_checkout
    created = {}

    def record_staging(parent, prefix: str):
        anchor = real_create_staging(parent, prefix)
        created.update(anchor=parent, name=anchor.name)
        return anchor

    def swap_staging_entry(stable_staging: Path) -> None:
        original = created["anchor"].child_path(created["name"])
        try:
            original.rename(orphan)
        except OSError as error:
            if os.name == "nt" and error.winerror == 32:
                raise RuntimeError("staging swap blocked") from error
            raise
        if os.name == "nt":
            completed = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(original),
                    str(victim),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert completed.returncode == 0, completed.stderr
        else:
            original.symlink_to(victim, target_is_directory=True)
        real_materialize(stable_staging)

    monkeypatch.setattr(
        fetch_module, "_create_staging_anchor", record_staging
    )
    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", swap_staging_entry
    )

    with pytest.raises(RuntimeError, match="staging|swap|identity|reparse"):
        fetch_module.fetch(destination)

    assert _snapshot_files(victim) == victim_before
    assert not os.path.lexists(destination)
    assert not orphan.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_blob_pin_mismatch_fails_without_publishing_destination(
    tmp_path: Path, monkeypatch
):
    source, commit, blobs = _create_local_upstream(tmp_path)
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    wrong_blobs = {path: blobs[path] for path in ALLOWED_UPSTREAM_FILES}
    wrong_blobs["phy.py"] = "0" * 40
    monkeypatch.setattr(fetch_module, "UPSTREAM_REPOSITORY", source.as_uri())
    monkeypatch.setattr(fetch_module, "UPSTREAM_COMMIT", commit)
    monkeypatch.setattr(
        fetch_module, "UPSTREAM_BLOBS", wrong_blobs, raising=False
    )
    monkeypatch.setattr(
        fetch_module, "ALLOWED_UPSTREAM_FILES", ALLOWED_UPSTREAM_FILES
    )

    with pytest.raises(RuntimeError, match="blob"):
        fetch_module.fetch(destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))
    assert not (tmp_path / ".checkout.fetch.lock").exists()


def test_fetch_failure_is_atomic_and_retryable(tmp_path: Path, monkeypatch):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    assert hasattr(fetch_module, "_materialize_checkout")
    assert hasattr(fetch_module, "_verify_checkout")

    def materialize(staging: Path) -> None:
        (staging / "marker").write_text("complete", encoding="utf-8")

    def reject(_staging: Path) -> None:
        raise RuntimeError("injected verification failure")

    monkeypatch.setattr(fetch_module, "_materialize_checkout", materialize)
    monkeypatch.setattr(fetch_module, "_verify_checkout", reject)
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")

    with pytest.raises(RuntimeError, match="injected verification failure"):
        fetch_module.fetch(destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))
    assert not (tmp_path / ".checkout.fetch.lock").exists()

    monkeypatch.setattr(
        fetch_module, "_verify_checkout", lambda _staging: None
    )
    monkeypatch.setattr(
        fetch_module,
        "_verify_final_materialized_tree",
        lambda _staging: None,
    )
    fetch_module.fetch(destination)
    assert (destination / "marker").read_text(encoding="utf-8") == "complete"


def test_fetch_preserves_existing_destination_and_rejects_concurrent_lock(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    destination.mkdir()
    marker = destination / "owned.txt"
    marker.write_text("do not replace", encoding="utf-8")
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")

    with pytest.raises((FileExistsError, ValueError)):
        fetch_module.fetch(destination)
    assert marker.read_text(encoding="utf-8") == "do not replace"

    other_destination = tmp_path / "other"
    lock_path = tmp_path / ".other.fetch.lock"
    lock_path.write_text("concurrent", encoding="utf-8")
    with pytest.raises(FileExistsError, match="lock|concurrent"):
        fetch_module.fetch(other_destination)
    assert lock_path.read_text(encoding="utf-8") == "concurrent"
    assert not other_destination.exists()
    assert not list(tmp_path.glob(".other.staging-*"))


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")


def _stub_fetch_io(fetch_module, monkeypatch) -> None:
    def materialize(staging: Path) -> None:
        (staging / "marker").write_text("complete", encoding="utf-8")

    monkeypatch.setattr(fetch_module, "_materialize_checkout", materialize)
    monkeypatch.setattr(
        fetch_module, "_verify_checkout", lambda _staging: None
    )
    monkeypatch.setattr(
        fetch_module,
        "_verify_final_materialized_tree",
        lambda _staging: None,
    )
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")


def test_fetch_rejects_dangling_destination_symlink_without_side_effects(
    tmp_path: Path, monkeypatch
):
    target = tmp_path / "unspecified-target"
    link = tmp_path / "requested-link"
    _symlink_or_skip(link, target)
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)

    with pytest.raises(FileExistsError, match="exists|symlink"):
        fetch_module.fetch(link)

    assert link.is_symlink()
    assert not target.exists()
    assert not (tmp_path / ".requested-link.fetch.lock").exists()
    assert not list(tmp_path.glob(".requested-link.staging-*"))
    assert not (tmp_path / ".unspecified-target.fetch.lock").exists()
    assert not list(tmp_path.glob(".unspecified-target.staging-*"))


def test_fetch_rejects_symlinked_parent_without_writing_through_it(
    tmp_path: Path, monkeypatch
):
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    _symlink_or_skip(linked_parent, actual_parent)
    destination = linked_parent / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)

    with pytest.raises(ValueError, match="symlink"):
        fetch_module.fetch(destination)

    assert linked_parent.is_symlink()
    assert list(actual_parent.iterdir()) == []
    assert not (actual_parent / ".checkout.fetch.lock").exists()
    assert not list(actual_parent.glob(".checkout.staging-*"))


def test_fetch_accepts_plain_unicode_destination(tmp_path: Path, monkeypatch):
    parent = tmp_path / "接收目录"
    parent.mkdir()
    destination = parent / "上游解调器"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)

    fetch_module.fetch(destination)

    assert (destination / "marker").read_text(encoding="utf-8") == "complete"
    assert not (parent / ".上游解调器.fetch.lock").exists()
    assert not list(parent.glob(".上游解调器.staging-*"))


def test_fetch_rejects_parent_traversal_without_side_effects(
    tmp_path: Path, monkeypatch
):
    nested_parent = tmp_path / "parent" / "nested"
    nested_parent.mkdir(parents=True)
    destination = nested_parent / ".." / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)

    with pytest.raises(ValueError, match="traversal"):
        fetch_module.fetch(destination)

    resolved_target = tmp_path / "parent" / "checkout"
    assert not resolved_target.exists()
    assert not (resolved_target.parent / ".checkout.fetch.lock").exists()
    assert not list(resolved_target.parent.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_fetch_rejects_windows_junction_parent_without_writing_through_it(
    tmp_path: Path, monkeypatch
):
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir()
    junction_parent = tmp_path / "junction-parent"
    completed = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction_parent),
            str(actual_parent),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not junction_parent.is_symlink()

    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    with pytest.raises(ValueError, match="reparse"):
        fetch_module.fetch(junction_parent / "checkout")

    assert list(actual_parent.iterdir()) == []
    assert not (actual_parent / ".checkout.fetch.lock").exists()
    assert not list(actual_parent.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent identity regression")
def test_parent_swap_at_anchor_open_rejects_replacement_parent(
    tmp_path: Path, monkeypatch
):
    requested_parent = tmp_path / "requested-parent"
    requested_parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    destination = requested_parent / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_open_anchor = fetch_module._open_parent_anchor

    def swap_then_open(parent: Path, *arguments):
        parent.rename(moved_parent)
        parent.mkdir()
        return real_open_anchor(parent, *arguments)

    monkeypatch.setattr(fetch_module, "_open_parent_anchor", swap_then_open)

    with pytest.raises(RuntimeError, match="identity"):
        fetch_module.fetch(destination)

    assert list(requested_parent.iterdir()) == []
    assert list(moved_parent.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent identity regression")
def test_parent_swap_after_validation_never_writes_replacement_parent(
    tmp_path: Path, monkeypatch
):
    requested_parent = tmp_path / "requested-parent"
    requested_parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    replacement_parent = tmp_path / "replacement-parent"
    replacement_parent.mkdir()
    destination = requested_parent / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")

    def swap_parent(staging: Path) -> None:
        (staging / "marker").write_text("complete", encoding="utf-8")
        requested_parent.rename(moved_parent)
        requested_parent.symlink_to(
            replacement_parent, target_is_directory=True
        )

    monkeypatch.setattr(fetch_module, "_materialize_checkout", swap_parent)
    monkeypatch.setattr(
        fetch_module, "_verify_checkout", lambda _staging: None
    )
    monkeypatch.setattr(
        fetch_module,
        "_verify_final_materialized_tree",
        lambda _staging: None,
    )

    with pytest.raises(RuntimeError, match="identity"):
        fetch_module.fetch(destination)

    assert requested_parent.is_symlink()
    assert list(replacement_parent.iterdir()) == []
    assert list(moved_parent.iterdir()) == []
    assert not (moved_parent / ".checkout.fetch.lock").exists()
    assert not list(moved_parent.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX renameat2 regression")
def test_atomic_publish_never_replaces_racing_empty_destination(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    assert hasattr(fetch_module, "_publish_no_replace")
    _stub_fetch_io(fetch_module, monkeypatch)
    real_publish = fetch_module._publish_no_replace
    inserted_inode = []

    def insert_then_publish(anchor, staging: Path, target: Path) -> None:
        target.mkdir()
        inserted_inode.append(target.stat().st_ino)
        real_publish(anchor, staging, target)

    monkeypatch.setattr(
        fetch_module, "_publish_no_replace", insert_then_publish
    )

    with pytest.raises(FileExistsError):
        fetch_module.fetch(destination)

    assert destination.is_dir()
    assert destination.stat().st_ino == inserted_inode[0]
    assert list(destination.iterdir()) == []
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX publish identity regression"
)
def test_posix_publish_rejects_staging_swap_at_rename_gap(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    orphan = tmp_path / "orphaned-staging"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "owned.txt").write_text("preserve", encoding="utf-8")
    victim_before = _snapshot_files(victim)
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_rename = fetch_module._rename_no_replace_posix

    def swap_then_rename(anchor, source_name: str, target_name: str) -> None:
        source = tmp_path / source_name
        source.rename(orphan)
        source.symlink_to(victim, target_is_directory=True)
        real_rename(anchor, source_name, target_name)

    monkeypatch.setattr(
        fetch_module, "_rename_no_replace_posix", swap_then_rename
    )

    with pytest.raises(RuntimeError, match="staging|identity|reparse"):
        fetch_module.fetch(destination)

    assert _snapshot_files(victim) == victim_before
    assert not os.path.lexists(destination)
    assert not orphan.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX regular replacement regression"
)
def test_posix_publish_never_deletes_regular_directory_replacement(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    orphan = tmp_path / "orphaned-staging"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "owned.txt").write_text("preserve", encoding="utf-8")
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_rename = fetch_module._rename_no_replace_posix

    def replace_with_victim(
        anchor, source_name: str, target_name: str
    ) -> None:
        source = tmp_path / source_name
        source.rename(orphan)
        victim.rename(source)
        real_rename(anchor, source_name, target_name)

    monkeypatch.setattr(
        fetch_module, "_rename_no_replace_posix", replace_with_victim
    )

    with pytest.raises(RuntimeError, match="staging|identity|rollback"):
        fetch_module.fetch(destination)

    assert (
        destination / "owned.txt"
    ).read_text(encoding="utf-8") == "preserve"
    assert not orphan.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_staging_anchor_rejects_regular_directory_swap_before_open(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "owned.txt").write_text("preserve", encoding="utf-8")
    orphan = tmp_path / "orphaned-staging"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_open = fetch_module._open_staging_anchor

    def swap_then_open(parent, name: str, *arguments, **keywords):
        source = parent.child_path(name)
        source.rename(orphan)
        victim.rename(source)
        return real_open(parent, name, *arguments, **keywords)

    monkeypatch.setattr(
        fetch_module, "_open_staging_anchor", swap_then_open
    )

    with pytest.raises(RuntimeError, match="staging|identity"):
        fetch_module.fetch(destination)

    preserved = list(tmp_path.rglob("owned.txt"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "preserve"
    assert not destination.exists()
    assert not orphan.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()


def test_staging_anchor_open_failure_cleans_created_directory(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)

    def fail_open(*_arguments, **_keywords):
        raise OSError("injected staging anchor open failure")

    monkeypatch.setattr(fetch_module, "_open_staging_anchor", fail_open)

    with pytest.raises(OSError, match="injected staging anchor open failure"):
        fetch_module.fetch(destination)

    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_expected_identity_cleanup_never_recursively_deletes_late_swap(
    tmp_path: Path, monkeypatch
):
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "trusted.txt").write_text("trusted", encoding="utf-8")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "owned.txt").write_text("preserve", encoding="utf-8")
    orphan = tmp_path / "trusted-orphan"
    fetch_module = _load_fetch_module()
    parent = fetch_module._open_parent_anchor(
        tmp_path, fetch_module._path_identity(tmp_path)
    )
    expected_identity = parent.entry_identity(expected.name)
    real_entry_identity = parent.entry_identity
    swapped = False

    def identity_then_swap(name: str):
        nonlocal swapped
        identity = real_entry_identity(name)
        if name == expected.name and not swapped:
            expected.rename(orphan)
            victim.rename(expected)
            swapped = True
        return identity

    monkeypatch.setattr(parent, "entry_identity", identity_then_swap)
    try:
        with pytest.raises(OSError):
            fetch_module._remove_entry_if_expected(
                parent, expected.name, expected_identity
            )
    finally:
        parent.close()

    assert (expected / "owned.txt").read_text(encoding="utf-8") == "preserve"
    assert (orphan / "trusted.txt").read_text(encoding="utf-8") == "trusted"


@pytest.mark.skipif(
    os.name != "nt", reason="Windows handle publish regression"
)
def test_windows_publish_renames_the_anchored_staging_handle(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    assert hasattr(fetch_module, "_windows_rename_handle_no_replace")
    assert not hasattr(fetch_module, "_rename_no_replace_windows")
    _stub_fetch_io(fetch_module, monkeypatch)

    fetch_module.fetch(destination)

    assert (destination / "marker").read_text(encoding="utf-8") == "complete"
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows handle regression")
def test_windows_parent_anchor_blocks_rename_at_publish_entry(
    tmp_path: Path, monkeypatch
):
    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    destination = parent / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_publish = fetch_module._publish_no_replace
    rename_errors = []

    def attempt_parent_rename(anchor, staging: Path, target: Path) -> None:
        try:
            parent.rename(moved_parent)
        except OSError as error:
            rename_errors.append(error.winerror)
        else:
            rename_errors.append(None)
        real_publish(anchor, staging, target)

    monkeypatch.setattr(
        fetch_module, "_publish_no_replace", attempt_parent_rename
    )

    fetch_module.fetch(destination)

    assert rename_errors == [32]
    assert (destination / "marker").read_text(encoding="utf-8") == "complete"
    assert parent.is_dir()
    assert not moved_parent.exists()
    assert not (parent / ".checkout.fetch.lock").exists()
    assert not list(parent.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX rollback regression")
def test_posix_publish_rolls_back_if_visible_parent_changes(
    tmp_path: Path, monkeypatch
):
    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    sentinel = replacement / "owned.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    replacement_inode = replacement.stat().st_ino
    destination = parent / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_rename = fetch_module._rename_no_replace_posix

    def swap_then_rename(anchor, source_name: str, target_name: str) -> None:
        parent.rename(moved_parent)
        replacement.rename(parent)
        real_rename(anchor, source_name, target_name)

    monkeypatch.setattr(
        fetch_module, "_rename_no_replace_posix", swap_then_rename
    )

    with pytest.raises(RuntimeError, match="identity"):
        fetch_module.fetch(destination)

    assert parent.stat().st_ino == replacement_inode
    assert (parent / sentinel.name).read_text(encoding="utf-8") == "preserve"
    assert not destination.exists()
    assert list(moved_parent.iterdir()) == []
    assert not (moved_parent / ".checkout.fetch.lock").exists()
    assert not list(moved_parent.glob(".checkout.staging-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX rollback regression")
def test_posix_publish_reports_identity_and_rollback_failures(
    tmp_path: Path, monkeypatch
):
    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    destination = parent / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    real_rename = fetch_module._rename_no_replace_posix

    def swap_then_rename(anchor, source_name: str, target_name: str) -> None:
        parent.rename(moved_parent)
        replacement.rename(parent)
        real_rename(anchor, source_name, target_name)

    def fail_rollback(_staging) -> None:
        raise OSError("injected rollback failure")

    monkeypatch.setattr(
        fetch_module, "_rename_no_replace_posix", swap_then_rename
    )
    monkeypatch.setattr(fetch_module, "_empty_staging_anchor", fail_rollback)

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "identity" in message
    assert "rollback" in message
    assert "injected rollback failure" in message
    assert not (moved_parent / ".checkout.fetch.lock").exists()
    assert not list(moved_parent.glob(".checkout.staging-*"))


def test_parent_anchor_closes_after_materialization_failure(
    tmp_path: Path, monkeypatch
):
    parent = tmp_path / "parent"
    parent.mkdir()
    destination = parent / "checkout"
    fetch_module = _load_fetch_module()
    assert hasattr(fetch_module, "_open_parent_anchor")
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")

    primary_failure = RuntimeError("injected materialization failure")

    def fail_materialization(_staging: Path) -> None:
        raise primary_failure

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    descriptors_before = None
    proc_fds = Path("/proc/self/fd")
    if proc_fds.is_dir():
        descriptors_before = set(os.listdir(proc_fds))

    with pytest.raises(
        RuntimeError, match="injected materialization failure"
    ) as raised:
        fetch_module.fetch(destination)

    assert raised.value is primary_failure
    if descriptors_before is not None:
        assert set(os.listdir(proc_fds)) == descriptors_before
    renamed_parent = tmp_path / "renamed-parent"
    parent.rename(renamed_parent)
    assert list(renamed_parent.iterdir()) == []


def test_fetch_reports_primary_and_missing_lock_cleanup_failure(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")
    real_unlink = fetch_module._ParentAnchor.unlink

    def fail_materialization(staging: Path) -> None:
        (staging / "marker").write_text("partial", encoding="utf-8")
        raise primary_failure

    def remove_lock_then_report_missing(anchor, name: str) -> None:
        real_unlink(anchor, name)
        raise FileNotFoundError("injected missing lock")

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    monkeypatch.setattr(
        fetch_module._ParentAnchor,
        "unlink",
        remove_lock_then_report_missing,
    )

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "lock cleanup" in message
    assert "FileNotFoundError" in message
    assert raised.value.__cause__ is primary_failure
    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_fetch_cleans_lock_when_staging_cleanup_fails(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")

    def fail_materialization(staging: Path) -> None:
        (staging / "marker").write_text("partial", encoding="utf-8")
        raise primary_failure

    def fail_staging_cleanup(_staging) -> None:
        raise OSError("injected staging cleanup failure")

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    monkeypatch.setattr(
        fetch_module, "_empty_staging_anchor", fail_staging_cleanup
    )

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "staging cleanup" in message
    assert "injected staging cleanup failure" in message
    assert raised.value.__cause__ is primary_failure
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert len(list(tmp_path.glob(".checkout.staging-*"))) == 1


def test_fetch_reports_primary_and_both_cleanup_failures(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")
    real_unlink = fetch_module._ParentAnchor.unlink

    def fail_materialization(staging: Path) -> None:
        (staging / "marker").write_text("partial", encoding="utf-8")
        raise primary_failure

    def remove_lock_then_report_missing(anchor, name: str) -> None:
        real_unlink(anchor, name)
        raise FileNotFoundError("injected missing lock")

    def fail_staging_cleanup(_staging) -> None:
        raise OSError("injected staging cleanup failure")

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    monkeypatch.setattr(
        fetch_module, "_empty_staging_anchor", fail_staging_cleanup
    )
    monkeypatch.setattr(
        fetch_module._ParentAnchor,
        "unlink",
        remove_lock_then_report_missing,
    )

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "injected staging cleanup failure" in message
    assert "FileNotFoundError" in message
    assert message.index("staging cleanup") < message.index("lock cleanup")
    assert raised.value.__cause__ is primary_failure
    assert not (tmp_path / ".checkout.fetch.lock").exists()


def test_fetch_reports_cleanup_failure_after_successful_publish(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    cleanup_failure = OSError("injected lock cleanup failure")
    real_unlink = fetch_module._ParentAnchor.unlink

    def remove_lock_then_fail(anchor, name: str) -> None:
        real_unlink(anchor, name)
        raise cleanup_failure

    monkeypatch.setattr(
        fetch_module._ParentAnchor, "unlink", remove_lock_then_fail
    )

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "lock cleanup" in message
    assert "injected lock cleanup failure" in message
    assert raised.value.__cause__ is cleanup_failure
    assert (destination / "marker").read_text(encoding="utf-8") == "complete"
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_fetch_reports_primary_and_parent_anchor_close_failure(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")
    real_close = fetch_module._ParentAnchor.close

    def fail_materialization(_staging: Path) -> None:
        raise primary_failure

    def close_then_fail(anchor) -> None:
        real_close(anchor)
        raise OSError("injected parent anchor close failure")

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    monkeypatch.setattr(fetch_module._ParentAnchor, "close", close_then_fail)

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "parent anchor close" in message
    assert "injected parent anchor close failure" in message
    assert raised.value.__cause__ is primary_failure
    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_fetch_reports_parent_anchor_close_failure_after_publish(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    _stub_fetch_io(fetch_module, monkeypatch)
    close_failure = OSError("injected parent anchor close failure")
    real_close = fetch_module._ParentAnchor.close

    def close_then_fail(anchor) -> None:
        real_close(anchor)
        raise close_failure

    monkeypatch.setattr(fetch_module._ParentAnchor, "close", close_then_fail)

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "parent anchor close" in message
    assert "injected parent anchor close failure" in message
    assert raised.value.__cause__ is close_failure
    assert (destination / "marker").read_text(encoding="utf-8") == "complete"
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_fetch_reports_primary_and_staging_anchor_close_failure(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    assert hasattr(fetch_module, "_StagingAnchor")
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")
    real_close = fetch_module._StagingAnchor.close

    def fail_materialization(_staging: Path) -> None:
        raise primary_failure

    def close_then_fail(anchor) -> None:
        real_close(anchor)
        raise OSError("injected staging anchor close failure")

    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )
    monkeypatch.setattr(fetch_module._StagingAnchor, "close", close_then_fail)

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "staging anchor close" in message
    assert "injected staging anchor close failure" in message
    assert raised.value.__cause__ is primary_failure
    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))


def test_fetch_reports_primary_and_lock_stream_close_failure(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "checkout"
    fetch_module = _load_fetch_module()
    monkeypatch.setattr(fetch_module.shutil, "which", lambda _name: "git")
    primary_failure = RuntimeError("injected primary failure")
    real_fdopen = fetch_module.os.fdopen

    class DeferredCloseFailure:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def write(self, value):
            return self.stream.write(value)

        def flush(self):
            return self.stream.flush()

        def close(self):
            self.stream.close()
            raise OSError("injected lock stream close failure")

    def deferred_close_fdopen(*arguments, **keywords):
        return DeferredCloseFailure(real_fdopen(*arguments, **keywords))

    def fail_materialization(_staging: Path) -> None:
        raise primary_failure

    monkeypatch.setattr(fetch_module.os, "fdopen", deferred_close_fdopen)
    monkeypatch.setattr(
        fetch_module, "_materialize_checkout", fail_materialization
    )

    with pytest.raises(RuntimeError) as raised:
        fetch_module.fetch(destination)

    message = str(raised.value)
    assert "injected primary failure" in message
    assert "lock stream close" in message
    assert "injected lock stream close failure" in message
    assert raised.value.__cause__ is primary_failure
    assert not destination.exists()
    assert not (tmp_path / ".checkout.fetch.lock").exists()
    assert not list(tmp_path.glob(".checkout.staging-*"))
