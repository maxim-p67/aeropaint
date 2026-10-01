"""Гироскоп, опубликованный контроллером как обычные оси джойстика.

Некоторые геймпады в режиме DirectInput отдают показания датчиков
дополнительными осями. Если диагностика нашла такие оси, их номера
и масштабы попадают в mapping.json, и приложение читает движение отсюда.
"""

from __future__ import annotations

from .base import InputBackend, RateMeter, SprayState
from .sdl_sensor import DEFAULT_BUTTON_MAP


class AxesBackend(InputBackend):
    name = "оси джойстика"
    description = "показания датчиков как дополнительные оси"

    def __init__(self, gyro_axes=(3, 4, 2), accel_axes=None,
                 gyro_scale: float = 8.0, accel_scale: float = 19.6,
                 index: int = 0, button_map: dict | None = None) -> None:
        self.gyro_axes = tuple(gyro_axes)
        self.accel_axes = tuple(accel_axes) if accel_axes else None
        self.gyro_scale = float(gyro_scale)
        self.accel_scale = float(accel_scale)
        self.index = index
        self.button_map = dict(button_map or DEFAULT_BUTTON_MAP)
        self.joystick = None
        self._rate = RateMeter()

    def start(self) -> bool:
        import pygame

        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() <= self.index:
            return False
        self.joystick = pygame.joystick.Joystick(self.index)
        self.joystick.init()
        needed = max(self.gyro_axes) + 1
        if self.accel_axes:
            needed = max(needed, max(self.accel_axes) + 1)
        return self.joystick.get_numaxes() >= needed

    def poll(self, dt: float) -> SprayState:
        js = self.joystick
        state = SprayState(connected=True, has_motion=True)

        gyro = [js.get_axis(a) * self.gyro_scale for a in self.gyro_axes]
        state.gyro = tuple(gyro)

        if self.accel_axes:
            state.accel = tuple(js.get_axis(a) * self.accel_scale for a in self.accel_axes)
        else:
            # Без акселерометра ориентация держится на одном гироскопе:
            # считаем, что баллончик стоит вертикально.
            state.accel = (0.0, 9.80665, 0.0)
            state.note = "акселерометр недоступен, встряхивание не распознаётся"

        raw = js.get_axis(5) if js.get_numaxes() > 5 else -1.0
        state.trigger = max(0.0, min(1.0, (raw + 1.0) * 0.5))

        count = js.get_numbuttons()
        state.buttons = {
            a: (bool(js.get_button(i)) if 0 <= i < count else False)
            for a, i in self.button_map.items()
        }
        state.buttons["spray"] = state.trigger > 0.06
        state.rate_hz = self._rate.tick()
        return state

    def rumble(self, strength: float, ms: int) -> None:
        try:
            self.joystick.rumble(strength, strength, ms)
        except Exception:
            pass
