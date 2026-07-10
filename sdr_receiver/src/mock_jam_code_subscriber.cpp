#include <rclcpp/rclcpp.hpp>
#include <sdr_receiver/msg/jam_code.hpp>

#include <iomanip>
#include <memory>
#include <sstream>
#include <string>

class MockJamCodeSubscriber final : public rclcpp::Node {
public:
    MockJamCodeSubscriber() : Node("mock_jam_code_subscriber") {
        const std::string topic = declare_parameter<std::string>("topic", "/sdr/jam_code");
        sub_ = create_subscription<sdr_receiver::msg::JamCode>(
            topic,
            10,
            [this](const sdr_receiver::msg::JamCode::SharedPtr msg) {
                std::ostringstream hex;
                hex << std::hex << std::setfill('0');
                for (uint8_t b : msg->key) hex << std::setw(2) << static_cast<int>(b);
                RCLCPP_INFO(get_logger(),
                            "jam_code valid=%s level=%u team=%s target=%s ascii=%s key_hex=%s radar_info=0x%02x mutable=%s",
                            msg->valid ? "true" : "false",
                            static_cast<unsigned>(msg->level),
                            msg->team.c_str(),
                            msg->target.c_str(),
                            msg->ascii_code.c_str(),
                            hex.str().c_str(),
                            static_cast<unsigned>(msg->radar_info_raw),
                            msg->key_mutable ? "true" : "false");
            });
        RCLCPP_INFO(get_logger(), "listening for JamCode on %s", topic.c_str());
    }

private:
    rclcpp::Subscription<sdr_receiver::msg::JamCode>::SharedPtr sub_;
};

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<MockJamCodeSubscriber>());
    rclcpp::shutdown();
    return 0;
}
