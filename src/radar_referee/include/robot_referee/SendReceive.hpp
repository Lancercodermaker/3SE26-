#include <boost/asio.hpp>
#include "rclcpp/rclcpp.hpp"
#include <variant>
#include "robot_referee/RefereeProtocol.hpp"
#include "robot_referee/CRC.hpp"
/*
<================该文件用于===================>
<---------------实现串口通信------------------>
<-----------------解析命令-------------------->
*/

using CmdData = std::variant<game_state_t, robot_status_t, radar_mark_data_t, radar_info_t, std::string>;
using SerialPort = boost::asio::serial_port;

/**
 * @brief 帧初始化函数
 * 
 * @param frame 帧
 * @param dataLength 数据帧长度
 * @param cmd_ID 命令ID
 */
void frameInit(frame_t& frame, uint16_t dataLength, uint16_t cmd_ID);

/**
 * @brief 将数据加入到发送帧
 * 
 * @param frame 发送帧
 * @param data data数据
 * @param dataLength 数据字节长度
 */
template<typename T>
void sendFrame(SerialPort &sp, frame_t& frame, T* data, uint16_t dataLength)
{ 
    static uint8_t frame_Seq = 0;
    // RCLCPP_ERROR(this->get_logger(),"send");
    size_t frameSize = FRAME_SIZE(dataLength);
    std::vector<uint8_t> frame_converse(frameSize);

    memset(frame.data, 0, DATA_SIZE);
    frame.frame_header.seq = frame_Seq;
    frame_Seq++;
    memcpy(frame.data, data, dataLength);
    memcpy(frame_converse.data(), &frame, frameSize);

    Append_CRC8_Check_Sum(frame_converse.data(), CRC8_OFFSET + 1);
    Append_CRC16_Check_Sum(frame_converse.data(), frameSize);
    boost::asio::write(sp, boost::asio::buffer(frame_converse.data(), frameSize));

    return;
}

/**
 * @brief 设置并尝试打开串口
 * @param sp 引用传递的串口对象
 * @param port 串口端口，默认/dev/ttyUSB0
 * @param baudrate 波特率，默认115200
 * @param timeout_ms 超时时间，以毫秒为单位，默认值为100毫秒
 * @return 成功打开返回true，否则返回false
 */
bool setupSerialPort(SerialPort &sp, const std::string& port="/dev/ttyUSB0", uint32_t baudrate=115200, uint32_t timeout_ms=100);


/**
* @brief 同步到数据流中的帧起始字节，并读取整个数据帧
* 
* @param sp 引用传递的串口对象，用于从串口读取数据
* @param dataBuffer 用于存储同步后字节数据的向量，包含从帧起始字节开始后的所有数据
* @param bufferLen 一次读取的字节数，默认为512
* @param timeout_ms 超时时间，以毫秒为单位，默认值为100毫秒
* @return std::vector<std::vector<uint8_t>>(buffer, buffer ……)
*/
std::vector<std::vector<uint8_t>> syncToFrameStart(SerialPort &sp, size_t bufferSize=128, int timeout_ms = 100);

/**
* @brief 辅助函数，解析产生 cmd_id 和 dataLength
* 
* @param buffer 帧数据
* @param data_length 解析结果
* @param cmd_id 解析结果
* @return 解析成功返回true
*/
bool framePreProcess(std::vector<uint8_t>& buffer, uint16_t& data_length, uint16_t& cmd_id);


/**
 * @brief 解析命令
 * 
 * @param buffer 帧
 * @param 
 */
CmdData cmdProcess(const std::vector<uint8_t>& buffer);