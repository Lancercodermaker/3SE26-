#pragma once
#include "sdr_receiver/sdr_controller.hpp"
#include "sdr_receiver/types.hpp"
#include <vector>

namespace sdr_receiver {

class CalibrationManager {
public:
    CalibrationManager(TuneConfig& tune, ReceiverState& state, SdrController& sdr_controller);

    CalibrationProfile make_cal_profile(RescueMode rescue, int offset, int gain, int rf_bw,
                                        const std::string& filter_name, const FilterParams& filter_params) const;
    std::vector<CalibrationProfile> build_calibration_queue(bool full = false) const;
    std::vector<CalibrationProfile> build_validation_queue(const std::vector<CalibrationResult>& results, int top_k) const;
    CalibrationProfile build_direct_profile(RescueMode rescue) const;
    CalibrationResult make_calibration_result(const CalibrationProfile& profile, const std::string& stage, double dwell_sec) const;
    void start_calibration(bool full = false);
    bool active() const;
    void cancel_calibration();
    void apply_direct_profile(RescueMode rescue);
    void start_next_calibration_profile();
    void finish_calibration();
    void update_calibration();
    std::vector<CalibrationResult> sorted_calibration_results(int limit = -1) const;
    void maybe_failover_cal_profile();

private:
    RescueMode calibration_scope_from_state() const;
    TuneConfig& tune_;
    ReceiverState& state_;
    SdrController& sdr_controller_;
};

} // namespace sdr_receiver
