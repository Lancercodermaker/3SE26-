from concurrent.futures import ThreadPoolExecutor
from queue import Empty
import threading

import numpy as np
import pytest

import sdr_receiver_py_wrapper.acquisition as acquisition_module
from sdr_receiver_py_wrapper.acquisition import AcquisitionEngine
from sdr_receiver_py_wrapper.device_session import DeviceReadError, DeviceSession
from sdr_receiver_py_wrapper.models import IqChunk


DEVICE_SNAPSHOT = {
    "sample_rate_hz": 2_000_000,
    "lo_hz": 434_920_000,
    "rf_bandwidth_hz": 940_000,
    "rx_gain_db": 20,
}


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
        return dict(DEVICE_SNAPSHOT)

    def reconnect(self):
        self.reconnect_count += 1
        return self._reconnect_result


class SnapshotSequenceDevice(FakeDevice):
    def __init__(self, reads, snapshots):
        super().__init__(reads)
        self._snapshots = iter(snapshots)

    def snapshot(self):
        result = next(self._snapshots)
        if isinstance(result, Exception):
            raise result
        return result


class SessionBackend:
    def __init__(self, iq=None, read_error=None):
        self.iq = np.zeros(4, np.complex64) if iq is None else iq
        self.read_error = read_error
        self.sample_rate = None
        self.rx_lo = None
        self.rx_rf_bandwidth = None
        self.gain_control_mode_chan0 = None
        self.rx_hardwaregain_chan0 = None
        self.close_calls = 0

    def rx(self):
        if self.read_error is not None:
            raise self.read_error
        return self.iq

    def close(self):
        self.close_calls += 1


class IncrementRendezvous:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries = 0
        self._release = threading.Event()

    def enter(self):
        with self._lock:
            self._entries += 1
            entry = self._entries
        if entry == 1:
            self._release.wait(timeout=0.1)
        elif entry == 2:
            self._release.set()


class YieldingInt(int):
    def __new__(cls, value, rendezvous):
        instance = int.__new__(cls, value)
        instance.rendezvous = rendezvous
        return instance

    def __iadd__(self, value):
        self.rendezvous.enter()
        return YieldingInt(int(self) + value, self.rendezvous)


class ConcurrentDevice(FakeDevice):
    def __init__(self, worker_count, result):
        super().__init__([])
        self._read_barrier = threading.Barrier(worker_count)
        self._result = result

    def read(self):
        self._read_barrier.wait(timeout=3.0)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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


def test_timestamps_are_captured_before_copy_and_separate_snapshot(monkeypatch):
    events = []

    class OrderedDevice(FakeDevice):
        def read(self):
            events.append("read")
            return super().read()

        def snapshot(self):
            events.append("snapshot")
            return super().snapshot()

    original_array = np.array

    def recording_array(*args, **kwargs):
        events.append("copy")
        return original_array(*args, **kwargs)

    device = OrderedDevice([np.zeros(2, np.complex64)])
    monkeypatch.setattr(
        acquisition_module.time,
        "time",
        lambda: events.append("wall") or 1000.0,
    )
    monkeypatch.setattr(
        acquisition_module.time,
        "monotonic_ns",
        lambda: events.append("monotonic") or 123_000,
    )
    monkeypatch.setattr(acquisition_module.np, "array", recording_array)
    engine = AcquisitionEngine(device, queue_size=1)

    engine.read_once()

    assert events.index("read") < events.index("wall")
    assert events.index("wall") < events.index("monotonic")
    assert events.index("monotonic") < events.index("copy")
    assert events.index("monotonic") < events.index("snapshot")


@pytest.mark.parametrize(
    ("bad_snapshot", "error_type", "message", "sample_count"),
    [
        (RuntimeError("snapshot failed"), RuntimeError, "snapshot failed", 3),
        (
            {
                key: value
                for key, value in DEVICE_SNAPSHOT.items()
                if key != "rx_gain_db"
            },
            KeyError,
            "rx_gain_db",
            4,
        ),
    ],
)
def test_metadata_failure_is_counted_and_preserves_the_sample_gap(
    bad_snapshot,
    error_type,
    message,
    sample_count,
):
    device = SnapshotSequenceDevice(
        [
            np.zeros(sample_count, np.complex64),
            np.zeros(2, np.complex64),
        ],
        [bad_snapshot, dict(DEVICE_SNAPSHOT)],
    )
    engine = AcquisitionEngine(device, queue_size=1)

    with pytest.raises(error_type, match=message):
        engine.read_once()

    assert engine.stats.read_errors == 1
    engine.read_once()
    chunk = engine.get_nowait()
    assert chunk.chunk_id == 1
    assert chunk.first_sample_index == sample_count


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


def test_device_session_reconnect_restores_hardware_before_next_chunk():
    first = SessionBackend(read_error=OSError("read failed"))
    replacement = SessionBackend(iq=np.zeros(8, np.complex64))
    backends = iter([first, replacement])
    session = DeviceSession(lambda: next(backends))
    session.configure(
        sample_rate=2_000_000,
        lo_hz=434_920_000,
        rf_bandwidth=940_000,
        gain=20,
    )
    engine = AcquisitionEngine(session, queue_size=1)

    assert engine.read_once() is None
    engine.read_once()

    chunk = engine.get_nowait()
    assert replacement.sample_rate == chunk.sample_rate_hz
    assert replacement.rx_lo == chunk.lo_hz
    assert replacement.rx_rf_bandwidth == chunk.rf_bandwidth_hz
    assert replacement.rx_hardwaregain_chan0 == chunk.rx_gain_db


def test_acquisition_uses_atomic_device_read_snapshot_during_set_gain(
    monkeypatch,
):
    backend = SessionBackend()
    read_started = threading.Event()
    release_read = threading.Event()
    gain_seen_by_read = []

    def blocking_read():
        gain_seen_by_read.append(backend.rx_hardwaregain_chan0)
        read_started.set()
        assert release_read.wait(timeout=3.0)
        return backend.iq

    backend.rx = blocking_read
    session = DeviceSession(lambda: backend)
    session.configure(
        sample_rate=2_000_000,
        lo_hz=434_920_000,
        rf_bandwidth=940_000,
        gain=20,
    )
    engine = AcquisitionEngine(session, queue_size=1)
    gain_finished = threading.Event()

    def delayed_separate_snapshot():
        assert gain_finished.wait(timeout=3.0)
        return DeviceSession.snapshot(session)

    def run_set_gain():
        session.set_gain(31)
        gain_finished.set()

    monkeypatch.setattr(session, "snapshot", delayed_separate_snapshot)
    with ThreadPoolExecutor(max_workers=2) as executor:
        read_future = executor.submit(engine.read_once)
        assert read_started.wait(timeout=3.0)
        gain_future = executor.submit(run_set_gain)
        release_read.set()
        read_future.result(timeout=3.0)
        gain_future.result(timeout=3.0)

    chunk = engine.get_nowait()
    assert gain_seen_by_read == [20]
    assert chunk.rx_gain_db == gain_seen_by_read[0]


def test_cancelled_reconnect_is_not_counted_as_successful():
    device = FakeDevice([OSError("read failed")], reconnect_result=False)
    engine = AcquisitionEngine(device, queue_size=1)

    assert engine.read_once() is None
    assert engine.stats.read_errors == 1
    assert engine.stats.reconnects == 0
    assert device.reconnect_count == 1


def test_concurrent_error_updates_are_not_lost_and_stats_stay_consistent():
    worker_count = 2
    engine = AcquisitionEngine(
        ConcurrentDevice(worker_count, OSError("read failed")),
        queue_size=1,
    )
    engine._read_errors = YieldingInt(0, IncrementRendezvous())
    engine._reconnects = YieldingInt(0, IncrementRendezvous())
    snapshots = []
    poll_interval = threading.Event()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(engine.read_once) for _ in range(worker_count)]
        while not all(future.done() for future in futures):
            snapshots.append(engine.stats)
            poll_interval.wait(timeout=0.001)
        for future in futures:
            future.result(timeout=3.0)

    assert int(engine.stats.read_errors) == worker_count
    assert int(engine.stats.reconnects) == worker_count
    assert all(snapshot.reconnects <= snapshot.read_errors for snapshot in snapshots)


def test_concurrent_successful_reads_reserve_unique_ids_and_sample_ranges():
    worker_count = 2
    engine = AcquisitionEngine(
        ConcurrentDevice(worker_count, np.zeros(2, np.complex64)),
        queue_size=worker_count,
    )
    engine._next_chunk_id = YieldingInt(0, IncrementRendezvous())
    engine._next_sample_index = YieldingInt(0, IncrementRendezvous())
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(engine.read_once) for _ in range(worker_count)]
        for future in futures:
            future.result(timeout=3.0)

    chunks = [engine.get_nowait() for _ in range(worker_count)]
    assert sorted(chunk.chunk_id for chunk in chunks) == [0, 1]
    assert sorted(chunk.first_sample_index for chunk in chunks) == [0, 2]


@pytest.mark.parametrize(
    ("samples", "error_type", "message"),
    [
        (np.zeros(4, np.float32), ValueError, "complex64"),
        (np.zeros((2, 2), np.complex64), ValueError, "one-dimensional"),
        (np.zeros(0, np.complex64), ValueError, "must not be empty"),
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


@pytest.mark.parametrize(
    "queue_size",
    [
        0,
        -1,
        True,
        False,
        np.bool_(True),
        np.bool_(False),
        1.5,
        float("nan"),
        float("inf"),
        "1",
    ],
)
def test_acquisition_requires_a_positive_bounded_queue_size(queue_size):
    device = FakeDevice([])

    with pytest.raises(ValueError, match="positive integer"):
        AcquisitionEngine(device, queue_size=queue_size)


def test_acquisition_accepts_an_integer_index_queue_size():
    engine = AcquisitionEngine(FakeDevice([]), queue_size=np.int64(2))

    assert engine.stats.queue_drops == 0
