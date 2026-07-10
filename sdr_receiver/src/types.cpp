#include "sdr_receiver/types.hpp"
#include <sstream>
#include <stdexcept>

namespace sdr_receiver {

std::string to_string(Team team) { return team == Team::Red ? "RED" : "BLUE"; }

std::string to_string(Target target) {
    switch (target) {
        case Target::Info: return "INFO";
        case Target::L1: return "L1";
        case Target::L2: return "L2";
        case Target::L3: return "L3";
    }
    return "INFO";
}

std::string to_string(RescueMode mode) {
    switch (mode) {
        case RescueMode::None: return "normal";
        case RescueMode::L2: return "L2";
        case RescueMode::L3: return "L3";
    }
    return "normal";
}

std::string to_string(ReceiverPhase phase) {
    switch (phase) {
        case ReceiverPhase::WaitingContext: return "WaitingContext";
        case ReceiverPhase::DebugManual: return "DebugManual";
        case ReceiverPhase::CompetitionInit: return "CompetitionInit";
        case ReceiverPhase::JamDecode: return "JamDecode";
        case ReceiverPhase::WaitLevelUpdate: return "WaitLevelUpdate";
        case ReceiverPhase::InfoDecode: return "InfoDecode";
    }
    return "WaitingContext";
}

Team team_from_string(const std::string& value) {
    if (value == "RED" || value == "red") return Team::Red;
    if (value == "BLUE" || value == "blue") return Team::Blue;
    throw std::invalid_argument("unknown team: " + value);
}

Target target_from_string(const std::string& value) {
    if (value == "INFO" || value == "info") return Target::Info;
    if (value == "L1" || value == "l1") return Target::L1;
    if (value == "L2" || value == "l2") return Target::L2;
    if (value == "L3" || value == "l3") return Target::L3;
    throw std::invalid_argument("unknown target: " + value);
}

std::string FilterParams::cache_key() const {
    std::ostringstream os;
    os << static_cast<int>(kind) << ':' << cutoff << ':' << transition << ':'
       << pass_low << ':' << pass_high << ':' << stop_low << ':' << stop_high << ':'
       << smooth_frac << ':' << trend_bits << ':' << max_ac_errors;
    return os.str();
}

} // namespace sdr_receiver
