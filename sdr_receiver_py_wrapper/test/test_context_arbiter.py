from sdr_receiver_py_wrapper.context_arbiter import ContextArbiter, Observation


def obs(level, *, source="/judge/radar_context", self_id=9, progress=4, t=0.0):
    return Observation(source, self_id, 2, 0x20 | (level << 3), level, True, progress, 400, t)


def test_diagnostic_source_cannot_override_authority():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    initial = arbiter.observe(obs(1, t=0.0))
    assert initial.accepted
    assert not initial.target_changed
    assert initial.reason == "initial_context"
    result = arbiter.observe(obs(3, source="/match_info", self_id=109, t=5.0))
    assert not result.accepted
    assert result.reason == "diagnostic_source"
    assert result.level == 1
    assert arbiter.accepted_level == 1
    assert arbiter.own_team == "RED"


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
