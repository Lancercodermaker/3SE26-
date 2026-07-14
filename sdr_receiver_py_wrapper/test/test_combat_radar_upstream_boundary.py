import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

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
