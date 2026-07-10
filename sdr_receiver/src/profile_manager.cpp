#include "sdr_receiver/profile_manager.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>

namespace sdr_receiver {
namespace {

std::string trim(std::string value) {
    const auto not_space = [](unsigned char c) { return !std::isspace(c); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

std::string strip_quotes(std::string value) {
    value = trim(value);
    if (value.size() >= 2 &&
        ((value.front() == '"' && value.back() == '"') ||
         (value.front() == '\'' && value.back() == '\''))) {
        return value.substr(1, value.size() - 2);
    }
    return value;
}

std::string upper_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::toupper(c));
    });
    return value;
}

std::string remove_inline_comment(const std::string& line) {
    bool single_quote = false;
    bool double_quote = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        if (c == '\'' && !double_quote) single_quote = !single_quote;
        if (c == '"' && !single_quote) double_quote = !double_quote;
        if (c == '#' && !single_quote && !double_quote) return line.substr(0, i);
    }
    return line;
}

int indent_of(const std::string& line) {
    int indent = 0;
    for (char c : line) {
        if (c == ' ') ++indent;
        else break;
    }
    return indent;
}

long long parse_int64(std::string value, bool* ok) {
    value = strip_quotes(value);
    value.erase(std::remove(value.begin(), value.end(), '_'), value.end());
    try {
        size_t idx = 0;
        const long long out = std::stoll(value, &idx, 0);
        *ok = idx == value.size();
        return out;
    } catch (...) {
        *ok = false;
        return 0;
    }
}

double parse_double(std::string value, bool* ok) {
    value = strip_quotes(value);
    value.erase(std::remove(value.begin(), value.end(), '_'), value.end());
    try {
        size_t idx = 0;
        const double out = std::stod(value, &idx);
        *ok = idx == value.size();
        return out;
    } catch (...) {
        *ok = false;
        return 0.0;
    }
}

bool is_complete_profile(const RadioProfile& profile) {
    return profile.rx_lo > 0 && profile.rf_bw > 0 && profile.gain > 0;
}

void apply_profile_field(RadioProfile& profile, const std::string& key, const std::string& raw_value) {
    bool ok = false;
    const std::string normalized = upper_copy(key);
    if (normalized == "RX_LO" || normalized == "FREQ" || normalized == "FREQUENCY") {
        profile.rx_lo = parse_int64(raw_value, &ok);
    } else if (normalized == "DIGITAL_SHIFT" || normalized == "LO_OFFSET") {
        profile.digital_shift = parse_int64(raw_value, &ok);
    } else if (normalized == "RF_BW" || normalized == "BANDWIDTH" || normalized == "RF_BANDWIDTH") {
        profile.rf_bw = static_cast<int>(parse_int64(raw_value, &ok));
    } else if (normalized == "GAIN" || normalized == "RX_GAIN") {
        profile.gain = static_cast<int>(parse_int64(raw_value, &ok));
    } else if (normalized == "FILTER" || normalized == "FILTER_NAME") {
        profile.filter_name = strip_quotes(raw_value);
    } else if (normalized == "FILTER_KIND") {
        const std::string kind = upper_copy(strip_quotes(raw_value));
        profile.filter_params.kind = kind == "ASYM_FFT" || kind == "ASYMFFT" ? FilterKind::AsymFft : FilterKind::SymFft;
        profile.has_filter_params = true;
    } else if (normalized == "CUTOFF") {
        profile.filter_params.cutoff = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "TRANSITION") {
        profile.filter_params.transition = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "PASS_LOW") {
        profile.filter_params.pass_low = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "PASS_HIGH") {
        profile.filter_params.pass_high = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "STOP_LOW") {
        profile.filter_params.stop_low = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "STOP_HIGH") {
        profile.filter_params.stop_high = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "SMOOTH_FRAC") {
        profile.filter_params.smooth_frac = parse_double(raw_value, &ok);
        profile.has_filter_params = true;
    } else if (normalized == "TREND_BITS") {
        profile.filter_params.trend_bits = static_cast<int>(parse_int64(raw_value, &ok));
        profile.has_filter_params = true;
    } else if (normalized == "MAX_AC_ERRORS") {
        profile.filter_params.max_ac_errors = static_cast<int>(parse_int64(raw_value, &ok));
        profile.has_filter_params = true;
    }
}

}  // namespace

std::string ProfileManager::make_key(const std::string& match_slot,
                                     const std::string& front_end_id,
                                     const std::string& team,
                                     const std::string& target_key) {
    return match_slot + "/" + front_end_id + "/" + upper_copy(team) + "/" + upper_copy(target_key);
}

bool ProfileManager::load(const std::string& path, std::string* error) {
    profiles_.clear();
    loaded_ = false;
    max_jam_break_level_ = 3;

    std::ifstream in(path);
    if (!in) {
        if (error) *error = "cannot open profile file: " + path;
        return false;
    }

    bool in_profile_sets = false;
    std::string match_slot;
    std::string front_end_id;
    std::string team;
    std::string target_key_name;
    RadioProfile current;
    bool have_current = false;

    auto flush_current = [&]() {
        if (!have_current) return;
        current.valid = is_complete_profile(current);
        if (current.valid) {
            profiles_[make_key(current.match_slot, current.front_end_id, current.team, current.target_key)] = current;
        }
        current = RadioProfile{};
        have_current = false;
    };

    std::string line;
    int line_no = 0;
    while (std::getline(in, line)) {
        ++line_no;
        std::string clean = remove_inline_comment(line);
        if (trim(clean).empty()) continue;

        const int indent = indent_of(clean);
        clean = trim(clean);
        const size_t colon = clean.find(':');
        if (colon == std::string::npos) continue;

        const std::string key = strip_quotes(clean.substr(0, colon));
        std::string value = trim(clean.substr(colon + 1));
        if (value == "{}") value.clear();

        if (indent == 0 && key == "profile_sets") {
            flush_current();
            in_profile_sets = true;
            continue;
        }
        if (indent == 0 && key == "max_jam_break_level") {
            bool ok = false;
            const int max_level = static_cast<int>(parse_int64(value, &ok));
            if (ok) max_jam_break_level_ = std::clamp(max_level, 1, 3);
            continue;
        }
        if (!in_profile_sets) continue;

        if (indent == 2) {
            flush_current();
            match_slot = key;
            front_end_id.clear();
            team.clear();
            target_key_name.clear();
        } else if (indent == 4) {
            flush_current();
            front_end_id = key;
            team.clear();
            target_key_name.clear();
        } else if (indent == 6) {
            flush_current();
            const std::string maybe_team = upper_copy(key);
            team = (maybe_team == "RED" || maybe_team == "BLUE") ? maybe_team : "";
            target_key_name.clear();
        } else if (indent == 8 && !team.empty()) {
            flush_current();
            target_key_name = upper_copy(key);
            current.match_slot = match_slot;
            current.front_end_id = front_end_id;
            current.team = team;
            current.target_key = target_key_name;
            current.filter_name = "";
            have_current = true;
        } else if (indent >= 10 && have_current) {
            apply_profile_field(current, key, value);
        } else {
            (void)line_no;
        }
    }
    flush_current();

    loaded_ = true;
    return true;
}

std::optional<RadioProfile> ProfileManager::find(const std::string& match_slot,
                                                 const std::string& front_end_id,
                                                 Team team,
                                                 const std::string& target_key) const {
    const auto it = profiles_.find(make_key(match_slot, front_end_id, to_string(team), target_key));
    if (it == profiles_.end()) return std::nullopt;
    return it->second;
}

std::string target_profile_key(Target target, int info_under_level) {
    switch (target) {
        case Target::L1: return "JAM_L1_KEY";
        case Target::L2: return "JAM_L2_KEY";
        case Target::L3: return "JAM_L3_KEY";
        case Target::Info:
            return "INFO_UNDER_L" + std::to_string(std::clamp(info_under_level, 1, 3));
    }
    return "INFO_UNDER_L1";
}

}  // namespace sdr_receiver
