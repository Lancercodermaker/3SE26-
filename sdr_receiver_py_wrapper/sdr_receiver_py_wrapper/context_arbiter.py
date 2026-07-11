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
        self._candidate_level = None
        self._candidate_count = 0
        self._candidate_since = None

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

        if observation.jam_level not in (1, 2, 3):
            return Decision(
                accepted=False,
                target_changed=False,
                reason="invalid_level",
                level=self.accepted_level,
                target=None,
            )

        if observation.jam_level != self._candidate_level:
            self._candidate_level = observation.jam_level
            self._candidate_count = 1
            self._candidate_since = observation.received_monotonic
        else:
            self._candidate_count += 1

        stable = self._candidate_count >= self.stable_count and (
            observation.received_monotonic - self._candidate_since >= self.stable_sec
        )
        if not stable:
            return Decision(
                accepted=False,
                target_changed=False,
                reason="level_not_stable",
                level=self.accepted_level,
                target=None,
            )

        changed = self.accepted_level != observation.jam_level
        self.accepted_level = observation.jam_level
        return Decision(
            accepted=True,
            target_changed=changed,
            reason="stable_level",
            level=self.accepted_level,
            target=f"L{self.accepted_level}",
        )
