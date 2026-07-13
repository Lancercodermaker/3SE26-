"""Exclusive, injected ownership of an SDR receive backend."""

import time
from dataclasses import dataclass
from threading import RLock


class DeviceConnectionError(RuntimeError):
    """A receiver backend could not be connected or configured."""


class DeviceReadError(RuntimeError):
    """IQ acquisition failed and the backend was invalidated."""


@dataclass(frozen=True)
class DeviceSessionStats:
    """Immutable lifecycle counters captured at one point in time."""

    connects: int
    reconnects: int
    read_errors: int
    connection_errors: int
    closes: int


class DeviceSession:
    """Own one backend and serialize every hardware operation with one lock."""

    def __init__(self, backend_factory, reconnect_backoff_sec=0.0):
        self._backend_factory = backend_factory
        self._reconnect_backoff_sec = reconnect_backoff_sec
        self._lock = RLock()
        self._backend = None
        self._settings = {}
        self._lifecycle_generation = 0
        self._connects = 0
        self._reconnects = 0
        self._read_errors = 0
        self._connection_errors = 0
        self._closes = 0
        self.connect()

    @property
    def stats(self):
        with self._lock:
            return DeviceSessionStats(
                connects=self._connects,
                reconnects=self._reconnects,
                read_errors=self._read_errors,
                connection_errors=self._connection_errors,
                closes=self._closes,
            )

    def connect(self):
        with self._lock:
            if self._backend is not None:
                return None
            try:
                self._connect_locked()
            except Exception as exc:
                self._connection_errors += 1
                raise DeviceConnectionError("failed to connect receiver") from exc
            return None

    def _connect_locked(self):
        backend = self._backend_factory()
        if backend is None:
            raise RuntimeError("backend factory returned None")
        self._backend = backend
        self._connects += 1
        self._lifecycle_generation += 1

    def _require_backend_locked(self):
        if self._backend is None:
            raise RuntimeError("receiver is not connected")
        return self._backend

    def configure(self, *, sample_rate, lo_hz, rf_bandwidth, gain):
        """Apply settings and commit the known snapshot after every write succeeds.

        A backend can retain partial hardware writes when a later write fails; only
        this session's last-known configuration snapshot is committed atomically.
        """
        with self._lock:
            settings = {
                "sample_rate_hz": sample_rate,
                "lo_hz": lo_hz,
                "rf_bandwidth_hz": rf_bandwidth,
                "rx_gain_db": gain,
            }
            try:
                backend = self._require_backend_locked()
                self._apply_settings(backend, settings)
            except Exception as exc:
                self._connection_errors += 1
                raise DeviceConnectionError("failed to configure receiver") from exc
            self._settings = settings

    def set_gain(self, gain):
        """Force manual gain and commit it only after both writes succeed.

        If the gain write fails, hardware may already be left in manual mode while
        the last-known configuration snapshot remains unchanged.
        """
        with self._lock:
            try:
                backend = self._require_backend_locked()
                backend.gain_control_mode_chan0 = "manual"
                backend.rx_hardwaregain_chan0 = gain
            except Exception as exc:
                self._connection_errors += 1
                raise DeviceConnectionError("failed to set receiver gain") from exc
            self._settings = {**self._settings, "rx_gain_db": gain}

    def read(self, *, with_snapshot=False):
        with self._lock:
            try:
                backend = self._require_backend_locked()
                iq = backend.rx()
            except Exception as exc:
                self._read_errors += 1
                try:
                    self._release_backend_locked()
                except Exception:
                    self._connection_errors += 1
                raise DeviceReadError("failed to read receiver IQ") from exc
            if with_snapshot:
                return iq, dict(self._settings)
            return iq

    def reconnect(self):
        with self._lock:
            lifecycle_generation = self._lifecycle_generation
        if self._reconnect_backoff_sec > 0:
            time.sleep(self._reconnect_backoff_sec)
        with self._lock:
            if lifecycle_generation != self._lifecycle_generation:
                return False
            try:
                self._release_backend_locked()
                self._connect_locked()
                self._apply_settings(self._backend, self._settings)
            except Exception as exc:
                self._connection_errors += 1
                try:
                    self._release_backend_locked()
                except Exception:
                    pass
                raise DeviceConnectionError("failed to reconnect receiver") from exc
            self._reconnects += 1
            return True

    def snapshot(self):
        """Return the last fully successful requested settings.

        This is not live hardware truth after a failed write. Reconnect and
        reconfigure the receiver before relying on settings after such a failure.
        """
        with self._lock:
            return dict(self._settings)

    def close(self):
        with self._lock:
            if self._backend is None:
                self._lifecycle_generation += 1
                return None
            try:
                self._release_backend_locked()
            except Exception as exc:
                self._connection_errors += 1
                raise DeviceConnectionError("failed to close receiver") from exc
            return None

    def _release_backend_locked(self):
        backend = self._backend
        if backend is None:
            return

        # Detach first so even a broken cleanup hook cannot leak a usable handle.
        self._backend = None
        self._lifecycle_generation += 1
        cleanup = self._find_cleanup(backend)
        if cleanup is not None:
            cleanup()
        self._closes += 1

    @staticmethod
    def _apply_settings(backend, settings):
        if "sample_rate_hz" in settings:
            backend.sample_rate = settings["sample_rate_hz"]
        if "lo_hz" in settings:
            backend.rx_lo = settings["lo_hz"]
        if "rf_bandwidth_hz" in settings:
            backend.rx_rf_bandwidth = settings["rf_bandwidth_hz"]
        if "rx_gain_db" in settings:
            backend.gain_control_mode_chan0 = "manual"
            backend.rx_hardwaregain_chan0 = settings["rx_gain_db"]

    @staticmethod
    def _find_cleanup(backend):
        for name in ("close", "destroy"):
            cleanup = getattr(backend, name, None)
            if callable(cleanup):
                return cleanup

        for context_name in ("ctx", "_ctx"):
            context = getattr(backend, context_name, None)
            if context is None:
                continue
            for name in ("close", "destroy"):
                cleanup = getattr(context, name, None)
                if callable(cleanup):
                    return cleanup
        return None
