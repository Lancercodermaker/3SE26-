from pathlib import Path

from sdr_receiver_py_wrapper.profile_import import load_adaptive_profile, normalize_adaptive_profile


def test_load_best_profile_yaml(tmp_path: Path):
    path = tmp_path / "best_profile.yaml"
    path.write_text(
        "\n".join(
            [
                "adaptive_profile:",
                "  team: RED",
                "  profile: INFO_L3",
                "  rescue: L3",
                "  filter: wide_weak_soft_notch",
                "  gain: 73",
                "  rf_bw_hz: 420000",
                "  freq_offset_hz: 150000",
                "  class: CRC8_STABLE",
                "  score: 123.5",
                "",
            ]
        ),
        encoding="utf-8",
    )

    profile = load_adaptive_profile(str(path))

    assert profile["team"] == "RED"
    assert profile["target"] == "INFO"
    assert profile["rescue"] == "L3"
    assert profile["gain"] == 73
    assert profile["rf_bw_hz"] == 420000
    assert profile["freq_offset_hz"] == 150000


def test_normalize_json_best_row_uses_khz_fields():
    profile = normalize_adaptive_profile(
        {
            "team": "BLUE",
            "profile": "INFO",
            "rescue": "normal",
            "filter": "loose3",
            "gain": "70",
            "rf_bw_khz": "300",
            "offset_khz": "-80",
        }
    )

    assert profile["team"] == "BLUE"
    assert profile["rescue"] == ""
    assert profile["rf_bw_hz"] == 300000
    assert profile["freq_offset_hz"] == -80000
