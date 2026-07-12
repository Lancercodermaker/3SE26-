"""Pure RF measurement and safety calculations for AD9363 IQ samples."""

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Integral, Real

import numpy as np

from .models import RfMetrics


class RfState(str, Enum):
    """Stable RF conditions used by the safety decision layer."""

    DISCONNECTED = "disconnected"
    CLIPPED = "clipped"
    TOO_STRONG = "too_strong"
    TOO_WEAK = "too_weak"
    LINEAR = "linear"


@dataclass(frozen=True)
class GainDecision:
    """An immutable requested gain and its stable audit reason."""

    new_gain: float
    reason: str


@dataclass(frozen=True)
class RfSafetyController:
    """Map an RF state to a bounded gain without touching receiver hardware."""

    min_gain: float
    max_gain: float
    clipped_step: float = 6
    strong_step: float = 3
    weak_step: float = 2

    def __post_init__(self) -> None:
        values = (
            self.min_gain,
            self.max_gain,
            self.clipped_step,
            self.strong_step,
            self.weak_step,
        )
        if (
            not all(_valid_real(value) for value in values)
            or self.min_gain > self.max_gain
            or self.clipped_step <= 0
            or self.strong_step <= 0
            or self.weak_step <= 0
        ):
            raise ValueError("RF safety controller configuration is invalid")

    def decide(self, state: RfState, current_gain: float) -> GainDecision:
        """Return the bounded gain adjustment for one classified RF state."""
        if not isinstance(state, RfState):
            raise TypeError("state must be an RfState")
        if not _valid_real(current_gain):
            raise ValueError("current_gain must be a finite number")

        gain = float(current_gain)
        min_gain = float(self.min_gain)
        max_gain = float(self.max_gain)
        if state == RfState.CLIPPED:
            target_gain = gain - float(self.clipped_step)
            reason = "clipping_reduce_gain"
        elif state == RfState.TOO_STRONG:
            target_gain = gain - float(self.strong_step)
            reason = "strong_signal_reduce_gain"
        elif state == RfState.TOO_WEAK:
            target_gain = gain + float(self.weak_step)
            reason = "weak_signal_increase_gain"
        elif state == RfState.LINEAR:
            target_gain = gain
            reason = "linear_hold_gain"
        else:
            target_gain = gain
            reason = "disconnected_hold_gain"

        new_gain = min(max_gain, max(min_gain, target_gain))
        return GainDecision(new_gain=new_gain, reason=reason)


def measure_rf(
    samples: np.ndarray,
    code_scale: float = 2048.0,
) -> RfMetrics:
    """Measure a one-dimensional complex IQ array in AD9363 code units.

    RMS and peak are complex magnitudes normalized by ``code_scale``. A sample
    is clipped when either its I or Q component reaches 98 percent of the code
    scale. Empty complex arrays return zero-valued metrics so callers can
    classify them as disconnected without numerical warnings.
    """
    if (
        isinstance(code_scale, (bool, np.bool_))
        or not isinstance(code_scale, Real)
        or not math.isfinite(float(code_scale))
        or code_scale <= 0
    ):
        raise ValueError("code_scale must be a finite positive number")
    if not isinstance(samples, np.ndarray):
        raise TypeError("samples must be a numpy.ndarray")
    if not np.issubdtype(samples.dtype, np.complexfloating):
        raise TypeError("samples must have a complex dtype")
    if samples.ndim != 1:
        raise ValueError("samples must be one-dimensional")
    if not np.isfinite(samples).all():
        raise ValueError("samples must contain only finite values")

    sample_count = int(samples.size)
    if sample_count == 0:
        return RfMetrics(
            rms=0.0,
            peak=0.0,
            clipping_ratio=0.0,
            sample_count=0,
        )

    scale = float(code_scale)
    real = samples.real.astype(np.float64, copy=False)
    imag = samples.imag.astype(np.float64, copy=False)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        magnitudes = np.hypot(real, imag)
        normalized_magnitudes = magnitudes / scale
    if not np.isfinite(magnitudes).all() or not np.isfinite(
        normalized_magnitudes
    ).all():
        raise ValueError("RF measurements exceed finite range")

    peak = float(np.max(normalized_magnitudes))
    if peak == 0.0:
        rms = 0.0
    else:
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            relative_magnitudes = normalized_magnitudes / peak
            mean_square = np.mean(
                np.square(relative_magnitudes),
                dtype=np.float64,
            )
            rms = float(peak * np.sqrt(mean_square))
    if not math.isfinite(rms) or not math.isfinite(peak):
        raise ValueError("RF measurements exceed finite range")

    clipping_level = 0.98 * scale
    clipped = (np.abs(real) >= clipping_level) | (np.abs(imag) >= clipping_level)
    clipping_ratio = float(np.count_nonzero(clipped) / sample_count)
    return RfMetrics(
        rms=rms,
        peak=peak,
        clipping_ratio=clipping_ratio,
        sample_count=sample_count,
    )


def classify_rf(
    metrics: RfMetrics,
    *,
    clipping_threshold: float = 0.001,
    strong_rms: float = 0.8,
    weak_rms: float = 0.05,
) -> RfState:
    """Classify RF metrics using safety-first, configurable thresholds."""
    if not isinstance(metrics, RfMetrics):
        raise TypeError("metrics must be an RfMetrics")
    if not _valid_real(clipping_threshold) or not 0 < clipping_threshold <= 1:
        raise ValueError("RF classification thresholds are invalid")
    if not _valid_real(strong_rms) or not _valid_real(weak_rms):
        raise ValueError("RF classification thresholds are invalid")
    if weak_rms < 0 or strong_rms <= weak_rms:
        raise ValueError("RF classification thresholds are invalid")
    if (
        not _valid_real(metrics.rms)
        or metrics.rms < 0
        or not _valid_real(metrics.peak)
        or metrics.peak < 0
        or metrics.rms > metrics.peak
        or not _valid_real(metrics.clipping_ratio)
        or not 0 <= metrics.clipping_ratio <= 1
        or isinstance(metrics.sample_count, (bool, np.bool_))
        or not isinstance(metrics.sample_count, Integral)
        or metrics.sample_count < 0
    ):
        raise ValueError("RF metrics are invalid")

    if metrics.sample_count == 0:
        return RfState.DISCONNECTED
    if metrics.clipping_ratio >= clipping_threshold:
        return RfState.CLIPPED
    if metrics.rms >= strong_rms:
        return RfState.TOO_STRONG
    if metrics.rms <= weak_rms:
        return RfState.TOO_WEAK
    return RfState.LINEAR


def _valid_real(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False
