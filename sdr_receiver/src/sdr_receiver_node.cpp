#include "sdr_receiver/calibration.hpp"
#include "sdr_receiver/config.hpp"
#include "sdr_receiver/demodulator.hpp"
#include "sdr_receiver/keyboard.hpp"
#include "sdr_receiver/pluto_sdr_device.hpp"
#include "sdr_receiver/profile_manager.hpp"
#include "sdr_receiver/radar_wireless_data.hpp"
#include "sdr_receiver/sdr_controller.hpp"
#include "sdr_receiver/terminal_ui.hpp"
#include "sdr_receiver/types.hpp"

#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/u_int8.hpp>

#include <sdr_receiver/msg/jam_code.hpp>
#include <sdr_receiver/msg/radar_context.hpp>
#include <sdr_receiver/msg/radar_wireless_buff.hpp>
#include <sdr_receiver/msg/radar_wireless_frame.hpp>
#include <sdr_receiver/msg/radar_wireless_gold_occupation.hpp>
#include <sdr_receiver/msg/radar_wireless_hp.hpp>
#include <sdr_receiver/msg/radar_wireless_key.hpp>
#include <sdr_receiver/msg/radar_wireless_position.hpp>
#include <sdr_receiver/msg/radar_wireless_projectile.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <functional>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>

namespace sdr_receiver {

using BuffMsg = sdr_receiver::msg::RadarWirelessBuff;
using ContextMsg = sdr_receiver::msg::RadarContext;
using FrameMsg = sdr_receiver::msg::RadarWirelessFrame;
using GoldOccupationMsg = sdr_receiver::msg::RadarWirelessGoldOccupation;
using HpMsg = sdr_receiver::msg::RadarWirelessHp;
using JamCodeMsg = sdr_receiver::msg::JamCode;
using KeyMsg = sdr_receiver::msg::RadarWirelessKey;
using PositionMsg = sdr_receiver::msg::RadarWirelessPosition;
using ProjectileMsg = sdr_receiver::msg::RadarWirelessProjectile;

namespace {

std::string upper_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::toupper(c));
    });
    return value;
}

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

Target target_from_level(int level) {
    if (level <= 1) return Target::L1;
    if (level == 2) return Target::L2;
    return Target::L3;
}

uint8_t level_from_target(Target target, int fallback_level) {
    switch (target) {
        case Target::L1: return 1;
        case Target::L2: return 2;
        case Target::L3: return 3;
        case Target::Info: return static_cast<uint8_t>(std::clamp(fallback_level, 1, 3));
    }
    return 0;
}

std::string key_to_ascii(const std::array<uint8_t, 6>& key) {
    std::string out;
    out.reserve(key.size());
    for (uint8_t ch : key) out.push_back(static_cast<char>(ch));
    return out;
}

bool is_printable_key(const std::string& key) {
    return key.size() == 6 &&
           std::all_of(key.begin(), key.end(), [](unsigned char ch) { return ch >= 32 && ch <= 126; });
}

Team team_from_self_id(uint8_t self_id, bool* valid, std::string* warning) {
    *valid = false;
    if (warning) warning->clear();
    if (self_id == 9) {
        *valid = true;
        return Team::Red;
    }
    if (self_id == 109) {
        *valid = true;
        return Team::Blue;
    }
    if (self_id >= 1 && self_id <= 99) {
        *valid = true;
        if (warning) *warning = "self_id is RED but not radar station id 9: " + std::to_string(self_id);
        return Team::Red;
    }
    if (self_id >= 101 && self_id <= 199) {
        *valid = true;
        if (warning) *warning = "self_id is BLUE but not radar station id 109: " + std::to_string(self_id);
        return Team::Blue;
    }
    if (warning) *warning = "invalid or unknown self_id: " + std::to_string(self_id);
    return Team::Red;
}

uint8_t jam_level_from_raw(uint8_t radar_info_raw) {
    return static_cast<uint8_t>((radar_info_raw >> 3) & 0x03);
}

bool key_mutable_from_raw(uint8_t radar_info_raw) {
    return ((radar_info_raw >> 5) & 0x01) != 0;
}

std::string command_hex(uint16_t command_id) {
    std::ostringstream os;
    os << "0x" << std::hex << std::uppercase << command_id;
    return os.str();
}

}  // namespace

class SdrReceiverNode final : public rclcpp::Node {
public:
    SdrReceiverNode()
        : Node("sdr_receiver_node"),
          tune_(),
          state_(config::make_default_state()),
          data_(config::make_default_data_model()),
          sdr_(nullptr),
          sdr_controller_(tune_, state_, data_),
          calibration_(tune_, state_, sdr_controller_),
          keyboard_(tune_, sdr_controller_, calibration_),
          demodulator_(tune_, state_, sdr_controller_) {
        run_mode_ = lower_copy(this->declare_parameter<std::string>("run_mode", "debug"));
        if (run_mode_ != "debug" && run_mode_ != "competition") {
            RCLCPP_WARN(this->get_logger(), "Unknown run_mode='%s', forcing competition", run_mode_.c_str());
            run_mode_ = "competition";
        }

        const std::string team_param = upper_copy(this->declare_parameter<std::string>("team", "RED"));
        const std::string target_param = upper_copy(this->declare_parameter<std::string>("target", "INFO"));
        const int period_ms = this->declare_parameter<int>("period_ms", 20);
        publish_debug_ = this->declare_parameter<bool>("publish_debug", true);
        terminal_ui_enabled_ = this->declare_parameter<bool>("terminal_ui", true);
        publish_wireless_data_ = this->declare_parameter<bool>("publish_wireless_data", true);
        publish_raw_air_frames_ = this->declare_parameter<bool>("publish_raw_air_frames", true);
        auto_context_control_ = this->declare_parameter<bool>("auto_context_control", run_mode_ == "competition");
        use_profiles_in_debug_ = this->declare_parameter<bool>("use_profiles_in_debug", false);
        keyboard_enabled_ = this->declare_parameter<bool>("keyboard_enabled", run_mode_ == "debug");
        if (run_mode_ == "competition") keyboard_enabled_ = false;

        judge_context_topic_ = this->declare_parameter<std::string>("judge_context_topic", "/judge/radar_context");
        judge_radar_info_topic_ = this->declare_parameter<std::string>("judge_radar_info_topic", "/judge/radar_info");
        judge_self_id_topic_ = this->declare_parameter<std::string>("judge_self_id_topic", "/judge/self_id");
        use_legacy_context_topics_ = this->declare_parameter<bool>("use_legacy_context_topics", true);

        match_slot_ = this->declare_parameter<std::string>("match_slot", "bo3_game1");
        front_end_id_ = this->declare_parameter<std::string>("front_end_id", "front_end_A");
        profile_path_ = this->declare_parameter<std::string>("profile_path", "config/sdr_profiles/competition_profiles.yaml");
        require_profile_ = this->declare_parameter<bool>("require_profile", run_mode_ == "competition");
        requested_max_jam_break_level_ = this->declare_parameter<int>("max_jam_break_level", 0);
        state_.max_jam_break_level = requested_max_jam_break_level_ >= 1 && requested_max_jam_break_level_ <= 3
            ? requested_max_jam_break_level_
            : 3;
        key_retry_interval_ms_ = this->declare_parameter<int>("key_retry_interval_ms", 500);
        key_retry_limit_ = this->declare_parameter<int>("key_retry_limit", 10);
        level_update_timeout_ms_ = this->declare_parameter<int>("level_update_timeout_ms", 2000);

        const bool use_real_sdr = this->declare_parameter<bool>("use_real_sdr", true);
        const bool fallback_to_mock = this->declare_parameter<bool>("fallback_to_mock", run_mode_ == "debug");
        const std::string sdr_uri = this->declare_parameter<std::string>("sdr_uri", "ip:192.168.2.1");

        tune_.team = team_from_string(team_param);
        tune_.target = target_from_string(target_param);
        state_.phase = run_mode_ == "debug" ? ReceiverPhase::DebugManual : ReceiverPhase::WaitingContext;
        state_.stats.run_mode = upper_copy(run_mode_);
        state_.stats.keyboard_enabled = keyboard_enabled_;
        state_.stats.receiver_phase = to_string(state_.phase);

        load_profiles();
        init_sdr_device(use_real_sdr, fallback_to_mock, sdr_uri);
        init_keyboard();
        init_ros_io();

        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(std::max(1, period_ms)),
            std::bind(&SdrReceiverNode::update, this));

        RCLCPP_INFO(this->get_logger(),
                    "sdr_receiver_node started: run_mode=%s auto_context=%s team=%s target=%s match_slot=%s front_end=%s max_level=%d",
                    run_mode_.c_str(),
                    auto_context_control_ ? "true" : "false",
                    to_string(tune_.team).c_str(),
                    to_string(tune_.target).c_str(),
                    match_slot_.c_str(),
                    front_end_id_.c_str(),
                    state_.max_jam_break_level);
    }

private:
    void load_profiles() {
        std::string error;
        if (profile_manager_.load(profile_path_, &error)) {
            if (requested_max_jam_break_level_ < 1 || requested_max_jam_break_level_ > 3) {
                state_.max_jam_break_level = profile_manager_.max_jam_break_level();
            }
            RCLCPP_INFO(this->get_logger(), "loaded SDR profile file: %s", profile_path_.c_str());
            return;
        }
        state_.stats.last_error = error;
        if (require_profile_) {
            RCLCPP_ERROR(this->get_logger(), "%s", error.c_str());
        } else {
            RCLCPP_WARN(this->get_logger(), "%s; using built-in defaults", error.c_str());
        }
    }

    void init_keyboard() {
        if (keyboard_enabled_) {
            keyboard_.init_input_terminal();
            RCLCPP_INFO(this->get_logger(), "run_mode=debug: keyboard control enabled");
        } else {
            RCLCPP_WARN(this->get_logger(), "keyboard control disabled");
        }
    }

    void init_ros_io() {
        status_pub_ = this->create_publisher<std_msgs::msg::String>("sdr/status", 10);
        useful_pub_ = this->create_publisher<std_msgs::msg::String>("sdr/useful_data", 10);

        position_pub_ = this->create_publisher<PositionMsg>("sdr/radar_wireless/position", 10);
        hp_pub_ = this->create_publisher<HpMsg>("sdr/radar_wireless/hp", 10);
        projectile_pub_ = this->create_publisher<ProjectileMsg>("sdr/radar_wireless/projectile", 10);
        gold_occupation_pub_ = this->create_publisher<GoldOccupationMsg>("sdr/radar_wireless/gold_occupation", 10);
        buff_pub_ = this->create_publisher<BuffMsg>("sdr/radar_wireless/buff", 10);
        key_pub_ = this->create_publisher<KeyMsg>("sdr/radar_wireless/key", 10);
        raw_frame_pub_ = this->create_publisher<FrameMsg>("sdr/radar_wireless/raw_frame", 10);
        jam_code_pub_ = this->create_publisher<JamCodeMsg>("sdr/jam_code", 10);

        context_sub_ = this->create_subscription<ContextMsg>(
            judge_context_topic_,
            rclcpp::QoS(rclcpp::KeepLast(5)).reliable(),
            std::bind(&SdrReceiverNode::context_callback, this, std::placeholders::_1));
        RCLCPP_INFO(this->get_logger(), "subscribed judge context: %s", judge_context_topic_.c_str());

        if (use_legacy_context_topics_) {
            radar_info_sub_ = this->create_subscription<std_msgs::msg::UInt8>(
                judge_radar_info_topic_,
                10,
                std::bind(&SdrReceiverNode::radar_info_callback, this, std::placeholders::_1));
            self_id_sub_ = this->create_subscription<std_msgs::msg::UInt8>(
                judge_self_id_topic_,
                10,
                std::bind(&SdrReceiverNode::self_id_callback, this, std::placeholders::_1));
        }
    }

    void init_sdr_device(bool use_real_sdr, bool fallback_to_mock, const std::string& uri) {
        if (!use_real_sdr) {
            sdr_ = std::make_unique<MockSdrDevice>();
            state_.stats.rf_state = "MOCK";
            state_.stats.last_error = "using MockSdrDevice: use_real_sdr=false";
            RCLCPP_WARN(this->get_logger(), "%s", state_.stats.last_error.c_str());
            return;
        }

        PlutoSdrConfig cfg;
        cfg.uri = uri;
        cfg.sample_rate = config::SDR_FS;
        cfg.buffer_size = config::RX_BUFFER_SIZE;
        const RadioParams initial = sdr_controller_.get_effective_radio_params(tune_.team, tune_.target);
        cfg.rx_lo = initial.rx_lo;
        cfg.rf_bandwidth = initial.rf_bw;
        cfg.gain_db = initial.gain;
        cfg.fallback_to_mock = fallback_to_mock;

        auto pluto = std::make_unique<PlutoSdrDevice>(cfg);
        if (pluto->open()) {
            state_.stats.rf_state = "PLUTO_CONNECTED";
            state_.stats.last_error = "PlutoSDR connected: " + uri;
            RCLCPP_INFO(this->get_logger(), "%s", state_.stats.last_error.c_str());
            sdr_ = std::move(pluto);
            return;
        }

        const std::string error = pluto->last_error();
        if (fallback_to_mock) {
            state_.stats.rf_state = "MOCK_FALLBACK";
            state_.stats.last_error = "PlutoSDR open failed, falling back to MockSdrDevice: " + error;
            RCLCPP_WARN(this->get_logger(), "%s", state_.stats.last_error.c_str());
            sdr_ = std::make_unique<MockSdrDevice>();
        } else {
            state_.stats.rf_state = "SDR_ERROR";
            state_.stats.last_error = "PlutoSDR open failed and fallback_to_mock=false: " + error;
            RCLCPP_FATAL(this->get_logger(), "%s", state_.stats.last_error.c_str());
            throw std::runtime_error(state_.stats.last_error);
        }
    }

    void context_callback(const ContextMsg::SharedPtr msg) {
        bool valid_id = false;
        std::string warning;
        Team team = team_from_self_id(msg->self_id, &valid_id, &warning);
        if (msg->self_color == 0) {
            team = Team::Blue;
        } else if (msg->self_color == 2) {
            team = Team::Red;
        }

        const uint8_t raw_level = msg->jam_level != 0 ? msg->jam_level : jam_level_from_raw(msg->radar_info_raw);
        update_context(msg->self_id,
                       valid_id,
                       team,
                       msg->radar_info_raw,
                       raw_level,
                       msg->key_mutable || key_mutable_from_raw(msg->radar_info_raw),
                       msg->game_progress,
                       msg->match_time,
                       msg->referee_online,
                       warning);
    }

    void radar_info_callback(const std_msgs::msg::UInt8::SharedPtr msg) {
        JudgeContext ctx = state_.judge_context;
        update_context(ctx.self_id,
                       ctx.valid_self_id,
                       ctx.team,
                       msg->data,
                       jam_level_from_raw(msg->data),
                       key_mutable_from_raw(msg->data),
                       ctx.game_progress,
                       ctx.match_time,
                       ctx.referee_online,
                       "");
    }

    void self_id_callback(const std_msgs::msg::UInt8::SharedPtr msg) {
        bool valid_id = false;
        std::string warning;
        const Team team = team_from_self_id(msg->data, &valid_id, &warning);
        JudgeContext ctx = state_.judge_context;
        update_context(msg->data,
                       valid_id,
                       team,
                       ctx.radar_info_raw,
                       ctx.jam_level,
                       ctx.key_mutable,
                       ctx.game_progress,
                       ctx.match_time,
                       ctx.referee_online,
                       warning);
    }

    void update_context(uint8_t self_id,
                        bool valid_self_id,
                        Team team,
                        uint8_t radar_info_raw,
                        uint8_t jam_level,
                        bool key_mutable,
                        uint8_t game_progress,
                        int16_t match_time,
                        bool referee_online,
                        const std::string& warning) {
        JudgeContext& ctx = state_.judge_context;
        ctx.received = true;
        ctx.self_id = self_id;
        ctx.valid_self_id = valid_self_id;
        ctx.team = team;
        ctx.radar_info_raw = radar_info_raw;
        ctx.jam_level = jam_level;
        ctx.valid_level = jam_level >= 1 && jam_level <= 3;
        ctx.key_mutable = key_mutable;
        ctx.game_progress = game_progress;
        ctx.match_time = match_time;
        ctx.referee_online = referee_online;
        ctx.last_update_sec = now_sec();

        state_.stats.context_self_id = self_id;
        state_.stats.context_jam_level = jam_level;
        state_.stats.radar_info_raw = radar_info_raw;
        state_.stats.key_mutable = key_mutable;
        if (!warning.empty()) {
            state_.stats.last_error = warning;
            RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 2000, "%s", warning.c_str());
        }
    }

    bool apply_context_control() {
        if (!auto_context_control_) {
            state_.phase = run_mode_ == "debug" ? ReceiverPhase::DebugManual : state_.phase;
            state_.stats.receiver_phase = to_string(state_.phase);
            const int info_under_level = tune_.target == Target::Info
                ? current_manual_info_under_level()
                : state_.encrypt_lvl;
            return apply_profile_for_target(tune_.target, info_under_level);
        }

        const JudgeContext& ctx = state_.judge_context;
        if (!ctx.received || !ctx.valid_self_id) {
            state_.phase = ReceiverPhase::WaitingContext;
            state_.stats.receiver_phase = to_string(state_.phase);
            state_.stats.last_error = "waiting for valid /judge/radar_context self_id";
            return run_mode_ == "debug";
        }
        if (tune_.team != ctx.team) {
            tune_.team = ctx.team;
            sdr_controller_.mark_sdr_config_dirty();
        }

        if (!ctx.valid_level) {
            if (run_mode_ == "competition") {
                state_.phase = ReceiverPhase::WaitingContext;
                state_.stats.receiver_phase = to_string(state_.phase);
                state_.stats.last_error = "waiting for valid radar_info jam_level 1..3";
                return false;
            }
            state_.encrypt_lvl = 1;
        } else {
            state_.encrypt_lvl = ctx.jam_level;
        }

        const int current_level = std::clamp(state_.encrypt_lvl, 1, state_.max_jam_break_level);
        if (state_.final_key_submitted || state_.phase == ReceiverPhase::InfoDecode) {
            const int info_level = std::clamp(state_.submitted_key_level > 0 ? state_.submitted_key_level : current_level,
                                              1,
                                              state_.max_jam_break_level);
            return enter_info_decode(info_level);
        }

        if (state_.phase == ReceiverPhase::WaitLevelUpdate) {
            if (ctx.valid_level && ctx.jam_level > state_.submitted_key_level) {
                state_.phase = ReceiverPhase::CompetitionInit;
                state_.submitted_key_level = 0;
            } else {
                const double waited_ms = (now_sec() - state_.wait_level_update_since) * 1000.0;
                if (waited_ms > level_update_timeout_ms_) {
                    state_.stats.last_error = "level update timeout; retrying current key level";
                    state_.wait_level_update_since = now_sec();
                }
                state_.stats.receiver_phase = to_string(state_.phase);
                const int retry_level = std::clamp(state_.submitted_key_level, 1, state_.max_jam_break_level);
                return apply_profile_for_target(target_from_level(retry_level), retry_level);
            }
        }

        state_.phase = ReceiverPhase::JamDecode;
        state_.stats.receiver_phase = to_string(state_.phase);
        return apply_profile_for_target(target_from_level(current_level), current_level);
    }

    int current_manual_info_under_level() const {
        if (state_.cal_profile.has_value()) {
            if (state_.cal_profile->rescue == RescueMode::L2) return 2;
            if (state_.cal_profile->rescue == RescueMode::L3) return 3;
        }
        if (state_.info_l2_rescue) return 2;
        if (state_.info_l3_rescue) return 3;
        if (state_.final_key_submitted && state_.submitted_key_level > 0) {
            return std::clamp(state_.submitted_key_level, 1, state_.max_jam_break_level);
        }
        return std::clamp(state_.encrypt_lvl, 1, state_.max_jam_break_level);
    }

    bool enter_info_decode(int info_under_level) {
        state_.phase = ReceiverPhase::InfoDecode;
        state_.stats.receiver_phase = to_string(state_.phase);
        return apply_profile_for_target(Target::Info, info_under_level);
    }

    bool apply_profile_for_target(Target target, int info_under_level) {
        const std::string profile_key = target_profile_key(target, info_under_level);
        std::optional<RadioProfile> profile;
        const bool use_profiles = run_mode_ == "competition" || use_profiles_in_debug_;
        if (use_profiles && profile_manager_.loaded()) {
            profile = profile_manager_.find(match_slot_, front_end_id_, tune_.team, profile_key);
        }

        if (!profile.has_value() && require_profile_) {
            state_.active_profile.reset();
            state_.stats.active_profile = "missing:" + match_slot_ + "/" + front_end_id_ + "/" +
                                          to_string(tune_.team) + "/" + profile_key;
            state_.stats.last_error = "missing required SDR profile: " + state_.stats.active_profile;
            RCLCPP_ERROR_THROTTLE(this->get_logger(), *this->get_clock(), 2000, "%s", state_.stats.last_error.c_str());
            return false;
        }

        if (tune_.target != target) {
            sdr_controller_.select_tune_target(target,
                                               target == Target::Info && info_under_level == 3,
                                               target == Target::Info && info_under_level == 2);
        } else if (target == Target::Info) {
            state_.info_l3_rescue = info_under_level == 3;
            state_.info_l2_rescue = info_under_level == 2;
        }

        const std::string active_key = profile.has_value()
            ? profile->match_slot + "/" + profile->front_end_id + "/" + profile->team + "/" + profile->target_key
            : "built_in:" + profile_key;
        const std::string current_key = state_.active_profile.has_value()
            ? state_.active_profile->match_slot + "/" + state_.active_profile->front_end_id + "/" +
                  state_.active_profile->team + "/" + state_.active_profile->target_key
            : "built_in:" + profile_key;

        if (profile.has_value()) {
            if (!state_.active_profile.has_value() || current_key != active_key) {
                state_.active_profile = profile;
                state_.stats.active_profile = active_key;
                sdr_controller_.mark_sdr_config_dirty();
                RCLCPP_INFO(this->get_logger(), "loaded active SDR profile: %s", active_key.c_str());
            }
        } else if (state_.active_profile.has_value()) {
            state_.active_profile.reset();
            state_.stats.active_profile = active_key;
            sdr_controller_.mark_sdr_config_dirty();
        } else {
            state_.stats.active_profile = active_key;
        }
        return true;
    }

    void update() {
        if (keyboard_enabled_) {
            if (!keyboard_.handle_keyboard()) {
                RCLCPP_INFO(this->get_logger(), "keyboard quit requested");
                rclcpp::shutdown();
                return;
            }
        }

        if (!apply_context_control()) {
            publish_waiting_status();
            return;
        }

        const double t0 = now_sec();
        const RadioParams radio = sdr_controller_.apply_sdr_config(*sdr_);

        const double rx0 = now_sec();
        auto rx_data = sdr_->receive(config::RX_BUFFER_SIZE);
        state_.stats.rx_ms = (now_sec() - rx0) * 1000.0;

        const double demod0 = now_sec();
        const DemodResult result = demodulator_.fast_demod(rx_data, radio.ac);
        state_.stats.demod_ms = (now_sec() - demod0) * 1000.0;

        update_wireless_data_model(result);

        calibration_.update_calibration();
        calibration_.maybe_failover_cal_profile();

        state_.stats.loop_ms = (now_sec() - t0) * 1000.0;

        if (terminal_ui_enabled_) terminal_ui_.render(tune_, state_, data_, radio);
        publish_useful_data(result, radio);
        if (publish_debug_) publish_status(result, radio);
        if (publish_wireless_data_) publish_radar_wireless_messages();
    }

    void update_wireless_data_model(const DemodResult& result) {
        if (!result.has_frame) return;
        if (publish_raw_air_frames_) publish_raw_frame(result);

        std::string error;
        if (!parse_radar_wireless_frame(result.command_id, result.payload, wireless_, &error)) {
            state_.stats.last_error = error;
            return;
        }

        switch (result.command_id) {
            case CMD_RADAR_WIRELESS_POSITION:
                data_.pos["H1"] = {wireless_.position.hero_x, wireless_.position.hero_y};
                data_.pos["E2"] = {wireless_.position.engineer_x, wireless_.position.engineer_y};
                data_.pos["I3"] = {wireless_.position.infantry3_x, wireless_.position.infantry3_y};
                data_.pos["I4"] = {wireless_.position.infantry4_x, wireless_.position.infantry4_y};
                data_.pos["A6"] = {wireless_.position.drone_x, wireless_.position.drone_y};
                data_.pos["S7"] = {wireless_.position.sentry_x, wireless_.position.sentry_y};
                break;
            case CMD_RADAR_WIRELESS_HP:
                data_.hp["H1"] = wireless_.hp.hero_hp;
                data_.hp["E2"] = wireless_.hp.engineer_hp;
                data_.hp["I3"] = wireless_.hp.infantry3_hp;
                data_.hp["I4"] = wireless_.hp.infantry4_hp;
                data_.hp["S7"] = wireless_.hp.sentry_hp;
                break;
            case CMD_RADAR_WIRELESS_PROJECTILE:
                data_.ammo["H1"] = wireless_.projectile.hero_projectile;
                data_.ammo["I3"] = wireless_.projectile.infantry3_projectile;
                data_.ammo["I4"] = wireless_.projectile.infantry4_projectile;
                data_.ammo["A6"] = wireless_.projectile.drone_projectile;
                data_.ammo["S7"] = wireless_.projectile.sentry_projectile;
                break;
            case CMD_RADAR_WIRELESS_GOLD_OCCUPATION:
                data_.coin_rem = wireless_.gold_occupation.remaining_gold;
                data_.coin_tot = wireless_.gold_occupation.total_gold;
                data_.occupation_raw = wireless_.gold_occupation.occupation_raw;
                break;
            case CMD_RADAR_WIRELESS_BUFF:
                data_.buff["H1"] = {static_cast<int>(wireless_.buff.hero_hp_recovery),
                                    static_cast<int>(wireless_.buff.hero_cooling_rate),
                                    static_cast<int>(wireless_.buff.hero_defense),
                                    static_cast<int>(wireless_.buff.hero_negative_defense),
                                    static_cast<int>(wireless_.buff.hero_attack)};
                data_.buff["E2"] = {static_cast<int>(wireless_.buff.engineer_hp_recovery),
                                    static_cast<int>(wireless_.buff.engineer_cooling_rate),
                                    static_cast<int>(wireless_.buff.engineer_defense),
                                    static_cast<int>(wireless_.buff.engineer_negative_defense),
                                    static_cast<int>(wireless_.buff.engineer_attack)};
                data_.buff["I3"] = {static_cast<int>(wireless_.buff.infantry3_hp_recovery),
                                    static_cast<int>(wireless_.buff.infantry3_cooling_rate),
                                    static_cast<int>(wireless_.buff.infantry3_defense),
                                    static_cast<int>(wireless_.buff.infantry3_negative_defense),
                                    static_cast<int>(wireless_.buff.infantry3_attack)};
                data_.buff["I4"] = {static_cast<int>(wireless_.buff.infantry4_hp_recovery),
                                    static_cast<int>(wireless_.buff.infantry4_cooling_rate),
                                    static_cast<int>(wireless_.buff.infantry4_defense),
                                    static_cast<int>(wireless_.buff.infantry4_negative_defense),
                                    static_cast<int>(wireless_.buff.infantry4_attack)};
                data_.buff["S7"] = {static_cast<int>(wireless_.buff.sentry_hp_recovery),
                                    static_cast<int>(wireless_.buff.sentry_cooling_rate),
                                    static_cast<int>(wireless_.buff.sentry_defense),
                                    static_cast<int>(wireless_.buff.sentry_negative_defense),
                                    static_cast<int>(wireless_.buff.sentry_attack)};
                data_.sentry_posture = wireless_.buff.sentry_posture;
                break;
            case CMD_RADAR_WIRELESS_KEY:
                handle_decoded_key();
                break;
            default:
                break;
        }

        data_.id = command_hex(result.command_id);
        state_.stats.last_data_update = now_sec();
        state_.stats.last_data_change = "radar_wireless cmd=" + data_.id;
    }

    void handle_decoded_key() {
        if (!wireless_.key.valid) return;
        const int level = level_from_target(tune_.target, state_.encrypt_lvl);
        if (level < 1 || level > 3 || tune_.target == Target::Info) return;

        const std::string key = key_to_ascii(wireless_.key.ascii_code);
        if (!is_printable_key(key)) {
            state_.stats.last_error = "jam key is not printable ASCII";
            return;
        }
        const Target key_target = target_from_level(level);
        state_.jam_keys[key_target] = key;
        state_.jam_keys_cnt[key_target] += 1;

        if (!should_publish_key(level, key)) return;
        publish_jam_code(level);

        if (!auto_context_control_) return;

        if (level >= state_.max_jam_break_level) {
            state_.final_key_submitted = true;
            state_.submitted_key_level = level;
            RCLCPP_INFO(this->get_logger(), "final L%d key submitted; switching directly to INFO", level);
            enter_info_decode(level);
        } else {
            state_.phase = ReceiverPhase::WaitLevelUpdate;
            state_.submitted_key_level = level;
            state_.wait_level_update_since = now_sec();
            state_.stats.receiver_phase = to_string(state_.phase);
        }
    }

    bool should_publish_key(int level, const std::string& key) {
        const double now = now_sec();
        const size_t idx = static_cast<size_t>(std::clamp(level, 1, 3));
        if (last_key_ascii_[idx] != key) {
            last_key_ascii_[idx] = key;
            key_publish_count_[idx] = 0;
            key_last_publish_[idx] = 0.0;
        }
        if (key_publish_count_[idx] >= key_retry_limit_) return false;
        if (key_last_publish_[idx] > 0.0 &&
            (now - key_last_publish_[idx]) * 1000.0 < static_cast<double>(key_retry_interval_ms_)) {
            return false;
        }
        ++key_publish_count_[idx];
        key_last_publish_[idx] = now;
        return true;
    }

    void fill_common_header(std_msgs::msg::Header& header) {
        header.stamp = this->now();
        header.frame_id = "sdr_receiver";
    }

    void publish_jam_code(int level) {
        JamCodeMsg msg;
        fill_common_header(msg.header);
        msg.valid = wireless_.key.valid;
        msg.command_id = CMD_RADAR_WIRELESS_KEY;
        msg.level = static_cast<uint8_t>(level);
        msg.team = to_string(tune_.team);
        msg.target = target_profile_key(target_from_level(level), level);
        msg.radio_mode = sdr_controller_.get_effective_radio_params(tune_.team, tune_.target).mode;
        msg.rf_state = state_.stats.rf_state;
        msg.radar_info_raw = state_.judge_context.radar_info_raw;
        msg.key_mutable = state_.judge_context.key_mutable;
        msg.key = wireless_.key.ascii_code;
        msg.ascii_code = key_to_ascii(wireless_.key.ascii_code);
        jam_code_pub_->publish(msg);
    }

    void publish_raw_frame(const DemodResult& result) {
        FrameMsg msg;
        fill_common_header(msg.header);
        msg.cmd_id = result.command_id;
        msg.payload_raw = result.payload;
        msg.crc8_ok = result.crc8_passed;
        msg.crc16_ok = result.crc16_passed;
        msg.air_chunk_index = result.air_chunk_index;
        msg.source_target = result.source_target;
        msg.team = to_string(tune_.team);
        raw_frame_pub_->publish(msg);
    }

    void publish_radar_wireless_messages() {
        PositionMsg pos;
        fill_common_header(pos.header);
        pos.command_id = CMD_RADAR_WIRELESS_POSITION;
        pos.valid = wireless_.position.valid;
        pos.hero_x = wireless_.position.hero_x;
        pos.hero_y = wireless_.position.hero_y;
        pos.engineer_x = wireless_.position.engineer_x;
        pos.engineer_y = wireless_.position.engineer_y;
        pos.infantry3_x = wireless_.position.infantry3_x;
        pos.infantry3_y = wireless_.position.infantry3_y;
        pos.infantry4_x = wireless_.position.infantry4_x;
        pos.infantry4_y = wireless_.position.infantry4_y;
        pos.drone_x = wireless_.position.drone_x;
        pos.drone_y = wireless_.position.drone_y;
        pos.sentry_x = wireless_.position.sentry_x;
        pos.sentry_y = wireless_.position.sentry_y;
        position_pub_->publish(pos);

        HpMsg hp;
        fill_common_header(hp.header);
        hp.command_id = CMD_RADAR_WIRELESS_HP;
        hp.valid = wireless_.hp.valid;
        hp.hero_hp = wireless_.hp.hero_hp;
        hp.engineer_hp = wireless_.hp.engineer_hp;
        hp.infantry3_hp = wireless_.hp.infantry3_hp;
        hp.infantry4_hp = wireless_.hp.infantry4_hp;
        hp.reserved = wireless_.hp.reserved;
        hp.sentry_hp = wireless_.hp.sentry_hp;
        hp_pub_->publish(hp);

        ProjectileMsg projectile;
        fill_common_header(projectile.header);
        projectile.command_id = CMD_RADAR_WIRELESS_PROJECTILE;
        projectile.valid = wireless_.projectile.valid;
        projectile.hero_projectile = wireless_.projectile.hero_projectile;
        projectile.infantry3_projectile = wireless_.projectile.infantry3_projectile;
        projectile.infantry4_projectile = wireless_.projectile.infantry4_projectile;
        projectile.drone_projectile = wireless_.projectile.drone_projectile;
        projectile.sentry_projectile = wireless_.projectile.sentry_projectile;
        projectile_pub_->publish(projectile);

        GoldOccupationMsg gold;
        fill_common_header(gold.header);
        gold.command_id = CMD_RADAR_WIRELESS_GOLD_OCCUPATION;
        gold.valid = wireless_.gold_occupation.valid;
        gold.remaining_gold = wireless_.gold_occupation.remaining_gold;
        gold.total_gold = wireless_.gold_occupation.total_gold;
        gold.occupation_raw = wireless_.gold_occupation.occupation_raw;
        gold_occupation_pub_->publish(gold);

        BuffMsg buff;
        fill_common_header(buff.header);
        buff.command_id = CMD_RADAR_WIRELESS_BUFF;
        buff.valid = wireless_.buff.valid;
        buff.hero_hp_recovery = wireless_.buff.hero_hp_recovery;
        buff.hero_cooling_rate = wireless_.buff.hero_cooling_rate;
        buff.hero_defense = wireless_.buff.hero_defense;
        buff.hero_negative_defense = wireless_.buff.hero_negative_defense;
        buff.hero_attack = wireless_.buff.hero_attack;
        buff.engineer_hp_recovery = wireless_.buff.engineer_hp_recovery;
        buff.engineer_cooling_rate = wireless_.buff.engineer_cooling_rate;
        buff.engineer_defense = wireless_.buff.engineer_defense;
        buff.engineer_negative_defense = wireless_.buff.engineer_negative_defense;
        buff.engineer_attack = wireless_.buff.engineer_attack;
        buff.infantry3_hp_recovery = wireless_.buff.infantry3_hp_recovery;
        buff.infantry3_cooling_rate = wireless_.buff.infantry3_cooling_rate;
        buff.infantry3_defense = wireless_.buff.infantry3_defense;
        buff.infantry3_negative_defense = wireless_.buff.infantry3_negative_defense;
        buff.infantry3_attack = wireless_.buff.infantry3_attack;
        buff.infantry4_hp_recovery = wireless_.buff.infantry4_hp_recovery;
        buff.infantry4_cooling_rate = wireless_.buff.infantry4_cooling_rate;
        buff.infantry4_defense = wireless_.buff.infantry4_defense;
        buff.infantry4_negative_defense = wireless_.buff.infantry4_negative_defense;
        buff.infantry4_attack = wireless_.buff.infantry4_attack;
        buff.sentry_hp_recovery = wireless_.buff.sentry_hp_recovery;
        buff.sentry_cooling_rate = wireless_.buff.sentry_cooling_rate;
        buff.sentry_defense = wireless_.buff.sentry_defense;
        buff.sentry_negative_defense = wireless_.buff.sentry_negative_defense;
        buff.sentry_attack = wireless_.buff.sentry_attack;
        buff.sentry_posture = wireless_.buff.sentry_posture;
        buff_pub_->publish(buff);

        KeyMsg key;
        fill_common_header(key.header);
        key.command_id = CMD_RADAR_WIRELESS_KEY;
        key.valid = wireless_.key.valid;
        key.ascii_code = wireless_.key.ascii_code;
        key_pub_->publish(key);
    }

    void publish_waiting_status() {
        RadioParams radio = sdr_controller_.get_effective_radio_params(tune_.team, tune_.target);
        DemodResult result;
        publish_useful_data(result, radio);
        if (publish_debug_) publish_status(result, radio);
    }

    void publish_useful_data(const DemodResult& result, const RadioParams& radio) {
        std_msgs::msg::String msg;
        std::ostringstream ss;
        ss << "{";
        ss << "\"team\":\"" << to_string(tune_.team) << "\",";
        ss << "\"target\":\"" << to_string(tune_.target) << "\",";
        ss << "\"phase\":\"" << to_string(state_.phase) << "\",";
        ss << "\"mode\":\"" << radio.mode << "\",";
        ss << "\"locked\":" << (result.locked ? "true" : "false") << ",";
        ss << "\"crc16_ok\":" << result.crc16_ok << ",";
        ss << "\"last_cmd\":\"" << state_.stats.last_crc16_cmd << "\",";
        ss << "\"last_frame_hex\":\"" << state_.stats.last_frame_hex << "\"";
        ss << "}";
        msg.data = ss.str();
        useful_pub_->publish(msg);
    }

    void publish_status(const DemodResult& result, const RadioParams& radio) {
        std_msgs::msg::String msg;
        std::ostringstream ss;
        ss << "{";
        ss << "\"team\":\"" << to_string(tune_.team) << "\",";
        ss << "\"target\":\"" << to_string(tune_.target) << "\",";
        ss << "\"phase\":\"" << to_string(state_.phase) << "\",";
        ss << "\"rf_state\":\"" << state_.stats.rf_state << "\",";
        ss << "\"run_mode\":\"" << state_.stats.run_mode << "\",";
        ss << "\"keyboard_enabled\":" << (state_.stats.keyboard_enabled ? "true" : "false") << ",";
        ss << "\"auto_context_control\":" << (auto_context_control_ ? "true" : "false") << ",";
        ss << "\"self_id\":" << static_cast<int>(state_.judge_context.self_id) << ",";
        ss << "\"radar_info_raw\":" << static_cast<int>(state_.judge_context.radar_info_raw) << ",";
        ss << "\"jam_level\":" << static_cast<int>(state_.judge_context.jam_level) << ",";
        ss << "\"max_jam_break_level\":" << state_.max_jam_break_level << ",";
        ss << "\"key_mutable\":" << (state_.judge_context.key_mutable ? "true" : "false") << ",";
        ss << "\"active_profile\":\"" << state_.stats.active_profile << "\",";
        ss << "\"sdr_connected\":" << (state_.stats.rf_state == "PLUTO_CONNECTED" ? "true" : "false") << ",";
        ss << "\"rx_lo\":" << radio.rx_lo << ",";
        ss << "\"gain\":" << radio.gain << ",";
        ss << "\"rf_bw\":" << radio.rf_bw << ",";
        ss << "\"mode\":\"" << radio.mode << "\",";
        ss << "\"ac\":" << state_.stats.ac << ",";
        ss << "\"sof\":" << state_.stats.sof << ",";
        ss << "\"crc8\":" << state_.stats.crc8 << ",";
        ss << "\"crc16\":" << state_.stats.crc16 << ",";
        ss << "\"candidates\":" << result.candidates << ",";
        ss << "\"loop_ms\":" << state_.stats.loop_ms << ",";
        ss << "\"rx_ms\":" << state_.stats.rx_ms << ",";
        ss << "\"demod_ms\":" << state_.stats.demod_ms << ",";
        ss << "\"cal_stage\":\"" << state_.cal.stage << "\",";
        ss << "\"last_error\":\"" << state_.stats.last_error << "\"";
        ss << "}";
        msg.data = ss.str();
        status_pub_->publish(msg);
    }

    TuneConfig tune_;
    ReceiverState state_;
    DataModel data_;
    RadarWirelessDataModel wireless_;
    ProfileManager profile_manager_;
    std::unique_ptr<ISdrDevice> sdr_;
    SdrController sdr_controller_;
    CalibrationManager calibration_;
    KeyboardController keyboard_;
    Demodulator demodulator_;
    TerminalUi terminal_ui_;

    rclcpp::TimerBase::SharedPtr timer_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr status_pub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr useful_pub_;
    rclcpp::Publisher<PositionMsg>::SharedPtr position_pub_;
    rclcpp::Publisher<HpMsg>::SharedPtr hp_pub_;
    rclcpp::Publisher<ProjectileMsg>::SharedPtr projectile_pub_;
    rclcpp::Publisher<GoldOccupationMsg>::SharedPtr gold_occupation_pub_;
    rclcpp::Publisher<BuffMsg>::SharedPtr buff_pub_;
    rclcpp::Publisher<KeyMsg>::SharedPtr key_pub_;
    rclcpp::Publisher<FrameMsg>::SharedPtr raw_frame_pub_;
    rclcpp::Publisher<JamCodeMsg>::SharedPtr jam_code_pub_;
    rclcpp::Subscription<ContextMsg>::SharedPtr context_sub_;
    rclcpp::Subscription<std_msgs::msg::UInt8>::SharedPtr radar_info_sub_;
    rclcpp::Subscription<std_msgs::msg::UInt8>::SharedPtr self_id_sub_;

    std::array<std::string, 4> last_key_ascii_{};
    std::array<int, 4> key_publish_count_{{0, 0, 0, 0}};
    std::array<double, 4> key_last_publish_{{0.0, 0.0, 0.0, 0.0}};

    bool publish_debug_ = true;
    bool terminal_ui_enabled_ = true;
    bool publish_wireless_data_ = true;
    bool publish_raw_air_frames_ = true;
    bool auto_context_control_ = true;
    bool keyboard_enabled_ = true;
    bool use_profiles_in_debug_ = false;
    bool use_legacy_context_topics_ = true;
    bool require_profile_ = false;
    std::string judge_context_topic_ = "/judge/radar_context";
    std::string judge_radar_info_topic_ = "/judge/radar_info";
    std::string judge_self_id_topic_ = "/judge/self_id";
    std::string run_mode_ = "debug";
    std::string profile_path_;
    std::string match_slot_;
    std::string front_end_id_;
    int key_retry_interval_ms_ = 500;
    int key_retry_limit_ = 10;
    int level_update_timeout_ms_ = 2000;
    int requested_max_jam_break_level_ = 0;
};

}  // namespace sdr_receiver

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<sdr_receiver::SdrReceiverNode>());
    rclcpp::shutdown();
    return 0;
}
