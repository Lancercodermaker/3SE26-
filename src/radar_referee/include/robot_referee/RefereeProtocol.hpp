#pragma once
#include <vector>
#include <cstring>
#include <iostream>
#include <codecvt>
#include <locale>
#include <chrono>

#include <std_msgs/msg/string.hpp>
#include <vision_interface/msg/radar2_sentry.hpp>
#include <vision_interface/msg/match_info.hpp>



/*
<=====================该文件用于======================>
<----------------定义裁判系统通信格式------------------>
*/

extern std::vector<uint8_t> Referee_Receive_Buffer;
extern size_t bufferSize;  // 预设读取的字节数

/*--------------CmdID(2-Byte)----------------*/
#define GAME_STATE_ID              0x0001          //(服务器->雷达) 比赛状态
#define ROBOT_STATUS_ID            0x0201          //(服务器->雷达) 机器人状态
#define RADAR_MARK_DATA_ID         0x020C          //(服务器->雷达) 标记进度
#define RADAR_INFO_ID              0x020E          //(服务器->雷达) 雷达自主决策信息同步
#define ROBOT_INTERACTION_DATA_ID  0x0301          //(雷达->服务器) 机器人间通信
#define MAP_ROBOT_DATA_ID          0x0305          //(雷达->服务器) 小地图
#define SITE_EVENT_ID              0x0101          //场地交互信息
#define CUSTOM_INFO_ID             0x0308          //(雷达->服务器) 链路通信
/*------------------(end)--------------------*/

/*--------------DataSize(Byte)----------------*/
#define DATA_SIZE                       (40)
#define FRAME_HEADER_SIZE               (5)
#define CMD_ID_SIZE                     (2)
#define FRAME_TAIL_SIZE                 (2)
#define FRAME_SIZE(n)                   ((n) + FRAME_HEADER_SIZE + CMD_ID_SIZE + FRAME_TAIL_SIZE)  // where n is the dataLength
#define GAME_STATE_SIZE                 (11)        // 0x0001 比赛状态数据
#define ROBOT_STATUS_SIZE               (13)        // 0x0201 机器人状态数据
#define MAP_ROBOT_DATA_SIZE             (48)        // 0x0305 小地图接受雷达数据
#define RADAR_MARK_DATA_SIZE            (2)         // 0x020C 雷达标记进度
#define RADAR_INFO_SIZE                 (1)         // 0x020E 雷达自主决策信息同步
#define ROBOT_INTERACTION_DATA_SIZE     (6)        // 0x0301 机器人间通信  // 6字节头+X字节数据
#define CUSTOM_INFO_SIZE                (34)        // 0x0308 链路通信
/*------------------(end)---------------------*/

/*--------------Offset(Byte)----------------*/
//接收数据
#define SOF_OFFSET                (0)
#define DATA_LENGTH_OFFSET        (1)
#define SEQ_OFFSET                (3)
#define CRC8_OFFSET               (4)
#define CMDID_OFFSET              (5)
#define DATA_OFFSET               (7)
#define CRC16_OFFSET(n)           ((n) + DATA_OFFSET)  // where n is the dataLength
//发送数据


/*-----------------(end)---------------------*/


#pragma pack(push, 1)  // start of data_struct

/*----------------------frame_header-------------------*/
/*
|   SOF     |   data_length |   seq     |   CRC8    |
|   1-byte  |   2-byte      |   1-byte  |   1-byte  |
*/
typedef struct 
{
	uint8_t SOF;
    uint16_t data_length;	
	uint8_t seq;
	uint8_t CRC8;
}frame_header_t;
/*------------------------(end)--------------------------*/

/*------------------------frame--------------------------*/
/*
|   frame_header    | cmd_id    |   data    |   frame_tail  |
|   5-byte          |   2-byte  |   n-byte  |   2-byte      |
*/
typedef struct 
{
    frame_header_t  frame_header;
    uint16_t cmd_id;
    uint8_t data[DATA_SIZE]{};
    uint16_t frame_tail;
} frame_t;
/*------------------------(end)--------------------------*/

/*========================== data =================================*/
/*-------------------------0x0001 1HZ------------------------------*/
// 比赛状态数据, (不同的比赛类型有不同数量的步兵)
// 1Hz接收
typedef struct
{
    uint8_t game_type : 4;              // 比赛类型，占用4位 1
    /*
    比赛类型 
    • 1：RoboMaster 机甲大师超级对抗赛 
    • 2：RoboMaster 机甲大师高校单项赛 
    • 3：ICRA RoboMaster高校人工智能挑战赛 
    • 4：RoboMaster机甲大师高校联盟赛3V3对抗 
    • 5：RoboMaster 机甲大师高校联盟赛步兵对抗 
    */
    uint8_t game_progress : 4;          // 当前比赛阶段，占用4位 1
    /*
    当前比赛阶段 
    • 0：未开始比赛 
    • 1：准备阶段   
    • 2：自检阶段 
    • 3：5秒倒计时  
    • 4：比赛中 
    • 5：比赛结算中 
    */
    uint16_t stage_remain_time;         // 当前阶段剩余时间，2字节

    uint64_t SyncTimeStamp;             // UNIX时间，8字节
}game_state_t;
/*----------------------------(end)----------------------------------*/

/*--------------------------0x0201 10Hz------------------------------*/
// 比赛机器人的状态, (用来获取红蓝方; 获取雷达ID)
// 10Hz
typedef struct
{
    /*
    机器人 ID：
    1：红方英雄机器人；
    2：红方工程机器人；
    3/4/5：红方步兵机器人；
    6：红方空中机器人；
    7：红方哨兵机器人；
    8：红方飞镖机器人；
    9：红方雷达站；  
    101：蓝方英雄机器人；
    102：蓝方工程机器人；
    103/104/105：蓝方步兵机器人；
    106：蓝方空中机器人；
    107：蓝方哨兵机器人。
    108：蓝方飞镖机器人； 
    109：蓝方雷达站。 
    */
   uint8_t robot_id;  // just need this    
} robot_status_t;
/*-----------------------------(end)---------------------------------*/

/*-------------------------0x020C 1Hz--------------------------------*/
// 雷达标记进度
// 1Hz
typedef struct
{
    uint16_t mark_progress;

} radar_mark_data_t;
/*-----------------------------(end)---------------------------------*/

/*------------------------0x020E 1Hz--------------------------*/
// 雷达自主决策信息同步
// 1Hz接受
typedef struct
{
    uint8_t radar_info;
}radar_info_t;
/*-----------------------------(end)---------------------------------*/

/*------------------------0x0301 limit_30Hz--------------------------*/
/*
|   子内容ID     |   发送者ID    |   接收者ID   |   内容数据段          |
|   2-byte      |   2-byte      |   2byte     |     x-byte limit_112 |
*/
// limit_30Hz
// 子内容ID：0x0121(雷达自主决策ID), 0x0200(机器人信息ID);
// 发送者ID: 雷达标号; 
// 接收者ID: 0x8080(裁判端) 或 哨兵ID
typedef struct
{
    uint16_t data_cmd_id = 0x0121;
    uint16_t sender_id;             // 发送者(雷达ID)
    uint16_t receiver_id;
    uint8_t user_data[48];          // 最大为112，48字节可容纳6个机器人信息
} robot_interaction_data_t;
/*-----------------------------(end)---------------------------------*/

/*-------------------------0x0305 5HZ------------------------------*/
// 选手端小地图接受雷达数据
// 5Hz
typedef struct
{
    uint16_t opponent_hero_position_x;
    uint16_t opponent_hero_position_y;
    uint16_t opponent_engineer_position_x;
    uint16_t opponent_engineer_position_y;
    uint16_t opponent_infantry_3_position_x;
    uint16_t opponent_infantry_3_position_y;
    uint16_t opponent_infantry_4_position_x;
    uint16_t opponent_infantry_4_position_y;
    uint16_t opponent_aerial_position_x;
    uint16_t opponent_aerial_position_y;
    uint16_t opponent_sentry_position_x;
    uint16_t opponent_sentry_position_y;
    uint16_t ally_hero_position_x;
    uint16_t ally_hero_position_y;
    uint16_t ally_engineer_position_x;
    uint16_t ally_engineer_position_y;
    uint16_t ally_infantry_3_position_x;
    uint16_t ally_infantry_3_position_y;
    uint16_t ally_infantry_4_position_x;
    uint16_t ally_infantry_4_position_y;
    uint16_t ally_aerial_position_x;
    uint16_t ally_aerial_position_y;
    uint16_t ally_sentry_position_x;
    uint16_t ally_sentry_position_y;
} map_robot_data_t;
/*-----------------------------(end)---------------------------------*/

/*--------------------------0x0308 3Hz------------------------------*/
// 链路通信，发送给无人机预警信息
// 3Hz
typedef struct
{
    uint16_t sender_id;             // 雷达的ID
    uint16_t receiver_id;           // 己方无人机的ID
    uint8_t user_data[30];          // utf-16格式编码           
} custom_info_t;
/*-----------------------------(end)---------------------------------*/

/*--------------------------0x0121 ------------------------------*/
//雷达自主决策
typedef struct
{
uint8_t radar_cmd = 0;
uint8_t password_cmd;
uint8_t password_1;
uint8_t password_2;
uint8_t password_3;
uint8_t password_4;
uint8_t password_5;
uint8_t password_6;
}radar_cmd_t;
/*-----------------------------(end)---------------------------------*/

#pragma pack(pop)  // end of data_truct