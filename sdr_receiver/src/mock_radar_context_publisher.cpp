#include <rclcpp/rclcpp.hpp>
#include <sdr_receiver/msg/jam_code.hpp>
#include <sdr_receiver/msg/radar_context.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>

class MockRadarContextPublisher final : public rclcpp::Node {
public:
    MockRadarContextPublisher() : Node("mock_radar_context_publisher") {
        topic_ = declare_parameter<std::string>("topic", "/judge/radar_context");
        jam_code_topic_ = declare_parameter<std::string>("jam_code_topic", "/sdr/jam_code");
        self_id_ = static_cast<uint8_t>(declare_parameter<int>("self_id", 9));
        const int start_level = static_cast<int>(declare_parameter<int>("start_level", 1));
        const int max_level = static_cast<int>(declare_parameter<int>("max_level", 3));
        level_ = std::clamp(start_level, 0, 3);
        max_level_ = std::clamp(max_level, 1, 3);
        key_mutable_ = declare_parameter<bool>("key_mutable", true);
        referee_online_ = declare_parameter<bool>("referee_online", true);
        advance_on_jam_code_ = declare_parameter<bool>("advance_on_jam_code", true);
        const int period_param_ms = static_cast<int>(declare_parameter<int>("period_ms", 200));
        const int period_ms = std::max(20, period_param_ms);

        pub_ = create_publisher<sdr_receiver::msg::RadarContext>(topic_, rclcpp::QoS(rclcpp::KeepLast(5)).reliable());
        sub_ = create_subscription<sdr_receiver::msg::JamCode>(
            jam_code_topic_,
            10,
            [this](const sdr_receiver::msg::JamCode::SharedPtr msg) {
                if (!advance_on_jam_code_ || !msg->valid) return;
                if (msg->level == level_ && level_ < max_level_) {
                    ++level_;
                    RCLCPP_INFO(get_logger(), "received L%d key, advancing mock context to L%d", msg->level, level_);
                } else if (msg->level >= max_level_) {
                    RCLCPP_INFO(get_logger(), "received final L%d key; holding mock context at max level", msg->level);
                }
            });

        timer_ = create_wall_timer(std::chrono::milliseconds(period_ms), [this]() { publish_context(); });
        RCLCPP_INFO(get_logger(), "publishing mock RadarContext on %s, self_id=%u, start_level=%d",
                    topic_.c_str(), static_cast<unsigned>(self_id_), level_);
    }

private:
    void publish_context() {
        sdr_receiver::msg::RadarContext msg;
        msg.header.stamp = now();
        msg.header.frame_id = "mock_referee";
        msg.self_id = self_id_;
        msg.self_color = self_id_ >= 100 ? 0 : 2;
        msg.radar_info_raw = static_cast<uint8_t>((level_ & 0x03) << 3);
        if (key_mutable_) msg.radar_info_raw |= static_cast<uint8_t>(1u << 5);
        msg.jam_level = static_cast<uint8_t>(level_);
        msg.key_mutable = key_mutable_;
        msg.game_progress = 4;
        msg.match_time = 300;
        msg.referee_online = referee_online_;
        pub_->publish(msg);
    }

    rclcpp::Publisher<sdr_receiver::msg::RadarContext>::SharedPtr pub_;
    rclcpp::Subscription<sdr_receiver::msg::JamCode>::SharedPtr sub_;
    rclcpp::TimerBase::SharedPtr timer_;
    std::string topic_;
    std::string jam_code_topic_;
    uint8_t self_id_ = 9;
    int level_ = 1;
    int max_level_ = 3;
    bool key_mutable_ = true;
    bool referee_online_ = true;
    bool advance_on_jam_code_ = true;
};

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<MockRadarContextPublisher>());
    rclcpp::shutdown();
    return 0;
}
