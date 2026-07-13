from dataclasses import FrozenInstanceError
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from sdr_receiver_py_wrapper.command_validator import (
    CommandValidator,
    ValidationResult,
)
from sdr_receiver_py_wrapper.models import DecodedCommand
from sdr_receiver_py_wrapper.patches import JamKeyEvent


MISSING = object()


def command(
    *,
    payload=b"fcYqTC",
    cmd_id=0x0A06,
    crc8_ok=True,
    crc16_ok=True,
    decoder_id="improved_v67",
    level=1,
):
    evidence = {} if level is MISSING else {"level": level}
    return DecodedCommand(
        cmd_id=cmd_id,
        payload=payload,
        decoder_id=decoder_id,
        profile="competition",
        crc8_ok=crc8_ok,
        crc16_ok=crc16_ok,
        crc_mode="validated",
        first_sample_index=10,
        last_sample_index=20,
        receive_wall_time=123.0,
        target="JAM_L1_KEY",
        team="RED",
        context_version=4,
        evidence=evidence,
    )


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"ABC12", "0x0A06 payload must be exactly 6 bytes"),
        (
            b"ABC12!",
            "0x0A06 payload must contain only ASCII letters or digits",
        ),
        (b"1234567", "0x0A06 payload must be exactly 6 bytes"),
        (
            b"\xC0ABC12",
            "0x0A06 payload must contain only ASCII letters or digits",
        ),
    ],
)
def test_0a06_rejects_invalid_payload_shape(payload, reason):
    result = CommandValidator().validate(command(payload=payload))

    assert result.accepted is False
    assert result.reason == reason


def test_0a06_accepts_six_ascii_alphanumeric_bytes():
    result = CommandValidator().validate(command(payload=b"fcYqTC"))

    assert result.accepted is True
    assert result.reason == "accepted"
    assert result.ascii_code == "fcYqTC"
    assert result.level == 1
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"


@pytest.mark.parametrize(
    ("crc8_ok", "crc16_ok", "reason"),
    [
        (False, True, "crc8_ok must be exact True"),
        (True, False, "crc16_ok must be exact True"),
        (1, True, "crc8_ok must be exact True"),
        (True, 1, "crc16_ok must be exact True"),
    ],
)
def test_crc_flags_must_both_be_exact_true(crc8_ok, crc16_ok, reason):
    result = CommandValidator().validate(
        command(crc8_ok=crc8_ok, crc16_ok=crc16_ok)
    )

    assert result.accepted is False
    assert result.reason == reason


def test_non_jam_command_is_explicitly_rejected():
    result = CommandValidator().validate(command(cmd_id=0x020A))

    assert result.accepted is False
    assert result.reason == "unsupported cmd_id: 0x020A"


@pytest.mark.parametrize(
    ("level", "reason"),
    [
        (MISSING, "0x0A06 evidence.level is missing"),
        (True, "0x0A06 evidence.level must be an exact int"),
        (False, "0x0A06 evidence.level must be an exact int"),
        (1.0, "0x0A06 evidence.level must be an exact int"),
        (0, "0x0A06 evidence.level must be between 1 and 3"),
        (4, "0x0A06 evidence.level must be between 1 and 3"),
        (-1, "0x0A06 evidence.level must be between 1 and 3"),
    ],
)
def test_level_must_be_present_exact_int_in_supported_range(level, reason):
    result = CommandValidator().validate(command(level=level))

    assert result.accepted is False
    assert result.reason == reason


def test_duplicate_key_is_cmd_payload_and_target_level():
    validator = CommandValidator()

    first = validator.validate(command(payload=b"ABC123", level=1))
    duplicate = validator.validate(command(payload=b"ABC123", level=1))
    different_level = validator.validate(command(payload=b"ABC123", level=2))
    different_payload = validator.validate(command(payload=b"DEF456", level=1))

    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.reason == (
        "duplicate command: cmd_id/payload/target_level already accepted"
    )
    assert duplicate.ascii_code == "ABC123"
    assert duplicate.level == 1
    assert different_level.accepted is True
    assert different_payload.accepted is True


class FakeRosMessage:
    def __init__(self):
        self.header = SimpleNamespace(stamp=None)


@pytest.fixture
def receiver_node_module(monkeypatch):
    rclpy = ModuleType("rclpy")
    rclpy_node = ModuleType("rclpy.node")
    rclpy_node.Node = type("Node", (), {})
    rclpy_qos = ModuleType("rclpy.qos")
    rclpy_qos.HistoryPolicy = SimpleNamespace(KEEP_LAST="keep_last")
    rclpy_qos.QoSProfile = type("QoSProfile", (), {})
    rclpy_qos.ReliabilityPolicy = SimpleNamespace(RELIABLE="reliable")
    std_msgs = ModuleType("std_msgs")
    std_msgs_msg = ModuleType("std_msgs.msg")
    std_msgs_msg.String = FakeRosMessage
    std_msgs_msg.UInt8 = FakeRosMessage
    sdr_receiver = ModuleType("sdr_receiver")
    sdr_receiver_msg = ModuleType("sdr_receiver.msg")
    sdr_receiver_msg.JamCode = FakeRosMessage
    sdr_receiver_msg.RadarContext = FakeRosMessage
    sdr_receiver_msg.RadarWirelessFrame = FakeRosMessage
    for name, module in {
        "rclpy": rclpy,
        "rclpy.node": rclpy_node,
        "rclpy.qos": rclpy_qos,
        "std_msgs": std_msgs,
        "std_msgs.msg": std_msgs_msg,
        "sdr_receiver": sdr_receiver,
        "sdr_receiver.msg": sdr_receiver_msg,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(
        sys.modules,
        "sdr_receiver_py_wrapper.receiver_node",
        raising=False,
    )
    module = importlib.import_module("sdr_receiver_py_wrapper.receiver_node")
    yield module
    sys.modules.pop("sdr_receiver_py_wrapper.receiver_node", None)


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def make_node(receiver_node_module):
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.primary_decoder_id = "improved_v67"
    node.command_validator = CommandValidator()
    node.publish_ros_outputs = True
    node.jam_code_pub = FakePublisher()
    node.latest_context = SimpleNamespace(
        self_id=9,
        self_color=2,
        radar_info_raw=0x2A,
        key_mutable=True,
    )
    node.run_mode = "competition"
    node.adapter = SimpleNamespace(
        get_stats_snapshot=lambda: {"rf_state": "receiving"},
    )
    node.controller = SimpleNamespace(rx_team="BLUE", own_team="RED")
    node.get_clock = lambda: SimpleNamespace(
        now=lambda: SimpleNamespace(to_msg=lambda: "stamp")
    )
    logger = SimpleNamespace(
        info=lambda _message: None,
        debug=lambda _message: None,
        warn=lambda _message: None,
    )
    node.get_logger = lambda: logger
    return node


def test_primary_gate_publishes_valid_command_once(receiver_node_module):
    node = make_node(receiver_node_module)

    shadow = node._handle_decoded_command(
        command(decoder_id="shadow_v67", payload=b"ABC123")
    )
    accepted = node._handle_decoded_command(command(payload=b"ABC123"))
    duplicate = node._handle_decoded_command(command(payload=b"ABC123"))
    invalid = node._handle_decoded_command(command(payload=b"BAD!!!"))

    assert shadow.accepted is False
    assert shadow.reason == (
        "decoder_id 'shadow_v67' is not primary decoder 'improved_v67'"
    )
    assert accepted.accepted is True
    assert duplicate.accepted is False
    assert "duplicate command" in duplicate.reason
    assert invalid.accepted is False
    assert len(node.jam_code_pub.messages) == 1
    message = node.jam_code_pub.messages[0]
    assert message.header.stamp == "stamp"
    assert message.valid is True
    assert message.command_id == 0x0A06
    assert message.level == 1
    assert message.team == "BLUE"
    assert message.target == "JAM_L1_KEY"
    assert message.radio_mode == "competition"
    assert message.rf_state == "receiving"
    assert message.radar_info_raw == 0x2A
    assert message.key_mutable is True
    assert message.key == list(b"ABC123")
    assert message.ascii_code == "ABC123"


def test_validated_publisher_rejects_unvalidated_or_reused_result(
    receiver_node_module,
):
    node = make_node(receiver_node_module)
    rejected = node.command_validator.validate(command(payload=b"BAD!!!"))

    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(command(payload=b"ABC123"), rejected)

    forged = ValidationResult(True, "accepted", ascii_code="ABC123", level=1)
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(command(payload=b"ABC123"), forged)

    foreign = CommandValidator().validate(command(payload=b"ABC123"))
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(command(payload=b"ABC123"), foreign)

    accepted = node.command_validator.validate(command(payload=b"ABC123"))
    node._publish_validated_jam_code(command(payload=b"ABC123"), accepted)
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(command(payload=b"ABC123"), accepted)
    assert len(node.jam_code_pub.messages) == 1


def test_legacy_callback_routes_invalid_key_through_validator(receiver_node_module):
    node = make_node(receiver_node_module)
    node.controller.handle_jam_key = lambda **_kwargs: SimpleNamespace(
        publish=True,
        warnings=(),
        level=1,
        target=None,
        reason="accepted by legacy controller",
    )
    event = JamKeyEvent(
        cmd_id=0x0A06,
        payload=b"BAD!!!",
        key=b"BAD!!!",
        ascii_code="BAD!!!",
        level=1,
        team="BLUE",
        target="L1",
        source="legacy-core",
        timestamp=321.0,
    )

    node._on_jam_key(event)

    assert node.jam_code_pub.messages == []
