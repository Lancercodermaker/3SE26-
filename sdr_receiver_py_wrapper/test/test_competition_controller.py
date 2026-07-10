import time

from sdr_receiver_py_wrapper.competition_controller import (
    CompetitionController,
    CompetitionState,
    RadarContext,
)


def ctx(level, self_id=9):
    return RadarContext(
        self_id=self_id,
        self_color=2 if self_id < 100 else 0,
        radar_info_raw=((level & 0x03) << 3) | (1 << 5),
        jam_level=level,
        key_mutable=True,
        referee_online=True,
    )


def test_competition_l1_l2_l3_info_flow():
    controller = CompetitionController(
        max_jam_break_level=3,
        key_publish_min_interval_sec=0.01,
        key_retry_limit=3,
    )

    decision = controller.update_context(ctx(1))
    assert decision.own_team == "RED"
    assert decision.rx_team == "BLUE"
    assert decision.team == "BLUE"
    assert decision.target == "L1"
    assert decision.state == CompetitionState.JAM_L1

    key_decision = controller.handle_jam_key(level=1, key=b"ABC123")
    assert key_decision.publish is True
    assert key_decision.state == CompetitionState.WAIT_LEVEL_L2

    decision = controller.update_context(ctx(2))
    assert decision.target == "L2"
    assert decision.state == CompetitionState.JAM_L2

    key_decision = controller.handle_jam_key(level=2, key=b"DEF456")
    assert key_decision.publish is True
    assert key_decision.state == CompetitionState.WAIT_LEVEL_L3

    decision = controller.update_context(ctx(3))
    assert decision.target == "L3"
    assert decision.state == CompetitionState.JAM_L3

    key_decision = controller.handle_jam_key(level=3, key=b"GHI789")
    assert key_decision.publish is True
    assert key_decision.target == "INFO"
    assert key_decision.state == CompetitionState.INFO


def test_duplicate_key_is_rate_limited():
    controller = CompetitionController(
        max_jam_break_level=3,
        key_publish_min_interval_sec=0.05,
        key_retry_limit=2,
    )
    controller.update_context(ctx(1))

    first = controller.handle_jam_key(level=1, key=b"ABC123")
    second = controller.handle_jam_key(level=1, key=b"ABC123")
    assert first.publish is True
    assert second.publish is False

    time.sleep(0.06)
    third = controller.handle_jam_key(level=1, key=b"ABC123")
    assert third.publish is True

    time.sleep(0.06)
    fourth = controller.handle_jam_key(level=1, key=b"ABC123")
    assert fourth.publish is False


def test_duplicate_key_has_no_default_retry_limit():
    controller = CompetitionController(
        max_jam_break_level=3,
        key_publish_min_interval_sec=0.01,
    )
    controller.update_context(ctx(1))

    first = controller.handle_jam_key(level=1, key=b"ABC123")
    assert first.publish is True

    for _ in range(4):
        time.sleep(0.02)
        duplicate = controller.handle_jam_key(level=1, key=b"ABC123")
        assert duplicate.publish is True


def test_jam_level_zero_does_not_select_target():
    controller = CompetitionController()
    decision = controller.update_context(ctx(0))
    assert decision.own_team == "RED"
    assert decision.rx_team == "BLUE"
    assert decision.team == "BLUE"
    assert decision.target is None
    assert controller.desired_target is None


def test_blue_radar_receives_red_waveforms():
    controller = CompetitionController()
    decision = controller.update_context(ctx(1, self_id=109))
    assert decision.own_team == "BLUE"
    assert decision.rx_team == "RED"
    assert decision.team == "RED"
    assert controller.own_team == "BLUE"
    assert controller.rx_team == "RED"
