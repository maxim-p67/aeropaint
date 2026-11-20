"""Общий вид источника движения: приложение не знает, откуда идут данные."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SprayState:
    """Мгновенное состояние баллончика."""

    connected: bool = False
    gyro: tuple = (0.0, 0.0, 0.0)    # угловые скорости, рад/с
    accel: tuple = (0.0, 0.0, 0.0)   # ускорения, м/с^2
    trigger: float = 0.0             # нажатие курка, 0..1
    buttons: dict = field(default_factory=dict)  # логическое имя -> нажата ли
    battery: str = "неизвестно"
    rate_hz: float = 0.0             # частота поступления данных
    note: str = ""                   # пояснение для строки состояния

    def pressed(self, action: str) -> bool:
        return bool(self.buttons.get(action))


class RateMeter:
    """Считает, сколько раз в секунду приходят данные (для отладки)."""

    def __init__(self, window: float = 0.5) -> None:
        self.window = window
        self._count = 0
        self._since = time.perf_counter()
        self.value = 0.0

    def tick(self) -> float:
        self._count += 1
        now = time.perf_counter()
        if now - self._since >= self.window:
            self.value = self._count / (now - self._since)
            self._count, self._since = 0, now
        return self.value


class InputBackend:
    """Источник данных. Наследники реализуют start/poll (и по желанию rumble/close)."""

    name = "базовый"
    description = ""
    applies_grip = True  # применять ли поворот хвата (мыши он не нужен)

    def start(self) -> bool:
        raise NotImplementedError

    def poll(self, dt: float) -> SprayState:
        raise NotImplementedError

    def rumble(self, strength: float, ms: int) -> None:
        pass

    def close(self) -> None:
        pass


class EdgeTracker:
    """Срабатывает один раз в момент нажатия кнопки, а не пока она удерживается."""

    def __init__(self) -> None:
        self._previous: dict = {}

    def just_pressed(self, state: SprayState, action: str) -> bool:
        now = state.pressed(action)
        was, self._previous[action] = self._previous.get(action, False), now
        return now and not was
