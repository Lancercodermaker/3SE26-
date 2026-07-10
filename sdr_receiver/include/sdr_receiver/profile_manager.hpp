#pragma once

#include "sdr_receiver/types.hpp"

#include <optional>
#include <string>
#include <unordered_map>

namespace sdr_receiver {

class ProfileManager {
public:
    bool load(const std::string& path, std::string* error = nullptr);
    bool loaded() const { return loaded_; }
    int max_jam_break_level() const { return max_jam_break_level_; }

    std::optional<RadioProfile> find(const std::string& match_slot,
                                     const std::string& front_end_id,
                                     Team team,
                                     const std::string& target_key) const;

private:
    static std::string make_key(const std::string& match_slot,
                                const std::string& front_end_id,
                                const std::string& team,
                                const std::string& target_key);

    std::unordered_map<std::string, RadioProfile> profiles_;
    int max_jam_break_level_ = 3;
    bool loaded_ = false;
};

std::string target_profile_key(Target target, int info_under_level);

}  // namespace sdr_receiver
