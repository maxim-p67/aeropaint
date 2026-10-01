"""Резервный ввод мышью.

Источник не просто двигает курсор: он синтезирует из движения мыши такие
же угловые скорости и ускорения, какие дал бы гироскоп, поэтому дальше
работает ровно тот же конвейер, включая детектор встряхивания. Чтобы
взболтать краску без геймпада, достаточно быстро подвигать мышью
вправо-влево.

Режим нужен для отладки и для работы, когда геймпад недоступен
(требование StRS-13).
"""

from __future__ import annotations

import math

from .base import InputBackend, RateMeter, SprayState

G = 9.80665

# Перевод изменения скорости курсора в ускорение "баллончика".
# Подобран так, чтобы энергичное потряхивание мышью давало около 15 м/с^2
# при пороге детектора в 6 м/с^2.
ACCEL_SCALE = 1.4e-4


class MouseBackend(InputBackend):
    name = "мышь"
    description = "движение мыши вместо датчиков баллончика"

    # Мышь движется сразу в координатах экрана, поворот хвата к ней
    # не применяется - иначе курсор поехал бы боком.
    applies_grip = False

    def __init__(self, px_per_rad: float = 900.0) -> None:
        self.px_per_rad = float(px_per_rad)
        self._prev_signed_speed = 0.0
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

        state = SprayState(connected=True, has_motion=True)
        state.note = "режим мыши: потрясите мышью, чтобы взболтать краску"

        # Смещение курсора трактуется как поворот баллончика.
        state.gyro = (
            dy / self.px_per_rad / dt,    # тангаж
            dx / self.px_per_rad / dt,    # рыскание
            0.0,
        )

        # Скорость со знаком горизонтального направления: смена знака
        # при встряхивании даёт детектору те же переходы, что и настоящий
        # акселерометр.
        speed = math.hypot(dx, dy) / dt
        signed = speed if dx >= 0 else -speed
        a_long = (signed - self._prev_signed_speed) / dt * ACCEL_SCALE
        self._prev_signed_speed = signed
        a_long = max(-60.0, min(60.0, a_long))

        state.accel = (0.0, G + a_long, 0.0)

        buttons_down = pygame.mouse.get_pressed(num_buttons=3)
        state.trigger = 1.0 if buttons_down[0] else 0.0
        state.buttons = {"spray": buttons_down[0]}
        state.battery = "не применимо"
        state.rate_hz = self._rate.tick()
        return state

    def rumble(self, strength: float, ms: int) -> None:
        pass
