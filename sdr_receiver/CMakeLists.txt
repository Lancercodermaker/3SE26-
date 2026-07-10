cmake_minimum_required(VERSION 3.16)
project(sdr_receiver LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(SDR_RECEIVER_CORE_SOURCES
    src/types.cpp
    src/config.cpp
    src/profile_manager.cpp
    src/bit_utils.cpp
    src/crc.cpp
    src/dsp.cpp
    src/sdr_controller.cpp
    src/demodulator.cpp
    src/calibration.cpp
    src/keyboard.cpp
    src/terminal_ui.cpp
    src/receiver_app.cpp
    src/radar_wireless_data.cpp
    src/pluto_sdr_device.cpp
)

add_library(sdr_receiver_core ${SDR_RECEIVER_CORE_SOURCES})
target_include_directories(sdr_receiver_core PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
target_compile_options(sdr_receiver_core PRIVATE -Wall -Wextra -Wpedantic)

find_path(IIO_INCLUDE_DIR iio.h)
find_library(IIO_LIBRARY iio)
if(IIO_INCLUDE_DIR AND IIO_LIBRARY)
    target_include_directories(sdr_receiver_core PRIVATE ${IIO_INCLUDE_DIR})
    target_link_libraries(sdr_receiver_core PUBLIC ${IIO_LIBRARY})
    target_compile_definitions(sdr_receiver_core PUBLIC SDR_RECEIVER_HAVE_LIBIIO)
endif()

find_package(ament_cmake QUIET)

if(ament_cmake_FOUND)
    find_package(rclcpp REQUIRED)
    find_package(std_msgs REQUIRED)
    find_package(rosidl_default_generators REQUIRED)

    rosidl_generate_interfaces(${PROJECT_NAME}
        msg/JamCode.msg
        msg/RadarContext.msg
        msg/RadarWirelessBuff.msg
        msg/RadarWirelessFrame.msg
        msg/RadarWirelessGoldOccupation.msg
        msg/RadarWirelessHp.msg
        msg/RadarWirelessKey.msg
        msg/RadarWirelessPosition.msg
        msg/RadarWirelessProjectile.msg
        DEPENDENCIES std_msgs
    )
    rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} "rosidl_typesupport_cpp")

    add_executable(sdr_receiver_node src/sdr_receiver_node.cpp)
    ament_target_dependencies(sdr_receiver_node rclcpp std_msgs)
    target_link_libraries(sdr_receiver_node sdr_receiver_core ${cpp_typesupport_target})

    add_executable(mock_radar_context_publisher src/mock_radar_context_publisher.cpp)
    ament_target_dependencies(mock_radar_context_publisher rclcpp std_msgs)
    target_link_libraries(mock_radar_context_publisher ${cpp_typesupport_target})

    add_executable(mock_jam_code_subscriber src/mock_jam_code_subscriber.cpp)
    ament_target_dependencies(mock_jam_code_subscriber rclcpp std_msgs)
    target_link_libraries(mock_jam_code_subscriber ${cpp_typesupport_target})

    add_executable(mock_raw_frame_subscriber src/mock_raw_frame_subscriber.cpp)
    ament_target_dependencies(mock_raw_frame_subscriber rclcpp std_msgs)
    target_link_libraries(mock_raw_frame_subscriber ${cpp_typesupport_target})

    install(TARGETS
        sdr_receiver_core
        sdr_receiver_node
        mock_radar_context_publisher
        mock_jam_code_subscriber
        mock_raw_frame_subscriber
        ARCHIVE DESTINATION lib
        LIBRARY DESTINATION lib
        RUNTIME DESTINATION lib/${PROJECT_NAME}
    )
    install(DIRECTORY include/ DESTINATION include)
    install(DIRECTORY config DESTINATION share/${PROJECT_NAME} OPTIONAL)

    ament_export_dependencies(rclcpp std_msgs rosidl_default_runtime)
    ament_package()
else()
    add_executable(sdr_receiver src/main.cpp)
    target_link_libraries(sdr_receiver PRIVATE sdr_receiver_core)
endif()
