from sdr_receiver_py_wrapper.context_arbiter import ContextArbiter, Observation


def obs(level, *, source="/judge/radar_context", self_id=9, progress=4, t=0.0):
    return Observation(source, self_id, 2, 0x20 | (level << 3), level, True, progress, 400, t)


def test_diagnostic_source_cannot_override_authority():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    pending = arbiter.observe(obs(1, t=0.0))
    assert not pending.accepted
    assert pending.reason == "level_not_stable"
    assert pending.level is None
    assert pending.target is None
    assert not arbiter.observe(obs(1, t=0.4)).accepted
    initial = arbiter.observe(obs(1, t=1.1))
    assert initial.accepted
    assert initial.target_changed
    assert initial.reason == "stable_level"
    assert initial.level == 1
    assert initial.target == "L1"
    result = arbiter.observe(obs(3, source="/match_info", self_id=109, t=5.0))
    assert not result.accepted
    assert result.reason == "diagnostic_source"
    assert result.level == 1
    assert arbiter.accepted_level == 1
    assert arbiter.own_team == "RED"


def test_level_requires_count_and_duration_then_can_return_lower():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert not arbiter.observe(obs(1, t=0.0)).target_changed
    assert not arbiter.observe(obs(1, t=0.4)).target_changed
    assert arbiter.observe(obs(1, t=1.1)).target == "L1"
    assert not arbiter.observe(obs(3, t=2.0)).target_changed
    assert not arbiter.observe(obs(3, t=2.4)).target_changed
    assert arbiter.observe(obs(3, t=3.1)).target == "L3"
    arbiter.observe(obs(1, t=4.0))
    arbiter.observe(obs(1, t=4.4))
    assert arbiter.observe(obs(1, t=5.1)).target == "L1"


def test_invalid_level_does_not_change_accepted_state():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, t=0.0))
    arbiter.observe(obs(1, t=0.4))
    accepted = arbiter.observe(obs(1, t=1.1))
    assert accepted.accepted
    assert accepted.level == 1

    result = arbiter.observe(obs(4, t=2.0))

    assert not result.accepted
    assert not result.target_changed
    assert result.reason == "invalid_level"
    assert result.level == 1
    assert result.target is None
    assert arbiter.accepted_level == 1


def test_invalid_self_id_never_flips_team():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, self_id=9, t=0.0))
    result = arbiter.observe(obs(1, self_id=176, t=1.0))
    assert not result.accepted
    assert result.reason == "invalid_radar_id"
    assert arbiter.own_team == "RED"


def test_prematch_l3_is_logged_but_does_not_retune():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    observations = [obs(1, progress=2, t=0.0)]
    observations.extend(obs(3, progress=2, t=t) for t in (1.0, 2.0, 3.0, 4.0))

    for observation in observations:
        result = arbiter.observe(observation)
        assert result.accepted is False
        assert result.target_changed is False
        assert result.reason == "prematch_observation"
        assert result.level is None

    assert arbiter.accepted_level is None
