from dataclasses import dataclass
import json


CANONICAL_CONTEXT_AUTHORITY = "/judge/radar_context"


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
    context_version: int


class ContextArbiter:
    def __init__(
        self,
        authority,
        stable_count=3,
        stable_sec=1.0,
        lock_team_after_start=True,
    ):
        self.authority = authority
        self.stable_count = stable_count
        self.stable_sec = stable_sec
        self.lock_team_after_start = lock_team_after_start
        self.own_team = None
        self.accepted_level = None
        self.context_version = 0
        self._candidate_level = None
        self._candidate_count = 0
        self._candidate_since = None
        self._team_locked = False

    def _reject(self, reason):
        return Decision(
            accepted=False,
            target_changed=False,
            reason=reason,
            level=self.accepted_level,
            target=None,
            context_version=self.context_version,
        )

    def observe(self, observation):
        if observation.source != self.authority:
            return self._reject("diagnostic_source")

        if observation.self_id not in (9, 109):
            return self._reject("invalid_radar_id")

        observed_team = "RED" if observation.self_id == 9 else "BLUE"
        if self._team_locked and observed_team != self.own_team:
            return self._reject("team_locked")
        self.own_team = observed_team
        if self.lock_team_after_start and observation.game_progress == 4:
            self._team_locked = True

        if observation.game_progress != 4:
            return self._reject("prematch_observation")

        if observation.jam_level not in (1, 2, 3):
            return self._reject("invalid_level")

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
            return self._reject("level_not_stable")

        changed = self.accepted_level != observation.jam_level
        self.accepted_level = observation.jam_level
        self.context_version += 1
        return Decision(
            accepted=True,
            target_changed=changed,
            reason="stable_level",
            level=self.accepted_level,
            target=f"L{self.accepted_level}",
            context_version=self.context_version,
        )


def format_context_decision_log(observation, decision):
    return json.dumps(
        {
            "source": observation.source,
            "raw": {
                "self_id": observation.self_id,
                "self_color": observation.self_color,
                "radar_info_raw": observation.radar_info_raw,
                "jam_level": observation.jam_level,
                "key_mutable": observation.key_mutable,
                "game_progress": observation.game_progress,
                "match_time": observation.match_time,
            },
            "accepted": decision.accepted,
            "target_changed": decision.target_changed,
            "reason": decision.reason,
            "context_version": decision.context_version,
        },
        sort_keys=True,
    )


def resolve_receiver_target(decision, controller_target):
    if not decision.accepted or not decision.target_changed:
        return None
    return controller_target


def resolve_context_authority(configured_authority, legacy_context_topic):
    configured = str(configured_authority or "").strip()
    legacy = str(legacy_context_topic or "").strip()
    if configured:
        return configured, False
    if legacy:
        return legacy, True
    return CANONICAL_CONTEXT_AUTHORITY, False


def resolve_diagnostic_values(
    *, radar_info_raw, jam_level, key_mutable, referee_online, match_time
):
    raw = int(radar_info_raw) & 0xFF
    resolved_level = (raw >> 3) & 0x03 if jam_level is None else int(jam_level)
    resolved_key_mutable = (
        ((raw >> 5) & 0x01) != 0 if key_mutable is None else bool(key_mutable)
    )
    resolved_referee_online = (
        int(match_time) != -200
        if referee_online is None
        else bool(referee_online)
    )
    return resolved_level, resolved_key_mutable, resolved_referee_online
