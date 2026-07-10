import numpy as np

from sdr_receiver_py_wrapper.iq_file_source import IqFilePluto


def test_iq_file_source_reads_and_loops(tmp_path):
    path = tmp_path / "capture.c64"
    samples = np.asarray([1 + 1j, 2 + 2j, 3 + 3j], dtype=np.complex64)
    path.write_bytes(samples.astype("<c8").tobytes())

    source = IqFilePluto(str(path), loop=True, throttle=False)
    source.rx_buffer_size = 5

    got = source.rx()

    assert got.dtype == np.complex64
    np.testing.assert_array_equal(got, np.asarray([1 + 1j, 2 + 2j, 3 + 3j, 1 + 1j, 2 + 2j], dtype=np.complex64))
    source.close()


def test_iq_file_source_returns_zeros_after_eof_when_loop_disabled(tmp_path):
    path = tmp_path / "capture.c64"
    samples = np.asarray([1 + 0j, 2 + 0j], dtype=np.complex64)
    path.write_bytes(samples.astype("<c8").tobytes())

    source = IqFilePluto(str(path), loop=False, throttle=False)
    source.rx_buffer_size = 4

    got = source.rx()

    np.testing.assert_array_equal(got, np.asarray([1 + 0j, 2 + 0j, 0 + 0j, 0 + 0j], dtype=np.complex64))
    source.close()


def test_iq_file_source_applies_virtual_lo_shift(tmp_path):
    path = tmp_path / "capture.c64"
    samples = np.ones(4, dtype=np.complex64)
    path.write_bytes(samples.astype("<c8").tobytes())

    source = IqFilePluto(str(path), loop=False, throttle=False, center_hz=100.0)
    source.sample_rate = 400.0
    source.rx_lo = 200.0
    source.rx_buffer_size = 4

    got = source.rx()
    expected = np.exp(-1j * 2.0 * np.pi * 100.0 * np.arange(4) / 400.0).astype(np.complex64)

    np.testing.assert_allclose(got, expected, atol=1e-6)
    source.close()
