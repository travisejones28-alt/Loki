"""One alert per confirmed appearance, with consecutive-frame re-arming."""

from dataclasses import dataclass
from enum import Enum

from .detector import DetectionResult


class DetectorState(str, Enum):
    ARMED = "ARMED"
    CONFIRMING = "CONFIRMING"
    TRIGGERED = "TRIGGERED"


@dataclass(frozen=True)
class StateUpdate:
    state: DetectorState
    candidate_started: bool = False
    triggered: bool = False
    rearmed: bool = False
    visible: bool = False


class DetectionLatch:
    def __init__(self, confirmation_frames=4, disappearance_frames=5):
        if (
            type(confirmation_frames) is not int
            or type(disappearance_frames) is not int
            or min(confirmation_frames, disappearance_frames) < 2
        ):
            raise ValueError("At least two confirmation and disappearance frames are required.")
        self.confirmation_frames = confirmation_frames
        self.disappearance_frames = disappearance_frames
        self.reset()

    def reset(self):
        self.state = DetectorState.ARMED
        self._hits = 0
        self._misses = 0
        self._candidate = None

    def update(self, result: DetectionResult):
        if self.state == DetectorState.TRIGGERED:
            if result.present or result.matched:
                self._misses = 0
                return StateUpdate(self.state, visible=True)
            self._misses += 1
            if self._misses >= self.disappearance_frames:
                self.reset()
                return StateUpdate(self.state, rearmed=True)
            return StateUpdate(self.state)
        if not result.matched:
            self.reset()
            return StateUpdate(self.state)
        stable = (
            self._candidate is not None
            and abs(result.x - self._candidate.x) <= max(8, result.width // 5)
            and abs(result.y - self._candidate.y) <= max(8, result.height // 5)
        )
        started = not stable
        self._hits = self._hits + 1 if stable else 1
        self._candidate = result
        self.state = DetectorState.CONFIRMING
        if self._hits >= self.confirmation_frames:
            self.state = DetectorState.TRIGGERED
            return StateUpdate(self.state, triggered=True, visible=True)
        return StateUpdate(self.state, candidate_started=started)
