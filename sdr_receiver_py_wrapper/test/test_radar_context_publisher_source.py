from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HEADER_PATH = REPOSITORY_ROOT / "src/radar_referee/include/robot_referee/RefereeControl.hpp"
SOURCE_PATH = REPOSITORY_ROOT / "src/radar_referee/src/RefereeControl.cpp"


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_braced_region(source: str, marker: str) -> str:
    marker_index = source.find(marker)
    assert marker_index != -1, f"missing source marker: {marker}"

    opening_brace = source.find("{", marker_index + len(marker))
    assert opening_brace != -1, f"missing opening brace after: {marker}"

    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1:index]

    raise AssertionError(f"unbalanced braces after: {marker}")


def test_header_declares_radar_context_publisher_contract():
    header = HEADER_PATH.read_text(encoding="utf-8")

    assert '#include "sdr_receiver/msg/radar_context.hpp"' in header
    assert (
        "rclcpp::Publisher<sdr_receiver::msg::RadarContext>::SharedPtr "
        "_radarContextPub;"
    ) in _normalize_whitespace(header)
    assert "void publishRadarContext();" in _normalize_whitespace(header)


def test_constructor_creates_single_authoritative_reliable_publisher():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    constructor_body = _normalize_whitespace(
        _extract_braced_region(source, "RefereeControl::RefereeControl()")
    )
    expected_creation = _normalize_whitespace(
        """
        _radarContextPub = this->create_publisher<sdr_receiver::msg::RadarContext>(
            "/judge/radar_context", rclcpp::QoS(10).reliable());
        """
    )

    assert constructor_body.count(expected_creation) == 1
    assert constructor_body.count("_radarContextPub") == 1


def test_publish_radar_context_builds_and_publishes_one_atomic_message():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    body = _normalize_whitespace(
        _extract_braced_region(source, "void RefereeControl::publishRadarContext()")
    )
    statements = [
        "sdr_receiver::msg::RadarContext msg;",
        "msg.header.stamp = this->get_clock()->now();",
        "msg.self_id = _self_ID;",
        "msg.self_color = _self_ID == 9 ? 2 : (_self_ID == 109 ? 0 : -1);",
        "msg.radar_info_raw = _radar_info_raw;",
        "msg.jam_level = _jam_level;",
        "msg.key_mutable = _key_mutable;",
        "msg.game_progress = _game_progress;",
        "msg.match_time = _game_progress == 4 ? static_cast<int16_t>(_stage_remain_time) : -200;",
        "msg.referee_online = _self_ID == 9 || _self_ID == 109;",
    ]

    for statement in statements:
        assert body.count(statement) == 1

    publish = "_radarContextPub->publish(msg);"
    assert body.count(publish) == 1
    publish_index = body.index(publish)
    assert all(body.index(statement) < publish_index for statement in statements)


def test_radar_info_branch_publishes_after_all_same_frame_assignments():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    branch = _normalize_whitespace(
        _extract_braced_region(source, "std::is_same_v<T, radar_info_t>")
    )
    assignments = [
        "_radar_info_raw = radar_info_data.radar_info;",
        "_jam_level = (radar_info_data.radar_info >> 3) & 0x03;",
        "_key_mutable = (radar_info_data.radar_info & 0x20) == 0x20;",
    ]
    publish = "publishRadarContext();"

    for assignment in assignments:
        assert branch.count(assignment) == 1
    assert branch.count(publish) == 1
    publish_index = branch.index(publish)
    assert all(branch.index(assignment) < publish_index for assignment in assignments)


def test_wireless_key_callback_does_not_override_authoritative_key_mutable():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    callback_body = _extract_braced_region(
        source, "void RefereeControl::wirelessKeyCallback"
    )

    assert re.search(r"\b_key_mutable\s*=", callback_body) is None
