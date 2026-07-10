#include "sdr_receiver/dsp.hpp"
#include "sdr_receiver/config.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <sstream>

namespace sdr_receiver {
namespace {
constexpr double PI = 3.14159265358979323846;

size_t next_power_of_two(size_t n) {
    size_t out = 1;
    while (out < n) out <<= 1;
    return out;
}
}

std::vector<float> DspProcessor::make_fft_mask(size_t n, const FilterParams& cfg) const {
    std::vector<float> mask(n, 0.0f);
    for (size_t i = 0; i < n; ++i) {
        double freq = 0.0;
        if (i <= n / 2) freq = static_cast<double>(i) * config::SDR_FS / static_cast<double>(n);
        else freq = -static_cast<double>(n - i) * config::SDR_FS / static_cast<double>(n);

        if (cfg.kind == FilterKind::SymFft) {
            const double af = std::abs(freq);
            if (af <= cfg.cutoff) mask[i] = 1.0f;
            else if (af < cfg.cutoff + cfg.transition) {
                mask[i] = static_cast<float>(0.5 * (1.0 + std::cos(PI * (af - cfg.cutoff) / cfg.transition)));
            }
        } else {
            if (freq >= cfg.pass_low && freq <= cfg.pass_high) {
                mask[i] = 1.0f;
            } else if (freq > cfg.stop_low && freq < cfg.pass_low) {
                mask[i] = static_cast<float>(0.5 * (1.0 - std::cos(PI * (freq - cfg.stop_low) / (cfg.pass_low - cfg.stop_low))));
            } else if (freq > cfg.pass_high && freq < cfg.stop_high) {
                mask[i] = static_cast<float>(0.5 * (1.0 + std::cos(PI * (freq - cfg.pass_high) / (cfg.stop_high - cfg.pass_high))));
            }
        }
    }
    return mask;
}

std::vector<Complex> DspProcessor::dft(const std::vector<Complex>& in, bool inverse) {
    const size_t n = next_power_of_two(std::max<size_t>(1, in.size()));
    std::vector<Complex> out(n, Complex{0.0f, 0.0f});
    std::copy(in.begin(), in.end(), out.begin());

    for (size_t i = 1, j = 0; i < n; ++i) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(out[i], out[j]);
    }

    for (size_t len = 2; len <= n; len <<= 1) {
        const double angle = (inverse ? 2.0 : -2.0) * PI / static_cast<double>(len);
        const Complex wlen{static_cast<float>(std::cos(angle)), static_cast<float>(std::sin(angle))};
        for (size_t i = 0; i < n; i += len) {
            Complex w{1.0f, 0.0f};
            for (size_t j = 0; j < len / 2; ++j) {
                const Complex u = out[i + j];
                const Complex v = out[i + j + len / 2] * w;
                out[i + j] = u + v;
                out[i + j + len / 2] = u - v;
                w *= wlen;
            }
        }
    }

    if (inverse) {
        const float scale = static_cast<float>(n);
        for (auto& value : out) value /= scale;
    }
    return out;
}

std::vector<Complex> DspProcessor::filter_iq(const std::vector<Complex>& rx_data, const FilterParams& cfg) {
    if (rx_data.empty()) return {};
    const auto fft = dft(rx_data, false);
    std::ostringstream os;
    os << fft.size() << ':' << cfg.cache_key();
    const std::string key = os.str();
    auto it = fft_mask_cache_.find(key);
    if (it == fft_mask_cache_.end()) {
        it = fft_mask_cache_.emplace(key, make_fft_mask(fft.size(), cfg)).first;
    }
    std::vector<Complex> masked(fft.size());
    for (size_t i = 0; i < fft.size(); ++i) masked[i] = fft[i] * it->second[i];
    auto filtered = dft(masked, true);
    filtered.resize(rx_data.size());
    return filtered;
}

std::vector<double> DspProcessor::moving_average(const std::vector<double>& x, int n) const {
    n = std::max(3, n);
    std::vector<double> y(x.size(), 0.0);
    if (x.empty()) return y;
    std::vector<double> prefix(x.size() + 1, 0.0);
    for (size_t i = 0; i < x.size(); ++i) prefix[i + 1] = prefix[i] + x[i];
    const int half = n / 2;
    for (size_t i = 0; i < x.size(); ++i) {
        const int lo = std::max<int>(0, static_cast<int>(i) - half);
        const int hi = std::min<int>(static_cast<int>(x.size()), static_cast<int>(i) + half + 1);
        const double sum = prefix[static_cast<size_t>(hi)] - prefix[static_cast<size_t>(lo)];
        y[i] = sum / static_cast<double>(hi - lo);
    }
    return y;
}

std::vector<std::pair<double, double>> DspProcessor::threshold_grid(const std::vector<double>& smoothed, const std::vector<double>& k_values) const {
    if (smoothed.empty()) return {};
    auto sorted = smoothed;
    std::sort(sorted.begin(), sorted.end());
    const auto pct = [&](double p) {
        const size_t idx = std::min(sorted.size() - 1, static_cast<size_t>(p * (sorted.size() - 1)));
        return sorted[idx];
    };
    const double mid = pct(0.5);
    const double spread = pct(0.75) - pct(0.25);
    std::vector<std::pair<double, double>> out;
    for (double k : k_values) out.emplace_back(k, spread < 1e-9 ? mid : mid + k * spread);
    return out;
}

} // namespace sdr_receiver
