from __future__ import annotations

import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from sdr_receiver.msg import JamCode, RadarWirelessFrame


class SdrReceiverTopicMonitor(Node):
    def __init__(self) -> None:
        super().__init__("sdr_receiver_topic_monitor")
        self.create_subscription(JamCode, "/sdr/jam_code", self._on_jam_code, 10)
        self.create_subscription(
            RadarWirelessFrame,
            "/sdr/radar_wireless/raw_frame",
            self._on_raw_frame,
            10,
        )
        self.create_subscription(String, "/sdr/status", self._on_status, 10)
        self.get_logger().info("monitoring /sdr/jam_code, /sdr/radar_wireless/raw_frame, /sdr/status")

    def _on_jam_code(self, msg: JamCode) -> None:
        key = bytes(msg.key).decode("ascii", errors="replace")
        self.get_logger().info(
            f"JamCode valid={msg.valid} level={msg.level} team={msg.team} "
            f"target={msg.target} key={key} radar_info=0x{msg.radar_info_raw:02X}"
        )

    def _on_raw_frame(self, msg: RadarWirelessFrame) -> None:
        payload_hex = bytes(msg.payload_raw).hex()
        self.get_logger().info(
            f"RawFrame cmd=0x{msg.cmd_id:04X} team={msg.team} "
            f"source={msg.source_target} payload={payload_hex}"
        )

    def _on_status(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
            state = data.get("competition", {}).get("state")
            target = data.get("core", {}).get("target")
            rf_state = data.get("core", {}).get("rf_state")
            self.get_logger().info(f"Status state={state} target={target} rf_state={rf_state}")
        except Exception:
            self.get_logger().info(f"Status {msg.data[:240]}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SdrReceiverTopicMonitor()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
