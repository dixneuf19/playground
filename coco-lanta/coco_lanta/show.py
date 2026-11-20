"""The show: which cameras exist, what is on air, scores, timer, banner.

Pure state with the clock passed in, so the automatic director is testable.
Kept in memory: one replica, one party. A restart forgets scores and timer;
cameras come back on their own when their sockets reconnect.
"""

from dataclasses import dataclass
from typing import Any

TEAMS = {"faucille": "Rouge Faucille", "marteau": "Rouge Marteau"}
LAYOUTS = ("solo", "duel", "mosaic")

ROTATE = 20.0  # auto: seconds on a camera before moving to the next one
QUIET = 4.0  # ... but not while it has been loud in the last few seconds
MAX_HOLD = 60.0  # ... unless it has been on for this long
MIN_HOLD = 6.0  # auto: a loud camera can't cut in sooner than this
GRACE = 6.0  # a camera whose socket dropped keeps its place this long


@dataclass
class Camera:
    id: str
    name: str
    gone_at: float | None = None


class Show:
    def __init__(self) -> None:
        self.cameras: dict[str, Camera] = {}  # in joining order
        self.auto = True
        self.layout = "solo"
        self.program: str | None = None  # on air in the solo layout
        self.duo: list[str] = []  # the two cameras of the duel layout
        self.scores = dict.fromkeys(TEAMS, 0)
        self.banner = ""
        self.audio = True
        self.qr = True
        self.timer_ends: float | None = None  # running: when it reaches zero
        self.timer_left: float | None = None  # paused: what is left
        self._switched_at = 0.0
        self._loud_at = 0.0

    # --- cameras ------------------------------------------------------------

    def join(self, cam_id: str, name: str, now: float) -> None:
        if cam_id in self.cameras:  # back within the grace period
            cam = self.cameras[cam_id]
            cam.name, cam.gone_at = name, None
        else:
            self.cameras[cam_id] = Camera(cam_id, name)
        self._fix(now)

    def leave(self, cam_id: str, now: float) -> None:
        if cam_id in self.cameras:
            self.cameras[cam_id].gone_at = now

    def kick(self, cam_id: str, now: float) -> None:
        self.cameras.pop(cam_id, None)
        self._fix(now)

    def on_air(self) -> list[str]:
        if self.layout == "mosaic":
            return list(self.cameras)
        if self.layout == "duel":
            return list(self.duo)
        return [self.program] if self.program else []

    # --- the director -------------------------------------------------------

    def take(self, cam_id: str, now: float) -> None:
        """The control room picks a camera."""
        if cam_id not in self.cameras:
            return
        if self.layout == "duel":  # replaces the camera picked longest ago
            if cam_id not in self.duo:
                self.duo = [*self.duo[-1:], cam_id]
            return
        self.layout = "solo"
        self.auto = False
        self._switch(cam_id, now)

    def set_auto(self, now: float) -> None:
        self.auto = True
        self._switched_at = now

    def set_layout(self, layout: str, now: float) -> None:
        if layout in LAYOUTS:
            self.layout = layout
            self._fix(now)

    def speakers(self, cam_ids: list[str], now: float) -> None:
        """The screen reports who is loud, loudest first."""
        loud = [cam_id for cam_id in cam_ids if cam_id in self.cameras]
        if self.program in loud:
            self._loud_at = now
        elif (
            loud
            and self.auto
            and self.layout == "solo"
            and now - self._switched_at >= MIN_HOLD
        ):
            self._switch(loud[0], now)
            self._loud_at = now

    def tick(self, now: float) -> None:
        for cam in list(self.cameras.values()):
            if cam.gone_at is not None and now - cam.gone_at > GRACE:
                del self.cameras[cam.id]
        self._fix(now)
        if not self.auto or self.layout != "solo" or len(self.cameras) < 2:
            return
        held = now - self._switched_at
        if held >= MAX_HOLD or (held >= ROTATE and now - self._loud_at >= QUIET):
            self._switch(self._next(), now)

    def _switch(self, cam_id: str | None, now: float) -> None:
        if cam_id != self.program:
            self.program = cam_id
            self._switched_at = now

    def _next(self) -> str | None:
        ids = list(self.cameras)
        if not ids:
            return None
        if self.program not in ids:
            return ids[0]
        return ids[(ids.index(self.program) + 1) % len(ids)]

    def _fix(self, now: float) -> None:
        """Never leave a camera that is gone on air."""
        if self.program not in self.cameras:
            self.auto = True  # the manual pick left: the show goes on
            self._switch(self._next(), now)
        self.duo = [cam_id for cam_id in self.duo if cam_id in self.cameras]
        spare = [cam_id for cam_id in self.cameras if cam_id not in self.duo]
        self.duo += spare[: 2 - len(self.duo)]

    # --- scores, timer, texts -----------------------------------------------

    def score(self, team: str, delta: int) -> None:
        if team in self.scores:
            self.scores[team] = max(0, min(999, self.scores[team] + delta))

    def timer_start(self, seconds: float, now: float) -> None:
        self.timer_ends, self.timer_left = now + max(1.0, min(seconds, 3600.0)), None

    def timer_toggle(self, now: float) -> None:
        if self.timer_ends is not None:
            self.timer_left = max(0.0, self.timer_ends - now)
            self.timer_ends = None
        elif self.timer_left:
            self.timer_ends, self.timer_left = now + self.timer_left, None

    def timer_reset(self) -> None:
        self.timer_ends = self.timer_left = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "cameras": [{"id": c.id, "name": c.name} for c in self.cameras.values()],
            "auto": self.auto,
            "layout": self.layout,
            "program": self.program,
            "duo": self.duo,
            "on_air": self.on_air(),
            "scores": self.scores,
            "banner": self.banner,
            "audio": self.audio,
            "qr": self.qr,
            "timer": {"ends": self.timer_ends, "left": self.timer_left},
        }
