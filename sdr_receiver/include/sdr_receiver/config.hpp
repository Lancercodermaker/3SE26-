#pragma once
#include "sdr_receiver/types.hpp"
#include <map>
#include <string>
#include <vector>

namespace sdr_receiver::config {

constexpr double TX_SAMPLE_RATE = 1'000'000.0;
constexpr double TX_SPS = 52.0;
constexpr double SYMBOL_RATE = TX_SAMPLE_RATE / TX_SPS;
constexpr int SDR_FS = 2'500'000;
constexpr int SPS = 130;
constexpr int RX_BUFFER_SIZE = 160'000;
constexpr int RX_GAIN_MIN = 5;
constexpr int RX_GAIN_MAX = 73;
constexpr int GAIN_STEP_DB = 1;
constexpr int INFO_HEADER_MAX_ERRORS = 3;
constexpr int INFO_L3_HEADER_MAX_ERRORS = 1;
constexpr int JAM_HEADER_MAX_ERRORS = 1;
constexpr int INFO_RESCUE_AC_ERRORS = 3;
constexpr int INFO_L3_RESCUE_SEARCH_AC_ERRORS = 3;
constexpr int INFO_L3_RESCUE_ACCEPT_AC_ERRORS = 2;
constexpr int INFO_L3_RESCUE_RF_BW = 760'000;
constexpr int INFO_L3_RESCUE_GAIN = 24;
constexpr int INFO_L2_RESCUE_SEARCH_AC_ERRORS = 3;
constexpr int INFO_L2_RESCUE_ACCEPT_AC_ERRORS = 2;
constexpr int INFO_L2_HEADER_MAX_ERRORS = 2;
constexpr int INFO_L2_RESCUE_RF_BW = 660'000;
constexpr int INFO_L2_RESCUE_GAIN = 40;
constexpr double CAL_DWELL_SEC = 2.5;
constexpr int CAL_TOP_K = 6;
constexpr int CAL_VALIDATE_ROUNDS = 2;
constexpr double CAL_VALIDATE_DWELL_SEC = 1.5;
constexpr int POOL_MAX_BITS = 2160;
constexpr int POOL_KEEP_BITS = 1080;
constexpr int POOL_STALE_KEEP_BITS = 720;
constexpr int MAX_ACTIVE_POOLS = 14;
constexpr int RESCUE_PLAN_LIMIT = 48;
constexpr double CRC16_STALE_SEC = 0.75;

inline const std::string AIR_HEADER = "00000000000011110000000000001111";
inline const std::string AC_INFO = "0010111101101111010011000111010010111001000101000100100100101110";
inline const std::string AC_JAM = "0001011011101000110100110111011100010101000111000111000100101101";

inline const std::vector<int> INFO_L3_RESCUE_LO_OFFSETS = {80'000, 160'000, 120'000};
inline const std::vector<int> INFO_L2_RESCUE_LO_OFFSETS = {80'000, 200'000, 240'000, 160'000, 120'000};
inline const std::vector<int> CAL_QUICK_GAINS = {30, 36, 40};
inline const std::vector<int> CAL_FULL_GAINS = {24, 30, 36, 40, 44};
inline const std::vector<int> CAL_L2_QUICK_OFFSETS = {80'000, 200'000, 240'000, 160'000, 120'000};
inline const std::vector<int> CAL_L3_QUICK_OFFSETS = {80'000, 120'000, 160'000, 200'000, 240'000, 280'000};
inline const std::vector<int> CAL_L2_FULL_OFFSETS = {80'000, 200'000, 240'000, 160'000, 120'000, 280'000, 40'000};
inline const std::vector<int> CAL_L3_FULL_OFFSETS = {40'000, 80'000, 120'000, 160'000, 200'000, 240'000, 280'000};
inline const std::vector<double> THRESHOLD_K_VALUES = {0.0, 0.35, -0.35, 0.2, -0.2, 0.1, -0.1};
inline const std::vector<double> INFO_L3_THRESHOLD_K_VALUES = {0.35, 0.25, 0.45, 0.15, 0.55, 0.0, -0.1};

RadioParams base_radio_params(Team team, Target target);
FilterParams base_filter_params(Target target);
FilterParams info_l2_rescue_filter_params();
FilterParams info_l3_rescue_filter_params();
ReceiverState make_default_state();
DataModel make_default_data_model();

} // namespace sdr_receiver::config
