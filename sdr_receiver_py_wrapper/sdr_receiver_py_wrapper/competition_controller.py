from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, List, Optional, Tuple


class CompetitionState(str, Enum):
    WAIT_CONTEXT = "WAIT_CONTEXT"
    JAM_L1 = "JAM_L1"
    WAIT_LEVEL_L2 = "WAIT_LEVEL_L2"
    JAM_L2 = "JAM_L2"
    WAIT_LEVEL_L3 = "WAIT_LEVEL_L3"
    JAM_L3 = "JAM_L3"
    INFO = "INFO"
    ERROR_HOLD = "ERROR_HOLD"


@dataclass(frozen=True)
class RadarContext:
    self_id: int
    self_color: int
    radar_info_raw: int
    jam_level: int
    key_mutable: bool
    game_progress: int = 0
    match_time: int = 0
    referee_online: bool = False
    source: str = "unknown"


@dataclass
class ControllerDecision:
    state: CompetitionState
    own_team: Optional[str] = None
    rx_team: Optional[str] = None
    team: Optional[str] = None
    target: Optional[str] = None
    reason: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class KeyDecision:
    publish: bool
    state: CompetitionState
    level: int = 0
    target: Optional[str] = None
    reason: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class _PublishedKeyRecord:
    key: Tuple[int, ...]
    count: int
    last_publish_monotonic: float


class CompetitionController:
    """Competition state machine driven by judge context and decoded keys."""

    def __init__(
        self,
        *,
        max_jam_break_level: int = 3,
        key_publish_min_interval_sec: float = 0.5,
        key_retry_limit: int = -1,
    ) -> None:
        if max_jam_break_level not in (1, 2, 3):
            raise ValueError("max_jam_break_level must be 1, 2, or 3")
        self.max_jam_break_level = int(max_jam_break_level)
        self.key_publish_min_interval_sec = float(key_publish_min_interval_sec)
        self.key_retry_limit = int(key_retry_limit)
        self.state = CompetitionState.WAIT_CONTEXT
        self.own_team: Optional[str] = None
        self.rx_team: Optional[str] = None
        # Backward-compatible alias used by receiver_node: this is the RF team
        # to receive, i.e. the opponent's RED/BLUE waveform set.
        self.team: Optional[str] = None
        self.active_level = 0
        self.completed_level = 0
        self.desired_target: Optional[str] = None
        self.latest_context: Optional[RadarContext] = None
        self.published_keys: Dict[int, _PublishedKeyRecord] = {}
        self.last_warning = ""

    def update_context(self, context: RadarContext) -> ControllerDecision:
        warnings: List[str] = []
        self.latest_context = context

        own_team, team_warning = self._own_team_from_context(context)
        if team_warning:
            warnings.append(team_warning)
        if own_team is None:
            self.state = CompetitionState.WAIT_CONTEXT
            return ControllerDecision(
                state=self.state,
                reason=f"waiting for valid self_id, got {context.self_id}",
                warnings=warnings,
            )

        rx_team = self._opponent_team(own_team)
        own_team_update = own_team if own_team != self.own_team else None
        rx_team_update = rx_team if rx_team != self.rx_team else None
        self.own_team = own_team
        self.rx_team = rx_team
        self.team = rx_team

        if context.jam_level not in (1, 2, 3):
            warning = f"competition ignores invalid jam_level={context.jam_level}"
            warnings.append(warning)
            self.last_warning = warning
            if self.state == CompetitionState.WAIT_CONTEXT:
                self.desired_target = None
            return ControllerDecision(
                state=self.state,
                own_team=own_team_update,
                rx_team=rx_team_update,
                team=rx_team_update,
                reason="waiting for next valid radar_info context",
                warnings=warnings,
            )

        level = min(int(context.jam_level), self.max_jam_break_level)
        if self.state == CompetitionState.INFO and self.completed_level >= self.max_jam_break_level:
            target_update = self._set_desired_target("INFO")
            return ControllerDecision(
                state=self.state,
                own_team=own_team_update,
                rx_team=rx_team_update,
                team=rx_team_update,
                target=target_update,
                reason="already in INFO after final key",
                warnings=warnings,
            )

        self.active_level = level
        if self._has_published_level(level) and level < self.max_jam_break_level:
            self.state = self._wait_state_for_next_level(level)
            target_update = self._set_desired_target(f"L{level}")
            return ControllerDecision(
                state=self.state,
                own_team=own_team_update,
                rx_team=rx_team_update,
                team=rx_team_update,
                target=target_update,
                reason=f"level {level} key sent, waiting for L{level + 1} context",
                warnings=warnings,
            )

        self.state = self._jam_state_for_level(level)
        target_update = self._set_desired_target(f"L{level}")
        return ControllerDecision(
            state=self.state,
            own_team=own_team_update,
            rx_team=rx_team_update,
            team=rx_team_update,
            target=target_update,
            reason=f"receive L{level} jam key",
            warnings=warnings,
        )

    def handle_jam_key(self, *, level: int, key: bytes) -> KeyDecision:
        warnings: List[str] = []
        if self.rx_team is None or self.latest_context is None:
            return KeyDecision(
                publish=False,
                state=self.state,
                reason="ignore key until valid judge context arrives",
            )

        if level not in (1, 2, 3):
            level = self.active_level
        if level != self.active_level:
            return KeyDecision(
                publish=False,
                state=self.state,
                level=level,
                reason=f"ignore L{level} key while active level is L{self.active_level}",
            )

        if self.state == CompetitionState.INFO:
            return KeyDecision(
                publish=False,
                state=self.state,
                level=level,
                reason="ignore jam key after entering INFO",
            )

        key_tuple = tuple(int(b) & 0xFF for b in key[:6])
        if len(key_tuple) != 6:
            return KeyDecision(
                publish=False,
                state=self.state,
                level=level,
                reason="ignore malformed key length",
            )

        now = time.monotonic()
        record = self.published_keys.get(level)
        if record is not None and record.key == key_tuple:
            age = now - record.last_publish_monotonic
            if age < self.key_publish_min_interval_sec:
                return KeyDecision(
                    publish=False,
                    state=self.state,
                    level=level,
                    reason=f"suppress duplicate key within {self.key_publish_min_interval_sec:.3f}s",
                )
            if self.key_retry_limit >= 0 and record.count >= self.key_retry_limit:
                return KeyDecision(
                    publish=False,
                    state=self.state,
                    level=level,
                    reason=f"suppress key after retry limit {self.key_retry_limit}",
                )
            record.count += 1
            record.last_publish_monotonic = now
        else:
            self.published_keys[level] = _PublishedKeyRecord(
                key=key_tuple,
                count=1,
                last_publish_monotonic=now,
            )

        self.completed_level = max(self.completed_level, level)
        if level >= self.max_jam_break_level:
            self.state = CompetitionState.INFO
            target_update = self._set_desired_target("INFO")
            return KeyDecision(
                publish=True,
                state=self.state,
                level=level,
                target=target_update,
                reason=f"L{level} final key published, switch to INFO",
                warnings=warnings,
            )

        self.state = self._wait_state_for_next_level(level)
        return KeyDecision(
            publish=True,
            state=self.state,
            level=level,
            reason=f"L{level} key published, waiting for L{level + 1} context",
            warnings=warnings,
        )

    def status_snapshot(self) -> dict:
        context = self.latest_context
        return {
            "state": self.state.value,
            "own_team": self.own_team,
            "rx_team": self.rx_team,
            "team": self.rx_team,
            "active_level": self.active_level,
            "completed_level": self.completed_level,
            "desired_target": self.desired_target,
            "max_jam_break_level": self.max_jam_break_level,
            "last_warning": self.last_warning,
            "latest_context": None
            if context is None
            else {
                "self_id": context.self_id,
                "self_color": context.self_color,
                "radar_info_raw": context.radar_info_raw,
                "jam_level": context.jam_level,
                "key_mutable": context.key_mutable,
                "match_time": context.match_time,
                "referee_online": context.referee_online,
                "source": context.source,
            },
            "published_key_counts": {
                str(level): record.count for level, record in self.published_keys.items()
            },
        }

    def _set_desired_target(self, target: str) -> Optional[str]:
        if self.desired_target == target:
            return None
        self.desired_target = target
        return target

    def _has_published_level(self, level: int) -> bool:
        record = self.published_keys.get(level)
        return record is not None and record.count > 0

    @staticmethod
    def _jam_state_for_level(level: int) -> CompetitionState:
        return {
            1: CompetitionState.JAM_L1,
            2: CompetitionState.JAM_L2,
            3: CompetitionState.JAM_L3,
        }[level]

    @staticmethod
    def _wait_state_for_next_level(level: int) -> CompetitionState:
        return CompetitionState.WAIT_LEVEL_L2 if level == 1 else CompetitionState.WAIT_LEVEL_L3

    @staticmethod
    def _own_team_from_context(context: RadarContext) -> Tuple[Optional[str], str]:
        self_id = int(context.self_id)
        if self_id == 9:
            return "RED", ""
        if self_id == 109:
            return "BLUE", ""
        if 1 <= self_id <= 99:
            return "RED", f"self_id={self_id} implies RED but is not radar station 9"
        if 101 <= self_id <= 199:
            return "BLUE", f"self_id={self_id} implies BLUE but is not radar station 109"
        if int(context.self_color) == 2:
            return "RED", "team inferred from self_color=2 because self_id is invalid"
        if int(context.self_color) == 0:
            return "BLUE", "team inferred from self_color=0 because self_id is invalid"
        return None, "cannot infer team from judge context"

    @staticmethod
    def _opponent_team(own_team: str) -> str:
        return "BLUE" if own_team == "RED" else "RED"
