from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    source: str
    self_id: int
    self_color: int
    radar_info_raw: int
    jam_level: int
    key_mutable: bool
    game_progress: int
    match_time: int
    received_monotonic: float


@dataclass(frozen=True)
class Decision:
    accepted: bool
    target_changed: bool
    reason: str
    level: int | None
    target: str | None


class ContextArbiter:
    def __init__(self, authority, stable_count=3, stable_sec=1.0):
        self.authority = authority
        self.stable_count = stable_count
        self.stable_sec = stable_sec
        self.own_team = None
        self.accepted_level = None

    def observe(self, observation):
        if observation.source != self.authority:
            return Decision(
                accepted=False,
                target_changed=False,
                reason="diagnostic_source",
                level=self.accepted_level,
                target=None,
            )

        if observation.self_id not in (9, 109):
            return Decision(
                accepted=False,
                target_changed=False,
                reason="invalid_radar_id",
                level=self.accepted_level,
                target=None,
            )

        self.own_team = "RED" if observation.self_id == 9 else "BLUE"

        if observation.game_progress != 4:
            return Decision(
                accepted=False,
                target_changed=False,
                reason="prematch_observation",
                level=self.accepted_level,
                target=None,
            )

        self.accepted_level = observation.jam_level
        return Decision(
            accepted=True,
            target_changed=False,
            reason="initial_context",
            level=observation.jam_level,
            target=None,
        )
