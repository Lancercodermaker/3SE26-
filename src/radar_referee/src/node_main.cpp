#include <iostream>
#include <thread>
#include <mutex>
#include <chrono>

#include "rclcpp/rclcpp.hpp"
#include "robot_referee/RefereeControl.hpp"

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);

    auto controller = std::make_shared<RefereeControl>();
    controller->refereeInit();

    std::thread commandThread([&controller]() {
        while (rclcpp::ok())
        {
            if (controller->getCommand())
            {
                controller->executeCommand();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    });

    std::thread locationThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->selectLocation();
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    });

    // std::thread warningThread([&controller]() {
    //     while (rclcpp::ok())
    //     {
    //         controller->sendWarnning();
    //         std::this_thread::sleep_for(std::chrono::milliseconds(100));
    //     }
    // });

    std::thread vulThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->vulProcess();
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        }
    });

    std::thread keyThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->sendKey();
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        }
    });

    std::thread robotInfoThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->sendRobotInfo();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    });
    std::thread eventInfoThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->sendEventInfo();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    });
    std::thread outpostAliveThread([&controller]() {
        while (rclcpp::ok())
        {
            controller->sendOutpostAlive();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    });

    while (rclcpp::ok())
    {
        rclcpp::spin_some(controller);
        rclcpp::sleep_for(std::chrono::milliseconds(10));
    }

    vulThread.join();
    commandThread.join();
    locationThread.join();
    //warningThread.join();
    robotInfoThread.join();

    rclcpp::shutdown();
    return 0;
}