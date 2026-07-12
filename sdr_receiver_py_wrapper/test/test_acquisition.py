from queue import Empty

import numpy as np
import pytest

import sdr_receiver_py_wrapper.acquisition as acquisition_module
from sdr_receiver_py_wrapper.acquisition import AcquisitionEngine
from sdr_receiver_py_wrapper.device_session import DeviceReadError
from sdr_receiver_py_wrapper.models import IqChunk


class FakeDevice:
    def __init__(self, reads, reconnect_result=True):
        self._reads = iter(reads)
        self._reconnect_result = reconnect_result
        self.read_count = 0
        self.reconnect_count = 0

    def read(self):
        self.read_count += 1
        result = next(self._reads)
        if isinstance(result, Exception):
            raise result
        return result

    def snapshot(self):
        return {
            "sample_rate_hz": 2_000_000,
            "lo_hz": 434_920_000,
            "rf_bandwidth_hz": 940_000,
            "rx_gain_db": 20,
        }

    def reconnect(self):
        self.reconnect_count += 1
        return self._reconnect_result


def test_acquisition_counts_drop_without_blocking_device():
    device = FakeDevice([np.zeros(16, np.complex64)] * 3)
    engine = AcquisitionEngine(device, queue_size=1)

    engine.read_once()
    engine.read_once()
    engine.read_once()

    assert device.read_count == 3
    assert engine.stats.queue_drops == 2


def test_acquisition_builds_immutable_chunks_with_snapshot_and_timestamps(
    monkeypatch,
):
    device_buffer = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)
    expected_samples = device_buffer.copy()
    device = FakeDevice([device_buffer, np.zeros(3, np.complex64)])
    wall_times = iter([1000.25, 1000.5])
    monotonic_times = iter([123_000, 456_000])
    monkeypatch.setattr(acquisition_module.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(
        acquisition_module.time,
        "monotonic_ns",
        lambda: next(monotonic_times),
    )
    engine = AcquisitionEngine(device, queue_size=2)

    engine.read_once()
    device_buffer[:] = 99 + 100j
    engine.read_once()

    first = engine.get_nowait()
    second = engine.get_nowait()
    assert isinstance(first, IqChunk)
    np.testing.assert_array_equal(first.samples, expected_samples)
    assert first.samples.dtype == np.complex64
    assert first.samples.ndim == 1
    assert first.samples.flags.c_contiguous
    assert first.samples.flags.owndata
    assert first.samples.base is None
    assert not first.samples.flags.writeable
    assert first.chunk_id == 0
    assert first.first_sample_index == 0
    assert first.rx_wall_time == 1000.25
    assert first.rx_monotonic_ns == 123_000
    assert first.sample_rate_hz == 2_000_000
    assert first.lo_hz == 434_920_000
    assert first.rf_bandwidth_hz == 940_000
    assert first.rx_gain_db == 20
    assert first.target_version == 0
    assert first.context_version == 0
    assert first.rf_metrics is None
    assert second.chunk_id == 1
    assert second.first_sample_index == len(expected_samples)
    assert second.rx_wall_time == 1000.5
    assert second.rx_monotonic_ns == 456_000


def test_dropped_chunk_still_advances_chunk_id_and_sample_index():
    device = FakeDevice(
        [
            np.zeros(2, np.complex64),
            np.zeros(3, np.complex64),
            np.zeros(4, np.complex64),
        ]
    )
    engine = AcquisitionEngine(device, queue_size=1)

    engine.read_once()
    engine.read_once()
    engine.get_nowait()
    engine.read_once()

    chunk = engine.get_nowait()
    assert chunk.chunk_id == 2
    assert chunk.first_sample_index == 5


def test_oserror_is_counted_and_reconnect_allows_the_next_chunk():
    device = FakeDevice(
        [OSError("libiio read failed"), np.zeros(8, np.complex64)]
    )
    engine = AcquisitionEngine(device, queue_size=1)

    assert engine.read_once() is None
    assert engine.stats.read_errors == 1
    assert engine.stats.reconnects == 1
    assert device.reconnect_count == 1

    engine.read_once()

    chunk = engine.get_nowait()
    assert isinstance(chunk, IqChunk)
    assert chunk.samples.shape == (8,)


def test_device_read_error_uses_the_same_reconnect_path():
    device = FakeDevice([DeviceReadError("wrapped read failed")])
    engine = AcquisitionEngine(device, queue_size=1)

    assert engine.read_once() is None
    assert engine.stats.read_errors == 1
    assert engine.stats.reconnects == 1


def test_cancelled_reconnect_is_not_counted_as_successful():
    device = FakeDevice([OSError("read failed")], reconnect_result=False)
    engine = AcquisitionEngine(device, queue_size=1)

    assert engine.read_once() is None
    assert engine.stats.read_errors == 1
    assert engine.stats.reconnects == 0
    assert device.reconnect_count == 1


@pytest.mark.parametrize(
    ("samples", "error_type", "message"),
    [
        (np.zeros(4, np.float32), ValueError, "complex64"),
        (np.zeros((2, 2), np.complex64), ValueError, "one-dimensional"),
        ([1 + 2j], TypeError, "numpy.ndarray"),
    ],
)
def test_invalid_device_iq_is_rejected_and_counted(
    samples,
    error_type,
    message,
):
    device = FakeDevice([samples])
    engine = AcquisitionEngine(device, queue_size=1)

    with pytest.raises(error_type, match=message):
        engine.read_once()

    assert engine.stats.read_errors == 1
    with pytest.raises(Empty):
        engine.get_nowait()


def test_acquisition_requires_a_positive_bounded_queue_size():
    device = FakeDevice([])

    with pytest.raises(ValueError, match="positive"):
        AcquisitionEngine(device, queue_size=0)
