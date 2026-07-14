"""Pinned metadata for the optional CombatRadarSdr2026 integration boundary."""

UPSTREAM_REPOSITORY = "https://github.com/qianchuan-wys/CombatRadarSdr2026.git"
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
LICENSE_STATUS = "NO_EXPLICIT_LICENSE"
