"""Общий интерфейс источников данных о движении баллончика.

Приложение работает только с этим интерфейсом и ничего не знает о том,
откуда взялись показания: от SDL, из сырых HID-отчётов или от мыши.
Это позволяет подменять источник, не трогая логику рисования.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# Логические имена органов управления. Физические кнопки отображаются
# на них в mapping.json, поэтому раскладку можно поменять без правки кода.
ACTIONS = (
    "spray",       # подача краски (аналоговый курок)
    "palette",     # палитра цветов
    "undo",
    "redo",
    "size_up",
    "size_down",
    "calibrate",
    "save",
    "debug",
)


@dataclass
class SprayState:
    """Мгновенное состояние баллончика."""

    connected: bool = False
    has_motion: bool = False          # доступны ли показания датчиков

    gyro: tuple = (0.0, 0.0, 0.0)     # угловые скорости, рад/с
    accel: tuple = (0.0, 0.0, 0.0)    # линейные ускорения, м/с^2

    trigger: float = 0.0              # степень нажатия курка, 0..1
    buttons: dict = field(default_factory=dict)   # логическое имя -> нажата

    battery: str = "неизвестно"
    rate_hz: float = 0.0              # фактическая частота поступления данных
    note: str = ""                    # пояснение для строки состояния

    def pressed(self, action: str) -> bool:
        return bool(self.buttons.get(action))


class RateMeter:
    """Измеритель частоты поступления отчётов для отладочного режима."""

    def __init__(self, window: float = 0.5) -> None:
        self.window = window
        self._count = 0
        self._since = time.perf_counter()
        self.value = 0.0

    def tick(self, n: int = 1) -> float:
        self._count += n
        now = time.perf_counter()
        elapsed = now - self._since
        if elapsed >= self.window:
            self.value = self._count / elapsed
            self._count = 0
            self._since = now
        return self.value


class InputBackend:
    """Базовый источник данных."""

    name = "базовый"
    description = ""

    # Применять ли к показаниям поворот хвата. Для настоящих датчиков да,
    # для эмуляции мышью нет: мышь и так движется в системе координат экрана.
    applies_grip = True

    def start(self) -> bool:
        """Подготовить источник. False означает, что источник недоступен."""
        raise NotImplementedError

    def poll(self, dt: float) -> SprayState:
        raise NotImplementedError

    def rumble(self, strength: float, ms: int) -> None:
        """Короткая отдача вибромотора. Источник вправе её не поддерживать."""

    def close(self) -> None:
        pass


class EdgeTracker:
    """Отслеживает моменты нажатия, чтобы действие срабатывало один раз."""

    def __init__(self) -> None:
        self._previous: dict = {}

    def just_pressed(self, state: SprayState, action: str) -> bool:
        now = state.pressed(action)
        was = self._previous.get(action, False)
        self._previous[action] = now
        return now and not was
