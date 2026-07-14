# CombatRadarSdr2026 upstream boundary

## Pinned source

- Repository: <https://github.com/qianchuan-wys/CombatRadarSdr2026.git>
- Commit: `13b13a68b7111a15163aedc97f1cb17722f45ad2`
- License audit date: 2026-07-14
- License status: **no explicit license**

The pinned Git tree contains no `LICENSE`, `LICENCE`, `COPYING`, or `NOTICE`
file. Its README uses the word "open source" but does not state license terms or
grant permission to copy, modify, or redistribute the code. The tree also has no
`pyproject.toml`, `setup.py`, or `setup.cfg`, so it is not an installable Python
distribution and must not be added as a PEP 508/VCS requirement.

Until the authors provide explicit written permission or a compatible license,
this repository must not publish, vendor, subtree, or redistribute upstream
source. `sdr_receiver_py_wrapper/requirements.txt` therefore remains an offline-
safe dependency list with no URL or VCS install hook.

## Allowlist held for future permission

These files are the complete integration allowlist, but **none is vendored here**:

| Upstream path | Pinned Git blob |
| --- | --- |
| `phy.py` | `b842cc16cb4b2b04874268839ebf705603e5f182` |
| `protocol.py` | `5195c9a7183c2087184f9e5de9cbeff96d044b0f` |
| `radio_profiles.py` | `b189816d6802e31a23c0ee567d6e7d72cf00fd5f` |
| `parser/gnuradio_frame_parser.py` | `ed1b4ec02ff147be7d9af98fe2fdf7f9ff01ff97` |

`server_comm.py` is explicitly excluded. It must not be imported, copied, or
made a runtime dependency; the integration boundary does not include upstream
TCP/server behavior.

## Explicit local fetch for authorized evaluation

The standard install and test paths never contact upstream. An operator who is
authorized to inspect the upstream code can audit the exact plan without network
or filesystem changes on Windows or WSL:

```text
python third_party/CombatRadarSdr2026/fetch_upstream.py --print-plan <destination>
```

The actual opt-in command requires an acknowledgement of the license status:

```text
python third_party/CombatRadarSdr2026/fetch_upstream.py --acknowledge-no-license <destination>
```

It uses a `blob:none` partial fetch and creates a separate sparse checkout at the
pinned commit. The helper verifies each `HEAD:path` and materialized file against
the blob table above with lazy fetching disabled. It also verifies that the local
Git object database contains exactly the four allowlisted blobs; non-allowlisted
upstream blobs are neither downloaded nor materialized.

Fetching occurs in a uniquely named staging directory beside the destination.
An exclusive sibling lock rejects concurrent helper runs for the same target.
Only a fully verified checkout is atomically renamed to the requested path. On
failure, the helper removes only its own staging directory and lock: a requested
target remains absent, or a pre-existing target remains untouched, so a later
retry is safe. The destination parent must already exist and filesystem roots are
rejected. The resulting checkout is not imported by this project and must not be
committed or redistributed without written permission.

## Local modifications and adapter boundary

- No upstream source has been modified because no upstream source is present.
- `__init__.py` contains only immutable provenance and allowlist metadata.
- `fetch_upstream.py` is a project-authored, explicit deployment/audit helper; it
  performs no work when this package or the receiver runtime is imported.
- Project adapters and tests must consume a future decoder interface without
  depending on upstream server communication. Any future upstream integration
  requires a renewed license review and a separate, reviewable change.
