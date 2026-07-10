#include "sdr_receiver/sdr_controller.hpp"
#include "sdr_receiver/config.hpp"
#include <algorithm>
#include <sstream>

namespace sdr_receiver {

SdrController::SdrController(TuneConfig& tune, ReceiverState& state, DataModel& data)
    : tune_(tune), state_(state), data_(data) {}

std::optional<CalibrationProfile> SdrController::get_active_cal_profile(Target target) const {
    if (target == Target::Info && state_.cal_profile.has_value()) return state_.cal_profile;
    return std::nullopt;
}

RescueMode SdrController::get_info_rescue_mode(Target target) const {
    if (target != Target::Info) return RescueMode::None;
    if (auto profile = get_active_cal_profile(target)) return profile->rescue;
    if (state_.info_l2_rescue) return RescueMode::L2;
    if (state_.info_l3_rescue) return RescueMode::L3;
    return RescueMode::None;
}

int SdrController::get_info_rescue_offset(RescueMode mode) {
    if (mode == RescueMode::L2) {
        auto& idx = state_.info_l2_rescue_offset_index;
        idx = idx % static_cast<int>(config::INFO_L2_RESCUE_LO_OFFSETS.size());
        return config::INFO_L2_RESCUE_LO_OFFSETS[idx];
    }
    auto& idx = state_.info_l3_rescue_offset_index;
    idx = idx % static_cast<int>(config::INFO_L3_RESCUE_LO_OFFSETS.size());
    return config::INFO_L3_RESCUE_LO_OFFSETS[idx];
}

int SdrController::get_info_rescue_accept_errors(RescueMode mode) const {
    return mode == RescueMode::L2 ? config::INFO_L2_RESCUE_ACCEPT_AC_ERRORS : config::INFO_L3_RESCUE_ACCEPT_AC_ERRORS;
}

int SdrController::get_info_rescue_search_errors(RescueMode mode) const {
    return mode == RescueMode::L2 ? config::INFO_L2_RESCUE_SEARCH_AC_ERRORS : config::INFO_L3_RESCUE_SEARCH_AC_ERRORS;
}

int SdrController::get_info_rescue_header_limit(RescueMode mode) const {
    return mode == RescueMode::L2 ? config::INFO_L2_HEADER_MAX_ERRORS : config::INFO_L3_HEADER_MAX_ERRORS;
}

std::vector<double> SdrController::get_info_rescue_threshold_values(RescueMode mode) const {
    if (mode == RescueMode::L2) return {-0.35, -0.2, -0.1, 0.0, 0.1, 0.2, 0.35};
    return config::INFO_L3_THRESHOLD_K_VALUES;
}

std::vector<char> SdrController::get_info_rescue_polarities(RescueMode mode) const {
    if (mode == RescueMode::L2) return {'+', '-'};
    return {'+'};
}

bool SdrController::is_info_l2_rescue(Target target) const { return get_info_rescue_mode(target) == RescueMode::L2; }
bool SdrController::is_info_l3_rescue(Target target) const { return get_info_rescue_mode(target) == RescueMode::L3; }
bool SdrController::is_info_rescue(Target target) const { return get_info_rescue_mode(target) != RescueMode::None; }

int SdrController::team_signed_rescue_offset(int offset, Team team) const {
    return team == Team::Blue ? -offset : offset;
}

FilterParams SdrController::mirror_asym_filter_params(const FilterParams& cfg) const {
    if (cfg.kind != FilterKind::AsymFft) return cfg;
    FilterParams out = cfg;
    out.pass_low = -cfg.pass_high;
    out.pass_high = -cfg.pass_low;
    out.stop_low = -cfg.stop_high;
    out.stop_high = -cfg.stop_low;
    return out;
}

RadioParams SdrController::get_effective_radio_params(std::optional<Team> team_opt, std::optional<Target> target_opt) {
    const Team team = team_opt.value_or(tune_.team);
    const Target target = target_opt.value_or(tune_.target);
    RadioParams p = config::base_radio_params(team, target);
    p.base_freq = p.freq;
    p.lo_offset = 0;
    p.digital_shift = 0;
    if (auto it = state_.manual_rx_gains.find(target); it != state_.manual_rx_gains.end()) p.gain = it->second;
    p.gain_floor = p.gain;
    p.mode = "normal";

    auto cal_profile = get_active_cal_profile(target);
    const RescueMode rescue_mode = get_info_rescue_mode(target);
    if (cal_profile.has_value()) {
        const int offset = team_signed_rescue_offset(cal_profile->offset, team);
        p.lo_offset = offset;
        p.digital_shift = offset;
        p.rf_bw = cal_profile->rf_bw;
        p.gain = cal_profile->gain;
        p.gain_floor = cal_profile->gain;
        p.mode = cal_profile->label;
    } else if (rescue_mode == RescueMode::L2) {
        const int offset = team_signed_rescue_offset(get_info_rescue_offset(RescueMode::L2), team);
        p.lo_offset = offset;
        p.digital_shift = offset;
        p.rf_bw = std::max(p.rf_bw, config::INFO_L2_RESCUE_RF_BW);
        p.gain = config::INFO_L2_RESCUE_GAIN;
        p.gain_floor = config::INFO_L2_RESCUE_GAIN;
        p.mode = "info_l2_rescue";
    } else if (rescue_mode == RescueMode::L3) {
        const int offset = team_signed_rescue_offset(get_info_rescue_offset(RescueMode::L3), team);
        p.lo_offset = offset;
        p.digital_shift = offset;
        p.rf_bw = std::max(p.rf_bw, config::INFO_L3_RESCUE_RF_BW);
        if (state_.manual_rx_gains.find(target) == state_.manual_rx_gains.end()) {
            p.gain = config::INFO_L3_RESCUE_GAIN;
            p.gain_floor = config::INFO_L3_RESCUE_GAIN;
        }
        p.mode = "info_l3_rescue";
    }

    if (state_.active_profile.has_value()) {
        const RadioProfile& profile = *state_.active_profile;
        if (profile.rx_lo > 0) {
            p.base_freq = profile.rx_lo;
            p.rx_lo = profile.rx_lo;
            p.lo_offset = 0;
        }
        p.digital_shift = profile.digital_shift;
        if (profile.rf_bw > 0) p.rf_bw = profile.rf_bw;
        if (profile.gain > 0) {
            p.gain = profile.gain;
            p.gain_floor = profile.gain;
        }
        p.mode = "profile:" + profile.target_key;
        if (!profile.filter_name.empty()) p.mode += ":" + profile.filter_name;
    }
    p.rx_lo = p.base_freq + p.lo_offset;
    return p;
}

FilterParams SdrController::get_effective_filter_params(std::optional<Target> target_opt) {
    const Target target = target_opt.value_or(tune_.target);
    FilterParams cfg;
    if (auto profile = get_active_cal_profile(target)) cfg = profile->filter_params;
    else if (is_info_l2_rescue(target)) cfg = config::info_l2_rescue_filter_params();
    else if (is_info_l3_rescue(target)) cfg = config::info_l3_rescue_filter_params();
    else cfg = config::base_filter_params(target);
    if (state_.active_profile.has_value() && state_.active_profile->has_filter_params) {
        cfg = state_.active_profile->filter_params;
    }
    return tune_.team == Team::Blue ? mirror_asym_filter_params(cfg) : cfg;
}

void SdrController::mark_sdr_config_dirty() { last_cfg_key_.clear(); }

void SdrController::clear_calibration_override(bool cancel_active) {
    state_.cal_profile.reset();
    if (cancel_active) {
        state_.cal.active = false;
        state_.cal.queue.clear();
        state_.cal.index = -1;
        state_.cal.stage = "idle";
        state_.cal.fallback_index = 0;
    }
}

void SdrController::reset_tracking_state(bool clear_scores) {
    state_.bit_pools.clear();
    state_.byte_pools.clear();
    if (clear_scores) state_.pool_scores.clear();
}

void SdrController::select_tune_target(Target target, bool info_l3_rescue, bool info_l2_rescue) {
    clear_calibration_override(true);
    tune_.target = target;
    state_.stats.jam_rf_match_streak = 0;
    state_.stats.jam_rf_target_changed = now_sec();
    state_.info_l2_rescue = target == Target::Info && info_l2_rescue;
    state_.info_l3_rescue = target == Target::Info && info_l3_rescue;
    if (state_.info_l2_rescue) state_.stats.last_error = "manual mode: INFO_L2_RESCUE";
    else if (state_.info_l3_rescue) state_.stats.last_error = "manual mode: INFO_L3_RESCUE";
    else state_.stats.last_error = "manual mode: " + to_string(target) + " normal";
    mark_sdr_config_dirty();
}

void SdrController::cycle_info_rescue_offset(int delta) {
    clear_calibration_override(true);
    const RescueMode mode = get_info_rescue_mode(Target::Info) == RescueMode::None ? RescueMode::L3 : get_info_rescue_mode(Target::Info);
    if (mode == RescueMode::L2) {
        auto& idx = state_.info_l2_rescue_offset_index;
        idx = (idx + delta + static_cast<int>(config::INFO_L2_RESCUE_LO_OFFSETS.size())) % static_cast<int>(config::INFO_L2_RESCUE_LO_OFFSETS.size());
        select_tune_target(Target::Info, false, true);
        state_.stats.last_error = "INFO_L2_RESCUE offset=" + std::to_string(config::INFO_L2_RESCUE_LO_OFFSETS[idx] / 1000) + "kHz";
    } else {
        auto& idx = state_.info_l3_rescue_offset_index;
        idx = (idx + delta + static_cast<int>(config::INFO_L3_RESCUE_LO_OFFSETS.size())) % static_cast<int>(config::INFO_L3_RESCUE_LO_OFFSETS.size());
        select_tune_target(Target::Info, true, false);
        state_.stats.last_error = "INFO_L3_RESCUE offset=" + std::to_string(config::INFO_L3_RESCUE_LO_OFFSETS[idx] / 1000) + "kHz";
    }
}

void SdrController::adjust_manual_gain(int delta) {
    clear_calibration_override(true);
    const Target target = tune_.target;
    int current = config::base_radio_params(tune_.team, target).gain;
    if (auto it = state_.manual_rx_gains.find(target); it != state_.manual_rx_gains.end()) current = it->second;
    const int new_gain = std::clamp(current + delta, config::RX_GAIN_MIN, config::RX_GAIN_MAX);
    state_.manual_rx_gains[target] = new_gain;
    state_.stats.gain_ceiling = new_gain;
    state_.stats.last_error = "manual gain " + to_string(target) + "=" + std::to_string(new_gain);
    mark_sdr_config_dirty();
}

void SdrController::set_rx_gain(ISdrDevice& sdr, int gain, const std::string& note) {
    sdr.set_rx_gain(gain);
    state_.stats.rx_gain = gain;
    state_.stats.gain_note = note;
}

RadioParams SdrController::apply_sdr_config(ISdrDevice& sdr) {
    const Target target = tune_.target;
    const Team team = tune_.team;
    if (target != Target::Info) {
        state_.info_l3_rescue = false;
        state_.info_l2_rescue = false;
        state_.cal_profile.reset();
    }
    const RescueMode rescue = get_info_rescue_mode(target);
    RadioParams p = get_effective_radio_params(team, target);
    std::ostringstream os;
    os << to_string(team) << ':' << to_string(target) << ':' << to_string(rescue) << ':'
       << p.rx_lo << ':' << p.digital_shift << ':' << p.gain << ':' << p.rf_bw << ':' << p.mode;
    const std::string cfg_key = os.str();
    if (last_cfg_key_ == cfg_key) return p;

    data_.bit_pool.clear();
    reset_tracking_state(true);
    state_.stats.ac = state_.stats.sof = state_.stats.crc8 = state_.stats.crc16 = 0;
    state_.stats.ac_raw = state_.stats.hdr_drop = state_.stats.len_drop = state_.stats.cmd_drop = 0;
    state_.stats.crc16_fail = state_.stats.crc16_fix = state_.stats.asm_chunks = state_.stats.asm_crc16 = 0;
    state_.stats.frame_reject = state_.stats.frame_pending = 0;
    state_.stats.last_data_update = 0.0;
    state_.stats.last_data_change = "none";
    state_.stats.last_ac_time = 0.0;
    state_.stats.last_crc16_time = 0.0;
    state_.stats.last_crc16_cmd = "none";
    state_.stats.last_error.clear();
    state_.stats.last_gain_adjust = 0.0;
    state_.stats.active_profile = state_.active_profile.has_value()
        ? state_.active_profile->match_slot + "/" + state_.active_profile->front_end_id + "/" +
              state_.active_profile->team + "/" + state_.active_profile->target_key
        : "none";
    state_.stats.gain_ceiling = p.gain;
    state_.stats.last_cfg_time = now_sec();

    sdr.set_rx_lo(p.rx_lo);
    sdr.set_rx_rf_bandwidth(p.rf_bw);
    set_rx_gain(sdr, p.gain, "manual");
    last_cfg_key_ = cfg_key;
    return p;
}

} // namespace sdr_receiver
