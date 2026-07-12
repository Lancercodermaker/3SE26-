import inspect
import threading
from dataclasses import FrozenInstanceError

import pytest

import sdr_receiver_py_wrapper.device_session as device_session_module
from sdr_receiver_py_wrapper.device_session import (
    DeviceConnectionError,
    DeviceReadError,
    DeviceSession,
)


POSITIVE_WAIT_TIMEOUT_SEC = 3.0


def wait_for_event(event):
    assert event.wait(timeout=POSITIVE_WAIT_TIMEOUT_SEC)


class FakePlutoBackend:
    def __init__(self, iq=None):
        self.sample_rate = None
        self.rx_lo = None
        self.rx_rf_bandwidth = None
        self.gain_control_mode_chan0 = None
        self.rx_hardwaregain_chan0 = None
        self.iq = iq if iq is not None else [1 + 2j]
        self.rx_calls = 0
        self.close_calls = 0

    def rx(self):
        self.rx_calls += 1
        return self.iq

    def close(self):
        self.close_calls += 1


class ConfigureFailingBackend(FakePlutoBackend):
    def __init__(self):
        self.fail_bandwidth_write = False
        self._rx_rf_bandwidth = None
        super().__init__()

    @property
    def rx_rf_bandwidth(self):
        return self._rx_rf_bandwidth

    @rx_rf_bandwidth.setter
    def rx_rf_bandwidth(self, value):
        if self.fail_bandwidth_write:
            raise RuntimeError("bandwidth write failed")
        self._rx_rf_bandwidth = value


class GainFailingBackend(FakePlutoBackend):
    def __init__(self):
        self.fail_gain_write = False
        self._rx_hardwaregain_chan0 = None
        super().__init__()

    @property
    def rx_hardwaregain_chan0(self):
        return self._rx_hardwaregain_chan0

    @rx_hardwaregain_chan0.setter
    def rx_hardwaregain_chan0(self, value):
        if self.fail_gain_write:
            raise RuntimeError("gain write failed")
        self._rx_hardwaregain_chan0 = value


class SequenceFactory:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


class ReadFailingBackend(FakePlutoBackend):
    def __init__(self, read_error):
        super().__init__()
        self.read_error = read_error

    def rx(self):
        self.rx_calls += 1
        raise self.read_error


class CloseAndDestroyBackend(FakePlutoBackend):
    def __init__(self):
        super().__init__()
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class DestroyOnlyBackend:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class ContextOnlyBackend:
    def __init__(self):
        self.ctx = DestroyOnlyBackend()


class CleanupFailingBackend(FakePlutoBackend):
    def __init__(self, cleanup_error):
        super().__init__()
        self.cleanup_error = cleanup_error

    def close(self):
        self.close_calls += 1
        raise self.cleanup_error


def test_device_session_is_only_owner_of_device_settings():
    backend = FakePlutoBackend()
    session = DeviceSession(lambda: backend)

    session.configure(
        sample_rate=2_000_000,
        lo_hz=434_920_000,
        rf_bandwidth=940_000,
        gain=20,
    )

    assert session.snapshot() == {
        "sample_rate_hz": 2_000_000,
        "lo_hz": 434_920_000,
        "rf_bandwidth_hz": 940_000,
        "rx_gain_db": 20,
    }
    assert backend.sample_rate == 2_000_000
    assert backend.rx_lo == 434_920_000
    assert backend.rx_rf_bandwidth == 940_000
    assert backend.gain_control_mode_chan0 == "manual"
    assert backend.rx_hardwaregain_chan0 == 20

    external_snapshot = session.snapshot()
    external_snapshot["rx_gain_db"] = 99
    assert session.snapshot()["rx_gain_db"] == 20


def test_set_gain_updates_known_snapshot_and_forces_manual_mode():
    backend = FakePlutoBackend()
    session = DeviceSession(lambda: backend)

    assert session.snapshot() == {}

    session.set_gain(17)

    assert backend.gain_control_mode_chan0 == "manual"
    assert backend.rx_hardwaregain_chan0 == 17
    assert session.snapshot() == {"rx_gain_db": 17}


def test_configure_failure_does_not_commit_snapshot():
    backend = ConfigureFailingBackend()
    session = DeviceSession(lambda: backend)
    session.configure(
        sample_rate=2_000_000,
        lo_hz=434_920_000,
        rf_bandwidth=940_000,
        gain=20,
    )
    previous = session.snapshot()
    backend.fail_bandwidth_write = True

    with pytest.raises(DeviceConnectionError) as error:
        session.configure(
            sample_rate=4_000_000,
            lo_hz=435_000_000,
            rf_bandwidth=1_000_000,
            gain=30,
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert session.snapshot() == previous


def test_connect_and_close_are_eager_and_idempotent_with_frozen_stats():
    backend = FakePlutoBackend()
    factory = SequenceFactory(backend)

    session = DeviceSession(factory)

    assert factory.calls == 1
    assert session.stats.connects == 1
    assert session.connect() is None
    assert factory.calls == 1

    stats = session.stats
    with pytest.raises(FrozenInstanceError):
        stats.connects = 99

    assert session.close() is None
    assert session.close() is None
    assert backend.close_calls == 1
    assert session.stats.closes == 1


def test_connect_factory_failure_is_chained_and_counted_without_false_connect():
    backend = FakePlutoBackend()
    factory_error = RuntimeError("device unavailable")
    factory = SequenceFactory(backend, factory_error)
    session = DeviceSession(factory)
    session.close()

    with pytest.raises(DeviceConnectionError) as error:
        session.connect()

    assert error.value.__cause__ is factory_error
    assert session.stats.connects == 1
    assert session.stats.connection_errors == 1


def test_eager_factory_failure_is_chained_as_connection_error():
    factory_error = RuntimeError("device unavailable")

    def failing_factory():
        raise factory_error

    with pytest.raises(DeviceConnectionError) as error:
        DeviceSession(failing_factory)

    assert error.value.__cause__ is factory_error


def test_set_gain_failure_keeps_snapshot_and_counts_connection_error():
    backend = GainFailingBackend()
    session = DeviceSession(lambda: backend)
    session.set_gain(12)
    previous = session.snapshot()
    backend.fail_gain_write = True

    with pytest.raises(DeviceConnectionError) as error:
        session.set_gain(18)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert session.snapshot() == previous
    assert session.stats.connection_errors == 1


def test_device_session_has_only_the_contract_public_methods_and_no_backend_getter():
    public_methods = {
        name
        for name, member in inspect.getmembers(DeviceSession, inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == {
        "close",
        "configure",
        "connect",
        "read",
        "reconnect",
        "set_gain",
        "snapshot",
    }
    assert not hasattr(DeviceSession, "backend")


def test_read_returns_backend_iq_without_processing():
    iq = object()
    backend = FakePlutoBackend(iq=iq)
    session = DeviceSession(lambda: backend)

    assert session.read() is iq
    assert backend.rx_calls == 1


def test_read_failure_closes_and_clears_backend_then_reconnects_to_next_one():
    read_error = RuntimeError("rx failed")
    first = ReadFailingBackend(read_error)
    iq = object()
    second = FakePlutoBackend(iq=iq)
    factory = SequenceFactory(first, second)
    session = DeviceSession(factory)

    with pytest.raises(DeviceReadError) as error:
        session.read()

    assert error.value.__cause__ is read_error
    assert first.rx_calls == 1
    assert first.close_calls == 1
    assert session.stats.read_errors == 1
    assert session.stats.closes == 1

    assert session.reconnect() is True
    assert factory.calls == 2
    assert session.stats.connects == 2
    assert session.stats.reconnects == 1
    assert session.read() is iq


def test_reconnect_failure_is_chained_and_does_not_increment_reconnects():
    first = FakePlutoBackend()
    connect_error = RuntimeError("reconnect failed")
    factory = SequenceFactory(first, connect_error)
    session = DeviceSession(factory)

    with pytest.raises(DeviceConnectionError) as error:
        session.reconnect()

    assert error.value.__cause__ is connect_error
    assert first.close_calls == 1
    assert session.stats.connects == 1
    assert session.stats.reconnects == 0
    assert session.stats.connection_errors == 1
    assert session.stats.closes == 1


def test_reconnect_does_not_hold_session_lock_during_backoff(monkeypatch):
    first = FakePlutoBackend()
    second = FakePlutoBackend()
    session = DeviceSession(SequenceFactory(first, second), reconnect_backoff_sec=1.0)
    sleeping = threading.Event()
    release_sleep = threading.Event()
    errors = []
    results = []

    def controlled_sleep(seconds):
        assert seconds == 1.0
        sleeping.set()
        wait_for_event(release_sleep)

    def run_reconnect():
        try:
            results.append(session.reconnect())
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)

    monkeypatch.setattr(device_session_module.time, "sleep", controlled_sleep)
    worker = threading.Thread(target=run_reconnect)
    worker.start()
    wait_for_event(sleeping)

    assert session.snapshot() == {}
    assert first.close_calls == 0

    release_sleep.set()
    worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)
    assert not worker.is_alive()
    assert errors == []
    assert results == [True]
    assert first.close_calls == 1


def test_close_cancels_reconnect_waiting_in_backoff(monkeypatch):
    first = FakePlutoBackend()
    factory = SequenceFactory(first, FakePlutoBackend())
    session = DeviceSession(factory, reconnect_backoff_sec=1.0)
    sleeping = threading.Event()
    release_sleep = threading.Event()
    results = []
    errors = []

    def controlled_sleep(seconds):
        assert seconds == 1.0
        sleeping.set()
        wait_for_event(release_sleep)

    def run_reconnect():
        try:
            results.append(session.reconnect())
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)

    monkeypatch.setattr(device_session_module.time, "sleep", controlled_sleep)
    worker = threading.Thread(target=run_reconnect)
    worker.start()
    wait_for_event(sleeping)

    session.close()
    release_sleep.set()
    worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)

    assert not worker.is_alive()
    assert errors == []
    assert results == [False]
    assert first.close_calls == 1
    assert factory.calls == 1
    assert session.stats.reconnects == 0


def test_close_cancels_reconnect_when_failed_read_already_cleared_backend(monkeypatch):
    read_error = RuntimeError("rx failed")
    first = ReadFailingBackend(read_error)
    factory = SequenceFactory(first, FakePlutoBackend())
    session = DeviceSession(factory, reconnect_backoff_sec=1.0)

    with pytest.raises(DeviceReadError):
        session.read()

    sleeping = threading.Event()
    release_sleep = threading.Event()
    results = []
    errors = []

    def controlled_sleep(seconds):
        assert seconds == 1.0
        sleeping.set()
        wait_for_event(release_sleep)

    def run_reconnect():
        try:
            results.append(session.reconnect())
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)

    monkeypatch.setattr(device_session_module.time, "sleep", controlled_sleep)
    worker = threading.Thread(target=run_reconnect)
    worker.start()
    wait_for_event(sleeping)

    session.close()
    release_sleep.set()
    worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)

    assert not worker.is_alive()
    assert errors == []
    assert results == [False]
    assert first.close_calls == 1
    assert factory.calls == 1
    assert session.stats.reconnects == 0


def test_close_and_connect_supersede_sleeping_reconnect_without_closing_new_backend(
    monkeypatch,
):
    first = FakePlutoBackend()
    replacement_iq = object()
    replacement = FakePlutoBackend(iq=replacement_iq)
    unused_third = FakePlutoBackend()
    factory = SequenceFactory(first, replacement, unused_third)
    session = DeviceSession(factory, reconnect_backoff_sec=1.0)
    sleeping = threading.Event()
    release_sleep = threading.Event()
    results = []
    errors = []

    def controlled_sleep(seconds):
        assert seconds == 1.0
        sleeping.set()
        wait_for_event(release_sleep)

    def run_reconnect():
        try:
            results.append(session.reconnect())
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)

    monkeypatch.setattr(device_session_module.time, "sleep", controlled_sleep)
    worker = threading.Thread(target=run_reconnect)
    worker.start()
    wait_for_event(sleeping)

    session.close()
    session.connect()
    release_sleep.set()
    worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)

    assert not worker.is_alive()
    assert errors == []
    assert results == [False]
    assert first.close_calls == 1
    assert replacement.close_calls == 0
    assert factory.calls == 2
    assert session.stats.reconnects == 0
    assert session.read() is replacement_iq


def test_two_reconnects_from_same_generation_allow_only_one_replacement(monkeypatch):
    first = FakePlutoBackend()
    replacement = FakePlutoBackend()
    unused_third = FakePlutoBackend()
    factory = SequenceFactory(first, replacement, unused_third)
    session = DeviceSession(factory, reconnect_backoff_sec=1.0)
    both_sleeping = threading.Barrier(3)
    results = []
    errors = []

    def controlled_sleep(seconds):
        assert seconds == 1.0
        both_sleeping.wait(timeout=POSITIVE_WAIT_TIMEOUT_SEC)

    def run_reconnect():
        try:
            results.append(session.reconnect())
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)

    monkeypatch.setattr(device_session_module.time, "sleep", controlled_sleep)
    workers = [threading.Thread(target=run_reconnect) for _ in range(2)]
    for worker in workers:
        worker.start()
    both_sleeping.wait(timeout=POSITIVE_WAIT_TIMEOUT_SEC)
    for worker in workers:
        worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert sorted(results) == [False, True]
    assert first.close_calls == 1
    assert replacement.close_calls == 0
    assert factory.calls == 2
    assert session.stats.reconnects == 1


@pytest.mark.parametrize(
    ("backend", "expected_close_calls", "destroy_counter"),
    [
        (CloseAndDestroyBackend(), 1, lambda backend: backend.destroy_calls),
        (DestroyOnlyBackend(), None, lambda backend: backend.destroy_calls),
        (ContextOnlyBackend(), None, lambda backend: backend.ctx.destroy_calls),
    ],
)
def test_close_prefers_close_and_falls_back_to_destroy(
    backend, expected_close_calls, destroy_counter
):
    session = DeviceSession(lambda: backend)

    session.close()

    if expected_close_calls is not None:
        assert backend.close_calls == expected_close_calls
    expected_destroy_calls = 0 if expected_close_calls is not None else 1
    assert destroy_counter(backend) == expected_destroy_calls


def test_cleanup_failure_is_chained_but_backend_is_cleared():
    cleanup_error = RuntimeError("close failed")
    first = CleanupFailingBackend(cleanup_error)
    second = FakePlutoBackend()
    factory = SequenceFactory(first, second)
    session = DeviceSession(factory)

    with pytest.raises(DeviceConnectionError) as error:
        session.close()

    assert error.value.__cause__ is cleanup_error
    assert first.close_calls == 1
    assert session.stats.connection_errors == 1
    assert session.stats.closes == 0
    assert session.connect() is None
    assert factory.calls == 2


def test_read_error_remains_primary_when_cleanup_also_fails_and_backend_clears():
    read_error = RuntimeError("rx failed")
    cleanup_error = RuntimeError("close failed")
    first = ReadFailingBackend(read_error)

    def failing_close():
        first.close_calls += 1
        raise cleanup_error

    first.close = failing_close
    second = FakePlutoBackend()
    factory = SequenceFactory(first, second)
    session = DeviceSession(factory)

    with pytest.raises(DeviceReadError) as error:
        session.read()

    assert error.value.__cause__ is read_error
    assert session.stats.read_errors == 1
    assert session.stats.connection_errors == 1
    assert session.stats.closes == 0
    session.connect()
    assert factory.calls == 2


def test_configure_while_disconnected_is_chained_and_does_not_change_snapshot():
    backend = FakePlutoBackend()
    session = DeviceSession(lambda: backend)
    session.close()

    with pytest.raises(DeviceConnectionError) as error:
        session.configure(sample_rate=1, lo_hz=2, rf_bandwidth=3, gain=4)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert session.snapshot() == {}
    assert session.stats.connection_errors == 1


@pytest.mark.parametrize(
    "operation_name",
    ["connect", "configure", "set_gain", "read", "reconnect", "close"],
)
def test_hardware_operations_all_use_the_session_rlock(operation_name):
    first = FakePlutoBackend()
    second = FakePlutoBackend()
    session = DeviceSession(SequenceFactory(first, second))
    started = threading.Event()
    finished = threading.Event()
    errors = []

    operations = {
        "connect": session.connect,
        "configure": lambda: session.configure(
            sample_rate=1, lo_hz=2, rf_bandwidth=3, gain=4
        ),
        "set_gain": lambda: session.set_gain(5),
        "read": session.read,
        "reconnect": session.reconnect,
        "close": session.close,
    }

    def run_operation():
        started.set()
        try:
            operations[operation_name]()
        except Exception as exc:  # pragma: no cover - assertion reports thread failure
            errors.append(exc)
        finally:
            finished.set()

    with session._lock:
        worker = threading.Thread(target=run_operation)
        worker.start()
        wait_for_event(started)
        assert not finished.wait(timeout=0.05)

    worker.join(timeout=POSITIVE_WAIT_TIMEOUT_SEC)
    assert not worker.is_alive()
    assert errors == []
