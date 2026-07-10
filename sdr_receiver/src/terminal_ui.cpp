#include "sdr_receiver/terminal_ui.hpp"
#include <algorithm>
#include <array>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <utility>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace sdr_receiver {
namespace {

constexpr size_t kDashboardWidth = 120;

void enable_ansi_console() {
#ifdef _WIN32
    HANDLE handle = GetStdHandle(STD_OUTPUT_HANDLE);
    if (handle == INVALID_HANDLE_VALUE) return;
    DWORD mode = 0;
    if (!GetConsoleMode(handle, &mode)) return;
    SetConsoleMode(handle, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
#endif
}

int map_int_or_zero(const std::map<std::string, int>& values, const std::string& key) {
    auto it = values.find(key);
    return it == values.end() ? 0 : it->second;
}

std::pair<int, int> map_pos_or_zero(const std::map<std::string, std::pair<int, int>>& values,
                                    const std::string& key) {
    auto it = values.find(key);
    return it == values.end() ? std::pair<int, int>{0, 0} : it->second;
}

std::array<int, 5> map_buff_or_zero(const std::map<std::string, std::array<int, 5>>& values,
                                    const std::string& key) {
    auto it = values.find(key);
    return it == values.end() ? std::array<int, 5>{{0, 0, 0, 0, 0}} : it->second;
}

std::string format_pos(const std::pair<int, int>& p) {
    std::ostringstream os;
    os << "(" << p.first << "," << p.second << ")";
    return os.str();
}

std::string map_key_or_default(const std::map<Target, std::string>& values, Target target) {
    auto it = values.find(target);
    return it == values.end() ? "---" : it->second;
}

int map_count_or_zero(const std::map<Target, int>& values, Target target) {
    auto it = values.find(target);
    return it == values.end() ? 0 : it->second;
}

}  // namespace

TerminalUi::~TerminalUi() { restore_terminal(); }

void TerminalUi::init_terminal() {
    if (initialized_) return;
    enable_ansi_console();
    std::cout << "\033[?1049h\033[2J\033[H\033[?25l";
    std::cout.flush();
    initialized_ = true;
}

void TerminalUi::restore_terminal() {
    if (!initialized_) return;
    std::cout << "\033[?25h\033[?1049l\n";
    std::cout.flush();
    initialized_ = false;
}

std::string TerminalUi::format_freq(int64_t hz) const {
    std::ostringstream os;
    os << std::fixed << std::setprecision(3) << (static_cast<double>(hz) / 1'000'000.0) << "MHz";
    return os.str();
}

std::string TerminalUi::lock_text(const ReceiverState& state) const {
    const double age = now_sec() - state.stats.last_crc16_time;
    if (state.stats.crc16 > 0 && age < 1.0) return "LOCKED";
    if (state.stats.ac > 0 && now_sec() - state.stats.last_ac_time < 1.0) return "AC_ACTIVE";
    return "SEARCH";
}

std::string TerminalUi::rescue_text(const ReceiverState& state) const {
    if (state.info_l2_rescue) return "L2rescue_ok_g40";
    if (state.info_l3_rescue) return "L3rescue_ok_g24";
    return "normal";
}

std::string TerminalUi::cal_text(const ReceiverState& state) const {
    if (!state.cal.active) return "CAL idle";
    std::ostringstream os;
    os << "CAL " << state.cal.stage << " " << (state.cal.index + 1) << "/" << state.cal.queue.size();
    if (state.cal_profile) os << " " << state.cal_profile->label;
    return os.str();
}

std::string TerminalUi::hp_line(const DataModel& data) const {
    std::ostringstream os;
    os << "HP   H1:" << map_int_or_zero(data.hp, "H1")
       << "  E2:" << map_int_or_zero(data.hp, "E2")
       << "  I3:" << map_int_or_zero(data.hp, "I3")
       << "  I4:" << map_int_or_zero(data.hp, "I4")
       << "  S7:" << map_int_or_zero(data.hp, "S7")
       << "      AMMO H1:" << map_int_or_zero(data.ammo, "H1")
       << " I3:" << map_int_or_zero(data.ammo, "I3")
       << " I4:" << map_int_or_zero(data.ammo, "I4")
       << " A6:" << map_int_or_zero(data.ammo, "A6")
       << " S7:" << map_int_or_zero(data.ammo, "S7");
    return os.str();
}

std::string TerminalUi::position_line(const DataModel& data) const {
    std::ostringstream os;
    os << "COIN " << data.coin_rem << "/" << data.coin_tot
       << "   POS H1:" << format_pos(map_pos_or_zero(data.pos, "H1"))
       << " E2:" << format_pos(map_pos_or_zero(data.pos, "E2"))
       << " I3:" << format_pos(map_pos_or_zero(data.pos, "I3"))
       << " I4:" << format_pos(map_pos_or_zero(data.pos, "I4"))
       << " A6:" << format_pos(map_pos_or_zero(data.pos, "A6"))
       << " S7:" << format_pos(map_pos_or_zero(data.pos, "S7"));
    return os.str();
}

std::string TerminalUi::occupation_line(const DataModel& data) const {
    const uint32_t bits = data.occupation_raw;
    std::ostringstream os;
    os << "OCCU Sup:" << (bits & 1u)
       << " Cen:" << ((bits >> 1) & 3u)
       << " Trp:" << ((bits >> 3) & 1u)
       << " For:" << ((bits >> 4) & 3u)
       << " Out:" << ((bits >> 6) & 3u)
       << " Base:" << ((bits >> 8) & 1u)
       << " Tun:" << ((bits >> 9) & 1u) << ((bits >> 10) & 1u)
       << ((bits >> 11) & 1u) << ((bits >> 12) & 1u)
       << " Hig:" << ((bits >> 13) & 1u)
       << " Fly:" << ((bits >> 14) & 1u)
       << " Roa:" << ((bits >> 15) & 1u);
    return os.str();
}

std::vector<std::string> TerminalUi::buff_lines(const DataModel& data) const {
    auto format = [&](const std::string& name) {
        const auto b = map_buff_or_zero(data.buff, name);
        std::ostringstream os;
        os << name << " Hp:" << b[0] << " Heat:" << b[1] << " Def:" << b[2]
           << " Vul:" << b[3] << " Atk:" << b[4];
        return os.str();
    };

    std::vector<std::string> lines;
    lines.push_back("BUFF " + format("H1") + "  " + format("E2"));
    lines.push_back("BUFF " + format("I3") + "  " + format("I4"));
    std::ostringstream sentry;
    sentry << "BUFF " << format("S7") << " Pose:" << data.sentry_posture;
    lines.push_back(sentry.str());
    return lines;
}

std::string TerminalUi::jam_line(const ReceiverState& state) const {
    std::ostringstream os;
    os << "JAM L1:[" << map_key_or_default(state.jam_keys, Target::L1) << "] "
       << map_count_or_zero(state.jam_keys_cnt, Target::L1)
       << "   L2:[" << map_key_or_default(state.jam_keys, Target::L2) << "] "
       << map_count_or_zero(state.jam_keys_cnt, Target::L2)
       << "   L3:[" << map_key_or_default(state.jam_keys, Target::L3) << "] "
       << map_count_or_zero(state.jam_keys_cnt, Target::L3);
    return os.str();
}

std::string TerminalUi::fit_line(const std::string& text) const {
    if (text.size() >= kDashboardWidth) return text.substr(0, kDashboardWidth);
    return text + std::string(kDashboardWidth - text.size(), ' ');
}

void TerminalUi::render(const TuneConfig& tune,
                        const ReceiverState& state,
                        const DataModel& data,
                        const RadioParams& radio,
                        bool force) {
    const double t = now_sec();
    if (!force && (t - last_render_sec_) < 0.20) return;
    last_render_sec_ = t;

    const bool crc_locked = state.stats.crc16 > 0 && (t - state.stats.last_crc16_time) < 1.0;
    const std::string team = to_string(tune.team);
    const std::string target = to_string(tune.target);

    std::vector<std::string> lines;
    lines.reserve(24);
    lines.push_back("v67 L2cal SDR receiver C++ port | 1 INFO 2 L1 3 L2 4 L3 5 INFO-L3 6 INFO-L2 7 L2 preset 8 L3 preset | C cal F full | q quit");
    lines.push_back("===============================================================================================");

    std::ostringstream os;
    os << std::fixed << std::setprecision(2);
    os << team << "-" << target << " " << lock_text(state)
       << "  ADC:" << state.stats.adc_rms
       << "  RMS:" << state.stats.adc_rms
       << "  gain:" << radio.gain << "/" << state.stats.gain_ceiling << " (" << state.stats.gain_note << ")"
       << "  AC:" << state.stats.ac << "/" << state.stats.ac_raw
       << "  HD:" << state.stats.hdr_drop
       << "  SOF:" << state.stats.sof
       << "  CRC8:" << state.stats.crc8
       << "  CRC16:" << state.stats.crc16
       << "  cmd:" << state.stats.last_crc16_cmd;
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << "RF state:" << (crc_locked ? "CRC_LOCKED  CRC16 path active" : state.stats.rf_state)
       << "  rx_log: ./rx_logs/rx_dec_"
       << "  mode=" << radio.mode
       << "  SPS=130";
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << "[CFG] " << target
       << " lo=" << format_freq(radio.rx_lo)
       << " shift=" << (radio.digital_shift / 1000) << "k"
       << " gain=" << radio.gain
       << " rf_bw=" << (radio.rf_bw / 1000) << "k"
       << " asy_pass=248..315kHz"
       << " mode=" << radio.mode;
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << "RM drop len:" << state.stats.len_drop
       << " cmd:" << state.stats.cmd_drop
       << " crc16fail:" << state.stats.crc16_fail
       << " fix:" << state.stats.crc16_fix
       << " asm:" << state.stats.asm_chunks << "/" << state.stats.asm_crc16
       << " rej:" << state.stats.frame_reject
       << " pend:" << state.stats.frame_pending;
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << "Timing data:" << (state.stats.last_data_update > 0.0 ? "active" : "never")
       << "  crc16:" << std::setprecision(2) << (t - state.stats.last_crc16_time)
       << "s ago(" << state.stats.last_crc16_cmd << ")"
       << "  loop:" << state.stats.loop_ms << "ms"
       << "  rx:" << state.stats.rx_ms << "ms"
       << "  demod:" << state.stats.demod_ms << "ms";
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << std::fixed << std::setprecision(2)
       << "JAM RF source:n/a  conf:" << state.stats.jam_rf_conf
       << "  streak:" << state.stats.jam_rf_match_streak
       << "  offset:" << state.stats.jam_rf_offset / 1000.0 << "kHz";
    lines.push_back(os.str());

    os.str("");
    os.clear();
    os << "Track pol=+ shift=0 k=0.35 lock:"
       << (crc_locked ? "1.00" : "0.00")
       << "s pools=" << state.bit_pools.size()
       << " bits=" << (state.bit_pools.empty() ? 0 : state.bit_pools.begin()->second.size()) << " max=240";
    lines.push_back(os.str());

    lines.push_back(cal_text(state));

    os.str("");
    os.clear();
    os << "Last frame src:" << (state.stats.last_frame_source.empty() ? "direct" : state.stats.last_frame_source)
       << " seq:" << (state.stats.last_frame_seq.empty() ? "--" : state.stats.last_frame_seq)
       << " hex:" << (state.stats.last_frame_hex.empty() ? "none" : state.stats.last_frame_hex);
    lines.push_back(os.str());
    lines.push_back("Last data: " + state.stats.last_data_change);
    lines.push_back("Error: " + (state.stats.last_error.empty() ? "none" : state.stats.last_error));
    lines.push_back("-----------------------------------------------------------------------------------------------");
    lines.push_back(hp_line(data));
    lines.push_back(position_line(data));
    lines.push_back(occupation_line(data));
    lines.push_back("-----------------------------------------------------------------------------------------------");
    const auto buffs = buff_lines(data);
    lines.insert(lines.end(), buffs.begin(), buffs.end());
    lines.push_back(jam_line(state));

    init_terminal();
    std::cout << "\033[H";
    for (const auto& line : lines) {
        std::cout << fit_line(line) << "\n";
    }
    for (size_t i = lines.size(); i < last_line_count_; ++i) {
        std::cout << std::string(kDashboardWidth, ' ') << "\n";
    }
    last_line_count_ = lines.size();
    std::cout.flush();
}

} // namespace sdr_receiver
