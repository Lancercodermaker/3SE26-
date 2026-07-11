#include "robot_referee/SendReceive.hpp"


void frameInit(frame_t &frame,uint16_t dataLength, uint16_t cmd_ID)
{

	frame.frame_header.SOF = 0xA5;                        //起始帧固定字节 0xA5
	frame.frame_header.data_length = dataLength;          //数据长度初始化
	frame.frame_header.seq = 0;                           //包序号初始化
	frame.frame_header.CRC8 = 0;                          //CRC校验初始化
    frame.cmd_id = cmd_ID;                                
	return;
}

bool setupSerialPort(SerialPort &sp, const std::string& port, uint32_t baudrate, uint32_t timeout_ms)
{

    try
    {
        sp.open(port);

        sp.set_option(boost::asio::serial_port_base::baud_rate(baudrate));
        sp.set_option(boost::asio::serial_port_base::character_size(8));
        sp.set_option(boost::asio::serial_port_base::parity(
            boost::asio::serial_port_base::parity::none));
        sp.set_option(boost::asio::serial_port_base::stop_bits(
            boost::asio::serial_port_base::stop_bits::one));
        sp.set_option(boost::asio::serial_port_base::flow_control(
            boost::asio::serial_port_base::flow_control::none));

        if (sp.is_open())
        {
            std::cout << port << " is opened at baudrate "
                      << baudrate << std::endl;
            return true;
        }
    }
    catch (const boost::system::system_error& e)
    {
        std::cerr << "setupSerialPort: Unable to open port "
                  << port << ": " << e.what() << std::endl;
        return false;
    }

    return false;
}


std::vector<std::vector<uint8_t>> syncToFrameStart(SerialPort &sp, size_t bufferSize, int timeout_ms)
{
    auto start = std::chrono::steady_clock::now();
	
    std::vector<uint8_t> bytesBatch(bufferSize);
	size_t bytesRead = 0;
	while (bytesRead < bufferSize) 
	{
        auto now = std::chrono::steady_clock::now();

        if (std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count() > timeout_ms)
        {
            break;
        }

        boost::system::error_code ec;

    	size_t result = sp.read_some(boost::asio::buffer(bytesBatch.data() + bytesRead, bufferSize - bytesRead), ec);
	bytesRead += result;
        if (bytesRead != bufferSize)
    {
        std::cerr << "syncToFrameStart: Read " << bytesRead << " bytes, expected " << bufferSize << " bytes (timeout or end of stream)" << std::endl;
    }
        if (ec)
        {
            std::cerr << "serial read error: " << ec.message() << std::endl;
            break;
        }
	}

    if (bytesRead == 0)
    {
        return {};
    }

    

    bytesBatch.resize(bytesRead);
    bufferSize = bytesRead;

	std::vector<uint8_t>::iterator buffer_it = bytesBatch.begin();
	std::vector<std::vector<uint8_t>> result;
	

    while (true)
    {
        buffer_it = std::find(buffer_it, bytesBatch.end(), 0xA5);
        if (buffer_it == bytesBatch.end())
		{
			// RCLCPP_WARN(this->get_logger(), "SyncToFrameStart: To end");
            return result;
		}

        size_t startOffset = std::distance(bytesBatch.begin(), buffer_it);
		bufferSize -= startOffset;
		// buffer_it += 1;
        
        if (bufferSize <= FRAME_HEADER_SIZE + CMD_ID_SIZE + FRAME_TAIL_SIZE)
        {
            // RCLCPP_WARN(this->get_logger(), "SyncToFrameStart: Not enough data");
            return result;
        }

        std::vector<uint8_t> tempBuffer(buffer_it, bytesBatch.end());
        uint16_t dataLength, cmd_ID;
        if (framePreProcess(tempBuffer, dataLength, cmd_ID))
        {
            if (bufferSize >= FRAME_SIZE(dataLength))
            {
                std::vector<uint8_t> dataBuffer(buffer_it, buffer_it + FRAME_SIZE(dataLength));
                
				bufferSize -= FRAME_SIZE(dataLength);
				buffer_it += FRAME_SIZE(dataLength);
				result.push_back(dataBuffer);
			}
            else
            {
                // RCLCPP_WARN(this->get_logger(), "SyncToFrameStart: Not enough data");
				return result;
            }
        }
        else
        {
            std::cerr << "SyncToFrameStart: Failed to process frame header. ..." << std::endl;
            return result;
        }
    }
    return std::vector<std::vector<uint8_t>>();
}

bool framePreProcess(std::vector<uint8_t>& buffer, uint16_t& data_length, uint16_t& cmd_id)
{
    // 小端格式
    // 解析数据长度
    data_length = (static_cast<uint16_t>(buffer[DATA_LENGTH_OFFSET + 1]) << 8) | static_cast<uint16_t>(buffer[DATA_LENGTH_OFFSET]);

    // 解析命令ID
    cmd_id = (static_cast<uint16_t>(buffer[CMDID_OFFSET + 1]) << 8) | static_cast<uint16_t>(buffer[CMDID_OFFSET]);

    //----------------------------------------------------------------------------------------------------//
    // // 验证CRC校验
    // if (Verify_CRC8_Check_Sum(buffer.data(), CRC8_OFFSET) &&
    //     Verify_CRC16_Check_Sum(buffer.data(), data_length + FRAME_HEADER_SIZE + CMD_ID_SIZE))
    // {
    //     return true;
    // }
    // else
    // {
    //     return false;
    // }
    //----------------------------------------------------------------------------------------------------//

    return true;
}

CmdData cmdProcess(const std::vector<uint8_t>& buffer)
{
    if (buffer.size() <= DATA_OFFSET) {
        return "cmdProcess: Buffer size too small";
    }

    uint16_t cmd_id = (static_cast<uint16_t>(buffer[CMDID_OFFSET + 1]) << 8) | static_cast<uint16_t>(buffer[CMDID_OFFSET]);
    std::vector<uint8_t> dataBuffer(buffer.begin() + DATA_OFFSET, buffer.end() - FRAME_TAIL_SIZE);

    switch (cmd_id)
    {
    case GAME_STATE_ID:  // 比赛状态数据
    {
        // std::cout<< GAME_STATE_ID <<std::endl;
        if (dataBuffer.size() != GAME_STATE_SIZE)
        {
            std::cerr << "cmdProcess: abnormal Buffer size,"<< GAME_STATE_ID << " maybe error in frame process." << std::endl;
            return "Buffer size error for GAME_STATE_ID";
        }
        game_state_t data;
        uint8_t matchInfo = dataBuffer[0];
        uint8_t game_progress = (matchInfo & 0xF0) >> 4;
        uint8_t game_type = matchInfo & 0x0F;
        uint16_t stage_remain_time = static_cast<uint16_t>(dataBuffer[1]) | (static_cast<uint16_t>(dataBuffer[2]) << 8);
        uint64_t SyncTimeStamp = 0;
        for (int i = 0; i < 8; ++i)
            SyncTimeStamp = SyncTimeStamp | static_cast<uint64_t>(dataBuffer[3 + i]) << (8 * i);

        data.game_type = game_type;
        data.game_progress = game_progress;
        data.stage_remain_time = stage_remain_time;
        data.SyncTimeStamp = SyncTimeStamp;
        return data;
    }
    case ROBOT_STATUS_ID:  // 机器人的状态
    {
        if (dataBuffer.size() != ROBOT_STATUS_SIZE)
        {
            std::cerr << "cmdProcess: abnormal Buffer size,"<< ROBOT_STATUS_ID << "  maybe error in frame process." << std::endl;
            return "Buffer size error for ROBOT_STATUS_ID";
        }
        robot_status_t data;
        data.robot_id = dataBuffer[0];
        return data;
    }
    case RADAR_MARK_DATA_ID:  // 雷达标记进度
    {
        // std::cout<< RADAR_MARK_DATA_ID <<std::endl;
        if (dataBuffer.size() != RADAR_MARK_DATA_SIZE)
        {
            std::cerr << "cmdProcess: abnormal Buffer size,"<< RADAR_MARK_DATA_ID << "  maybe error in frame process." << std::endl;
            return "Buffer size error for RADAR_MARK_DATA_ID";
        }
        radar_mark_data_t data;
        data.mark_progress = static_cast<uint16_t>(dataBuffer[0]) | (static_cast<uint16_t>(dataBuffer[1]) << 8);
        return data;
    }
    case RADAR_INFO_ID:
    {
        // std::cout<< RADAR_INFO_ID << std::endl;
        if(dataBuffer.size() != RADAR_INFO_SIZE)
        {
            std::cerr << "cmdProcess: abnormal Buffer size,"<< RADAR_INFO_ID << "  maybe error in frame process." << std::endl;
            return "Buffer size error for RADAR_MARK_DATA_ID";
        }
        radar_info_t data;
        data.radar_info = dataBuffer[0];
        return data;

    }
    default:
        return "Unknown cmd_id";
    }
}