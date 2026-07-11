from pathlib import Path
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
VISION_INTERFACE = REPO_ROOT / "src" / "interface" / "vision_interface"
RADAR_REFEREE = REPO_ROOT / "src" / "radar_referee"


def _message_fields(message_path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in message_path.read_text(encoding="utf-8").splitlines():
        declaration = raw_line.split("#", 1)[0].strip()
        if not declaration:
            continue
        field_type, field_name = declaration.split()
        fields[field_name] = field_type
    return fields


def _child_texts(root: ET.Element, tag: str) -> set[str]:
    return {
        child.text.strip()
        for child in root.findall(tag)
        if child.text and child.text.strip()
    }


def _field_contract_errors(
    message_name: str, actual: dict[str, str], expected: dict[str, str]
) -> list[str]:
    return [
        f"{message_name}: expected {field_type} {field_name}, "
        f"got {actual.get(field_name)!r}"
        for field_name, field_type in expected.items()
        if actual.get(field_name) != field_type
    ]


def test_message_schemas_cover_current_radar_referee_consumer_contract():
    radar_to_sentry = _message_fields(VISION_INTERFACE / "msg" / "Radar2Sentry.msg")
    detect_result = _message_fields(VISION_INTERFACE / "msg" / "DetectResult.msg")
    match_info = _message_fields(VISION_INTERFACE / "msg" / "MatchInfo.msg")
    errors = _field_contract_errors(
        "Radar2Sentry.msg",
        radar_to_sentry,
        {
            "radar_enemy_x": "float32[5]",
            "radar_enemy_y": "float32[5]",
            "radar_ally_x": "float32[5]",
            "radar_ally_y": "float32[5]",
        },
    )
    errors += _field_contract_errors(
        "DetectResult.msg",
        detect_result,
        {
            "header": "std_msgs/Header",
            "blue_x": "float32[5]",
            "blue_y": "float32[5]",
            "red_x": "float32[5]",
            "red_y": "float32[5]",
            "outpost_alive": "bool",
        },
    )
    errors += _field_contract_errors(
        "MatchInfo.msg",
        match_info,
        {
            "self_color": "int8",
            "match_time": "int16",
            "robot_hp": "uint8[16]",
            "marks": "uint8[5]",
            "ultimate": "uint8",
            "eventtype": "uint32",
            "self_id": "uint8",
            "radar_info_raw": "uint8",
            "jam_level": "uint8",
            "key_mutable": "bool",
            "referee_online": "bool",
        },
    )
    assert not errors, "\n" + "\n".join(errors)


def test_package_manifests_declare_direct_dependencies_and_mit_license():
    vision_manifest = ET.parse(VISION_INTERFACE / "package.xml").getroot()
    radar_manifest = ET.parse(RADAR_REFEREE / "package.xml").getroot()
    errors = []
    vision_licenses = _child_texts(vision_manifest, "license")
    if vision_licenses != {"MIT"}:
        errors.append(f"vision_interface license: expected MIT, got {vision_licenses}")
    missing_vision_buildtools = {"ament_cmake", "ament_cmake_auto"} - _child_texts(
        vision_manifest, "buildtool_depend"
    )
    if missing_vision_buildtools:
        errors.append(
            f"vision_interface missing buildtool dependencies: {missing_vision_buildtools}"
        )

    radar_licenses = _child_texts(radar_manifest, "license")
    if radar_licenses != {"MIT"}:
        errors.append(f"radar_referee license: expected MIT, got {radar_licenses}")
    missing_radar_dependencies = {
        "rclcpp",
        "std_msgs",
        "vision_interface",
        "sdr_receiver",
        "libopencv-dev",
        "boost",
    } - _child_texts(radar_manifest, "depend")
    if missing_radar_dependencies:
        errors.append(
            f"radar_referee missing direct dependencies: {missing_radar_dependencies}"
        )
    missing_radar_exec_dependencies = {"launch", "launch_ros"} - _child_texts(
        radar_manifest, "exec_depend"
    )
    if missing_radar_exec_dependencies:
        errors.append(
            "radar_referee missing exec dependencies: "
            f"{missing_radar_exec_dependencies}"
        )
    assert not errors, "\n" + "\n".join(errors)


def test_radar_referee_requires_cpp17():
    cmake = (RADAR_REFEREE / "CMakeLists.txt").read_text(encoding="utf-8")
    required_lines = {
        "set(CMAKE_CXX_STANDARD 17)",
        "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
    }
    missing_lines = {line for line in required_lines if line not in cmake}
    assert not missing_lines, f"missing C++17 requirements: {missing_lines}"
