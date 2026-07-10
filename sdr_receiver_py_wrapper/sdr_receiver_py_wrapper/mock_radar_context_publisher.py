from __future__ import annotations

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from sdr_receiver.msg import JamCode, RadarContext


def level_to_radar_info(level: int, *, key_mutable: bool = True) -> int:
    raw = (int(level) & 0x03) << 3
    if key_mutable:
        raw |= 1 << 5
    return raw & 0xFF


class MockRadarContextPublisher(Node):
    def __init__(self) -> None:
        super().__init__("mock_radar_context_publisher_py")
        self.declare_parameter("self_id", 9)
        self.declare_parameter("start_level", 1)
        self.declare_parameter("max_level", 3)
        self.declare_parameter("publish_hz", 5.0)
        self.declare_parameter("auto_advance_on_jam_code", True)
        self.declare_parameter("topic", "/judge/radar_context")

        self.self_id = int(self.get_parameter("self_id").value)
        self.level = int(self.get_parameter("start_level").value)
        self.max_level = int(self.get_parameter("max_level").value)
        self.auto_advance = bool(self.get_parameter("auto_advance_on_jam_code").value)
        topic = str(self.get_parameter("topic").value)

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.pub = self.create_publisher(RadarContext, topic, qos)
        self.sub = self.create_subscription(JamCode, "/sdr/jam_code", self._on_jam_code, qos)
        period = 1.0 / max(0.1, float(self.get_parameter("publish_hz").value))
        self.timer = self.create_timer(period, self._publish_context)
        self.get_logger().info(
            f"mock RadarContext publishing on {topic}, self_id={self.self_id}, level={self.level}"
        )

    def _publish_context(self) -> None:
        msg = RadarContext()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.self_id = self.self_id
        msg.self_color = 2 if self.self_id < 100 else 0
        msg.radar_info_raw = level_to_radar_info(self.level)
        msg.jam_level = self.level
        msg.key_mutable = True
        msg.game_progress = 4
        msg.match_time = 420
        msg.referee_online = True
        self.pub.publish(msg)

    def _on_jam_code(self, msg: JamCode) -> None:
        if not self.auto_advance or not msg.valid:
            return
        if int(msg.level) == self.level and self.level < self.max_level:
            self.level += 1
            self.get_logger().info(f"mock context advanced to L{self.level}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MockRadarContextPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
