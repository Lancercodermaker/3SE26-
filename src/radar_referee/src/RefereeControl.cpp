#include "robot_referee/RefereeControl.hpp"
#include "rclcpp/rclcpp.hpp"

#include <thread>
#include <iomanip>
#include <sstream>
#include <ctime>
#include <locale>
#include <codecvt>
#include <future>
#include <type_traits>
#include <variant>

void RefereeControl::robotLocationCallback(const vision_interface::msg::Radar2Sentry::SharedPtr msg)
{
    auto current_time = std::chrono::steady_clock::now();
    int ids[5] = {1, 2, 3, 4, 7};

    for (int i = 0; i < 5; i++)
    {
        uint8_t robot_id = ids[i];
           // cout<<"robot----------------- "<<robot_id<<" location: "<<msg->radar_enemy_x[i]<<" "<<msg->radar_enemy_y[i]<<endl;
        if (msg->radar_enemy_x[i] != 0.0f || msg->radar_enemy_y[i] != 0.0f)
        {
            _robotList[robot_id]._time_l = current_time;
            _robotList[robot_id]._location = cv::Point2f(static_cast<float>(msg->radar_enemy_x[i] * 100.0f ), static_cast<float>(msg->radar_enemy_y[i] * 100.0f ));
            _robotList[robot_id]._guessTimes = 0;
           //cout<<"robot "<<robot_id<<" location: "<<_robotList[robot_id]._location.x<<" "<<_robotList[robot_id]._location.y<<endl;
        }
        if (msg->radar_ally_x[i] != 0.0f || msg->radar_ally_y[i] != 0.0f)
        {
            _robotList[-robot_id]._time_l = current_time;
            _robotList[-robot_id]._location = cv::Point2f(static_cast<float>(msg->radar_ally_x[i] * 100.0f ), static_cast<float>(msg->radar_ally_y[i] * 100.0f ));
            _robotList[-robot_id]._guessTimes = 0;
        }
    }
}

void RefereeControl::wirelessKeyCallback(const sdr_receiver::msg::JamCode::SharedPtr msg)
{
    RCLCPP_INFO(this->get_logger(), "Received JamCode - command_id: 0x%04X, valid: %d, level: %d, team: %s, target: %s", 
                msg->command_id, msg->valid, msg->level, msg->team.c_str(), msg->target.c_str());
    _logger.INFO("Received JamCode - command_id: 0x" + std::to_string(msg->command_id) + 
                 ", valid: " + std::to_string(msg->valid) + ", level: " + std::to_string(msg->level) +
                 ", team: " + msg->team + ", target: " + msg->target);

    if (msg->valid && msg->key.size() == 6)
    {
        std::string key_str;
        for (uint8_t byte : msg->key)
        {
            if (byte >= 32 && byte <= 126)
            {
                key_str += static_cast<char>(byte);
            }
            else
            {
                key_str += "?";
            }
        }
        RCLCPP_INFO(this->get_logger(), "ASCII Key: [%s]", key_str.c_str());
        _logger.INFO("ASCII Key: [" + key_str + "]");

        radar_cmd.password_1 = msg->key[0];
        radar_cmd.password_2 = msg->key[1];
        radar_cmd.password_3 = msg->key[2];
        radar_cmd.password_4 = msg->key[3];
        radar_cmd.password_5 = msg->key[4];
        radar_cmd.password_6 = msg->key[5];
        _password_updated = true;

        RCLCPP_INFO(this->get_logger(), "Stored password: %02X %02X %02X %02X %02X %02X",
                    radar_cmd.password_1, radar_cmd.password_2, radar_cmd.password_3,
                    radar_cmd.password_4, radar_cmd.password_5, radar_cmd.password_6);
        
        _logger.INFO("Stored password: " + std::to_string(radar_cmd.password_1) + " " +
                     std::to_string(radar_cmd.password_2) + " " +
                     std::to_string(radar_cmd.password_3) + " " +
                     std::to_string(radar_cmd.password_4) + " " +
                     std::to_string(radar_cmd.password_5) + " " +
                     std::to_string(radar_cmd.password_6));
        _logger.INFO("_password_updated: "+std::to_string(_password_updated));

        RCLCPP_INFO(this->get_logger(), "JamCode key mutable input: %d", msg->key_mutable);
        _logger.INFO("JamCode key mutable input: " + std::to_string(msg->key_mutable));
    }
    else
    {
        RCLCPP_WARN(this->get_logger(), "Invalid key data - valid: %d, size: %zu", 
                   msg->valid, msg->key.size());
        _logger.WARNING("Invalid key data - valid: " + std::to_string(msg->valid) + 
                       ", size: " + std::to_string(msg->key.size()));
    }
}

void RefereeControl::detectResultCallback(const vision_interface::msg::DetectResult::SharedPtr msg)
{
    outpost_alive = msg->outpost_alive;
    //std::cerr<<"outpost_alive: "<<outpost_alive<<std::endl;
    RCLCPP_INFO(this->get_logger(), "Received DetectResult - outpost_alive: %d", outpost_alive);

}

void RefereeControl::wirelessFrameCallback(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg)
{
    RCLCPP_INFO(this->get_logger(), "Received RadarWirelessFrame - cmd_id: 0x%04X, source_target: %s, team: %s", 
                msg->cmd_id, msg->source_target.c_str(), msg->team.c_str());
    _logger.INFO("Received RadarWirelessFrame - cmd_id: 0x" + std::to_string(msg->cmd_id) + 
                 ", source_target: " + msg->source_target + ", team: " + msg->team);

    if (!msg->crc8_ok || !msg->crc16_ok)
    {
        RCLCPP_WARN(this->get_logger(), "CRC check failed - crc8_ok: %d, crc16_ok: %d", 
                   msg->crc8_ok, msg->crc16_ok);
        _logger.WARNING("CRC check failed - crc8_ok: " + std::to_string(msg->crc8_ok) + 
                       ", crc16_ok: " + std::to_string(msg->crc16_ok));
        return;
    }

    RCLCPP_INFO(this->get_logger(), "CRC check passed");
    _logger.INFO("CRC check passed");

    if (msg->payload_raw.empty())
    {
        RCLCPP_WARN(this->get_logger(), "Payload is empty");
        _logger.WARNING("Payload is empty");
        return;
    }

    switch (msg->cmd_id)
    {
    case 0x0A01:
        processWirelessPosition(msg);
        break;
    case 0x0A02:
        processWirelessHp(msg);
        break;
    case 0x0A03:
        processWirelessProjectile(msg);
        break;
    case 0x0A04:
        processWirelessEvent(msg);
        break;
    default:
        RCLCPP_INFO(this->get_logger(), "Unknown cmd_id: 0x%04X", msg->cmd_id);
        _logger.INFO("Unknown cmd_id: 0x" + std::to_string(msg->cmd_id));
        break;
    }
}

void RefereeControl::processWirelessPosition(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg)
{
    if (msg->payload_raw.size() < 24)
    {
        RCLCPP_WARN(this->get_logger(), "Invalid payload size for position: %zu", msg->payload_raw.size());
        _logger.WARNING("Invalid payload size for position: " + std::to_string(msg->payload_raw.size()));
        return;
    }

    auto current_time = std::chrono::steady_clock::now();
    int opponent_ids[] = {1, 2, 3, 4, 6, 7};
    int payload_offset = 0;

    for (int i = 0; i < 6; i++)
    {
        
        int robot_id = opponent_ids[i];
        uint16_t x = static_cast<uint16_t>(msg->payload_raw[payload_offset]) | 
                     (static_cast<uint16_t>(msg->payload_raw[payload_offset + 1]) << 8);
        uint16_t y = static_cast<uint16_t>(msg->payload_raw[payload_offset + 2]) | 
                     (static_cast<uint16_t>(msg->payload_raw[payload_offset + 3]) << 8);
        payload_offset += 4;

        _robotList[robot_id]._location.x = static_cast<float>(x);
        _robotList[robot_id]._location.y = static_cast<float>(y);
        _robotList[robot_id]._time_l = current_time;
        _robotList[robot_id]._guessTimes = 0;

        RCLCPP_INFO(this->get_logger(), "Opponent %d position: (%.1f, %.1f)", robot_id, x, y);
        _logger.INFO("Opponent " + std::to_string(robot_id) + " position: (" + 
                     std::to_string(x) + ", " + std::to_string(y) + ")");
    }
}

void RefereeControl::processWirelessHp(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg)
{
    if (msg->payload_raw.size() < 12)
    {
        RCLCPP_WARN(this->get_logger(), "Invalid payload size for HP: %zu", msg->payload_raw.size());
        _logger.WARNING("Invalid payload size for HP: " + std::to_string(msg->payload_raw.size()));
        return;
    }

    int opponent_ids[] = {1, 2, 3, 4, 7};
    int payload_offset = 0;

    for (int i = 0; i < 5; i++)
    {
        int robot_id = opponent_ids[i];
        uint16_t hp = static_cast<uint16_t>(msg->payload_raw[payload_offset]) | 
                     (static_cast<uint16_t>(msg->payload_raw[payload_offset + 1]) << 8);
        payload_offset += 2;

        _robotList[robot_id]._hp = hp;

        RCLCPP_INFO(this->get_logger(), "Opponent %d HP: %d", robot_id, hp);
        _logger.INFO("Opponent " + std::to_string(robot_id) + " HP: " + std::to_string(hp));
    }

    uint16_t reserved = static_cast<uint16_t>(msg->payload_raw[payload_offset]) | 
                       (static_cast<uint16_t>(msg->payload_raw[payload_offset + 1]) << 8);
    payload_offset += 2;
    RCLCPP_INFO(this->get_logger(), "Reserved: %d", reserved);
}

void RefereeControl::processWirelessProjectile(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg)
{
    if (msg->payload_raw.size() < 10)
    {
        RCLCPP_WARN(this->get_logger(), "Invalid payload size for projectile: %zu", msg->payload_raw.size());
        _logger.WARNING("Invalid payload size for projectile: " + std::to_string(msg->payload_raw.size()));
        return;
    }

    int opponent_ids[] = {1, 3, 4, 6, 7};
    int payload_offset = 0;

    for (int i = 0; i < 5; i++)
    {
        int robot_id = opponent_ids[i];
        uint16_t projectile = static_cast<uint16_t>(msg->payload_raw[payload_offset]) | 
                            (static_cast<uint16_t>(msg->payload_raw[payload_offset + 1]) << 8);
        payload_offset += 2;

        _robotList[robot_id]._remaining_bullets = projectile;

        RCLCPP_INFO(this->get_logger(), "Opponent %d projectile: %d", robot_id, projectile);
        _logger.INFO("Opponent " + std::to_string(robot_id) + " projectile: " + std::to_string(projectile));
    }
}
void RefereeControl::processWirelessEvent(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg)
{
    if (msg->payload_raw.size() < 8)
    {
        RCLCPP_WARN(this->get_logger(), "Invalid payload size for gold: %zu", msg->payload_raw.size());
        _logger.WARNING("Invalid payload size for gold: " + std::to_string(msg->payload_raw.size()));
        return;
    }
 
    remaining_gold = static_cast<uint16_t>(msg->payload_raw[0]) | 
                             (static_cast<uint16_t>(msg->payload_raw[1]) << 8);
    uint16_t total_gold = static_cast<uint16_t>(msg->payload_raw[2]) | 
                         (static_cast<uint16_t>(msg->payload_raw[3]) << 8);
    uint32_t occupation_raw = static_cast<uint32_t>(msg->payload_raw[4]) | 
                              (static_cast<uint32_t>(msg->payload_raw[5]) << 8) |
                              (static_cast<uint32_t>(msg->payload_raw[6]) << 16) |
                              (static_cast<uint32_t>(msg->payload_raw[7]) << 24);

    
    _event = occupation_raw;
 
    RCLCPP_INFO(this->get_logger(), "Gold - remaining: %d, total: %d, occupation_raw: 0x%08X", 
                remaining_gold, total_gold, occupation_raw);
    _logger.INFO("Gold - remaining: " + std::to_string(remaining_gold) + 
                 ", total: " + std::to_string(total_gold) + 
                 ", occupation_raw: 0x" + std::to_string(occupation_raw));
}
 

RefereeControl::RefereeControl(): Node("referee_control_node"),_timeThreshold(1500),_guessThreshold(20), _sp(_io)
{
    std::time_t now = std::time(nullptr);
    std::ostringstream oss;
    oss << std::put_time(std::localtime(&now), "%Y-%m-%d_%H-%M-%S");
    std::string timeString = oss.str();
    _logger.init(Logger::file, Logger::debug, ("./log_referee/result_" + timeString + ".log"));
    _radarContextPub = this->create_publisher<sdr_receiver::msg::RadarContext>(
        "/judge/radar_context", rclcpp::QoS(10).reliable());
    
   
}

RefereeControl::~RefereeControl() {}

void RefereeControl::refereeInit()
{
    _location_Sub = this->create_subscription<vision_interface::msg::Radar2Sentry>("/Radar2Sentry",10,
        std::bind(&RefereeControl::robotLocationCallback, this, std::placeholders::_1));

    _wirelessFrame_Sub = this->create_subscription<sdr_receiver::msg::RadarWirelessFrame>("/sdr/radar_wireless/raw_frame",10,
        std::bind(&RefereeControl::wirelessFrameCallback, this, std::placeholders::_1));

    _wirelessKey_Sub = this->create_subscription<sdr_receiver::msg::JamCode>("/sdr/jam_code",10,
        std::bind(&RefereeControl::wirelessKeyCallback, this, std::placeholders::_1));

    _detectResult_Sub = this->create_subscription<vision_interface::msg::DetectResult>("/detect_result",rclcpp::SensorDataQoS(),
        std::bind(&RefereeControl::detectResultCallback, this, std::placeholders::_1));

    // _matchInfo_Sub = this->create_subscription<vision_interface::msg::MatchInfo>("/match_info",10,
    //     std::bind(&RefereeControl::matchInfoCallback, this, std::placeholders::_1));

    _matchInfo_Pub = this->create_publisher<vision_interface::msg::MatchInfo>("/match_info",10);

    for (int i = 0; i < 6; i++)
    {
        Robot2Time robot(robot_ids[i]);
        _robotList[robot_ids[i]] = robot;
        Robot2Time ally_robot(-robot_ids[i]);
        _robotList[-robot_ids[i]] = ally_robot;
    }

    _self_ID = 109;
    
    _isVulnerable = false;
    _vulMutex = false;
    _jamMutex = false;
    _vulnerableOpp = 0;
    _vulTimes = 0;
    _game_progress = 0;
    _stage_remain_time = 0;
    _markProgress = 0;
    _event = 0x00;
    _jam_level = 1;
    _jam_time = 430;
    _key_mutable = true;
    _password_updated = false;
    outpost_alive = true;
    _init_locked = false;
    std::cout << "refereeInit" << std::endl;
    _logger.INFO("refereeInit");
    while (!setupSerialPort(this->_sp)) {}
}

void RefereeControl::mapPolygonInit()
{
    if (_self_ID < 100)
    {
        for (auto &position : _warningPolygon)
        {
            for (auto &point : position)
            {
                point.x = 28 - point.x;
                point.y = 15 - point.y;
            }
        }

        for (auto &point : _sentryGuessPolygon)
        {
            point.x = 28 - point.x;
            point.y = 15 - point.y;
        }
    }
}

bool RefereeControl::getCommand()
{
    this->_frameBuffer.clear();
    this->_frameBuffer = syncToFrameStart(this->_sp);
    return !this->_frameBuffer.empty();
}

void RefereeControl::executeCommand()
{
    for (auto &command : _frameBuffer)
    {
        CmdData result = cmdProcess(command);

        std::visit([this](auto &&arg) {
            using T = std::decay_t<decltype(arg)>;

            if constexpr (std::is_same_v<T, game_state_t>) {
                RCLCPP_INFO(this->get_logger(), "received %d", GAME_STATE_ID);
                _logger.INFO("received" + std::to_string(GAME_STATE_ID));

                game_state_t game_state = static_cast<game_state_t>(arg);
                _game_progress = game_state.game_progress;
                _stage_remain_time = game_state.stage_remain_time;

                RCLCPP_ERROR(this->get_logger(), "_game_progress: %d", _game_progress);
                RCLCPP_ERROR(this->get_logger(), "_stage_remain_time: %d", _stage_remain_time);

                // if (_game_progress == 0 && !_init_locked)
                // {
                //     RCLCPP_WARN(this->get_logger(), "Game state = 0, calling refereeInit and locking");
                //     _logger.WARNING("Game state = 0, calling refereeInit and locking");
                //     refereeInit();
                //     _init_locked = true;
                // }
                // else if (_game_progress == 4 && _init_locked)
                // {
                //     RCLCPP_INFO(this->get_logger(), "Game state = 4, unlocking");
                //     _logger.INFO("Game state = 4, unlocking");
                //     _init_locked = false;
                // }
                
            } else if constexpr (std::is_same_v<T, robot_status_t>) {
                RCLCPP_INFO(this->get_logger(), "received %d", ROBOT_STATUS_ID);
                _logger.INFO("received" + std::to_string(ROBOT_STATUS_ID));

                robot_status_t robot_status = static_cast<robot_status_t>(arg);

                //if (_self_ID != 100) return;

                _self_ID = static_cast<uint8_t>(robot_status.robot_id);
                _logger.INFO("self_ID:" + std::to_string(_self_ID));

            } else if constexpr (std::is_same_v<T, radar_mark_data_t>) {
                RCLCPP_INFO(this->get_logger(), "received %d", RADAR_MARK_DATA_ID);
                _logger.INFO("received" + std::to_string(RADAR_MARK_DATA_ID));

                radar_mark_data_t radar_mark_data = static_cast<radar_mark_data_t>(arg);
                _markProgress = radar_mark_data.mark_progress;
                for(int i = 1; i <= 7; i++)
                {
                    if(i == 5) continue;
                    _robotList[i]._time_w = std::chrono::steady_clock::now();
                    if(i == 1) _robotList[i]._markProgress = _markProgress & 0x01;
                    if(i == 2) _robotList[i]._markProgress = (_markProgress >> 1) & 0x01;
                    if(i == 3) _robotList[i]._markProgress = (_markProgress >> 2) & 0x01;
                    if(i == 4) _robotList[i]._markProgress = (_markProgress >> 3) & 0x01;
                    if(i == 6) _robotList[i]._markProgress = (_markProgress >> 4) & 0x01;
                    if(i == 7) _robotList[i]._markProgress = (_markProgress >> 5) & 0x01;
                    RCLCPP_INFO(this->get_logger(), "%d mark_progress: %d",i, _robotList[i]._markProgress);
                    _logger.INFO(std::to_string(i) + " mark_progress " + std::to_string(_robotList[i]._markProgress));
                }
                for(int i = 1; i <= 7; i++)
                {
                    if(i == 5) continue;
                    _robotList[-i]._time_w = std::chrono::steady_clock::now();
                    if(i == 1) _robotList[-i]._markProgress = (_markProgress >> 6) & 0x01;
                    if(i == 2) _robotList[-i]._markProgress = (_markProgress >> 7) & 0x01;
                    if(i == 3) _robotList[-i]._markProgress = (_markProgress >> 8) & 0x01;
                    if(i == 4) _robotList[-i]._markProgress = (_markProgress >> 9) & 0x01;
                    if(i == 6) _robotList[-i]._markProgress = (_markProgress >> 10) & 0x01;
                    if(i == 7) _robotList[-i]._markProgress = (_markProgress >> 11) & 0x01;
                    RCLCPP_INFO(this->get_logger(), "%d mark_progress: %d", -i, _robotList[-i]._markProgress);
                    _logger.INFO(std::to_string(-i) + " mark_progress " + std::to_string(_robotList[-i]._markProgress));
                } 
                // ----------------end of action-----------------

            } else if constexpr (std::is_same_v<T, radar_info_t>) {
                RCLCPP_INFO(this->get_logger(), "received %d", RADAR_INFO_ID);
                _logger.INFO("received" + std::to_string(RADAR_INFO_ID));

                radar_info_t radar_info_data = static_cast<radar_info_t>(arg);
                _radar_info_raw = radar_info_data.radar_info;
                _vulnerableOpp = radar_info_data.radar_info & 0x03;
                _jam_level = (radar_info_data.radar_info >> 3) & 0x03;
                _key_mutable = (radar_info_data.radar_info & 0x20) == 0x20;
                publishRadarContext();
                
                _logger.INFO("vulopp: " + std::to_string(_vulnerableOpp));
                _logger.INFO("jam_level: " + std::to_string(_jam_level));
                _logger.INFO("can_modify_key: " + std::to_string(_key_mutable));



                if (!_isVulnerable && (radar_info_data.radar_info & 0x04) == 0x04)
                {
                    _isVulnerable = true;
                    _vulTimes += 1;
                    _logger.WARNING("vul act successfully-----------------------------");
                    RCLCPP_ERROR(this->get_logger(), "vul act successfully--------------------------------");
                }
                else if (_isVulnerable && (radar_info_data.radar_info & 0x04) == 0)
                {
                    _logger.WARNING("vul else if-----------------------------");
                    _isVulnerable = false;
                    _vulMutex = false;
                }
                
                publishMatchInfo();

            } else if constexpr (std::is_same_v<T, std::string>) {
                // error string
            }
        }, result);
    }
}

void RefereeControl::selectLocation()
{
    static int guessIndex = 0;
    map_robot_data_t map_robot_data{};
    frame_t frame{};

for(int index = 1; index <= 7; index++)
    {
        if(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -  _robotList[index]._time_l) > _timeThreshold)
        {  // 超时处理
            // if(index == 7)
            // {
            //     // 猜烧饼
            //     map_robot_data.opponent_sentry_position_x = _sentryGuessPolygon[guessIndex].x ;
            //     map_robot_data.opponent_sentry_position_y = _sentryGuessPolygon[guessIndex].y ;
            //     _robotList[index]._guessTimes++;
            //     if( _robotList[index]._guessTimes >= _guessThreshold) // 一定次数后没标上就换点
            //     {
            //         guessIndex = (guessIndex + 1) % _sentryGuessPolygon.size();
            //         _robotList[index]._guessTimes = 0;
            //     }
            //     if(_robotList[index]._markProgress > 0)
            //     {
            //         _robotList[index]._guessTimes = 0;
            //     }
            // }
            RCLCPP_WARN(this->get_logger(), "%d not find ", index);
            continue;
        }
        _logger.INFO("send_location: " + std::to_string(index) + ": " + "( " + std::to_string(_robotList[index]._location.x) + ", " + std::to_string(_robotList[index]._location.y) + " )" );
        
        switch (index)
        {
        case 1:
            map_robot_data.opponent_hero_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_hero_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        case 2:
            map_robot_data.opponent_engineer_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_engineer_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        case 3:
            map_robot_data.opponent_infantry_3_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_infantry_3_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        case 4:
            map_robot_data.opponent_infantry_4_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_infantry_4_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        case 6:
            map_robot_data.opponent_aerial_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_aerial_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        case 7:
            map_robot_data.opponent_sentry_position_x = static_cast<uint16_t>(_robotList[index]._location.x);
            map_robot_data.opponent_sentry_position_y = static_cast<uint16_t>(_robotList[index]._location.y);
            break;
        default:
            break;
        }

        RCLCPP_INFO(this->get_logger(), "%d find ", index);
    }
    for(int index = 1; index <= 7; index++)
    {
        if(index == 5) continue;
        int ally_index = -index;
        
        if(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -  _robotList[ally_index]._time_l) > _timeThreshold)
        {
            RCLCPP_WARN(this->get_logger(), "%d not find ", ally_index);
            continue;
        }
        _logger.INFO("send_location: " + std::to_string(ally_index) + ": " + "( " + std::to_string(_robotList[ally_index]._location.x) + ", " + std::to_string(_robotList[ally_index]._location.y) + " )" );
        
        switch (index)
        {
        case 1:
            map_robot_data.ally_hero_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_hero_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        case 2:
            map_robot_data.ally_engineer_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_engineer_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        case 3:
            map_robot_data.ally_infantry_3_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_infantry_3_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        case 4:
            map_robot_data.ally_infantry_4_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_infantry_4_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        case 6:
            map_robot_data.ally_aerial_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_aerial_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        case 7:
            map_robot_data.ally_sentry_position_x = static_cast<uint16_t>(_robotList[ally_index]._location.x);
            map_robot_data.ally_sentry_position_y = static_cast<uint16_t>(_robotList[ally_index]._location.y);
            break;
        default:
            break;
        }
 
        RCLCPP_INFO(this->get_logger(), "%d find ", ally_index);
    }

    frameInit(frame, MAP_ROBOT_DATA_SIZE, MAP_ROBOT_DATA_ID);
    sendFrame<map_robot_data_t>(this->_sp, frame, &map_robot_data, MAP_ROBOT_DATA_SIZE);
}

void RefereeControl::sendWarnning()
{
    for (int i = 1; i <= 7; i++)
    {
        if (i == 6) continue;

        for (size_t j = 0; j < _warningPolygon.size(); j++)
        {
            if (cv::pointPolygonTest(_warningPolygon[j], _robotList[i]._location, false) == 1 &&
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - _robotList[i]._time_l) < _timeThreshold)
            {
                RCLCPP_ERROR(this->get_logger(), "Ployg_time%d", i);
                if (_robotList[i]._warning == false)
                {
                    _robotList[i]._locationIndex = static_cast<int>(j);
                    _robotList[i]._warning = true;

                    std::time_t now = std::time(nullptr);
                    std::ostringstream oss;
                    oss << std::put_time(std::localtime(&now), "%T");
                    std::string timeString = oss.str();

                    std::string utf8String = timeString + u8": " + _index2robot[i] + u8"在" + _warningPosition[j];
                    utfProcess(utf8String);
                }

                _robotList[i]._time_w = std::chrono::steady_clock::now();
            }
            else
            {
                if (_robotList[i]._warning == true && static_cast<int>(j) == _robotList[i]._locationIndex)
                {
                    _robotList[i]._warning = false;
                    RCLCPP_ERROR(this->get_logger(), "id%d", i);
                    std::time_t now = std::time(nullptr);
                    std::ostringstream oss;
                    oss << std::put_time(std::localtime(&now), "%T");
                    std::string timeString = oss.str();

                    std::string utf8String = timeString + u8": " + _index2robot[i] + u8"离" + _warningPosition[j];
                    utfProcess(utf8String);
                }
            }
        }
    }
}

void RefereeControl::utfProcess(std::string utf8String)
{
    frame_t frame{};
    custom_info_t custom_info{};

    frameInit(frame, CUSTOM_INFO_SIZE, CUSTOM_INFO_ID);

    custom_info.sender_id = _self_ID;
    custom_info.receiver_id = _self_ID < 100 ? 0x0106 : 0x016A;

    std::wstring_convert<std::codecvt_utf8_utf16<char16_t>, char16_t> convert;
    std::u16string utf16String = convert.from_bytes(utf8String);

    std::vector<uint8_t> buffer(30, 0);
    auto buf_it = buffer.begin();

    for (auto c : utf16String) 
    {
        // 将每个16位数分成两个字节，并以小端格式存储
        *buf_it++ = static_cast<uint8_t>(c & 0xFF);
        *buf_it++ = static_cast<uint8_t>((c >> 8) & 0xFF);
    }

    for (int i = 0; i < 30; i++)
    {
        custom_info.user_data[i] = buffer[i];
    }

    sendFrame<custom_info_t>(this->_sp, frame, &custom_info, CUSTOM_INFO_SIZE);
    std::this_thread::sleep_for(std::chrono::milliseconds(334));
}

void RefereeControl::vulProcess()
{
    if (_game_progress == 4)
    {
        RCLCPP_INFO(this->get_logger(), "game_start");
        _logger.INFO("game_start");
    }
    else
    {
        RCLCPP_INFO(this->get_logger(), "game state %d", _game_progress);
        _logger.INFO("vul wait.......");
        _logger.INFO("game state: " + std::to_string(_game_progress));
        return;
    }

    if (_stage_remain_time < 361 && _vulTimes == 0)
    {
        RCLCPP_ERROR(this->get_logger(), "first time vul");
        _logger.INFO("first time vul");
        sendVul();
    }
    else if (_stage_remain_time < 361 && _vulTimes == 1)
    {
        _vulMutex = false;

        RCLCPP_ERROR(this->get_logger(), "second time vul");
        _logger.INFO("second time vul");
        sendVul();
    }
}

void RefereeControl::sendVul()
{
    robot_interaction_data_t robot_interaction_data{};
    robot_interaction_data.data_cmd_id = 0x0121;
    robot_interaction_data.receiver_id = 0x8080;

    if (_vulnerableOpp > 0 && !_isVulnerable && !_vulMutex)
    {
        _logger.WARNING("vul opp " + std::to_string(_vulnerableOpp));
        robot_interaction_data.sender_id = _self_ID;
        robot_interaction_data.user_data[0] = ++radar_cmd.radar_cmd;
        _logger.WARNING("radar_cmd.radar_cmd: " + std::to_string(radar_cmd.radar_cmd));
        _vulMutex = true;
    }
    else if (_vulnerableOpp > 0 && !_isVulnerable && _vulMutex)
    {
        _logger.WARNING("already send vul but no response");
        robot_interaction_data.sender_id = _self_ID;
        robot_interaction_data.user_data[0] = radar_cmd.radar_cmd;
        _logger.WARNING("radar_cmd.radar_cmd: " + std::to_string(radar_cmd.radar_cmd));
    }
    else
    {
        _logger.WARNING("vul else else");
        return;
    }

    _logger.WARNING("vul has send");
    frame_t frame{};
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 1, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data, ROBOT_INTERACTION_DATA_SIZE + 1);
}



void RefereeControl::sendKey()
{
    robot_interaction_data_t robot_interaction_data{};
    robot_interaction_data.data_cmd_id = 0x0121;
    robot_interaction_data.receiver_id = 0x8080;

    if (_game_progress == 4 && !_jamMutex)
    {
        _logger.WARNING("key phase 1 start");
        radar_cmd.password_cmd = 2;
        robot_interaction_data.sender_id = _self_ID;
        robot_interaction_data.user_data[0] = radar_cmd.radar_cmd;
        robot_interaction_data.user_data[1] = radar_cmd.password_cmd;
        _jamMutex = true;
    }
    //else if (_jam_time - _stage_remain_time > 10 && _password_updated)
    else if (_password_updated)
    {
        _logger.WARNING("key phase 2 start");
        radar_cmd.password_cmd = 3;
        robot_interaction_data.sender_id = _self_ID;
        robot_interaction_data.user_data[0] = radar_cmd.radar_cmd;
        robot_interaction_data.user_data[1] = radar_cmd.password_cmd;
        _jamMutex = false;
        _jam_time = _stage_remain_time;

        robot_interaction_data.user_data[2] = radar_cmd.password_1;
        robot_interaction_data.user_data[3] = radar_cmd.password_2;
        robot_interaction_data.user_data[4] = radar_cmd.password_3;
        robot_interaction_data.user_data[5] = radar_cmd.password_4;
        robot_interaction_data.user_data[6] = radar_cmd.password_5;
        robot_interaction_data.user_data[7] = radar_cmd.password_6;
        _password_updated = false;
        _logger.WARNING("key has send");
    }
    else if (_jamMutex)
    {
        _logger.WARNING("already send key phase 1 but no response ,maybe wait 10s");
        robot_interaction_data.sender_id = _self_ID;
        robot_interaction_data.user_data[0] = radar_cmd.radar_cmd;
        robot_interaction_data.user_data[1] = radar_cmd.password_cmd;
    }
    else
    {
        _logger.WARNING("key else else");
        return;
    }

    frame_t frame{};
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 8, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data, ROBOT_INTERACTION_DATA_SIZE + 8);
}

void RefereeControl::sendRobotInfo()
{
    robot_interaction_data_t robot_interaction_data2sentry;
    robot_interaction_data_t robot_interaction_data2infantry3;
    robot_interaction_data_t robot_interaction_data2infantry4;
    robot_interaction_data_t robot_interaction_data2flyer;
    robot_interaction_data2sentry.data_cmd_id = 0x02B0;
    robot_interaction_data2sentry.sender_id = _self_ID;
    robot_interaction_data2sentry.receiver_id = _self_ID - 2;

    int data_offset = 0;
    
    for (int i = 0; i < 5; i++)
    {
        int robot_id = robot_ids[i];
        uint16_t position_x = static_cast<uint16_t>(_robotList[robot_id]._location.x);
        uint16_t position_y = static_cast<uint16_t>(_robotList[robot_id]._location.y);
        // uint16_t position_x = static_cast<uint16_t>(2500.0f);
        // uint16_t position_y = static_cast<uint16_t>(500.0f);
        uint16_t hp = _robotList[robot_id]._hp;
        uint16_t remaining_bullets = _robotList[robot_id]._remaining_bullets;
        //cout<<position_x<<" "<<position_y<<endl;
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>(position_x & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>((position_x >> 8) & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>(position_y & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>((position_y >> 8) & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>(hp & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>((hp >> 8) & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>(remaining_bullets & 0xFF);
        robot_interaction_data2sentry.user_data[data_offset++] = static_cast<uint8_t>((remaining_bullets >> 8) & 0xFF);
    }
    robot_interaction_data2infantry3 = robot_interaction_data2sentry;
    robot_interaction_data2infantry4 = robot_interaction_data2sentry;
    robot_interaction_data2flyer = robot_interaction_data2sentry;
    robot_interaction_data2infantry3.data_cmd_id = 0x02B1;
    robot_interaction_data2infantry4.data_cmd_id = 0x02B2;
    robot_interaction_data2flyer.data_cmd_id = 0x02B3;
    robot_interaction_data2infantry3.receiver_id = _self_ID - 6;
    robot_interaction_data2infantry4.receiver_id = _self_ID - 5;
    robot_interaction_data2flyer.receiver_id = _self_ID - 3;
    
    frame_t frame{};
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 40, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data2sentry, ROBOT_INTERACTION_DATA_SIZE + 40);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 40, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data2infantry3, ROBOT_INTERACTION_DATA_SIZE + 40);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 40, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data2infantry4, ROBOT_INTERACTION_DATA_SIZE + 40);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 40, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &robot_interaction_data2flyer, ROBOT_INTERACTION_DATA_SIZE + 40);

    _logger.INFO("robot_info sent to sentry: " + std::to_string(_self_ID - 2));
    //RCLCPP_INFO(this->get_logger(), "Robot info sent to sentry: %d", _self_ID - 2);
}
void RefereeControl::sendEventInfo()
{
    robot_interaction_data_t event_info2sentry;
    robot_interaction_data_t event_info2infantry3;
    robot_interaction_data_t event_info2infantry4;
    robot_interaction_data_t event_info2flyer;
    event_info2sentry.data_cmd_id = 0x02A0;
    event_info2sentry.sender_id = _self_ID;
    event_info2sentry.receiver_id = _self_ID - 2;
    for(int i = 0; i < 4; i++)
    {
        event_info2sentry.user_data[i] = _event >> (8 * i) & 0xFF;
    }
    event_info2sentry.user_data[4] = remaining_gold & 0xFF;
    event_info2sentry.user_data[5] = (remaining_gold >> 8) & 0xFF;

    event_info2infantry3 = event_info2sentry;
    event_info2infantry4 = event_info2sentry;
    event_info2flyer = event_info2sentry;
    event_info2infantry3.data_cmd_id = 0x02A1;
    event_info2infantry4.data_cmd_id = 0x02A2;
    event_info2flyer.data_cmd_id = 0x02A3;
    event_info2infantry3.receiver_id = _self_ID - 6;
    event_info2infantry4.receiver_id = _self_ID - 5;
    event_info2flyer.receiver_id = _self_ID - 3;
    
    frame_t frame{};
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 6, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &event_info2sentry, ROBOT_INTERACTION_DATA_SIZE + 6);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 6, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &event_info2infantry3, ROBOT_INTERACTION_DATA_SIZE + 6);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 6, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &event_info2infantry4, ROBOT_INTERACTION_DATA_SIZE + 6);
    
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 6, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &event_info2flyer, ROBOT_INTERACTION_DATA_SIZE + 6);

    _logger.INFO("event info sent to shaobing");
    //RCLCPP_INFO(this->get_logger(), "Event info sent to shaobing");
}

void RefereeControl::sendOutpostAlive()
{
    robot_interaction_data_t outpost_info;
    outpost_info.data_cmd_id = 0x02C0;
    outpost_info.sender_id = _self_ID;
    outpost_info.receiver_id = _self_ID - 2;
    outpost_info.user_data[0] = outpost_alive ? 1 : 0;

    frame_t frame{};
    frameInit(frame, ROBOT_INTERACTION_DATA_SIZE + 1, ROBOT_INTERACTION_DATA_ID);
    sendFrame<robot_interaction_data_t>(this->_sp, frame, &outpost_info, ROBOT_INTERACTION_DATA_SIZE + 1);

    _logger.INFO("outpost alive status sent to sentry: " + std::to_string(outpost_alive));
    //RCLCPP_INFO(this->get_logger(), "Outpost alive status sent to sentry: %d", outpost_alive);
}

void RefereeControl::publishRadarContext()
{
    sdr_receiver::msg::RadarContext msg;
    msg.header.stamp = this->get_clock()->now();
    msg.self_id = _self_ID;
    msg.self_color = _self_ID == 9 ? 2 : (_self_ID == 109 ? 0 : -1);
    msg.radar_info_raw = _radar_info_raw;
    msg.jam_level = _jam_level;
    msg.key_mutable = _key_mutable;
    msg.game_progress = _game_progress;
    msg.match_time = _game_progress == 4 ? static_cast<int16_t>(_stage_remain_time) : -200;
    msg.referee_online = _self_ID == 9 || _self_ID == 109;
    _radarContextPub->publish(msg);
}

void RefereeControl::publishMatchInfo()
{
    vision_interface::msg::MatchInfo match_info_msg;
    
    match_info_msg.self_color = _self_ID >= 100 ? 0 : 2;   //红方为9，蓝方为109
    match_info_msg.match_time = static_cast<int16_t>(_stage_remain_time);
    match_info_msg.self_id = _self_ID;
    match_info_msg.jam_level = _jam_level;
    match_info_msg.key_mutable = _key_mutable;
    match_info_msg.radar_info_raw = _radar_info_raw;
    if (_game_progress == 4)
    {
        match_info_msg.match_time = static_cast<int16_t>(_stage_remain_time);
    }
    else if (_game_progress == 3)
    {
        match_info_msg.match_time = -static_cast<int16_t>(5 - (_stage_remain_time / 60));
    }
    else
    {
        match_info_msg.match_time = -200;
    }
    
    for (int i = 0; i < 5; i++)
    {
        match_info_msg.marks[i] = (_markProgress >> i) & 0x01;
    }
    
    match_info_msg.ultimate = 0;
    match_info_msg.eventtype = 0;
    
    for (int i = 0; i < 16; i++)
    {
        match_info_msg.robot_hp[i] = 0;
    }
    
    if (match_info_msg.match_time == -200) match_info_msg.referee_online = false;
    else match_info_msg.referee_online = true;


    _matchInfo_Pub->publish(match_info_msg);
    
    RCLCPP_INFO(this->get_logger(), "Published MatchInfo - Color: %d, Time: %d", 
                match_info_msg.self_color, match_info_msg.match_time);
}