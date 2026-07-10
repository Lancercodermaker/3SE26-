#pragma once
#include "sdr_receiver/types.hpp"
#include <string>
#include <unordered_map>
#include <vector>

namespace sdr_receiver {

class DspProcessor {
public:
    std::vector<float> make_fft_mask(size_t n, const FilterParams& cfg) const;
    std::vector<Complex> filter_iq(const std::vector<Complex>& rx_data, const FilterParams& cfg);
    std::vector<double> moving_average(const std::vector<double>& x, int n) const;
    std::vector<std::pair<double, double>> threshold_grid(const std::vector<double>& smoothed, const std::vector<double>& k_values) const;

private:
    std::unordered_map<std::string, std::vector<float>> fft_mask_cache_;
    static std::vector<Complex> dft(const std::vector<Complex>& in, bool inverse);
};

} // namespace sdr_receiver
