import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY = REPOSITORY_ROOT / "third_party" / "CombatRadarSdr2026"
UPSTREAM_URL = "https://github.com/qianchuan-wys/CombatRadarSdr2026.git"
UPSTREAM_COMMIT = "13b13a68b7111a15163aedc97f1cb17722f45ad2"
ALLOWED_UPSTREAM_FILES = (
    "phy.py",
    "protocol.py",
    "radio_profiles.py",
    "parser/gnuradio_frame_parser.py",
)


def _load_boundary_metadata():
    module_path = BOUNDARY / "__init__.py"
    assert module_path.is_file(), "the local upstream boundary is missing"
    spec = importlib.util.spec_from_file_location("combat_radar_upstream", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upstream_metadata_is_pinned_without_vendored_source():
    metadata = _load_boundary_metadata()

    assert metadata.UPSTREAM_REPOSITORY == UPSTREAM_URL
    assert metadata.UPSTREAM_COMMIT == UPSTREAM_COMMIT
    assert metadata.ALLOWED_UPSTREAM_FILES == ALLOWED_UPSTREAM_FILES
    assert metadata.LICENSE_STATUS == "NO_EXPLICIT_LICENSE"

    for relative_path in ALLOWED_UPSTREAM_FILES:
        assert not (BOUNDARY / relative_path).exists()

    upstream_notice = (BOUNDARY / "UPSTREAM.md").read_text(encoding="utf-8")
    assert UPSTREAM_URL in upstream_notice
    assert UPSTREAM_COMMIT in upstream_notice
    assert "server_" + "comm.py" in upstream_notice
    assert "written permission" in upstream_notice.lower()


def test_runtime_boundary_has_no_server_or_tcp_dependency():
    production_sources = [BOUNDARY / "__init__.py", BOUNDARY / "fetch_upstream.py"]
    forbidden_markers = (
        "RadarServer" + "Comm",
        "server_" + "comm",
        "socket." + "send",
    )

    for source_path in production_sources:
        assert source_path.is_file()
        source = source_path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden_markers)

    requirements_path = REPOSITORY_ROOT / "sdr_receiver_py_wrapper" / "requirements.txt"
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
    assert fetch_script.is_file()
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
    assert tuple(plan["files"]) == ALLOWED_UPSTREAM_FILES
    assert all(isinstance(command, list) for command in plan["commands"])
    assert not destination.exists()
