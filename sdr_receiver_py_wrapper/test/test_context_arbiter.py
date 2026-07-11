from sdr_receiver_py_wrapper.context_arbiter import ContextArbiter, Observation


def obs(level, *, source="/judge/radar_context", self_id=9, progress=4, t=0.0):
    return Observation(source, self_id, 2, 0x20 | (level << 3), level, True, progress, 400, t)


def test_diagnostic_source_cannot_override_authority():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert arbiter.observe(obs(1, t=0.0)).accepted
    result = arbiter.observe(obs(3, source="/match_info", t=5.0))
    assert not result.accepted
    assert result.reason == "diagnostic_source"
    assert arbiter.accepted_level == 1


def test_invalid_self_id_never_flips_team():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, self_id=9, t=0.0))
    result = arbiter.observe(obs(1, self_id=176, t=1.0))
    assert not result.accepted
    assert arbiter.own_team == "RED"


def test_prematch_l3_is_logged_but_does_not_retune():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, progress=2, t=0.0))
    for t in (1.0, 2.0, 3.0, 4.0):
        result = arbiter.observe(obs(3, progress=2, t=t))
    assert not result.target_changed
    assert arbiter.accepted_level is None
