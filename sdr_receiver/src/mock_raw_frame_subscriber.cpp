#include <rclcpp/rclcpp.hpp>
#include <sdr_receiver/msg/radar_wireless_frame.hpp>

#include <iomanip>
#include <memory>
#include <sstream>
#include <string>

class MockRawFrameSubscriber final : public rclcpp::Node {
public:
    MockRawFrameSubscriber() : Node("mock_raw_frame_subscriber") {
        const std::string topic = declare_parameter<std::string>("topic", "/sdr/radar_wireless/raw_frame");
        sub_ = create_subscription<sdr_receiver::msg::RadarWirelessFrame>(
            topic,
            10,
            [this](const sdr_receiver::msg::RadarWirelessFrame::SharedPtr msg) {
                std::ostringstream payload;
                payload << std::hex << std::setfill('0');
                for (uint8_t b : msg->payload_raw) payload << std::setw(2) << static_cast<int>(b);
                RCLCPP_INFO(get_logger(),
                            "raw_frame cmd=0x%04x team=%s source=%s len=%zu crc8=%s crc16=%s payload=%s",
                            static_cast<unsigned>(msg->cmd_id),
                            msg->team.c_str(),
                            msg->source_target.c_str(),
                            msg->payload_raw.size(),
                            msg->crc8_ok ? "ok" : "bad",
                            msg->crc16_ok ? "ok" : "bad",
                            payload.str().c_str());
            });
        RCLCPP_INFO(get_logger(), "listening for raw wireless frames on %s", topic.c_str());
    }

private:
    rclcpp::Subscription<sdr_receiver::msg::RadarWirelessFrame>::SharedPtr sub_;
};

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<MockRawFrameSubscriber>());
    rclcpp::shutdown();
    return 0;
}
