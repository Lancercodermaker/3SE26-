#pragma once
#include "sdr_receiver/dsp.hpp"
#include "sdr_receiver/sdr_controller.hpp"
#include "sdr_receiver/types.hpp"
#include <string>
#include <vector>

namespace sdr_receiver {

class Demodulator {
public:
    Demodulator(TuneConfig& tune, ReceiverState& state, SdrController& sdr_controller);
    DemodResult fast_demod(const std::vector<Complex>& rx_data, const std::string& ac_target);

private:
    TuneConfig& tune_;
    ReceiverState& state_;
    SdrController& sdr_controller_;
    DspProcessor dsp_;

    static double median(std::vector<double> values);
    std::vector<std::tuple<double, double, int, char>> prioritized_plans(
        const std::vector<std::tuple<double, double, int, char>>& plans) const;
    double plan_score(const std::tuple<double, double, int, char>& plan) const;
};

} // namespace sdr_receiver
