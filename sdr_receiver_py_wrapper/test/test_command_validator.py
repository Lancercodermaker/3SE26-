from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import importlib
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from sdr_receiver_py_wrapper.command_validator import (
    CommandValidator,
    ValidationResult,
)
from sdr_receiver_py_wrapper.competition_controller import (
    CompetitionController,
    CompetitionState,
    RadarContext,
)
from sdr_receiver_py_wrapper.models import DecodedCommand
from sdr_receiver_py_wrapper.patches import JamKeyEvent


MISSING = object()
SUBSTITUTED_COMMAND_CHANGES = [
    {"decoder_id": "shadow_v67"},
    {"crc8_ok": False},
    {"crc16_ok": False},
    {"target": "JAM_L2_KEY"},
    {"team": "BLUE"},
    {"context_version": 99},
    {"evidence": {"level": 1, "source": "substituted"}},
]


def command(
    *,
    payload=b"fcYqTC",
    cmd_id=0x0A06,
    crc8_ok=True,
    crc16_ok=True,
    decoder_id="improved_v67",
    level=1,
    target="JAM_L1_KEY",
    team="RED",
    context_version=4,
    evidence=MISSING,
):
    if evidence is MISSING:
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
        target=target,
        team=team,
        context_version=context_version,
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
    ("cmd_id", "reason"),
    [
        (True, "cmd_id must be an exact int"),
        (False, "cmd_id must be an exact int"),
        ("0x0A06", "cmd_id must be an exact int"),
        (-1, "cmd_id must be between 0x0000 and 0xFFFF"),
        (0x10000, "cmd_id must be between 0x0000 and 0xFFFF"),
    ],
)
def test_malformed_command_ids_are_stably_rejected(cmd_id, reason):
    result = CommandValidator().validate(command(cmd_id=cmd_id))

    assert result.accepted is False
    assert result.reason == reason


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


@pytest.mark.parametrize(
    "changes",
    SUBSTITUTED_COMMAND_CHANGES,
)
def test_publish_authorization_is_bound_to_original_command_identity(changes):
    validator = CommandValidator()
    original = command(payload=b"ABC123")
    result = validator.validate(original)
    substituted = replace(original, **changes)

    assert validator.begin_publish_authorization(substituted, result) is False
    assert validator.begin_publish_authorization(original, result) is True
    assert validator.commit_publish_authorization(original, result) is True


def test_publish_authorization_is_bound_to_original_result_identity():
    validator = CommandValidator()
    original = command(payload=b"ABC123")
    result = validator.validate(original)
    substituted = replace(result, ascii_code="FORGED")

    assert validator.begin_publish_authorization(original, substituted) is False
    assert validator.begin_publish_authorization(original, result) is True
    assert validator.commit_publish_authorization(original, result) is True


def test_pending_publish_authorization_capacity_rejects_without_evicting():
    validator = CommandValidator()
    commands = [command(payload=f"{index:06d}".encode()) for index in range(1025)]
    results = [validator.validate(item) for item in commands]

    assert all(result.accepted for result in results[:1024])
    assert results[-1].accepted is False
    assert results[-1].reason == "publication authorization capacity reached"
    assert validator.begin_publish_authorization(commands[0], results[0]) is True
    assert validator.commit_publish_authorization(commands[0], results[0]) is True

    replacement = validator.validate(commands[-1])
    assert replacement.accepted is True


def test_aborted_publication_releases_reservation_for_retry():
    validator = CommandValidator()
    item = command(payload=b"ABC123")
    first = validator.validate(item)

    assert validator.begin_publish_authorization(item, first) is True
    assert validator.abort_publish_authorization(item, first) is True

    retry = validator.validate(item)
    assert retry.accepted is True


def test_concurrent_duplicate_validation_accepts_exactly_one():
    validator = CommandValidator()
    workers = 32
    barrier = threading.Barrier(workers)

    def validate_once(_index):
        item = command(payload=b"RACE01")
        barrier.wait()
        return validator.validate(item)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(validate_once, range(workers)))

    assert sum(result.accepted for result in results) == 1
    assert sum("duplicate command" in result.reason for result in results) == 31


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


class FakeAdapter:
    def __init__(self):
        self.targets = []

    def get_stats_snapshot(self):
        return {"rf_state": "receiving"}

    def set_target(self, target):
        self.targets.append(target)


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
    node.adapter = FakeAdapter()
    node.profile_config = {}
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


def make_real_controller_node(
    receiver_node_module,
    *,
    level=1,
    min_interval=0.0,
    retry_limit=-1,
):
    node = make_node(receiver_node_module)
    node.controller = CompetitionController(
        max_jam_break_level=3,
        key_publish_min_interval_sec=min_interval,
        key_retry_limit=retry_limit,
    )
    context = RadarContext(
        self_id=9,
        self_color=2,
        radar_info_raw=((level & 0x03) << 3) | (1 << 5),
        jam_level=level,
        key_mutable=True,
        referee_online=True,
    )
    node.latest_context = context
    node.controller.update_context(context)
    return node


def jam_event(payload, *, level=1):
    key = bytes(payload[:6])
    return JamKeyEvent(
        cmd_id=0x0A06,
        payload=bytes(payload),
        key=key,
        ascii_code=key.decode("ascii", errors="replace"),
        level=level,
        team="BLUE",
        target=f"L{level}",
        source="legacy-core",
        timestamp=321.0,
    )


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

    foreign_command = command(payload=b"ABC123")
    foreign = CommandValidator().validate(foreign_command)
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(foreign_command, foreign)

    accepted_command = command(payload=b"ABC123")
    accepted = node.command_validator.validate(accepted_command)
    node._publish_validated_jam_code(accepted_command, accepted)
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(accepted_command, accepted)
    assert len(node.jam_code_pub.messages) == 1


@pytest.mark.parametrize("changes", SUBSTITUTED_COMMAND_CHANGES)
def test_publisher_rejects_every_substituted_command(
    receiver_node_module,
    changes,
):
    node = make_node(receiver_node_module)
    original = command(payload=b"ABC123")
    result = node.command_validator.validate(original)
    substituted = replace(original, **changes)

    with pytest.raises(ValueError):
        node._publish_validated_jam_code(substituted, result)

    assert node.jam_code_pub.messages == []
    node._publish_validated_jam_code(original, result)
    assert len(node.jam_code_pub.messages) == 1


def test_publisher_rechecks_primary_decoder_for_validator_signed_shadow(
    receiver_node_module,
):
    node = make_node(receiver_node_module)
    shadow = command(decoder_id="shadow_v67", payload=b"ABC123")
    result = node.command_validator.validate(shadow)

    with pytest.raises(ValueError, match="primary decoder"):
        node._publish_validated_jam_code(shadow, result)

    assert node.jam_code_pub.messages == []


def test_disabled_ros_output_discards_accepted_publish_authorization(
    receiver_node_module,
):
    node = make_node(receiver_node_module)
    node.publish_ros_outputs = False
    item = command(payload=b"ABC123")

    result = node._handle_decoded_command(item)

    assert result.accepted is True
    node.publish_ros_outputs = True
    with pytest.raises(ValueError, match="validated command"):
        node._publish_validated_jam_code(item, result)
    assert node.jam_code_pub.messages == []


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


@pytest.mark.parametrize("payload", [b"BAD!!!", b"ABC123X"])
def test_legacy_semantic_validation_precedes_real_controller_mutation(
    receiver_node_module,
    payload,
):
    node = make_real_controller_node(receiver_node_module)
    before = node.controller.status_snapshot()

    node._on_jam_key(jam_event(payload))

    assert node.controller.status_snapshot() == before
    assert node.controller.state == CompetitionState.JAM_L1
    assert node.controller.completed_level == 0
    assert node.controller.published_keys == {}
    assert node.controller.desired_target == "L1"
    assert node.adapter.targets == []
    assert node.jam_code_pub.messages == []


def test_adapter_stats_failure_aborts_controller_and_validator_for_retry(
    receiver_node_module,
):
    node = make_real_controller_node(receiver_node_module)
    before = node.controller.status_snapshot()

    def fail_stats():
        raise RuntimeError("stats unavailable")

    node.adapter.get_stats_snapshot = fail_stats
    with pytest.raises(RuntimeError, match="stats unavailable"):
        node._on_jam_key(jam_event(b"ABC123"))

    assert node.jam_code_pub.messages == []
    assert node.controller.status_snapshot() == before
    assert node.adapter.targets == []

    node.adapter.get_stats_snapshot = lambda: {"rf_state": "receiving"}
    node._on_jam_key(jam_event(b"ABC123"))

    assert len(node.jam_code_pub.messages) == 1
    assert node.controller.state == CompetitionState.WAIT_LEVEL_L2
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 1}


def test_ros_publish_failure_aborts_controller_and_validator_for_retry(
    receiver_node_module,
):
    node = make_real_controller_node(receiver_node_module)
    before = node.controller.status_snapshot()
    original_publish = node.jam_code_pub.publish

    def fail_publish(_message):
        raise RuntimeError("ROS publish failed")

    node.jam_code_pub.publish = fail_publish
    with pytest.raises(RuntimeError, match="ROS publish failed"):
        node._on_jam_key(jam_event(b"ABC123"))

    assert node.jam_code_pub.messages == []
    assert node.controller.status_snapshot() == before
    assert node.adapter.targets == []

    node.jam_code_pub.publish = original_publish
    node._on_jam_key(jam_event(b"ABC123"))

    assert len(node.jam_code_pub.messages) == 1
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 1}


def test_real_controller_retry_policy_can_publish_same_key_twice(
    receiver_node_module,
):
    node = make_real_controller_node(
        receiver_node_module,
        min_interval=0.0,
        retry_limit=-1,
    )
    event = jam_event(b"ABC123")

    node._on_jam_key(event)
    node._on_jam_key(event)

    assert len(node.jam_code_pub.messages) == 2
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 2}


def test_rate_limited_controller_decision_does_not_leak_reservation(
    receiver_node_module,
):
    node = make_real_controller_node(
        receiver_node_module,
        min_interval=60.0,
        retry_limit=-1,
    )
    event = jam_event(b"ABC123")

    node._on_jam_key(event)
    node._on_jam_key(event)
    assert len(node.jam_code_pub.messages) == 1
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 1}

    node.controller.key_publish_min_interval_sec = 0.0
    node._on_jam_key(event)

    assert len(node.jam_code_pub.messages) == 2
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 2}


def test_final_l3_commits_info_and_rf_target_only_after_ros_success(
    receiver_node_module,
):
    node = make_real_controller_node(receiver_node_module, level=3)
    before = node.controller.status_snapshot()
    original_publish = node.jam_code_pub.publish

    def fail_publish(_message):
        raise RuntimeError("final publish failed")

    node.jam_code_pub.publish = fail_publish
    with pytest.raises(RuntimeError, match="final publish failed"):
        node._on_jam_key(jam_event(b"XYZ789", level=3))

    assert node.controller.status_snapshot() == before
    assert node.controller.state == CompetitionState.JAM_L3
    assert node.controller.desired_target == "L3"
    assert node.controller.published_keys == {}
    assert node.adapter.targets == []
    assert node.jam_code_pub.messages == []

    node.jam_code_pub.publish = original_publish
    node._on_jam_key(jam_event(b"XYZ789", level=3))

    assert len(node.jam_code_pub.messages) == 1
    assert node.controller.state == CompetitionState.INFO
    assert node.controller.completed_level == 3
    assert node.controller.desired_target == "INFO"
    assert node.controller.status_snapshot()["published_key_counts"] == {"3": 1}
    assert node.adapter.targets == ["INFO"]


def test_legacy_callback_preserves_controller_target_without_publish(
    receiver_node_module,
):
    node = make_node(receiver_node_module)
    node.controller.handle_jam_key = lambda **_kwargs: SimpleNamespace(
        publish=False,
        warnings=(),
        level=1,
        target="L2",
        reason="advance RF only",
    )
    event = JamKeyEvent(
        cmd_id=0x0A06,
        payload=b"ABC123",
        key=b"ABC123",
        ascii_code="ABC123",
        level=1,
        team="BLUE",
        target="L1",
        source="legacy-core",
        timestamp=321.0,
    )

    node._on_jam_key(event)

    assert node.jam_code_pub.messages == []
    assert node.adapter.targets == ["L2"]


def test_legacy_callback_uses_controller_level_for_validation_and_output(
    receiver_node_module,
):
    node = make_node(receiver_node_module)
    node.controller.handle_jam_key = lambda **_kwargs: SimpleNamespace(
        publish=True,
        warnings=(),
        level=2,
        target=None,
        reason="controller selected L2",
    )
    event = JamKeyEvent(
        cmd_id=0x0A06,
        payload=b"ABC123",
        key=b"ABC123",
        ascii_code="ABC123",
        level=1,
        team="BLUE",
        target="L1",
        source="legacy-core",
        timestamp=321.0,
    )

    node._on_jam_key(event)

    assert len(node.jam_code_pub.messages) == 1
    message = node.jam_code_pub.messages[0]
    assert message.level == 2
    assert message.target == "JAM_L2_KEY"
