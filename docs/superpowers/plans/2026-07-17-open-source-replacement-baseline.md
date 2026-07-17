# Open-source Replacement Reference Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `codex/open-source-replacement` as a reproducible, upstream-only reference receiver that reuses the common SDR/context/validation/ROS shell, never selects the improved v67 decoder, and passes deterministic offline and ROS closed-loop acceptance.

**Architecture:** Start from the reviewed `codex/hybrid-receiver` commit `f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750`, because that commit already contains the hardened common acquisition, RF safety, context, command validation, recorder, and ROS output boundaries. Replace its decoder registry with one `UpstreamDecoder` backed by an operator-authorized checkout of pinned CombatRadarSdr2026 commit `13b13a68b7111a15163aedc97f1cb17722f45ad2`; the upstream checkout remains outside Git because upstream has no explicit license. Delete v67 and dual-decoder production paths from this branch, then prove the resulting branch with an immutable recording manifest, a single-decoder offline runner, and the existing mock-radar ROS loop.

**Tech Stack:** Git worktrees, Python 3.10, NumPy, pytest, ROS 2 Humble, `rclpy`, pyadi-iio/libiio, SHA-256, JSON.

---

## Preconditions and immutable inputs

- Execute in a new worktree created from exact commit `f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750`; never alter `main` or the existing `codex/hybrid-receiver` worktree.
- Target branch is exactly `codex/open-source-replacement`.
- The pinned upstream repository has no explicit license. Do not commit, vendor, subtree, package, copy into Docker images, or redistribute its source. The only permitted integration in this plan is an explicit local evaluation checkout created by `third_party/CombatRadarSdr2026/fetch_upstream.py --acknowledge-no-license` after the operator confirms they are authorized to evaluate it.
- The reference branch is not allowed to silently fall back to `improved_v67`. If the authorized checkout is absent or invalid, startup must fail before opening Pluto or publishing ROS output.
- Official RoboMasterEngine integration and live RF hardware acceptance are higher-level integration targets. They do not block this software reference baseline.
- Recording files stay outside Git. Tests use small generated fixtures; acceptance commands bind external recordings by the SHA-256 values in `docs/superpowers/specs/2026-07-17-recording-evidence-manifest.md` and `sdr_receiver_py_wrapper/fixtures/manifest.json`.

## Per-task execution and review gate

For **every** task below, the coordinator must follow this order and may not combine two tasks in one implementation subagent:

1. Dispatch one fresh implementation subagent with only that task.
2. Leave its changes uncommitted and dispatch a fresh requirements-compliance reviewer.
3. Return every compliance finding to the same implementation subagent and rerun the focused tests.
4. Dispatch a fresh code-quality reviewer only after compliance passes.
5. Return every quality finding to the implementation subagent and rerun the focused tests.
6. Run the task's final test command from a clean shell.
7. Commit only when both reviews report no findings and the final command has the stated result.

Any test failure, missing authorized checkout, ambiguous upstream API, or review finding stops the task. It is not acceptable to weaken assertions, substitute v67, or mark a recording confirmed merely to make the gate pass.

## File map

- Create `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/combat_radar_backend.py`: load and validate the external pinned checkout, adapt its pure PHY/parser surface, and return `VerifiedParsedFrame` objects without ROS, SDR, or TCP access.
- Create `sdr_receiver_py_wrapper/test/test_combat_radar_backend.py`: checkout provenance, import isolation, profile mapping, frame conversion, and fail-closed tests.
- Modify `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`: upstream-only registry and construction; remove shadow-decoder runtime selection from the reference branch.
- Modify `sdr_receiver_py_wrapper/config/competition_receiver.yaml`: fixed upstream decoder and explicit external checkout path.
- Modify `sdr_receiver_py_wrapper/launch/competition_receiver.launch.py`: forward the checkout path; expose no decoder-selection arguments.
- Modify `sdr_receiver_py_wrapper/setup.py`: install the new backend module and single-decoder acceptance CLI.
- Delete `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/v67_decoder.py`: improved decoder is outside the reference baseline.
- Delete `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_benchmark.py`: dual-decoder comparison belongs only to the hybrid branch.
- Delete their branch-specific tests and benchmark console entry point.
- Create `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/reference_acceptance.py`: one-decoder immutable offline acceptance runner.
- Create `sdr_receiver_py_wrapper/test/test_reference_acceptance.py`: confirmed/candidate/context-negative/fault-sample semantics and atomic report behavior.
- Create `sdr_receiver_py_wrapper/launch/open_source_reference_closed_loop.launch.py`: IQ replay, mock radar context, reference receiver, and JamCode observer.
- Create `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/open_source_reference_launch.py`: importable three-node launch builder and parameter forwarding boundary.
- Create `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/jam_code_assertion_node.py`: terminating exact-message, timeout, malformed-message, and duplicate-window assertion process.
- Create `sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_launch.py`: static ROS graph/topic/QoS and launch forwarding contract.
- Create `sdr_receiver_py_wrapper/test/test_jam_code_assertion_node.py` and `test_open_source_reference_closed_loop_process.py`: assertion state-machine and real process exit-code tests.
- Create `sdr_receiver_py_wrapper/test/test_open_source_reference_provenance.py`: green starting-point guard for the pinned adapter and non-vendoring boundary.
- Create `sdr_receiver_py_wrapper/test/test_open_source_reference_scope.py`: permanent final branch guard against v67, shadow mode, TCP, or vendored upstream source.
- Create `docs/open_source_reference_acceptance_zh.md`: exact operator workflow and evidence interpretation.

### Task 1: Create and verify the isolated reference branch

**Files:**
- Create: `sdr_receiver_py_wrapper/test/test_open_source_reference_provenance.py`

- [ ] **Step 1: Create the isolated worktree and branch**

Run from the repository root, not from an existing worktree:

```powershell
git worktree add E:\sdr\.worktrees\open-source-replacement -b codex/open-source-replacement f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750
git -C E:\sdr\.worktrees\open-source-replacement rev-parse HEAD
git -C E:\sdr\.worktrees\open-source-replacement branch --show-current
```

Expected: the first command succeeds; the next lines print `f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750` and `codex/open-source-replacement`. If the branch or worktree already exists, inspect it and continue only when both values match and `git status --short` is empty.

- [ ] **Step 2: Write the green provenance test**

Create the following complete test:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOUNDARY = ROOT / "third_party" / "CombatRadarSdr2026"


def test_starting_point_contains_the_pinned_pure_adapter():
    source = (
        ROOT / "sdr_receiver_py_wrapper" / "sdr_receiver_py_wrapper" / "upstream_decoder.py"
    ).read_text(encoding="utf-8")
    assert 'decoder_id = "combat_radar_sdr_13b13a6"' in source
    assert "VerifiedParsedFrame" in source


def test_starting_point_does_not_vendor_upstream():
    assert {
        path.relative_to(BOUNDARY).as_posix()
        for path in BOUNDARY.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    } == {"UPSTREAM.md", "__init__.py", "fetch_upstream.py"}
```

- [ ] **Step 3: Run and verify the provenance test**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_open_source_reference_provenance.py -q
```

Expected: both tests PASS, proving the branch starts from the reviewed adapter and exact non-vendoring boundary.

- [ ] **Step 4: Commit only the executable scope contract after both reviews**

```powershell
git add sdr_receiver_py_wrapper/test/test_open_source_reference_provenance.py
git commit -m "test: pin open-source reference starting point"
```

Expected: one new commit and a clean worktree.

### Task 2: Adapt the authorized pinned checkout behind the pure backend boundary

**Files:**
- Create: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/combat_radar_backend.py`
- Create: `sdr_receiver_py_wrapper/test/test_combat_radar_backend.py`
- Modify: `third_party/CombatRadarSdr2026/UPSTREAM.md`

- [ ] **Step 1: Materialize the external source only for authorized inspection**

After the operator explicitly confirms authorization, run outside the worktree:

```powershell
New-Item -ItemType Directory -Force E:\sdr\.external | Out-Null
python third_party/CombatRadarSdr2026/fetch_upstream.py --acknowledge-no-license E:\sdr\.external\CombatRadarSdr2026-13b13a6
git -C E:\sdr\.external\CombatRadarSdr2026-13b13a6 rev-parse HEAD
git -C E:\sdr\.external\CombatRadarSdr2026-13b13a6 status --short
```

Expected: the hash is `13b13a68b7111a15163aedc97f1cb17722f45ad2`, status is empty, and the checkout contains only the four allowlisted upstream files. If authorization is not confirmed, stop this task; do not invent a backend or copy source into the repository.

- [ ] **Step 2: Write failing checkout and isolation tests**

The test creates a synthetic checkout with the same public module boundary, so CI never needs the no-license source:

```python
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from sdr_receiver_py_wrapper.combat_radar_backend import (
    CombatRadarBackend,
    UpstreamCheckoutError,
)


PIN = "13b13a68b7111a15163aedc97f1cb17722f45ad2"


def write_checkout(root: Path) -> tuple[Path, str]:
    root.mkdir()
    (root / "radio_profiles.py").write_text(
        "JAM_PROFILES = {}\n",
        encoding="utf-8",
    )
    (root / "phy.py").write_text(
        "def fm_demod(samples, sample_rate):\n    return samples.real\n",
        encoding="utf-8",
    )
    (root / "protocol.py").write_text(
        "SOF = 0xA5\n"
        "def crc8_maxim(data, init=0xFF): return 0\n"
        "def crc16_ibm(data, init=0xFFFF): return 0\n",
        encoding="utf-8",
    )
    parser = root / "parser"
    parser.mkdir()
    (parser / "gnuradio_frame_parser.py").write_text(
        "def slice_packet_candidates(*args, **kwargs):\n"
        "    frame = bytes.fromhex('A506000700060A4142433132330000')\n"
        "    packets = [\n"
        "      {'kind':'JAM','valid':True,'payload':bytes(14)+frame[:1]},\n"
        "      {'kind':'JAM','valid':True,'payload':frame[1:]+bytes(1)},\n"
        "    ]\n"
        "    return [{'packets':packets,'best_jam_dist':0,'packet_n':2}]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Backend Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "backend@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return root, commit


def test_backend_rejects_wrong_pin_before_import(tmp_path):
    checkout, _commit = write_checkout(tmp_path / "checkout")
    with pytest.raises(UpstreamCheckoutError, match="pinned commit"):
        CombatRadarBackend(checkout)


def test_backend_converts_only_verified_frames(tmp_path):
    checkout, commit = write_checkout(tmp_path / "checkout")
    backend = CombatRadarBackend(checkout, expected_commit=commit, expected_blobs=None)
    frames = backend.decode(
        samples=np.ones(16, dtype=np.complex64),
        sample_rate_hz=2_000_000,
        profile=SimpleNamespace(level=1),
    )
    assert len(frames) == 1
    frame = frames[0]
    assert tuple(frame) == (
        0x0A06, b"ABC123", 7, True, True, "kermit-x3014"
    )


def test_backend_never_adds_checkout_to_global_import_path(tmp_path):
    import sys
    checkout, commit = write_checkout(tmp_path / "checkout")
    before = tuple(sys.path)
    CombatRadarBackend(checkout, expected_commit=commit, expected_blobs=None)
    assert tuple(sys.path) == before
```

- [ ] **Step 3: Run the focused test and verify it fails**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_combat_radar_backend.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: ...combat_radar_backend`.

- [ ] **Step 4: Implement the fail-closed backend loader**

Implement these public types and exact invariants in `combat_radar_backend.py`:

```python
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import numpy as np

from .upstream_decoder import VerifiedParsedFrame


PINNED_COMMIT = "13b13a68b7111a15163aedc97f1cb17722f45ad2"
UPSTREAM_BLOBS = {
    "phy.py": "b842cc16cb4b2b04874268839ebf705603e5f182",
    "protocol.py": "5195c9a7183c2087184f9e5de9cbeff96d044b0f",
    "radio_profiles.py": "b189816d6802e31a23c0ee567d6e7d72cf00fd5f",
    "parser/gnuradio_frame_parser.py": "ed1b4ec02ff147be7d9af98fe2fdf7f9ff01ff97",
}
ALLOWED_FILES = {
    "phy.py",
    "protocol.py",
    "radio_profiles.py",
    "parser/gnuradio_frame_parser.py",
}


class UpstreamCheckoutError(RuntimeError):
    pass


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    ).stdout.strip()


def _validate_git_checkout(checkout, expected_commit, allowed, expected_blobs):
    raw = Path(os.path.abspath(os.path.expanduser(str(checkout))))
    anchor = Path(raw.anchor)
    candidate = anchor
    for part in raw.parts[1:]:
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError as exc:
            raise UpstreamCheckoutError(f"missing checkout component: {candidate}") from exc
        if stat.S_ISLNK(mode):
            raise UpstreamCheckoutError("symlinked checkout path is forbidden")
    root = raw.resolve(strict=True)
    if _git(root, "rev-parse", "HEAD") != expected_commit:
        raise UpstreamCheckoutError("checkout is not at pinned commit")
    visible = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }
    if visible != allowed:
        raise UpstreamCheckoutError("checkout file allowlist mismatch")
    if expected_blobs is not None:
        actual = {path: _git(root, "rev-parse", f"HEAD:{path}") for path in allowed}
        if actual != expected_blobs:
            raise UpstreamCheckoutError("checkout blob allowlist mismatch")
    return root


def _load_pinned_modules(root):
    prefix = f"_combat_radar_{id(root):x}"
    package = ModuleType(prefix)
    package.__path__ = [str(root)]
    parser_package = ModuleType(f"{prefix}.parser")
    parser_package.__path__ = [str(root / "parser")]
    created = {prefix: package, f"{prefix}.parser": parser_package}
    loaded = {}
    try:
        sys.modules.update(created)
        for short, relative in (
            ("protocol", "protocol.py"), ("phy", "phy.py"),
            ("radio_profiles", "radio_profiles.py"),
            ("parser", "parser/gnuradio_frame_parser.py"),
        ):
            name = f"{prefix}.parser.gnuradio_frame_parser" if short == "parser" else f"{prefix}.{short}"
            spec = importlib.util.spec_from_file_location(name, root / relative)
            if spec is None or spec.loader is None:
                raise UpstreamCheckoutError(f"cannot load {relative}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            created[name] = module
            spec.loader.exec_module(module)
            loaded[short] = module
        return SimpleNamespace(**loaded)
    finally:
        for name, module in created.items():
            if sys.modules.get(name) is module:
                del sys.modules[name]


def _select_jam_candidate(candidates):
    if not candidates:
        return {"packets": ()}
    return max(candidates, key=lambda row: (
        sum(p.get("kind") == "JAM" and p.get("valid") for p in row["packets"]),
        -int(row["best_jam_dist"]), int(row["packet_n"]),
    ))


class CombatRadarBackend:
    def __init__(self, checkout, *, expected_commit=PINNED_COMMIT, expected_blobs=UPSTREAM_BLOBS):
        self._root = _validate_git_checkout(
            checkout, expected_commit, ALLOWED_FILES, expected_blobs
        )
        modules = _load_pinned_modules(self._root)
        self._fm_demod = modules.phy.fm_demod
        self._slice = modules.parser.slice_packet_candidates
        self._crc8 = modules.protocol.crc8_maxim
        self._crc16 = modules.protocol.crc16_ibm
        self._sof = modules.protocol.SOF
        self._previous_air_payload = None
        self._last_frame = None

    def reset(self, *, reason, profile):
        self._previous_air_payload = None
        self._last_frame = None

    def decode(self, *, samples, sample_rate_hz, profile):
        inst = self._fm_demod(
            np.array(samples, dtype=np.complex64, copy=True), int(sample_rate_hz)
        )
        candidates = self._slice(
            inst,
            sps=max(1, round(int(sample_rate_hz) / 19_200)),
            bt=0.35,
            sensitivity={1: 2.8323, 2: 2.5809, 3: 0.6646}[profile.level],
            max_access_bit_errors=1,
            allow_jam=True,
            info_only=False,
            refine_span=3,
            max_candidates=5,
        )
        selected = _select_jam_candidate(candidates)
        return self._consume_air_packets(selected.get("packets", ()))

    def _consume_air_packets(self, packets):
        out = []
        for packet in packets:
            payload = packet.get("payload", b"")
            if packet.get("kind") != "JAM" or not packet.get("valid") or len(payload) != 15:
                self._previous_air_payload = None
                continue
            joined = (self._previous_air_payload or b"") + bytes(payload)
            self._previous_air_payload = bytes(payload)
            for start in range(max(0, len(joined) - 15) + 1):
                frame = joined[start:start + 15]
                if len(frame) != 15 or frame[0] != self._sof:
                    continue
                if int.from_bytes(frame[1:3], "little") != 6:
                    continue
                if self._crc8(frame[:4]) != frame[4]:
                    continue
                if int.from_bytes(frame[5:7], "little") != 0x0A06:
                    continue
                if self._crc16(frame[:13]) != int.from_bytes(frame[13:15], "little"):
                    continue
                data = bytes(frame[7:13])
                if len(data) != 6 or not data.decode("ascii").isalnum() or frame == self._last_frame:
                    continue
                self._last_frame = bytes(frame)
                out.append(VerifiedParsedFrame(
                    cmd_id=0x0A06, data=data, seq=frame[3],
                    crc8_ok=True, crc16_ok=True, crc_mode="kermit-x3014",
                ))
        return tuple(out)
```

`_validate_git_checkout` must resolve the path, reject symlinks in every path component, run `git -C <checkout> rev-parse HEAD`, require the supplied commit, verify the four materialized files against `expected_blobs` when it is not `None`, and reject materialized files outside the exact allowlist (excluding `.git`). The production defaults are immutable; injectable expectations exist only for locally generated Git fixtures. `_load_pinned_modules` must create one private package namespace with `__path__=[checkout]`, then load `protocol.py`, `phy.py`, `radio_profiles.py`, and `parser/gnuradio_frame_parser.py` with `importlib.util.spec_from_file_location`; this is required because the pinned parser uses relative imports. It must restore every temporary `sys.modules` entry in `finally` and never mutate `sys.path`.

The mapping above is based on the pinned API, not an assumed `phy.decode`: `phy.fm_demod(iq, sample_rate)` produces instantaneous frequency; `parser.slice_packet_candidates(...)` returns packet candidates; `radio_profiles.JAM_PROFILES` supplies `sensitivity`; and `protocol.crc8_maxim`/`crc16_ibm` validate the reconstructed referee frame. `_select_jam_candidate` must choose `max(candidates, key=(valid JAM packet count, -best_jam_dist, packet_n))`. `_consume_air_packets` must retain one previous valid 15-byte JAM payload, scan every 15-byte window in `previous + current`, and emit only frames satisfying SOF `0xA5`, data length 6, command `0x0A06`, CRC8, CRC16, and six ASCII alphanumeric bytes. Each emitted object is `VerifiedParsedFrame(cmd_id=0x0A06, data=frame[7:13], seq=frame[3], crc8_ok=True, crc16_ok=True, crc_mode="kermit-x3014")`; duplicate complete frame bytes are suppressed until reset. `sps=round(sample_rate/19200)`, BT `0.35`, access errors `1`, refine span `3`, and candidate count `5` are the exact pinned `jam_rx_app.py` defaults generalized from 1 Msps (`sps=52`) to the recording sample rate. Do not modify the checkout or copy upstream implementation into Git. Record these inspected callable names and constants in `UPSTREAM.md`.

- [ ] **Step 5: Run backend and upstream-boundary tests**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_combat_radar_backend.py sdr_receiver_py_wrapper/test/test_upstream_decoder.py sdr_receiver_py_wrapper/test/test_combat_radar_upstream_boundary.py -q
```

Expected: all tests PASS; no test contacts the network or reads `E:\sdr\.external`.

- [ ] **Step 6: Commit after both reviews**

```powershell
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/combat_radar_backend.py sdr_receiver_py_wrapper/test/test_combat_radar_backend.py third_party/CombatRadarSdr2026/UPSTREAM.md
git commit -m "feat: bind authorized upstream decoder checkout"
```

### Task 3: Make the production registry upstream-only

**Files:**
- Modify: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`
- Modify: `sdr_receiver_py_wrapper/config/competition_receiver.yaml`
- Modify: `sdr_receiver_py_wrapper/launch/competition_receiver.launch.py`
- Modify: `sdr_receiver_py_wrapper/test/test_receiver_pipeline.py`

- [ ] **Step 1: Write failing construction and configuration tests**

Add these tests to `test_receiver_pipeline.py`, using the file's existing ROS stubs and `receiver_node_module` fixture:

```python
def test_reference_registry_constructs_only_pinned_upstream(receiver_node_module, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    backend = object()
    monkeypatch.setattr(
        receiver_node_module,
        "CombatRadarBackend",
        lambda path: backend if Path(path) == checkout else pytest.fail("wrong checkout"),
    )
    decoder = receiver_node_module._create_decoder_plugin(str(checkout))
    assert decoder.decoder_id == "combat_radar_sdr_13b13a6"
    assert decoder._backend is backend


def test_reference_configuration_has_no_runtime_decoder_selector():
    defaults = _node_declared_defaults()
    launch_defaults, forwarded = _launch_defaults_and_forwarding()
    assert "decoder_primary" not in defaults
    assert "decoder_shadow" not in defaults
    assert "decoder_primary" not in launch_defaults
    assert "decoder_shadow" not in launch_defaults
    assert "upstream_checkout_path" in forwarded
```

Update `EXPECTED_DEFAULTS` so it contains `upstream_checkout_path: ""` and contains neither decoder field.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_receiver_pipeline.py -k "reference_registry or reference_configuration or common_defaults" -q
```

Expected: FAIL because the current registry constructs `V67Decoder` and current configuration exposes primary/shadow selectors.

- [ ] **Step 3: Replace the registry and fail before hardware startup**

Make these exact structural changes in `receiver_node.py`:

```python
from .combat_radar_backend import CombatRadarBackend
from .upstream_decoder import UpstreamDecoder

PRIMARY_DECODER_ID = "combat_radar_sdr_13b13a6"


def _create_decoder_plugin(upstream_checkout_path: str):
    if not isinstance(upstream_checkout_path, str) or not upstream_checkout_path.strip():
        raise ValueError("upstream_checkout_path is required")
    backend = CombatRadarBackend(upstream_checkout_path)
    return UpstreamDecoder(backend=backend)
```

Remove `decoder_primary` and `decoder_shadow` from `ReceiverFoundationConfig`, parameter declarations, status output, and plugin construction. Add `upstream_checkout_path: str = ""`; validate and construct the backend before `DeviceSession` or `IqFilePluto` is created. `ReceiverPipeline` may retain its generic optional `shadow` argument for unit-test reuse, but the ROS node must always pass `shadow=None` and expose no shadow parameter.

- [ ] **Step 4: Pin YAML and launch configuration**

In `competition_receiver.yaml`, replace decoder selector entries with:

```yaml
    upstream_checkout_path: ""
```

In `competition_receiver.launch.py`, declare and forward only:

```python
DeclareLaunchArgument(
    "upstream_checkout_path",
    default_value=EnvironmentVariable(
        "COMBAT_RADAR_UPSTREAM_CHECKOUT", default_value=""
    ),
)
```

The launch file must not contain `decoder_primary` or `decoder_shadow`.

- [ ] **Step 5: Run focused and common runtime tests**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_receiver_pipeline.py sdr_receiver_py_wrapper/test/test_device_session.py sdr_receiver_py_wrapper/test/test_acquisition.py sdr_receiver_py_wrapper/test/test_context_arbiter.py sdr_receiver_py_wrapper/test/test_competition_controller.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit after both reviews**

```powershell
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py sdr_receiver_py_wrapper/config/competition_receiver.yaml sdr_receiver_py_wrapper/launch/competition_receiver.launch.py sdr_receiver_py_wrapper/test/test_receiver_pipeline.py
git commit -m "refactor: select only upstream reference decoder"
```

### Task 4: Remove hybrid-only decoder and benchmark surfaces

**Files:**
- Delete: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/v67_decoder.py`
- Delete: `sdr_receiver_py_wrapper/test/test_v67_decoder.py`
- Delete: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_benchmark.py`
- Delete: `sdr_receiver_py_wrapper/test/test_decoder_benchmark.py`
- Create: `sdr_receiver_py_wrapper/test/test_open_source_reference_scope.py`
- Modify: `sdr_receiver_py_wrapper/setup.py`
- Modify: `sdr_receiver_py_wrapper/README.md`
- Modify: `README.md`

- [ ] **Step 1: Remove the four hybrid-only files**

Use `apply_patch` delete operations, then remove the `decoder_benchmark` console entry point from `setup.py`. Do not delete common contracts, RF safety, recorder, context arbitration, fixture validation, or `upstream_decoder.py`.

- [ ] **Step 2: Replace hybrid instructions with reference-branch instructions**

Document these exact properties in both READMEs:

```text
Branch: codex/open-source-replacement
Decoder: combat_radar_sdr_13b13a6 only
External checkout: COMBAT_RADAR_UPSTREAM_CHECKOUT
No fallback: startup fails if the checkout is absent, unpinned, or malformed
Excluded: improved_v67, decoder shadow comparison, RadarServerComm/TCP
```

- [ ] **Step 3: Create and run the permanent scope test**

Create:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sdr_receiver_py_wrapper"


def test_reference_tree_has_no_hybrid_runtime_surface():
    forbidden_files = (
        PACKAGE / "sdr_receiver_py_wrapper" / "v67_decoder.py",
        PACKAGE / "sdr_receiver_py_wrapper" / "decoder_benchmark.py",
        PACKAGE / "test" / "test_v67_decoder.py",
        PACKAGE / "test" / "test_decoder_benchmark.py",
    )
    assert all(not path.exists() for path in forbidden_files)
    files = (
        PACKAGE / "sdr_receiver_py_wrapper" / "receiver_node.py",
        PACKAGE / "launch" / "competition_receiver.launch.py",
        PACKAGE / "config" / "competition_receiver.yaml",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "improved_v67" not in source
    assert "decoder_shadow" not in source


def test_reference_tree_has_no_vendored_upstream_or_tcp_bridge():
    boundary = ROOT / "third_party" / "CombatRadarSdr2026"
    assert {
        path.relative_to(boundary).as_posix()
        for path in boundary.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    } == {"UPSTREAM.md", "__init__.py", "fetch_upstream.py"}
    production = [
        path for path in PACKAGE.rglob("*.py")
        if "test" not in path.relative_to(PACKAGE).parts
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in production)
    for marker in ("RadarServerComm", "server_comm", "socket.send"):
        assert marker not in source
```

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_open_source_reference_scope.py -q
```

Expected: both tests PASS for the first time.

- [ ] **Step 4: Run a stale-reference scan**

Run:

```powershell
rg -n "improved_v67|V67Decoder|decoder_shadow|decoder_benchmark" `
  sdr_receiver_py_wrapper/sdr_receiver_py_wrapper `
  sdr_receiver_py_wrapper/config `
  sdr_receiver_py_wrapper/launch `
  sdr_receiver_py_wrapper/setup.py `
  sdr_receiver_py_wrapper/README.md README.md
```

Expected: no matches. Generic unit-test fixtures may still use arbitrary labels such as `shadow`; historical plans may mention the hybrid decoder. Both are deliberately outside this production-surface scan.

- [ ] **Step 5: Run package unit tests**

```powershell
python -m pytest sdr_receiver_py_wrapper/test -q
```

Expected: all collected tests PASS; the removed test modules are not collected.

- [ ] **Step 6: Commit after both reviews**

```powershell
git add -A sdr_receiver_py_wrapper README.md
git commit -m "refactor: remove hybrid decoder surfaces from reference branch"
```

### Task 5: Add deterministic single-decoder offline acceptance

**Files:**
- Create: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/reference_acceptance.py`
- Create: `sdr_receiver_py_wrapper/test/test_reference_acceptance.py`
- Modify: `sdr_receiver_py_wrapper/setup.py`
- Modify: `sdr_receiver_py_wrapper/fixtures/manifest.json`

- [ ] **Step 1: Extend the manifest without inventing oracle values**

Keep `RX_BLUE_ganrao_1` as the only confirmed key oracle; its hash, 2 Msps sample rate, command `0x0A06`, and key `fcYqTC` are fixed by `P-BLUE-L1-ORACLE` in `2026-07-17-recording-evidence-manifest.md`. Add only recordings whose sample rate is authoritative: BLUE L2/L3 at 2 Msps as `candidate`, BO3 at 1 Msps as `fault`, and C-RED-L1-6S at 1 Msps as `context-negative`. Entries contain format, authoritative sample rate, path environment-variable name, and SHA-256; never place an absolute workstation path in the runtime manifest. P-BLUE-L1, C-RED-RAW2, C-RED-L1/L2/L3, and C-RED-RAW have unknown sample rates and therefore remain evidence-only in the specification: do not add them to the runtime manifest, and `reference_acceptance` must reject any entry with absent, zero, guessed, or non-integer `sample_rate_hz`.

- [ ] **Step 2: Write failing acceptance tests**

Create tests around the following public API:

```python
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from sdr_receiver_py_wrapper.models import DecodedCommand
from sdr_receiver_py_wrapper.reference_acceptance import main, run_reference_acceptance


class FakeManifest:
    def __init__(self, root: Path):
        self.iq_path = root / "fixture.c64"
        np.ones(32, dtype=np.complex64).tofile(self.iq_path)
        self.root = root

    def _write(self, verification, **extra):
        entry = {
            "format": "complex64-le",
            "sample_rate_hz": 2_000_000,
            "team": "BLUE",
            "target": "L1",
            "verification": verification,
            "sha256": hashlib.sha256(self.iq_path.read_bytes()).hexdigest(),
            **extra,
        }
        path = self.root / f"{verification}.json"
        path.write_text(json.dumps({verification: entry}), encoding="utf-8")
        return path

    def confirmed(self, payload):
        return self._write(
            "confirmed", expected_cmd_id=0x0A06,
            expected_ascii=payload.decode("ascii"), expected_publications=1,
        )

    def candidate(self):
        return self._write("candidate")

    def context_negative(self):
        return self._write(
            "context-negative", authorized_team="RED",
            physical_frames_allowed=True, expected_publications=0,
        )


@pytest.fixture
def fake_manifest(tmp_path):
    return FakeManifest(tmp_path)


def make_decoder_factory(*, payload=b"ABC123", error=None):
    class Decoder:
        decoder_id = "combat_radar_sdr_13b13a6"

        def reset(self, reason, context):
            self.context = context

        def decode(self, chunk, context):
            if error is not None:
                raise error
            return [DecodedCommand(
                cmd_id=0x0A06, payload=payload, decoder_id=self.decoder_id,
                profile=f"{context.team}-{context.target}", crc8_ok=True,
                crc16_ok=True, crc_mode="kermit-x3014",
                first_sample_index=chunk.first_sample_index,
                last_sample_index=chunk.first_sample_index + len(chunk.samples) - 1,
                receive_wall_time=chunk.rx_wall_time, target=context.target,
                team=context.team, context_version=context.context_version,
                evidence={"level": 1},
            )]

        def stats(self):
            return None

    return Decoder


@pytest.fixture
def decoder_factory():
    return make_decoder_factory


def test_confirmed_fixture_requires_exact_oracle(tmp_path, fake_manifest, decoder_factory):
    report = run_reference_acceptance(
        manifest_path=fake_manifest.confirmed(b"ABC123"),
        fixture_name="confirmed",
        iq_path=fake_manifest.iq_path,
        decoder_factory=decoder_factory(payload=b"ABC123"),
        output_path=tmp_path / "report.json",
    )
    assert report["status"] == "pass"
    assert report["publications"] == [{"cmd_id": 0x0A06, "ascii": "ABC123"}]


def test_context_negative_may_decode_but_never_publish(tmp_path, fake_manifest, decoder_factory):
    report = run_reference_acceptance(
        manifest_path=fake_manifest.context_negative(),
        fixture_name="negative",
        iq_path=fake_manifest.iq_path,
        decoder_factory=decoder_factory(payload=b"ABC123"),
        output_path=tmp_path / "report.json",
    )
    assert report["physical_frames"] == 1
    assert report["publications"] == []
    assert report["status"] == "pass"


def test_candidate_result_is_evidence_not_a_pass_oracle(tmp_path, fake_manifest, decoder_factory):
    report = run_reference_acceptance(
        manifest_path=fake_manifest.candidate(),
        fixture_name="candidate",
        iq_path=fake_manifest.iq_path,
        decoder_factory=decoder_factory(payload=b"ABC123"),
        output_path=tmp_path / "report.json",
    )
    assert report["status"] == "observed"
    assert report["oracle_asserted"] is False


def test_wrong_hash_fails_before_decode(tmp_path, fake_manifest, decoder_factory):
    manifest = fake_manifest.candidate()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["candidate"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        run_reference_acceptance(
            manifest_path=manifest, fixture_name="candidate",
            iq_path=fake_manifest.iq_path, decoder_factory=decoder_factory(),
            output_path=tmp_path / "report.json",
        )


def test_truncated_complex64_fails(tmp_path, fake_manifest, decoder_factory):
    fake_manifest.iq_path.write_bytes(b"1234567")
    manifest = fake_manifest.candidate()
    with pytest.raises(ValueError, match="multiple of 8"):
        run_reference_acceptance(
            manifest_path=manifest, fixture_name="candidate",
            iq_path=fake_manifest.iq_path, decoder_factory=decoder_factory(),
            output_path=tmp_path / "report.json",
        )


def test_invalid_ascii_and_decoder_error_are_fail_closed(tmp_path, fake_manifest, decoder_factory):
    manifest = fake_manifest.confirmed(b"ABC123")
    invalid = run_reference_acceptance(
        manifest_path=manifest, fixture_name="confirmed",
        iq_path=fake_manifest.iq_path, decoder_factory=decoder_factory(payload=b"AB-123"),
        output_path=tmp_path / "invalid.json",
    )
    assert invalid["status"] == "fail"
    with pytest.raises(RuntimeError, match="decode failed"):
        run_reference_acceptance(
            manifest_path=manifest, fixture_name="confirmed",
            iq_path=fake_manifest.iq_path,
            decoder_factory=decoder_factory(error=RuntimeError("decode failed")),
            output_path=tmp_path / "error.json",
        )


def test_report_replace_failure_preserves_previous_report(
    tmp_path, fake_manifest, decoder_factory, monkeypatch
):
    output = tmp_path / "report.json"
    output.write_text('{"old":true}\n', encoding="utf-8")
    manifest = fake_manifest.candidate()
    monkeypatch.setattr(
        "sdr_receiver_py_wrapper.reference_acceptance.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        run_reference_acceptance(
            manifest_path=manifest, fixture_name="candidate",
            iq_path=fake_manifest.iq_path, decoder_factory=decoder_factory(),
            output_path=output,
        )
    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}
```

- [ ] **Step 3: Run and verify failure**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_reference_acceptance.py -q
```

Expected: FAIL during collection because `reference_acceptance.py` does not exist.

- [ ] **Step 4: Implement the runner and CLI**

Implement:

```python
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np

from .models import DecodeContext, IqChunk, ResetReason
from .combat_radar_backend import CombatRadarBackend
from .upstream_decoder import UpstreamDecoder


def _load_entry(manifest_path, fixture_name):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if fixture_name not in manifest:
        raise KeyError(f"unknown fixture: {fixture_name}")
    entry = dict(manifest[fixture_name])
    rate = entry.get("sample_rate_hz")
    if type(rate) is not int or rate <= 0:
        raise ValueError("sample_rate_hz must be an authoritative positive integer")
    return entry


def _verify_iq(entry, iq_path):
    path = Path(iq_path)
    size = path.stat().st_size
    if size == 0 or size % 8:
        raise ValueError("complex64 IQ byte length must be a positive multiple of 8")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    if digest != entry["sha256"]:
        raise ValueError("fixture SHA-256 mismatch")
    return path


def _decode_all_chunks(decoder, entry, iq_path):
    context = DecodeContext(
        team=entry["team"], target=entry["target"], profile="reference",
        target_version=1, context_version=1,
    )
    decoder.reset(ResetReason.STARTUP, context)
    source = np.memmap(iq_path, dtype=np.dtype("<c8"), mode="r")
    commands = []
    first = 0
    for chunk_id, offset in enumerate(range(0, source.size, 160_000)):
        samples = np.array(source[offset:offset + 160_000], dtype=np.complex64, copy=True)
        samples.flags.writeable = False
        chunk = IqChunk(
            chunk_id=chunk_id, first_sample_index=first, samples=samples,
            sample_rate_hz=int(entry["sample_rate_hz"]), rx_wall_time=time.time(),
            rx_monotonic_ns=time.monotonic_ns(), lo_hz=0, rf_bandwidth_hz=0,
            rx_gain_db=0, target_version=1, context_version=1,
        )
        commands.extend(decoder.decode(chunk, context))
        first += samples.size
    return commands


def _apply_oracle_and_context_gate(commands, entry):
    physical = [command for command in commands if command.cmd_id == 0x0A06]
    authorized = entry.get("authorized_team", entry["team"]) == entry["team"]
    publications = []
    if authorized:
        for command in physical:
            try:
                key = command.payload.decode("ascii")
            except UnicodeDecodeError:
                continue
            if len(key) == 6 and key.isalnum():
                publications.append({"cmd_id": command.cmd_id, "ascii": key})
    verification = entry["verification"]
    oracle_asserted = verification == "confirmed"
    if verification in ("context-negative", "fault"):
        status = "pass" if not publications else "fail"
    elif verification == "confirmed":
        expected = [{
            "cmd_id": int(entry["expected_cmd_id"]),
            "ascii": entry["expected_ascii"],
        }]
        status = "pass" if publications == expected else "fail"
    else:
        status = "observed"
    return {
        "status": status, "oracle_asserted": oracle_asserted,
        "physical_frames": len(physical), "publications": publications,
    }


def _write_json_atomically(output_path, report):
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_reference_acceptance(
    *, manifest_path, fixture_name, iq_path, decoder_factory, output_path
) -> dict:
    entry = _load_entry(manifest_path, fixture_name)
    verified_iq = _verify_iq(entry, iq_path)
    decoder = decoder_factory()
    commands = _decode_all_chunks(decoder, entry, verified_iq)
    report = _apply_oracle_and_context_gate(commands, entry)
    report.update({"fixture": fixture_name, "sha256": entry["sha256"]})
    _write_json_atomically(output_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="reference_acceptance")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--iq", required=True)
    parser.add_argument("--upstream-checkout", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        report = run_reference_acceptance(
            manifest_path=args.manifest, fixture_name=args.fixture,
            iq_path=args.iq,
            decoder_factory=lambda: UpstreamDecoder(
                backend=CombatRadarBackend(args.upstream_checkout)
            ),
            output_path=args.out,
        )
    except Exception as exc:
        parser.exit(3, f"reference acceptance error: {exc}\n")
    return 0 if report["status"] in ("pass", "observed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

The CLI is:

```text
reference_acceptance --manifest <json> --fixture <name> --iq <path> --upstream-checkout <path> --out <json>
```

It returns 0 only for `status=pass` or `status=observed`, returns 2 for an oracle/context failure, and returns 3 for input/backend/report errors. Register `reference_acceptance = sdr_receiver_py_wrapper.reference_acceptance:main` in `setup.py`.

Append these CLI/rate tests:

```python
def test_cli_forwards_all_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sdr_receiver_py_wrapper.reference_acceptance.run_reference_acceptance",
        lambda **kwargs: calls.append(kwargs) or {"status": "observed"},
    )
    assert main(["--manifest", "m.json", "--fixture", "candidate", "--iq", "x.c64",
                 "--upstream-checkout", "checkout", "--out", "out.json"]) == 0
    assert calls[0]["manifest_path"] == "m.json"
    assert calls[0]["fixture_name"] == "candidate"
    assert calls[0]["iq_path"] == "x.c64"
    assert calls[0]["output_path"] == "out.json"
    assert callable(calls[0]["decoder_factory"])


@pytest.mark.parametrize(("status", "code"), [("fail", 2), ("pass", 0)])
def test_cli_status_exit_code(monkeypatch, status, code):
    monkeypatch.setattr(
        "sdr_receiver_py_wrapper.reference_acceptance.run_reference_acceptance",
        lambda **_kwargs: {"status": status},
    )
    argv = ["--manifest", "m", "--fixture", "f", "--iq", "i",
            "--upstream-checkout", "u", "--out", "o"]
    assert main(argv) == code


def test_cli_runtime_error_exits_three(monkeypatch):
    monkeypatch.setattr(
        "sdr_receiver_py_wrapper.reference_acceptance.run_reference_acceptance",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(SystemExit) as raised:
        main(["--manifest", "m", "--fixture", "f", "--iq", "i",
              "--upstream-checkout", "u", "--out", "o"])
    assert raised.value.code == 3


def test_unknown_sample_rate_is_rejected(tmp_path, fake_manifest, decoder_factory):
    manifest = fake_manifest.candidate()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["candidate"]["sample_rate_hz"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    called = []
    with pytest.raises(ValueError, match="sample_rate_hz"):
        run_reference_acceptance(
            manifest_path=manifest, fixture_name="candidate",
            iq_path=fake_manifest.iq_path,
            decoder_factory=lambda: called.append(True),
            output_path=tmp_path / "out.json",
        )
    assert called == []
```

- [ ] **Step 5: Run focused tests and a generated-fixture CLI smoke test**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_reference_acceptance.py sdr_receiver_py_wrapper/test/test_fixture_manifest.py sdr_receiver_py_wrapper/test/test_command_validator.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit after both reviews**

```powershell
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/reference_acceptance.py sdr_receiver_py_wrapper/test/test_reference_acceptance.py sdr_receiver_py_wrapper/setup.py sdr_receiver_py_wrapper/fixtures/manifest.json
git commit -m "test: add upstream reference offline acceptance"
```

### Task 6: Add the upstream-only ROS closed-loop launch

**Files:**
- Create: `sdr_receiver_py_wrapper/launch/open_source_reference_closed_loop.launch.py`
- Create: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/open_source_reference_launch.py`
- Create: `sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_launch.py`
- Create: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/jam_code_assertion_node.py`
- Create: `sdr_receiver_py_wrapper/test/test_jam_code_assertion_node.py`
- Create: `sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_process.py`
- Modify: `sdr_receiver_py_wrapper/setup.py` (install the builder module and assertion entry point)

- [ ] **Step 1: Write the failing launch contract test**

```python
import ast
from pathlib import Path


BUILDER = (
    Path(__file__).resolve().parents[1]
    / "sdr_receiver_py_wrapper"
    / "open_source_reference_launch.py"
)


def test_closed_loop_launch_contains_three_ros_roles_and_fixed_topics():
    source = BUILDER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert len([node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Node"]) == 3
    assert "mock_radar_context_publisher" in source
    assert "sdr_receiver_py_wrapper_node" in source
    assert "jam_code_assertion_node" in source
    assert "/judge/radar_context" in source
    assert "/sdr/jam_code" in source
    assert "upstream_checkout_path" in source
    assert "grace_sec" in source
    assert "decoder_shadow" not in source
    assert "improved_v67" not in source
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_launch.py -q
```

Expected: FAIL with `FileNotFoundError` for the new importable builder module.

- [ ] **Step 3: Implement the terminating JamCode assertion node**

Create a node with this complete state machine; factor `_observe` and `_finish` so unit tests can invoke them with fake messages and a fake shutdown callback:

```python
import rclpy
from rclpy.node import Node

from sdr_receiver.msg import JamCode


class JamCodeAssertionNode(Node):
    def __init__(self):
        super().__init__("jam_code_assertion")
        self.expected_level = int(self.declare_parameter("expected_level", 1).value)
        self.expected_key = str(self.declare_parameter("expected_key", "").value)
        self.timeout_sec = float(self.declare_parameter("timeout_sec", 15.0).value)
        self.grace_sec = float(self.declare_parameter("grace_sec", 1.0).value)
        if len(self.expected_key) != 6 or not self.expected_key.isalnum():
            raise ValueError("expected_key must be six ASCII alphanumeric characters")
        self.messages = []
        self.exit_code = None
        self.matched = False
        self.create_subscription(JamCode, "/sdr/jam_code", self._observe, 10)
        self.create_timer(self.timeout_sec, lambda: self._finish(4, "timeout"))

    def _observe(self, message):
        try:
            key = bytes(message.key).decode("ascii", errors="strict")
        except UnicodeDecodeError:
            self._finish(2, "malformed non-ASCII key")
            return
        observed = (int(message.level), key)
        self.messages.append(observed)
        if self.matched or len(self.messages) > 1:
            self._finish(3, "duplicate JamCode")
        elif observed != (self.expected_level, self.expected_key):
            self._finish(2, f"mismatch: {observed!r}")
        else:
            self.matched = True
            self.get_logger().info("matched; entering duplicate-detection grace window")
            self.create_timer(self.grace_sec, lambda: self._finish(0, "matched without duplicate"))

    def _finish(self, code, reason):
        if self.exit_code is None:
            self.exit_code = int(code)
            self.get_logger().info(reason)
            rclpy.shutdown()


def main(args=None):
    rclpy.init(args=args)
    node = JamCodeAssertionNode()
    rclpy.spin(node)
    code = 4 if node.exit_code is None else node.exit_code
    node.destroy_node()
    return code
```

`if __name__ == "__main__": raise SystemExit(main())` is mandatory. Register `jam_code_assertion_node = sdr_receiver_py_wrapper.jam_code_assertion_node:main` in `setup.py`. Create the complete unit test below; make `_observe` catch `UnicodeDecodeError` and call `_finish(2, "malformed non-ASCII key")` instead of letting the executor swallow the callback exception:

```python
from types import SimpleNamespace

import pytest

from sdr_receiver_py_wrapper.jam_code_assertion_node import JamCodeAssertionNode


def make_node():
    node = object.__new__(JamCodeAssertionNode)
    node.expected_level = 1
    node.expected_key = "ABC123"
    node.messages = []
    node.matched = False
    node.exit_code = None
    node.grace_sec = 1.0
    node.finished = []
    node._finish = lambda code, reason: node.finished.append((code, reason))
    node.get_logger = lambda: SimpleNamespace(info=lambda _text: None)
    node.create_timer = lambda seconds, callback: setattr(node, "grace_callback", callback)
    return node


def message(level=1, key=b"ABC123"):
    return SimpleNamespace(level=level, key=list(key))


def test_exact_message_waits_for_grace_then_succeeds():
    node = make_node()
    node._observe(message())
    assert node.finished == []
    assert node.matched is True
    node.grace_callback()
    assert node.finished == [(0, "matched without duplicate")]


@pytest.mark.parametrize("bad", [message(level=2), message(key=b"WRONG1")])
def test_mismatch_finishes_two(bad):
    node = make_node()
    node._observe(bad)
    assert node.finished[0][0] == 2


def test_duplicate_during_grace_finishes_three():
    node = make_node()
    node._observe(message())
    node._observe(message())
    assert node.finished[0][0] == 3


def test_malformed_non_ascii_finishes_two():
    node = make_node()
    node._observe(message(key=b"ABC\xff12"))
    assert node.finished[0] == (2, "malformed non-ASCII key")


def test_timeout_finishes_four():
    node = make_node()
    node._finish(4, "timeout")
    assert node.finished == [(4, "timeout")]
```

- [ ] **Step 4: Implement the launch graph**

Create a launch description with exactly three ROS processes:

```python
Node(
    package="sdr_receiver_py_wrapper",
    executable="mock_radar_context_publisher",
    parameters=[{"context_topic": "/judge/radar_context"}],
),
Node(
    package="sdr_receiver_py_wrapper",
    executable="sdr_receiver_py_wrapper_node",
    parameters=[{
        "run_mode": "competition",
        "iq_source_path": LaunchConfiguration("iq_source_path"),
        "upstream_checkout_path": LaunchConfiguration("upstream_checkout_path"),
        "context_authority_topic": "/judge/radar_context",
        "publish_ros_outputs": True,
    }],
),
Node(
    package="sdr_receiver_py_wrapper",
    executable="jam_code_assertion_node",
    parameters=[{
        "expected_level": LaunchConfiguration("expected_level"),
        "expected_key": LaunchConfiguration("expected_key"),
        "timeout_sec": LaunchConfiguration("timeout_sec"),
        "grace_sec": LaunchConfiguration("grace_sec"),
    }],
),
```

Place the three actions inside the importable package module `sdr_receiver_py_wrapper/open_source_reference_launch.py` as `build_closed_loop_description(iq_source_path, upstream_checkout_path, expected_level, expected_key, timeout_sec, grace_sec)`. The `jam_assert` node parameters must include both `"timeout_sec": timeout_sec` and `"grace_sec": grace_sec`. Return `(LaunchDescription([radar_context, receiver, jam_assert]), {"jam_assert": jam_assert})`. The `.launch.py` file is a thin wrapper which imports this helper, calls it with six `LaunchConfiguration` values, and returns only the description. This gives unit and launch tests an importable builder and the exact process object whose exit status they assert.

Declare IQ path, IQ format metadata, team/level, expected six-byte key, timeout, grace window, and upstream checkout. `OnProcessExit` may shut down remaining nodes, but it does **not** prove or propagate the verifier exit code. Test the verifier process independently with an explicit subprocess supervisor; this avoids pretending that a synthetic upstream checkout or waveform proves the real decoder closed loop:

```python
import os
import subprocess
import sys
import time
import pytest

def run_assertion_process(*, expected_key="ABC123", publish_key=None, timeout="1.0"):
    env = os.environ.copy()
    env["ROS_DOMAIN_ID"] = str(180 + (os.getpid() % 20))
    command = [
        sys.executable, "-m", "sdr_receiver_py_wrapper.jam_code_assertion_node",
        "--ros-args", "-p", "expected_level:=1", "-p", f"expected_key:={expected_key}",
        "-p", f"timeout_sec:={timeout}", "-p", "grace_sec:=0.25",
    ]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(0.5)
        if publish_key is not None:
            payload = ",".join(str(byte) for byte in publish_key.encode("ascii"))
            subprocess.run(
                ["ros2", "topic", "pub", "--once", "/sdr/jam_code", "sdr_receiver/msg/JamCode",
                 "{level: 1, key: [" + payload + "]}"],
                env=env, check=True, capture_output=True, text=True, timeout=5,
            )
        stdout, stderr = process.communicate(timeout=5)
        return process.returncode, stdout, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_matching_process_exits_zero_after_grace():
    assert run_assertion_process(publish_key="ABC123")[0] == 0


def test_wrong_key_process_exits_two():
    assert run_assertion_process(publish_key="WRONG1")[0] == 2


def test_silent_process_exits_four():
    assert run_assertion_process(timeout="0.5")[0] == 4
```

The static builder test separately asserts that all six inputs, including `grace_sec`, reach their node parameters. The real recording command in Task 7 is the decoder closed-loop proof; these process tests prove only the verifier's actual shell exit codes 0, 2, and 4.

- [ ] **Step 5: Run static, assertion-node, and ROS package tests**

Run on Ubuntu 22.04/ROS 2 Humble:

```bash
python3 -m pytest sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_launch.py sdr_receiver_py_wrapper/test/test_jam_code_assertion_node.py sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_process.py -q
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper --symlink-install
. install/setup.bash
ros2 launch sdr_receiver_py_wrapper open_source_reference_closed_loop.launch.py --show-args
```

Expected: pytest PASS, both packages build successfully, and `--show-args` lists `iq_source_path` and `upstream_checkout_path` without decoder selectors.

- [ ] **Step 6: Commit after both reviews**

```bash
git add sdr_receiver_py_wrapper/launch/open_source_reference_closed_loop.launch.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/open_source_reference_launch.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/jam_code_assertion_node.py sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_launch.py sdr_receiver_py_wrapper/test/test_jam_code_assertion_node.py sdr_receiver_py_wrapper/test/test_open_source_reference_closed_loop_process.py sdr_receiver_py_wrapper/setup.py
git commit -m "test: add upstream reference ROS closed loop"
```

### Task 7: Document and execute reference-baseline acceptance

**Files:**
- Create: `docs/open_source_reference_acceptance_zh.md`

- [ ] **Step 1: Write the operator document**

The document must contain these exact gates in order:

1. Verify branch and clean status.
2. Verify the external checkout pin and exact allowlist.
3. Run all Python tests without network access.
4. Build both ROS packages on Ubuntu 22.04/Humble.
5. Run `reference_acceptance` for every manifest entry; confirmed entries must pass, candidates are observations, context-negative/fault entries must publish nothing.
6. Run the ROS closed loop on the confirmed L1 recording.
7. Collect Git commit, checkout pin, recording SHA-256, command lines, exit codes, JSON reports, and ROS logs in one timestamped evidence directory.

Include the following canonical commands:

```bash
export COMBAT_RADAR_UPSTREAM_CHECKOUT=/opt/sdr-external/CombatRadarSdr2026-13b13a6
python3 -m pytest sdr_receiver_py_wrapper/test -q
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper --symlink-install
. install/setup.bash
reference_acceptance --manifest sdr_receiver_py_wrapper/fixtures/manifest.json --fixture RX_BLUE_ganrao_1 --iq "$RX_BLUE_GANRAO_1" --upstream-checkout "$COMBAT_RADAR_UPSTREAM_CHECKOUT" --out evidence/RX_BLUE_ganrao_1.json
ros2 launch sdr_receiver_py_wrapper open_source_reference_closed_loop.launch.py iq_source_path:="$RX_BLUE_GANRAO_1" upstream_checkout_path:="$COMBAT_RADAR_UPSTREAM_CHECKOUT"
```

State explicitly that an unavailable authorized checkout is a licensing/deployment blocker, not a decoder failure, and that official赛事引擎/live RF remain later integration layers.

- [ ] **Step 2: Run the complete software gate**

On Ubuntu 22.04/ROS 2 Humble, run:

```bash
python3 -m pytest sdr_receiver_py_wrapper/test -q
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper --symlink-install
. install/setup.bash
ros2 pkg executables sdr_receiver_py_wrapper
```

Expected: pytest has zero failures, colcon builds both packages, and the executable list includes `reference_acceptance`, `sdr_receiver_py_wrapper_node`, `mock_radar_context_publisher`, and `jam_code_assertion_node`.

- [ ] **Step 3: Run the confirmed offline and ROS evidence gates**

Run the two canonical confirmed-L1 commands from Step 1. Expected: offline report has `status: "pass"`, command `0x0A06`, the manifest's exact six-byte ASCII key, and matching SHA-256; the ROS launch exits successfully after exactly one matching `/sdr/jam_code` observation.

- [ ] **Step 4: Verify the branch diff and forbidden surfaces**

Run:

```bash
git status --short
git diff --check f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750..HEAD
rg -n "improved_v67|V67Decoder|decoder_shadow|RadarServerComm|server_comm|socket\.send" sdr_receiver_py_wrapper/sdr_receiver_py_wrapper sdr_receiver_py_wrapper/config sdr_receiver_py_wrapper/launch sdr_receiver_py_wrapper/setup.py sdr_receiver_py_wrapper/README.md README.md
find third_party/CombatRadarSdr2026 -type f -printf '%P\n' | sort
```

Expected: status is clean; `git diff --check` has no output; ripgrep has no matches; the final command prints only `UPSTREAM.md`, `__init__.py`, and `fetch_upstream.py` (ignoring removable `__pycache__`).

- [ ] **Step 5: Commit the acceptance document after both reviews**

```bash
git add docs/open_source_reference_acceptance_zh.md
git commit -m "docs: define open-source reference acceptance"
```

Expected: commit succeeds and `git status --short` is empty.

## Final branch acceptance

The coordinator may call `codex/open-source-replacement` complete only when all seven task commits exist, every task has separate recorded compliance and quality approvals, the complete Python and colcon gates pass, the confirmed recording produces the expected key through both offline and ROS paths, context-negative/fault samples produce zero publications, and no upstream source is tracked by Git. Candidate L2/L3 observations may remain unconfirmed; they must not be reported as failures or promoted to oracle status without independent CRC-valid repeated evidence and a manifest review.
