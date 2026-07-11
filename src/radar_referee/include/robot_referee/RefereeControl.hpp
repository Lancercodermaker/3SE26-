#ifndef REFEREECONTRPOL_H
#define REFEREECONTRPOL_H

#include <chrono>
#include <iostream>
#include <map>
#include <vector>
#include <string>

#include <opencv2/opencv.hpp>
#include "rclcpp/rclcpp.hpp"

#include "robot_referee/SendReceive.hpp"
#include "robot_referee/log_sys.h"
#include "sdr_receiver/msg/radar_wireless_frame.hpp"
#include "sdr_receiver/msg/jam_code.hpp"
#include "vision_interface/msg/detect_result.hpp"



struct Robot2Time
{
    std::chrono::steady_clock::time_point _time_l;
    std::chrono::steady_clock::time_point _time_w;
    std::chrono::steady_clock::time_point _time_m;
    uint8_t _robotId;
    cv::Point2f _location;
    bool _markProgress;
    bool _warning;
    int _guessTimes;
    int _locationIndex;
    uint16_t _hp;
    uint16_t _remaining_bullets;

    Robot2Time(uint8_t robotId)
    {
        _time_l = std::chrono::steady_clock::now();
        _time_w = std::chrono::steady_clock::now();
        _time_m = std::chrono::steady_clock::now();
        _robotId = robotId;
        _warning = false;
        _guessTimes = 0;
        _locationIndex = 0;
        _hp = 200;
        _remaining_bullets = 0;
    }

    Robot2Time() = default;
};

typedef enum
{
    Color_Init = -1,
    Color_Blue,
    Color_Green,
    Color_Red
} Robot_Color_t;

class RefereeControl : public rclcpp::Node
{
private:
    rclcpp::Subscription<vision_interface::msg::MatchInfo>::SharedPtr _matchInfo_Sub;
    rclcpp::Subscription<vision_interface::msg::Radar2Sentry>::SharedPtr _location_Sub;
    rclcpp::Subscription<sdr_receiver::msg::RadarWirelessFrame>::SharedPtr _wirelessFrame_Sub;
    rclcpp::Subscription<sdr_receiver::msg::JamCode>::SharedPtr _wirelessKey_Sub;
    rclcpp::Subscription<vision_interface::msg::DetectResult>::SharedPtr _detectResult_Sub;
    rclcpp::Publisher<vision_interface::msg::MatchInfo>::SharedPtr _matchInfo_Pub;

    std::vector<std::vector<uint8_t>> _frameBuffer;
    boost::asio::io_context _io;
    SerialPort _sp;

    std::vector<std::string> _warningPosition =
    {"飞坡", "环高", "打符", "吊射"};

    std::vector<std::vector<cv::Point2f>> _warningPolygon =
    {
        {
            cv::Point2f(12.02, 15),
            cv::Point2f(13.85, 15),
            cv::Point2f(13.85, 13.87),
            cv::Point2f(13.04, 12.76),
            cv::Point2f(11.99, 13.90)
        },
        {
            cv::Point2f(17.79, 7.73),
            cv::Point2f(19.4, 8.17),
            cv::Point2f(19.4, 5.7),
            cv::Point2f(17.79, 6.10)
        },
        {
            cv::Point2f(8.08, 2.30),
            cv::Point2f(9.22, 2.30),
            cv::Point2f(9.22, 1.30),
            cv::Point2f(8.08, 1.30)
        },
        {
            cv::Point2f(2.77, 13.87),
            cv::Point2f(2.80, 11.40),
            cv::Point2f(5.08, 11.40),
            cv::Point2f(6.88, 13.87)
        }
    };

    std::map<int8_t, Robot2Time> _robotList;

    std::vector<cv::Point2f> _sentryGuessPolygon =
    {
        cv::Point2f(5.45, 8.43),
        cv::Point2f(5.45, 6.22)
    };

    std::vector<std::string> _index2robot =
    {"", "英雄", "工程", "步兵3", "步兵4", "", "", "烧饼"};

    void robotLocationCallback(const vision_interface::msg::Radar2Sentry::SharedPtr msg);
    void wirelessFrameCallback(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg);
    void wirelessKeyCallback(const sdr_receiver::msg::JamCode::SharedPtr msg);
    void detectResultCallback(const vision_interface::msg::DetectResult::SharedPtr msg);
    void processWirelessPosition(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg);
    void processWirelessHp(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg);
    void processWirelessProjectile(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg);
    void processWirelessEvent(const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg);
    // void matchInfoCallback(const vision_interface::msg::MatchInfo::SharedPtr msg);
    void publishMatchInfo();

    uint8_t _self_ID;
    uint16_t _markProgress;
    bool _isVulnerable;                     // 对方现在是否处于易伤
    uint8_t _vulnerableOpp;                 // 易伤次数
    bool _vulMutex;                         // 易伤变量上锁
    bool _jamMutex;                         // 加密变量上锁
    int _vulTimes;                          // 已经发送易伤的次数
    int robot_ids[5] = {1, 2, 3, 4, 7};
    uint32_t _event;                                //解析波获得的增益事件
    uint8_t _jam_level;                      //己方加密等级（1-3）
    bool _key_mutable;                           //当前是否可以修改密钥
    bool _password_updated;                      //密码是否已更新
    std::chrono::milliseconds _timeThreshold;    // 超时时间
    int _guessThreshold;                    // 超时预测次数
    uint8_t _game_progress;                 // 当前比赛阶段
    uint16_t _stage_remain_time;            // 当前阶段剩余时间
    uint16_t _jam_time;                      // 加密时间
    uint8_t _radar_info_raw;                //0x020E接收的雷达信息原始数据
    radar_cmd_t radar_cmd;                  //雷达自主决策信息
    uint16_t remaining_gold;                // 剩余金币数
    bool outpost_alive;                            // 前哨战是否存活
    bool _init_locked;                            // 初始化锁，防止重复初始化
    void utfProcess(std::string utf8String);
    void sendVul();


    Logger _logger;

public:
    RefereeControl();
    ~RefereeControl();

    void refereeInit();
    void mapPolygonInit();
    bool getCommand();
    void executeCommand();
    void selectLocation();
    void sendWarnning();
    void vulProcess();
    void sendKey();
    void sendRobotInfo();
    void sendEventInfo();
    void sendOutpostAlive();
};

#endif // REFEREECONTRPOL_H