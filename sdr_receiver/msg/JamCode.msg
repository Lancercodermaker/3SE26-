# Jam code/key extracted from Radar Wireless Link command 0x0A06.
# This topic is intended for judge_bridge, which can forward the key via radar_cmd_t.
std_msgs/Header header
bool valid
uint16 command_id

# Current receiver context. team is the decoded RF/opponent team, not our own team.
uint8 level
string team
string target
string radio_mode
string rf_state
uint8 radar_info_raw
bool key_mutable

# 6 ASCII-encoded bytes from payload[0..5]
uint8[6] key
string ascii_code
