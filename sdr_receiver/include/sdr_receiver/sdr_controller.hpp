#pragma once
#include "sdr_receiver/types.hpp"
#include <optional>
#include <string>

namespace sdr_receiver {

class SdrController {
public:
    SdrController(TuneConfig& tune, ReceiverState& state, DataModel& data);

    std::optional<CalibrationProfile> get_active_cal_profile(Target target) const;
    RescueMode get_info_rescue_mode(Target target) const;
    int get_info_rescue_offset(RescueMode mode);
    int get_info_rescue_accept_errors(RescueMode mode) const;
    int get_info_rescue_search_errors(RescueMode mode) const;
    int get_info_rescue_header_limit(RescueMode mode) const;
    std::vector<double> get_info_rescue_threshold_values(RescueMode mode) const;
    std::vector<char> get_info_rescue_polarities(RescueMode mode) const;
    bool is_info_l2_rescue(Target target) const;
    bool is_info_l3_rescue(Target target) const;
    bool is_info_rescue(Target target) const;

    int team_signed_rescue_offset(int offset, Team team) const;
    FilterParams mirror_asym_filter_params(const FilterParams& cfg) const;
    RadioParams get_effective_radio_params(std::optional<Team> team = {}, std::optional<Target> target = {});
    FilterParams get_effective_filter_params(std::optional<Target> target = {});

    void mark_sdr_config_dirty();
    void clear_calibration_override(bool cancel_active);
    void reset_tracking_state(bool clear_scores);
    void select_tune_target(Target target, bool info_l3_rescue = false, bool info_l2_rescue = false);
    void cycle_info_rescue_offset(int delta);
    void adjust_manual_gain(int delta);
    void set_rx_gain(ISdrDevice& sdr, int gain, const std::string& note);
    RadioParams apply_sdr_config(ISdrDevice& sdr);

private:
    TuneConfig& tune_;
    ReceiverState& state_;
    DataModel& data_;
    std::string last_cfg_key_;
};

} // namespace sdr_receiver
