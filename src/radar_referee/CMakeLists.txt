cmake_minimum_required(VERSION 3.8)
project(radar_referee)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(OpenCV REQUIRED)
find_package(Boost REQUIRED COMPONENTS system)
find_package(vision_interface REQUIRED)
find_package(sdr_receiver REQUIRED)

add_executable(radar_node
  src/node_main.cpp
  src/RefereeControl.cpp
  src/CRC.cpp
  src/SendReceive.cpp
  src/log_sys.cpp
)

target_include_directories(radar_node PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${OpenCV_INCLUDE_DIRS}
)

ament_target_dependencies(radar_node
  rclcpp
  std_msgs
  vision_interface
  sdr_receiver
)

target_link_libraries(radar_node
  ${OpenCV_LIBS}
  Boost::system
)

install(TARGETS radar_node
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
