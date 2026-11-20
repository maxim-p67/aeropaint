"""Запасной ввод мышью: движение мыши имитирует гироскоп и акселерометр."""

from __future__ import annotations

import math

from .base import InputBackend, RateMeter, SprayState

G = 9.80665
ACCEL_SCALE = 1.4e-4  # перевод изменения скорости курсора в ускорение "баллончика"


class MouseBackend(InputBackend):
    name = "мышь"
    description = "движение мыши вместо датчиков баллончика"
    applies_grip = False  # мышь и так движется в координатах экрана

    def __init__(self, px_per_rad: float = 900.0) -> None:
        self.px_per_rad = float(px_per_rad)
        self._prev_speed = 0.0
        self._rate = RateMeter()

    def start(self) -> bool:
        import pygame

        if not pygame.get_init():
            pygame.init()
        return True

    def poll(self, dt: float) -> SprayState:
        import pygame

        dt = max(1e-4, dt)
        dx, dy = pygame.mouse.get_rel()
        state = SprayState(connected=True,
                           note="режим мыши: потрясите мышью, чтобы взболтать краску")
        state.gyro = (dy / self.px_per_rad / dt, dx / self.px_per_rad / dt, 0.0)

        # Скорость со знаком по горизонтали: при встряхивании знак меняется,
        # и детектор видит те же переходы, что от настоящего акселерометра.
        speed = math.hypot(dx, dy) / dt
        signed = speed if dx >= 0 else -speed
        a_long = max(-60.0, min(60.0, (signed - self._prev_speed) / dt * ACCEL_SCALE))
        self._prev_speed = signed
        state.accel = (0.0, G + a_long, 0.0)

        pressed = pygame.mouse.get_pressed(num_buttons=3)[0]
        state.trigger = 1.0 if pressed else 0.0
        state.buttons = {"spray": pressed}
        state.battery = "не применимо"
        state.rate_hz = self._rate.tick()
        return state
