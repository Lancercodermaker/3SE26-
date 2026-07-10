#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace sdr_receiver {

constexpr uint16_t CMD_RADAR_WIRELESS_POSITION = 0x0A01;
constexpr uint16_t CMD_RADAR_WIRELESS_HP = 0x0A02;
constexpr uint16_t CMD_RADAR_WIRELESS_PROJECTILE = 0x0A03;
constexpr uint16_t CMD_RADAR_WIRELESS_GOLD_OCCUPATION = 0x0A04;
constexpr uint16_t CMD_RADAR_WIRELESS_BUFF = 0x0A05;
constexpr uint16_t CMD_RADAR_WIRELESS_KEY = 0x0A06;

struct RadarWirelessPositionData {
    bool valid = false;
    uint16_t hero_x = 0;
    uint16_t hero_y = 0;
    uint16_t engineer_x = 0;
    uint16_t engineer_y = 0;
    uint16_t infantry3_x = 0;
    uint16_t infantry3_y = 0;
    uint16_t infantry4_x = 0;
    uint16_t infantry4_y = 0;
    uint16_t drone_x = 0;
    uint16_t drone_y = 0;
    uint16_t sentry_x = 0;
    uint16_t sentry_y = 0;
};

struct RadarWirelessHpData {
    bool valid = false;
    uint16_t hero_hp = 0;
    uint16_t engineer_hp = 0;
    uint16_t infantry3_hp = 0;
    uint16_t infantry4_hp = 0;
    uint16_t reserved = 0;
    uint16_t sentry_hp = 0;
};

struct RadarWirelessProjectileData {
    bool valid = false;
    uint16_t hero_projectile = 0;
    uint16_t infantry3_projectile = 0;
    uint16_t infantry4_projectile = 0;
    uint16_t drone_projectile = 0;
    uint16_t sentry_projectile = 0;
};

struct RadarWirelessGoldOccupationData {
    bool valid = false;
    uint16_t remaining_gold = 0;
    uint16_t total_gold = 0;
    uint32_t occupation_raw = 0;
};

struct RadarWirelessBuffData {
    bool valid = false;

    uint8_t hero_hp_recovery = 0;
    uint16_t hero_cooling_rate = 0;
    uint8_t hero_defense = 0;
    uint8_t hero_negative_defense = 0;
    uint16_t hero_attack = 0;

    uint8_t engineer_hp_recovery = 0;
    uint16_t engineer_cooling_rate = 0;
    uint8_t engineer_defense = 0;
    uint8_t engineer_negative_defense = 0;
    uint16_t engineer_attack = 0;

    uint8_t infantry3_hp_recovery = 0;
    uint16_t infantry3_cooling_rate = 0;
    uint8_t infantry3_defense = 0;
    uint8_t infantry3_negative_defense = 0;
    uint16_t infantry3_attack = 0;

    uint8_t infantry4_hp_recovery = 0;
    uint16_t infantry4_cooling_rate = 0;
    uint8_t infantry4_defense = 0;
    uint8_t infantry4_negative_defense = 0;
    uint16_t infantry4_attack = 0;

    uint8_t sentry_hp_recovery = 0;
    uint16_t sentry_cooling_rate = 0;
    uint8_t sentry_defense = 0;
    uint8_t sentry_negative_defense = 0;
    uint16_t sentry_attack = 0;
    uint8_t sentry_posture = 0;
};

struct RadarWirelessKeyData {
    bool valid = false;
    std::array<uint8_t, 6> ascii_code{{0, 0, 0, 0, 0, 0}};
};

struct RadarWirelessDataModel {
    RadarWirelessPositionData position;
    RadarWirelessHpData hp;
    RadarWirelessProjectileData projectile;
    RadarWirelessGoldOccupationData gold_occupation;
    RadarWirelessBuffData buff;
    RadarWirelessKeyData key;
    uint16_t last_command_id = 0;
    double last_update_sec = 0.0;
};

uint16_t read_u16_le(const std::vector<uint8_t>& payload, size_t offset);
uint32_t read_u32_le(const std::vector<uint8_t>& payload, size_t offset);

bool parse_radar_wireless_frame(uint16_t command_id,
                                const std::vector<uint8_t>& payload,
                                RadarWirelessDataModel& model,
                                std::string* error = nullptr);

}  // namespace sdr_receiver
