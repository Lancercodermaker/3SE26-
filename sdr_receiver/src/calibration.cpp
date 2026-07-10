#include "sdr_receiver/calibration.hpp"
#include "sdr_receiver/config.hpp"
#include <algorithm>

namespace sdr_receiver {

CalibrationManager::CalibrationManager(TuneConfig& tune, ReceiverState& state, SdrController& sdr_controller)
    : tune_(tune), state_(state), sdr_controller_(sdr_controller) {}

CalibrationProfile CalibrationManager::make_cal_profile(RescueMode rescue, int offset, int gain, int rf_bw,
                                                        const std::string& filter_name, const FilterParams& filter_params) const {
    CalibrationProfile p;
    p.rescue = rescue;
    p.offset = offset;
    p.gain = gain;
    p.rf_bw = rf_bw;
    p.filter_name = filter_name;
    p.filter_params = filter_params;
    p.label = "cal_" + to_string(rescue) + "_off" + std::to_string(offset / 1000) + "k_g" + std::to_string(gain) + "_bw" + std::to_string(rf_bw / 1000) + "k_" + filter_name;
    return p;
}

RescueMode CalibrationManager::calibration_scope_from_state() const {
    if (state_.info_l2_rescue) return RescueMode::L2;
    if (state_.info_l3_rescue) return RescueMode::L3;
    return RescueMode::None;
}

std::vector<CalibrationProfile> CalibrationManager::build_calibration_queue(bool full) const {
    std::vector<CalibrationProfile> q;
    const RescueMode scope = calibration_scope_from_state();
    const auto& gains = full ? config::CAL_FULL_GAINS : config::CAL_QUICK_GAINS;
    const auto& l2_offsets = full ? config::CAL_L2_FULL_OFFSETS : config::CAL_L2_QUICK_OFFSETS;
    const auto& l3_offsets = full ? config::CAL_L3_FULL_OFFSETS : config::CAL_L3_QUICK_OFFSETS;
    if (scope == RescueMode::None || scope == RescueMode::L2) {
        for (int off : l2_offsets) {
            for (int gain : gains) q.push_back(make_cal_profile(RescueMode::L2, off, gain, config::INFO_L2_RESCUE_RF_BW, "hist248", config::info_l2_rescue_filter_params()));
        }
    }
    if (scope == RescueMode::None || scope == RescueMode::L3) {
        for (int off : l3_offsets) {
            for (int gain : gains) q.push_back(make_cal_profile(RescueMode::L3, off, gain, config::INFO_L3_RESCUE_RF_BW, "l3cur", config::info_l3_rescue_filter_params()));
        }
    }
    return q;
}

CalibrationProfile CalibrationManager::build_direct_profile(RescueMode rescue) const {
    if (rescue == RescueMode::L2) {
        return make_cal_profile(RescueMode::L2,
                                80'000,
                                40,
                                config::INFO_L2_RESCUE_RF_BW,
                                "hist248",
                                config::info_l2_rescue_filter_params());
    }
    return make_cal_profile(RescueMode::L3,
                            200'000,
                            22,
                            config::INFO_L3_RESCUE_RF_BW,
                            "l3tight",
                            config::info_l3_rescue_filter_params());
}

std::vector<CalibrationProfile> CalibrationManager::build_validation_queue(const std::vector<CalibrationResult>& results, int top_k) const {
    auto sorted = results;
    std::sort(sorted.begin(), sorted.end(), [](const auto& a, const auto& b) { return a.score > b.score; });
    std::vector<CalibrationProfile> q;
    for (int i = 0; i < static_cast<int>(sorted.size()) && i < top_k; ++i) q.push_back(sorted[i].profile);
    return q;
}

CalibrationResult CalibrationManager::make_calibration_result(const CalibrationProfile& profile, const std::string& stage, double dwell_sec) const {
    CalibrationResult r;
    r.profile = profile;
    r.stage = stage;
    r.dwell_sec = dwell_sec;
    r.ac = state_.stats.ac;
    r.crc8 = state_.stats.crc8;
    r.crc16 = state_.stats.crc16;
    r.crc16_fail = state_.stats.crc16_fail;
    r.score = r.crc16 * 100.0 + r.crc8 * 10.0 + r.ac - r.crc16_fail * 20.0;
    r.quality = r.crc16 > 0 ? "good" : (r.crc8 > 0 ? "weak" : "bad");
    return r;
}

void CalibrationManager::start_calibration(bool full) {
    state_.cal.active = true;
    state_.cal.queue = build_calibration_queue(full);
    state_.cal.index = -1;
    state_.cal.results.clear();
    state_.cal.seed_results.clear();
    state_.cal.stage = "seed";
    const RescueMode scope = calibration_scope_from_state();
    state_.cal.scope = scope == RescueMode::None ? "ALL" : to_string(scope);
    state_.cal.full = full;
    state_.cal.dwell_sec = config::CAL_DWELL_SEC;
    state_.stats.last_error = full ? "full calibration started" : "quick calibration started";
    start_next_calibration_profile();
}

bool CalibrationManager::active() const { return state_.cal.active; }

void CalibrationManager::cancel_calibration() {
    sdr_controller_.clear_calibration_override(true);
    state_.stats.last_error = "CAL cancelled";
    sdr_controller_.mark_sdr_config_dirty();
}

void CalibrationManager::apply_direct_profile(RescueMode rescue) {
    if (rescue != RescueMode::L2 && rescue != RescueMode::L3) return;
    CalibrationProfile profile = build_direct_profile(rescue);
    sdr_controller_.clear_calibration_override(true);
    tune_.target = Target::Info;
    state_.info_l2_rescue = rescue == RescueMode::L2;
    state_.info_l3_rescue = rescue == RescueMode::L3;
    state_.cal_profile = profile;
    state_.cal.last_failover = now_sec();
    state_.stats.last_error = "direct preset -> " + profile.label;
    sdr_controller_.mark_sdr_config_dirty();
}

void CalibrationManager::start_next_calibration_profile() {
    auto& cal = state_.cal;
    ++cal.index;
    if (cal.index >= static_cast<int>(cal.queue.size())) {
        if (cal.stage == "seed" && !cal.results.empty()) {
            cal.seed_results = cal.results;
            cal.queue = build_validation_queue(cal.seed_results, cal.validate_top_k);
            cal.results.clear();
            cal.index = -1;
            cal.stage = "validate";
            cal.dwell_sec = config::CAL_VALIDATE_DWELL_SEC;
            start_next_calibration_profile();
            return;
        }
        finish_calibration();
        return;
    }
    state_.cal_profile = cal.queue[cal.index];
    state_.info_l2_rescue = state_.cal_profile->rescue == RescueMode::L2;
    state_.info_l3_rescue = state_.cal_profile->rescue == RescueMode::L3;
    cal.step_start = now_sec();
    state_.stats.last_error = "cal profile: " + state_.cal_profile->label;
    sdr_controller_.mark_sdr_config_dirty();
}

void CalibrationManager::finish_calibration() {
    auto sorted = sorted_calibration_results(1);
    if (!sorted.empty()) {
        state_.cal.best = sorted.front();
        state_.cal_profile = sorted.front().profile;
        state_.stats.last_error = "calibration best: " + sorted.front().profile.label;
    } else {
        state_.cal_profile.reset();
        state_.stats.last_error = "calibration finished: no result";
    }
    state_.cal.active = false;
    state_.cal.stage = "idle";
    sdr_controller_.mark_sdr_config_dirty();
}

void CalibrationManager::update_calibration() {
    auto& cal = state_.cal;
    if (!cal.active) return;
    if (!state_.cal_profile.has_value()) {
        start_next_calibration_profile();
        return;
    }
    const double dwell = cal.dwell_sec;
    if (now_sec() - cal.step_start < dwell) return;
    auto result = make_calibration_result(*state_.cal_profile, cal.stage, dwell);
    cal.results.push_back(result);
    start_next_calibration_profile();
}

std::vector<CalibrationResult> CalibrationManager::sorted_calibration_results(int limit) const {
    auto out = state_.cal.results;
    if (out.empty() && !state_.cal.seed_results.empty()) out = state_.cal.seed_results;
    std::sort(out.begin(), out.end(), [](const auto& a, const auto& b) { return a.score > b.score; });
    if (limit > 0 && out.size() > static_cast<size_t>(limit)) out.resize(limit);
    return out;
}

void CalibrationManager::maybe_failover_cal_profile() {
    if (state_.cal.active || !state_.cal_profile.has_value()) return;
    if (now_sec() - state_.stats.last_crc16_time < config::CRC16_STALE_SEC) return;
    if (now_sec() - state_.cal.last_failover < 0.5) return;
    auto sorted = sorted_calibration_results();
    if (sorted.empty()) return;
    const auto current = state_.cal_profile->label;
    for (const auto& r : sorted) {
        if (r.profile.label != current) {
            state_.cal_profile = r.profile;
            state_.info_l2_rescue = r.profile.rescue == RescueMode::L2;
            state_.info_l3_rescue = r.profile.rescue == RescueMode::L3;
            state_.cal.last_failover = now_sec();
            state_.stats.last_error = "CAL stale: failover to " + r.profile.label;
            sdr_controller_.mark_sdr_config_dirty();
            return;
        }
    }
}

} // namespace sdr_receiver
