#include "sdr_receiver/pluto_sdr_device.hpp"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <stdexcept>
#include <vector>

#ifdef SDR_RECEIVER_HAVE_LIBIIO
#include <iio.h>
#endif

namespace sdr_receiver {

#ifdef SDR_RECEIVER_HAVE_LIBIIO

struct PlutoSdrDevice::Impl {
    iio_context* ctx = nullptr;
    iio_device* phy = nullptr;
    iio_device* rx = nullptr;
    iio_channel* rx_lo = nullptr;
    iio_channel* rx_phy = nullptr;
    iio_channel* rx_i = nullptr;
    iio_channel* rx_q = nullptr;
    iio_buffer* buffer = nullptr;

    ~Impl() {
        if (buffer) iio_buffer_destroy(buffer);
        if (ctx) iio_context_destroy(ctx);
    }
};

namespace {

bool write_attr_ll(iio_channel* ch, const char* attr, long long value, std::string& err) {
    if (!ch) { err = std::string("missing channel for attr ") + attr; return false; }
    const int ret = iio_channel_attr_write_longlong(ch, attr, value);
    if (ret < 0) {
        err = std::string("failed to write ") + attr + "=" + std::to_string(value);
        return false;
    }
    return true;
}

bool write_attr_str(iio_channel* ch, const char* attr, const char* value, std::string& err) {
    if (!ch) { err = std::string("missing channel for attr ") + attr; return false; }
    const int ret = iio_channel_attr_write(ch, attr, value);
    if (ret < 0) {
        err = std::string("failed to write ") + attr + "=" + value;
        return false;
    }
    return true;
}

} // namespace

PlutoSdrDevice::PlutoSdrDevice(const PlutoSdrConfig& cfg)
    : impl_(std::make_unique<Impl>()), cfg_(cfg) {}

PlutoSdrDevice::~PlutoSdrDevice() = default;

bool PlutoSdrDevice::open() {
    last_error_.clear();
    impl_->ctx = iio_create_context_from_uri(cfg_.uri.c_str());
    if (!impl_->ctx) {
        last_error_ = "cannot create libiio context for " + cfg_.uri;
        connected_ = false;
        return false;
    }

    impl_->phy = iio_context_find_device(impl_->ctx, "ad9361-phy");
    impl_->rx = iio_context_find_device(impl_->ctx, "cf-ad9361-lpc");
    if (!impl_->phy || !impl_->rx) {
        last_error_ = "cannot find Pluto devices: ad9361-phy / cf-ad9361-lpc";
        connected_ = false;
        return false;
    }

    impl_->rx_lo = iio_device_find_channel(impl_->phy, "altvoltage0", true);
    impl_->rx_phy = iio_device_find_channel(impl_->phy, "voltage0", false);
    impl_->rx_i = iio_device_find_channel(impl_->rx, "voltage0", false);
    impl_->rx_q = iio_device_find_channel(impl_->rx, "voltage1", false);
    if (!impl_->rx_lo || !impl_->rx_phy || !impl_->rx_i || !impl_->rx_q) {
        last_error_ = "cannot find required Pluto RX channels";
        connected_ = false;
        return false;
    }

    if (!write_attr_str(impl_->rx_phy, "gain_control_mode", "manual", last_error_)) return false;
    if (!write_attr_ll(impl_->rx_phy, "sampling_frequency", cfg_.sample_rate, last_error_)) return false;
    if (!write_attr_ll(impl_->rx_phy, "rf_bandwidth", cfg_.rf_bandwidth, last_error_)) return false;
    if (!write_attr_ll(impl_->rx_lo, "frequency", cfg_.rx_lo, last_error_)) return false;
    if (!write_attr_ll(impl_->rx_phy, "hardwaregain", cfg_.gain_db, last_error_)) return false;

    iio_channel_enable(impl_->rx_i);
    iio_channel_enable(impl_->rx_q);

    impl_->buffer = iio_device_create_buffer(impl_->rx, cfg_.buffer_size, false);
    if (!impl_->buffer) {
        last_error_ = "cannot create Pluto RX buffer";
        connected_ = false;
        return false;
    }

    connected_ = true;
    return true;
}

void PlutoSdrDevice::set_rx_lo(int64_t hz) {
    cfg_.rx_lo = hz;
    if (connected_) write_attr_ll(impl_->rx_lo, "frequency", hz, last_error_);
}

void PlutoSdrDevice::set_rx_rf_bandwidth(int hz) {
    cfg_.rf_bandwidth = hz;
    if (connected_) write_attr_ll(impl_->rx_phy, "rf_bandwidth", hz, last_error_);
}

void PlutoSdrDevice::set_rx_gain(int db) {
    cfg_.gain_db = db;
    if (connected_) {
        write_attr_str(impl_->rx_phy, "gain_control_mode", "manual", last_error_);
        write_attr_ll(impl_->rx_phy, "hardwaregain", db, last_error_);
    }
}

std::vector<Complex> PlutoSdrDevice::receive(size_t samples) {
    if (!connected_ || !impl_->buffer) return std::vector<Complex>(samples, Complex{0.0f, 0.0f});

    if (samples != cfg_.buffer_size) {
        if (impl_->buffer) iio_buffer_destroy(impl_->buffer);
        cfg_.buffer_size = samples;
        impl_->buffer = iio_device_create_buffer(impl_->rx, cfg_.buffer_size, false);
        if (!impl_->buffer) {
            last_error_ = "cannot resize Pluto RX buffer";
            connected_ = false;
            return std::vector<Complex>(samples, Complex{0.0f, 0.0f});
        }
    }

    const auto nbytes = iio_buffer_refill(impl_->buffer);
    if (nbytes < 0) {
        last_error_ = "iio_buffer_refill failed";
        return std::vector<Complex>(samples, Complex{0.0f, 0.0f});
    }

    std::vector<Complex> out;
    out.reserve(samples);

    const char* p = static_cast<const char*>(iio_buffer_first(impl_->buffer, impl_->rx_i));
    const char* end = static_cast<const char*>(iio_buffer_end(impl_->buffer));
    const ptrdiff_t step = iio_buffer_step(impl_->buffer);

    // Pluto RX sample layout is interleaved signed 16-bit I/Q. Normalize to roughly [-1, 1].
    for (; p < end && out.size() < samples; p += step) {
        int16_t i = 0;
        int16_t q = 0;
        std::memcpy(&i, p + 0, sizeof(int16_t));
        std::memcpy(&q, p + sizeof(int16_t), sizeof(int16_t));
        out.emplace_back(static_cast<float>(i) / 32768.0f, static_cast<float>(q) / 32768.0f);
    }
    if (out.size() < samples) out.resize(samples, Complex{0.0f, 0.0f});
    return out;
}

#else

struct PlutoSdrDevice::Impl {};

PlutoSdrDevice::PlutoSdrDevice(const PlutoSdrConfig& cfg)
    : impl_(std::make_unique<Impl>()), cfg_(cfg) {}
PlutoSdrDevice::~PlutoSdrDevice() = default;

bool PlutoSdrDevice::open() {
    connected_ = false;
    last_error_ = "libiio was not found at build time; PlutoSdrDevice is disabled";
    return false;
}

void PlutoSdrDevice::set_rx_lo(int64_t hz) { cfg_.rx_lo = hz; }
void PlutoSdrDevice::set_rx_rf_bandwidth(int hz) { cfg_.rf_bandwidth = hz; }
void PlutoSdrDevice::set_rx_gain(int db) { cfg_.gain_db = db; }
std::vector<Complex> PlutoSdrDevice::receive(size_t samples) {
    return std::vector<Complex>(samples, Complex{0.0f, 0.0f});
}

#endif

} // namespace sdr_receiver
