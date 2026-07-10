#include "sdr_receiver/radar_wireless_data.hpp"
#include "sdr_receiver/types.hpp"

namespace sdr_receiver {
namespace {

bool require_size(const std::vector<uint8_t>& payload, size_t need, std::string* error) {
    if (payload.size() >= need) return true;
    if (error) {
        *error = "payload too short: need " + std::to_string(need) + ", got " + std::to_string(payload.size());
    }
    return false;
}

}  // namespace

uint16_t read_u16_le(const std::vector<uint8_t>& payload, size_t offset) {
    if (offset + 1 >= payload.size()) return 0;
    return static_cast<uint16_t>(payload[offset]) |
           (static_cast<uint16_t>(payload[offset + 1]) << 8);
}

uint32_t read_u32_le(const std::vector<uint8_t>& payload, size_t offset) {
    if (offset + 3 >= payload.size()) return 0;
    return static_cast<uint32_t>(payload[offset]) |
           (static_cast<uint32_t>(payload[offset + 1]) << 8) |
           (static_cast<uint32_t>(payload[offset + 2]) << 16) |
           (static_cast<uint32_t>(payload[offset + 3]) << 24);
}

bool parse_radar_wireless_frame(uint16_t command_id,
                                const std::vector<uint8_t>& payload,
                                RadarWirelessDataModel& model,
                                std::string* error) {
    switch (command_id) {
    case CMD_RADAR_WIRELESS_POSITION: {
        if (!require_size(payload, 24, error)) return false;
        auto& p = model.position;
        p.hero_x = read_u16_le(payload, 0);
        p.hero_y = read_u16_le(payload, 2);
        p.engineer_x = read_u16_le(payload, 4);
        p.engineer_y = read_u16_le(payload, 6);
        p.infantry3_x = read_u16_le(payload, 8);
        p.infantry3_y = read_u16_le(payload, 10);
        p.infantry4_x = read_u16_le(payload, 12);
        p.infantry4_y = read_u16_le(payload, 14);
        p.drone_x = read_u16_le(payload, 16);
        p.drone_y = read_u16_le(payload, 18);
        p.sentry_x = read_u16_le(payload, 20);
        p.sentry_y = read_u16_le(payload, 22);
        p.valid = true;
        break;
    }
    case CMD_RADAR_WIRELESS_HP: {
        if (!require_size(payload, 12, error)) return false;
        auto& hp = model.hp;
        hp.hero_hp = read_u16_le(payload, 0);
        hp.engineer_hp = read_u16_le(payload, 2);
        hp.infantry3_hp = read_u16_le(payload, 4);
        hp.infantry4_hp = read_u16_le(payload, 6);
        hp.reserved = read_u16_le(payload, 8);
        hp.sentry_hp = read_u16_le(payload, 10);
        hp.valid = true;
        break;
    }
    case CMD_RADAR_WIRELESS_PROJECTILE: {
        if (!require_size(payload, 10, error)) return false;
        auto& a = model.projectile;
        a.hero_projectile = read_u16_le(payload, 0);
        a.infantry3_projectile = read_u16_le(payload, 2);
        a.infantry4_projectile = read_u16_le(payload, 4);
        a.drone_projectile = read_u16_le(payload, 6);
        a.sentry_projectile = read_u16_le(payload, 8);
        a.valid = true;
        break;
    }
    case CMD_RADAR_WIRELESS_GOLD_OCCUPATION: {
        if (!require_size(payload, 8, error)) return false;
        auto& c = model.gold_occupation;
        c.remaining_gold = read_u16_le(payload, 0);
        c.total_gold = read_u16_le(payload, 2);
        c.occupation_raw = read_u32_le(payload, 4);
        c.valid = true;
        break;
    }
    case CMD_RADAR_WIRELESS_BUFF: {
        if (!require_size(payload, 36, error)) return false;
        auto& b = model.buff;
        b.hero_hp_recovery = payload[0];
        b.hero_cooling_rate = read_u16_le(payload, 1);
        b.hero_defense = payload[3];
        b.hero_negative_defense = payload[4];
        b.hero_attack = read_u16_le(payload, 5);

        b.engineer_hp_recovery = payload[7];
        b.engineer_cooling_rate = read_u16_le(payload, 8);
        b.engineer_defense = payload[10];
        b.engineer_negative_defense = payload[11];
        b.engineer_attack = read_u16_le(payload, 12);

        b.infantry3_hp_recovery = payload[14];
        b.infantry3_cooling_rate = read_u16_le(payload, 15);
        b.infantry3_defense = payload[17];
        b.infantry3_negative_defense = payload[18];
        b.infantry3_attack = read_u16_le(payload, 19);

        b.infantry4_hp_recovery = payload[21];
        b.infantry4_cooling_rate = read_u16_le(payload, 22);
        b.infantry4_defense = payload[24];
        b.infantry4_negative_defense = payload[25];
        b.infantry4_attack = read_u16_le(payload, 26);

        b.sentry_hp_recovery = payload[28];
        b.sentry_cooling_rate = read_u16_le(payload, 29);
        b.sentry_defense = payload[31];
        b.sentry_negative_defense = payload[32];
        b.sentry_attack = read_u16_le(payload, 33);
        b.sentry_posture = payload[35];
        b.valid = true;
        break;
    }
    case CMD_RADAR_WIRELESS_KEY: {
        if (!require_size(payload, 6, error)) return false;
        auto& k = model.key;
        for (size_t i = 0; i < k.ascii_code.size(); ++i) k.ascii_code[i] = payload[i];
        k.valid = true;
        break;
    }
    default:
        if (error) *error = "unsupported radar wireless command id: 0x" + std::to_string(command_id);
        return false;
    }

    model.last_command_id = command_id;
    model.last_update_sec = now_sec();
    return true;
}

}  // namespace sdr_receiver
