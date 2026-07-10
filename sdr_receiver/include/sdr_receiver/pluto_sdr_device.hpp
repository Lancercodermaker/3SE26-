#pragma once

#include "sdr_receiver/types.hpp"
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace sdr_receiver {

struct PlutoSdrConfig {
    std::string uri = "ip:192.168.2.1";
    int64_t sample_rate = 2'500'000;
    int64_t rx_lo = 433'200'000;
    int64_t rf_bandwidth = 540'000;
    int gain_db = 24;
    size_t buffer_size = 2048;
    bool fallback_to_mock = true;
};

// Real ADALM-Pluto SDR device using libiio.
// This replaces MockSdrDevice when use_real_sdr:=true.
class PlutoSdrDevice final : public ISdrDevice {
public:
    explicit PlutoSdrDevice(const PlutoSdrConfig& cfg);
    ~PlutoSdrDevice() override;

    bool open();
    bool is_connected() const { return connected_; }
    std::string last_error() const { return last_error_; }

    void set_rx_lo(int64_t hz) override;
    void set_rx_rf_bandwidth(int hz) override;
    void set_rx_gain(int db) override;
    std::vector<Complex> receive(size_t samples) override;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    PlutoSdrConfig cfg_;
    bool connected_ = false;
    std::string last_error_;
};

} // namespace sdr_receiver
