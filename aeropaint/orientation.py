"""Оценка ориентации баллончика и перевод её в точку на холсте.

Используется комплементарный фильтр: углы получают интегрированием
показаний гироскопа, а крен и тангаж медленно подтягиваются к направлению
силы тяжести по акселерометру. Угол поворота вокруг вертикали (рыскание)
по акселерометру не наблюдаем, поэтому его дрейф снимается калибровкой.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import OrientationConfig


def _wrap(angle: float) -> float:
    """Привести угол к диапазону от -pi до pi."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def rotate_grip(vec, degrees: float):
    """Повернуть показания датчика вокруг продольной оси устройства.

    Нужно, когда геймпад держат повёрнутым в руке, как баллончик.
    Поворот применяется сразу к сырым показаниям гироскопа и
    акселерометра, поэтому вся дальнейшая обработка — оценка ориентации,
    коррекция по силе тяжести, детектор встряхивания и перевод в
    координаты холста — работает так же, как при обычном хвате,
    и отдельных поправок не требует.

    Угол 90 градусов означает, что поворот геймпада вправо уводит след
    вверх, вверх - влево, влево - вниз, вниз - вправо.
    """
    deg = degrees % 360.0
    if deg == 0.0:
        return (vec[0], vec[1], vec[2])
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = vec
    return (x * c + y * s, -x * s + y * c, z)


@dataclass
class Pose:
    yaw: float = 0.0     # поворот вокруг вертикали: смещение следа по горизонтали
    pitch: float = 0.0   # наклон вверх-вниз: смещение следа по вертикали
    roll: float = 0.0    # поворот вокруг оси баллончика


class CalibrationError(Exception):
    """Калибровка не удалась: баллончик двигали."""


class OrientationFilter:
    def __init__(self, cfg: OrientationConfig | None = None) -> None:
        self.cfg = cfg or OrientationConfig()
        self.pose = Pose()
        self.bias = [0.0, 0.0, 0.0]
        self.zero = Pose()
        self._calibrating = False
        self._samples: list = []
        self.correcting = False

    # -- калибровка -----------------------------------------------------------

    def begin_calibration(self) -> None:
        self._calibrating = True
        self._samples = []

    @property
    def calibrating(self) -> bool:
        return self._calibrating

    @property
    def calibration_progress(self) -> float:
        if not self._calibrating:
            return 1.0
        return min(1.0, len(self._samples) / float(self.cfg.calibration_samples))

    def _finish_calibration(self) -> None:
        n = len(self._samples)
        mean = [sum(s[i] for s in self._samples) / n for i in range(3)]
        spread = max(
            max(abs(s[i] - mean[i]) for s in self._samples) for i in range(3)
        )
        self._calibrating = False
        self._samples = []
        if spread > self.cfg.calibration_tolerance:
            raise CalibrationError(
                "во время калибровки баллончик двигали, повторите на неподвижном устройстве"
            )
        self.bias = mean
        # Текущее положение объявляется нулевым.
        self.zero = Pose(self.pose.yaw, self.pose.pitch, self.pose.roll)

    def recenter(self) -> None:
        """Быстрый сброс: текущее направление считается центром холста."""
        self.zero = Pose(self.pose.yaw, self.pose.pitch, self.pose.roll)

    # -- основной шаг ---------------------------------------------------------

    def update(self, gyro, accel, dt: float) -> Pose:
        cfg = self.cfg

        if self._calibrating:
            self._samples.append(tuple(gyro))
            if len(self._samples) >= cfg.calibration_samples:
                self._finish_calibration()
            return self.pose

        gx = gyro[0] - self.bias[0]
        gy = gyro[1] - self.bias[1]
        gz = gyro[2] - self.bias[2]

        dz = cfg.gyro_deadzone
        gx = 0.0 if abs(gx) < dz else gx
        gy = 0.0 if abs(gy) < dz else gy
        gz = 0.0 if abs(gz) < dz else gz

        # Оси SDL для геймпада: X - тангаж, Y - рыскание, Z - крен.
        self.pose.pitch = _wrap(self.pose.pitch + gx * dt)
        self.pose.yaw = _wrap(self.pose.yaw + gy * dt)
        self.pose.roll = _wrap(self.pose.roll + gz * dt)

        # Коррекция наклона по силе тяжести. Включается только когда
        # устройство почти неподвижно и модуль ускорения близок к g:
        # во время ведения она тянула бы вертикаль назад к центру, а при
        # резких движениях акселерометр всё равно измеряет не тяжесть.
        ax, ay, az = accel
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        rate = math.sqrt(gx * gx + gy * gy + gz * gz)

        self.correcting = (8.4 < norm < 11.2) and (rate < cfg.accel_still_rate)
        if self.correcting:
            ax, ay, az = ax / norm, ay / norm, az / norm
            accel_pitch = math.atan2(-az, math.sqrt(ax * ax + ay * ay) or 1e-9)
            accel_roll = math.atan2(ax, ay if abs(ay) > 1e-9 else 1e-9)
            # Доля подтягивания за шаг не зависит от частоты опроса.
            a = min(0.25, dt / max(1e-3, cfg.accel_tau))
            self.pose.pitch = _wrap(self.pose.pitch * (1.0 - a) + accel_pitch * a)
            self.pose.roll = _wrap(self.pose.roll * (1.0 - a) + accel_roll * a)

        return self.pose

    # -- перевод в координаты холста -----------------------------------------

    def clamp_to_canvas(self, width: int, height: int) -> bool:
        """Удержать накопленный угол в пределах холста.

        Без этого угол растёт неограниченно: повернув баллончик дальше края,
        пользователь уводит точку за экран и, чтобы вернуть её, вынужден
        отматывать ровно столько же назад. Хуже того, перевалив за половину
        оборота, угол приводится к противоположному знаку, и точка
        перепрыгивает на другую сторону холста.

        Ограничение накладывается на сам угол, а не на выводимую точку,
        поэтому обратное движение возвращает курсор сразу же.
        Возвращает True, если упёрлись в край.
        """
        k = self.cfg.px_per_rad
        max_yaw = (width * 0.5) / k
        max_pitch = (height * 0.5) / k

        dyaw = _wrap(self.pose.yaw - self.zero.yaw)
        dpitch = _wrap(self.pose.pitch - self.zero.pitch)

        clamped_yaw = max(-max_yaw, min(max_yaw, dyaw))
        clamped_pitch = max(-max_pitch, min(max_pitch, dpitch))

        if clamped_yaw != dyaw:
            self.pose.yaw = _wrap(self.zero.yaw + clamped_yaw)
        if clamped_pitch != dpitch:
            self.pose.pitch = _wrap(self.zero.pitch + clamped_pitch)

        # Упор - это состояние, а не событие: пока точка стоит на краю,
        # признак держится, даже если в этом кадре угол не менялся.
        eps = 1e-9
        return (abs(clamped_yaw) >= max_yaw - eps
                or abs(clamped_pitch) >= max_pitch - eps)

    def auto_center(self, dt: float) -> None:
        """Медленно стягивать точку к центру, съедая накопленный дрейф.

        Применяется только когда краска не идёт, поэтому на сам мазок
        не влияет. Скорость подобрана заметно ниже любого осознанного
        движения руки.
        """
        rate = self.cfg.auto_center_rate
        if rate <= 0.0:
            return
        step = rate * dt
        for axis in ("yaw", "pitch"):
            delta = _wrap(getattr(self.pose, axis) - getattr(self.zero, axis))
            if abs(delta) <= step:
                new = getattr(self.zero, axis)
            else:
                new = getattr(self.pose, axis) - math.copysign(step, delta)
            setattr(self.pose, axis, _wrap(new))

    def canvas_point(self, width: int, height: int) -> tuple:
        """Точка нанесения в пикселях холста.

        Возвращается без ограничения границами: выход за холст обрабатывает
        вызывающий код, чтобы связь движения и положения точки не терялась.
        """
        k = self.cfg.px_per_rad
        dx = _wrap(self.pose.yaw - self.zero.yaw) * k
        dy = _wrap(self.pose.pitch - self.zero.pitch) * k
        return (width * 0.5 + dx, height * 0.5 + dy)


class DistanceEstimator:
    """Условное удаление баллончика от холста.

    Двойное интегрирование ускорения быстро накапливает ошибку, поэтому
    скорость непрерывно затухает, а само удаление возвращается к нулю,
    если продольного движения нет дольше hold_time. Такой оценки хватает
    для художественного эффекта и не хватило бы для измерений.
    """

    def __init__(self, gain: float = 0.020, hold_time: float = 2.0) -> None:
        self.gain = gain
        self.hold_time = hold_time
        self.value = 0.0
        self.velocity = 0.0
        self._still_for = 0.0

    def update(self, axis_accel: float, dt: float) -> float:
        if abs(axis_accel) < 0.9:
            self._still_for += dt
        else:
            self._still_for = 0.0

        self.velocity = self.velocity * 0.90 + axis_accel * dt
        self.value += self.velocity * self.gain

        if self._still_for > self.hold_time:
            # Плавный возврат к исходному удалению.
            self.value *= 0.90
            self.velocity *= 0.5

        self.value = max(-0.85, min(1.6, self.value))
        return self.value

    def reset(self) -> None:
        self.value = 0.0
        self.velocity = 0.0
        self._still_for = 0.0
