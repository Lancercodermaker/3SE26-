#include "sdr_receiver/receiver_app.hpp"
#include <memory>

int main() {
    auto sdr = std::make_unique<sdr_receiver::MockSdrDevice>();
    sdr_receiver::ReceiverApp app(std::move(sdr));
    return app.run();
}
