import math
import warnings
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from sdr_receiver_py_wrapper.models import RfMetrics
from sdr_receiver_py_wrapper.rf_safety import (
    GainDecision,
    RfSafetyController,
    RfState,
    classify_rf,
    measure_rf,
)


def test_ad9363_code_scale_normalizes_complex_magnitude_without_hiding_clipping():
    samples = np.full(4096, 2047 + 2047j, dtype=np.complex64)

    metrics = measure_rf(samples, code_scale=2048.0)

    assert isinstance(metrics, RfMetrics)
    assert metrics.rms == pytest.approx(np.sqrt(2) * 2047 / 2048)
    assert metrics.rms > 1.0
    assert metrics.peak == pytest.approx(np.sqrt(2) * 2047 / 2048)
    assert metrics.clipping_ratio > 0.99
    assert metrics.sample_count == 4096


def test_clipping_counts_a_sample_when_either_i_or_q_reaches_threshold():
    samples = np.array(
        [
            0 + 0j,
            0.98 * 2048 + 0j,
            0 + 0.98j * 2048,
            0.97 * 2048 + 0.97j * 2048,
        ],
        dtype=np.complex64,
    )

    metrics = measure_rf(samples)

    assert metrics.clipping_ratio == pytest.approx(0.5)


def test_empty_complex_array_produces_zero_metrics_without_runtime_warning():
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        metrics = measure_rf(np.array([], dtype=np.complex64))

    assert caught_warnings == []
    assert metrics == RfMetrics(
        rms=0.0,
        peak=0.0,
        clipping_ratio=0.0,
        sample_count=0,
    )


@pytest.mark.parametrize(
    "code_scale",
    [0, -1, np.nan, np.inf, -np.inf, True, 10**400],
)
def test_measure_rf_rejects_invalid_code_scale(code_scale):
    with pytest.raises(ValueError, match="code_scale must be a finite positive number"):
        measure_rf(np.array([1 + 1j]), code_scale=code_scale)


@pytest.mark.parametrize(
    ("samples", "error_type", "message"),
    [
        ([1 + 1j], TypeError, "samples must be a numpy.ndarray"),
        (np.array([1.0]), TypeError, "samples must have a complex dtype"),
        (
            np.array([[1 + 1j]], dtype=np.complex64),
            ValueError,
            "samples must be one-dimensional",
        ),
        (
            np.array([np.nan + 1j], dtype=np.complex64),
            ValueError,
            "samples must contain only finite values",
        ),
        (
            np.array([1 + np.inf * 1j], dtype=np.complex128),
            ValueError,
            "samples must contain only finite values",
        ),
    ],
)
def test_measure_rf_rejects_invalid_sample_inputs(samples, error_type, message):
    with pytest.raises(error_type, match=message):
        measure_rf(samples)


def test_measure_rf_accepts_complex128_without_mutating_the_input():
    samples = np.array([3 + 4j, -3 - 4j], dtype=np.complex128)
    before = samples.copy()

    metrics = measure_rf(samples, code_scale=5.0)

    assert metrics.rms == pytest.approx(1.0)
    assert metrics.peak == pytest.approx(1.0)
    np.testing.assert_array_equal(samples, before)


@pytest.mark.parametrize(
    ("dtype", "component"),
    [
        (np.complex64, 1e30),
        (np.complex128, 1e200),
    ],
)
def test_measure_rf_keeps_representable_extreme_metrics_finite(dtype, component):
    samples = np.array([complex(component, component)], dtype=dtype)

    metrics = measure_rf(samples)

    assert math.isfinite(metrics.rms)
    assert math.isfinite(metrics.peak)
    assert metrics.rms == pytest.approx(math.hypot(component, component) / 2048.0)
    assert metrics.peak == pytest.approx(math.hypot(component, component) / 2048.0)


def test_measure_rf_rejects_normalized_magnitude_beyond_float64_range():
    component = np.finfo(np.float64).max
    samples = np.array([complex(component, component)], dtype=np.complex128)

    with pytest.raises(ValueError, match="RF measurements exceed finite range"):
        measure_rf(samples, code_scale=1.0)


def test_measure_rf_normalizes_components_before_extreme_magnitude():
    component = np.finfo(np.float64).max
    samples = np.array([complex(component, component)], dtype=np.complex128)
    expected = math.hypot(component / 2048.0, component / 2048.0)

    metrics = measure_rf(samples)

    assert math.isfinite(metrics.rms)
    assert math.isfinite(metrics.peak)
    assert metrics.rms == pytest.approx(expected)
    assert metrics.peak == pytest.approx(expected)


def test_ad9363_clipping_is_not_rf_low():
    samples = np.full(4096, 2047 + 2047j, dtype=np.complex64)
    metrics = measure_rf(samples, code_scale=2048.0)

    assert metrics.rms > 1.0
    assert metrics.clipping_ratio > 0.99
    assert classify_rf(metrics) == RfState.CLIPPED


def test_rf_states_are_stable_string_enum_values():
    assert {state.value for state in RfState} == {
        "disconnected",
        "clipped",
        "too_strong",
        "too_weak",
        "linear",
    }
    assert all(isinstance(state, str) for state in RfState)


def test_classification_priority_is_disconnected_then_clipped_then_strong():
    disconnected_but_extreme = RfMetrics(
        rms=10.0,
        peak=10.0,
        clipping_ratio=1.0,
        sample_count=0,
    )
    clipped_and_strong = RfMetrics(
        rms=2.0,
        peak=2.0,
        clipping_ratio=0.5,
        sample_count=100,
    )

    assert classify_rf(disconnected_but_extreme) == RfState.DISCONNECTED
    assert classify_rf(clipped_and_strong) == RfState.CLIPPED


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (RfMetrics(0.8, 0.9, 0.0, 100), RfState.TOO_STRONG),
        (RfMetrics(0.05, 0.1, 0.0, 100), RfState.TOO_WEAK),
        (RfMetrics(0.4, 0.5, 0.0, 100), RfState.LINEAR),
    ],
)
def test_classify_rf_covers_non_clipped_signal_states(metrics, expected):
    assert classify_rf(metrics) == expected


def test_classify_rf_accepts_configurable_thresholds():
    metrics = RfMetrics(
        rms=0.2,
        peak=0.3,
        clipping_ratio=0.001,
        sample_count=1000,
    )

    assert classify_rf(metrics, clipping_threshold=0.001) == RfState.CLIPPED
    assert (
        classify_rf(
            metrics,
            clipping_threshold=0.01,
            strong_rms=0.15,
            weak_rms=0.1,
        )
        == RfState.TOO_STRONG
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"clipping_threshold": 0},
        {"clipping_threshold": -0.001},
        {"clipping_threshold": 1.001},
        {"clipping_threshold": np.nan},
        {"weak_rms": -0.1},
        {"strong_rms": np.inf},
        {"weak_rms": 0.5, "strong_rms": 0.5},
        {"weak_rms": 0.6, "strong_rms": 0.5},
    ],
)
def test_classify_rf_rejects_invalid_thresholds(kwargs):
    metrics = RfMetrics(0.2, 0.3, 0.0, 100)

    with pytest.raises(ValueError, match="RF classification thresholds"):
        classify_rf(metrics, **kwargs)


def test_classify_rf_requires_rf_metrics():
    with pytest.raises(TypeError, match="metrics must be an RfMetrics"):
        classify_rf(object())


@pytest.mark.parametrize(
    "metrics",
    [
        RfMetrics(np.nan, 1.0, 0.0, 100),
        RfMetrics(np.inf, np.inf, 0.0, 100),
        RfMetrics(-0.1, 1.0, 0.0, 100),
        RfMetrics(True, 1.0, 0.0, 100),
        RfMetrics(0.1, np.nan, 0.0, 100),
        RfMetrics(0.1, np.inf, 0.0, 100),
        RfMetrics(0.1, -0.1, 0.0, 100),
        RfMetrics(0.1, True, 0.0, 100),
        RfMetrics(0.1, 1.0, np.nan, 100),
        RfMetrics(0.1, 1.0, np.inf, 100),
        RfMetrics(0.1, 1.0, -0.1, 100),
        RfMetrics(0.1, 1.0, 1.1, 100),
        RfMetrics(0.1, 1.0, True, 100),
        RfMetrics(0.1, 1.0, 0.0, -1),
        RfMetrics(0.1, 1.0, 0.0, 1.5),
        RfMetrics(0.1, 1.0, 0.0, True),
        RfMetrics(1.1, 1.0, 0.0, 100),
    ],
)
def test_classify_rf_rejects_invalid_metrics(metrics):
    with pytest.raises(ValueError, match="RF metrics are invalid"):
        classify_rf(metrics)


def test_clipped_signal_reduces_gain_by_six_db():
    decision = RfSafetyController(min_gain=0, max_gain=50).decide(
        RfState.CLIPPED,
        current_gain=40,
    )

    assert decision.new_gain == 34
    assert decision.reason == "clipping_reduce_gain"


def test_clipping_priority_prevents_low_rms_signal_from_increasing_gain():
    metrics = RfMetrics(
        rms=0.01,
        peak=1.0,
        clipping_ratio=0.002,
        sample_count=1000,
    )
    controller = RfSafetyController(min_gain=0, max_gain=50)

    state = classify_rf(metrics)
    decision = controller.decide(state, current_gain=40)

    assert state == RfState.CLIPPED
    assert decision == GainDecision(
        new_gain=34,
        reason="clipping_reduce_gain",
    )


@pytest.mark.parametrize(
    ("state", "new_gain", "reason"),
    [
        (RfState.DISCONNECTED, 20, "disconnected_hold_gain"),
        (RfState.CLIPPED, 14, "clipping_reduce_gain"),
        (RfState.TOO_STRONG, 17, "strong_signal_reduce_gain"),
        (RfState.TOO_WEAK, 22, "weak_signal_increase_gain"),
        (RfState.LINEAR, 20, "linear_hold_gain"),
    ],
)
def test_controller_covers_every_rf_state(state, new_gain, reason):
    decision = RfSafetyController(min_gain=0, max_gain=50).decide(
        state,
        current_gain=20,
    )

    assert decision == GainDecision(new_gain=new_gain, reason=reason)


def test_gain_decision_is_frozen():
    decision = GainDecision(new_gain=20, reason="linear_hold_gain")

    with pytest.raises(FrozenInstanceError):
        decision.new_gain = 30


@pytest.mark.parametrize(
    ("state", "current_gain", "expected_gain"),
    [
        (RfState.CLIPPED, 2, 0),
        (RfState.TOO_STRONG, 1, 0),
        (RfState.TOO_WEAK, 49, 50),
        (RfState.DISCONNECTED, 100, 50),
        (RfState.LINEAR, -10, 0),
    ],
)
def test_controller_clamps_every_decision_and_out_of_range_input_gain(
    state,
    current_gain,
    expected_gain,
):
    controller = RfSafetyController(min_gain=0, max_gain=50)

    assert controller.decide(state, current_gain).new_gain == expected_gain


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_gain": 10, "max_gain": 9},
        {"min_gain": np.nan, "max_gain": 50},
        {"min_gain": 0, "max_gain": np.inf},
        {"min_gain": True, "max_gain": 50},
        {"min_gain": 0, "max_gain": 50, "clipped_step": 0},
        {"min_gain": 0, "max_gain": 50, "strong_step": -1},
        {"min_gain": 0, "max_gain": 50, "weak_step": np.nan},
        {"min_gain": 0, "max_gain": 50, "weak_step": True},
    ],
)
def test_controller_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError, match="RF safety controller configuration"):
        RfSafetyController(**kwargs)


def test_controller_rejects_integer_too_large_for_finite_float():
    with pytest.raises(ValueError, match="RF safety controller configuration"):
        RfSafetyController(min_gain=0, max_gain=10**400)


def test_controller_handles_finite_numpy_scalars_without_arithmetic_warning():
    float_max = np.finfo(np.float64).max
    controller = RfSafetyController(
        min_gain=np.float64(-float_max),
        max_gain=np.float64(float_max),
        weak_step=np.float64(float_max),
    )

    decision = controller.decide(RfState.TOO_WEAK, np.float64(float_max))

    assert type(decision.new_gain) is float
    assert decision.new_gain == float_max
    assert decision.reason == "weak_signal_increase_gain"


@pytest.mark.parametrize("current_gain", [np.nan, np.inf, -np.inf, True, "20"])
def test_controller_rejects_non_finite_or_non_numeric_current_gain(current_gain):
    controller = RfSafetyController(min_gain=0, max_gain=50)

    with pytest.raises(ValueError, match="current_gain must be a finite number"):
        controller.decide(RfState.LINEAR, current_gain)


def test_controller_rejects_unknown_rf_state():
    controller = RfSafetyController(min_gain=0, max_gain=50)

    with pytest.raises(TypeError, match="state must be an RfState"):
        controller.decide("clipped", current_gain=20)
