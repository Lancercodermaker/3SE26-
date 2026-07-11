import pytest

from sdr_receiver_py_wrapper.context_arbiter import ContextArbiter, Observation


def obs(level, *, source="/judge/radar_context", self_id=9, progress=4, t=0.0):
    return Observation(source, self_id, 2, 0x20 | (level << 3), level, True, progress, 400, t)


def assert_rejected_decision(result, reason, *, level):
    assert result.accepted is False
    assert result.target_changed is False
    assert result.reason == reason
    assert result.level == level
    assert result.target is None


def assert_stable_decision(result, level, *, changed=True):
    assert result.accepted is True
    assert result.target_changed is changed
    assert result.reason == "stable_level"
    assert result.level == level
    assert result.target == f"L{level}"


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
    first_lower = arbiter.observe(obs(1, t=4.0))
    assert_rejected_decision(first_lower, "level_not_stable", level=3)
    second_lower = arbiter.observe(obs(1, t=4.4))
    assert_rejected_decision(second_lower, "level_not_stable", level=3)
    stable_lower = arbiter.observe(obs(1, t=5.1))
    assert_stable_decision(stable_lower, 1, changed=True)


@pytest.mark.parametrize(
    "times",
    [
        pytest.param((0.0, 0.1, 0.2), id="count_without_duration"),
        pytest.param((0.0, 1.1), id="duration_without_count"),
    ],
)
def test_level_requires_count_and_duration_independently(times):
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    results = [arbiter.observe(obs(1, t=t)) for t in times]

    assert_rejected_decision(results[-1], "level_not_stable", level=None)


def test_level_change_resets_candidate_window():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert_rejected_decision(
        arbiter.observe(obs(1, t=0.0)), "level_not_stable", level=None
    )
    assert_rejected_decision(
        arbiter.observe(obs(1, t=0.4)), "level_not_stable", level=None
    )
    assert_rejected_decision(
        arbiter.observe(obs(3, t=0.5)), "level_not_stable", level=None
    )
    assert_rejected_decision(
        arbiter.observe(obs(1, t=0.6)), "level_not_stable", level=None
    )
    assert_rejected_decision(
        arbiter.observe(obs(1, t=1.2)), "level_not_stable", level=None
    )
    assert_stable_decision(arbiter.observe(obs(1, t=1.7)), 1, changed=True)


@pytest.mark.parametrize(
    ("rejected_observation", "reason"),
    [
        pytest.param(
            obs(2, source="/match_info", t=0.5),
            "diagnostic_source",
            id="diagnostic_source",
        ),
        pytest.param(
            obs(2, self_id=176, t=0.5),
            "invalid_radar_id",
            id="invalid_radar_id",
        ),
        pytest.param(
            obs(2, progress=2, t=0.5),
            "prematch_observation",
            id="prematch_observation",
        ),
        pytest.param(obs(4, t=0.5), "invalid_level", id="invalid_level"),
    ],
)
def test_rejected_observation_does_not_advance_or_clear_candidate(
    rejected_observation, reason
):
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert_rejected_decision(
        arbiter.observe(obs(2, t=0.0)), "level_not_stable", level=None
    )

    rejected = arbiter.observe(rejected_observation)
    assert_rejected_decision(rejected, reason, level=None)

    second_candidate = arbiter.observe(obs(2, t=1.1))
    assert_rejected_decision(second_candidate, "level_not_stable", level=None)
    third_candidate = arbiter.observe(obs(2, t=1.2))
    assert_stable_decision(third_candidate, 2, changed=True)


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
