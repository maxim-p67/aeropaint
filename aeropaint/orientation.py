"""Ориентация геймпада и перевод её в точку на холсте.

Углы получаются интегрированием гироскопа; наклон слегка подтягивается
к направлению силы тяжести (акселерометр), когда геймпад неподвижен.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import OrientationConfig


def _wrap(angle: float) -> float:
    """Привести угол к диапазону от -pi до pi."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def rotate_grip(vec, degrees: float):
    """Повернуть показания датчика вокруг продольной оси (поворот хвата)."""
    a = math.radians(degrees % 360.0)
    c, s = math.cos(a), math.sin(a)
    x, y, z = vec
    return (x * c + y * s, -x * s + y * c, z)


@dataclass
class Pose:
    yaw: float = 0.0    # поворот вокруг вертикали - сдвиг по горизонтали
    pitch: float = 0.0  # наклон вверх-вниз - сдвиг по вертикали
    roll: float = 0.0


class CalibrationError(Exception):
    """Калибровка не удалась: геймпад двигали."""


class OrientationFilter:
    def __init__(self, cfg: OrientationConfig | None = None) -> None:
        self.cfg = cfg or OrientationConfig()
        self.pose = Pose()
        self.zero = Pose()          # положение, считающееся центром холста
        self.bias = [0.0, 0.0, 0.0]  # собственный дрейф гироскопа
        self.rate = 0.0             # текущая угловая скорость, рад/с (0 - неподвижен)
        self._samples: list | None = None  # не None - идёт калибровка

    # -- калибровка -----------------------------------------------------------

    def begin_calibration(self) -> None:
        self._samples = []

    @property
    def calibrating(self) -> bool:
        return self._samples is not None

    @property
    def calibration_progress(self) -> float:
        if not self.calibrating:
            return 1.0
        return min(1.0, len(self._samples) / self.cfg.calibration_samples)

    def _finish_calibration(self) -> None:
        samples, self._samples = self._samples, None
        mean = [sum(s[i] for s in samples) / len(samples) for i in range(3)]
        spread = max(abs(s[i] - mean[i]) for s in samples for i in range(3))
        if spread > self.cfg.calibration_tolerance:
            raise CalibrationError("геймпад двигали, повторите на неподвижном устройстве")
        self.bias = mean
        self.recenter()

    def recenter(self) -> None:
        """Текущее направление становится центром холста."""
        self.zero = Pose(self.pose.yaw, self.pose.pitch, self.pose.roll)

    # -- основной шаг ---------------------------------------------------------

    def update(self, gyro, accel, dt: float) -> Pose:
        cfg, pose = self.cfg, self.pose
        if self.calibrating:
            self._samples.append(tuple(gyro))
            if len(self._samples) >= cfg.calibration_samples:
                self._finish_calibration()
            return pose

        gx, gy, gz = (0.0 if abs(g - b) < cfg.gyro_deadzone else g - b
                      for g, b in zip(gyro, self.bias))
        pose.pitch = _wrap(pose.pitch + gx * dt)
        pose.yaw = _wrap(pose.yaw + gy * dt)
        pose.roll = _wrap(pose.roll + gz * dt)

        # Подтяжка наклона по силе тяжести - только в покое, иначе она
        # стягивала бы точку обратно к центру прямо во время рисования.
        ax, ay, az = accel
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        rate = self.rate = math.sqrt(gx * gx + gy * gy + gz * gz)
        if 8.4 < norm < 11.2 and rate < cfg.accel_still_rate:
            ax, ay, az = ax / norm, ay / norm, az / norm
            accel_pitch = math.atan2(-az, math.sqrt(ax * ax + ay * ay) or 1e-9)
            accel_roll = math.atan2(ax, ay if abs(ay) > 1e-9 else 1e-9)
            a = min(0.25, dt / max(1e-3, cfg.accel_tau))
            pose.pitch = _wrap(pose.pitch * (1.0 - a) + accel_pitch * a)
            pose.roll = _wrap(pose.roll * (1.0 - a) + accel_roll * a)
        return pose

    # -- перевод в координаты холста -----------------------------------------

    def clamp_to_canvas(self, width: int, height: int) -> bool:
        """Не давать углу уйти дальше края холста. True, если упёрлись в край."""
        k = self.cfg.px_per_rad
        at_edge = False
        for axis, half in (("yaw", width * 0.5), ("pitch", height * 0.5)):
            limit = half / k
            delta = _wrap(getattr(self.pose, axis) - getattr(self.zero, axis))
            clamped = max(-limit, min(limit, delta))
            if clamped != delta:
                setattr(self.pose, axis, _wrap(getattr(self.zero, axis) + clamped))
            at_edge = at_edge or abs(clamped) >= limit - 1e-9
        return at_edge

    def auto_center(self, dt: float, strength: float = 1.0) -> None:
        """Плавно подтянуть точку к центру (strength 0..1 - сила возврата)."""
        tau = self.cfg.auto_center_tau
        if tau <= 0.0 or strength <= 0.0:
            return
        follow = 1.0 - math.exp(-dt / tau)
        floor = self.cfg.auto_center_min_rate * dt
        for axis in ("yaw", "pitch"):
            zero = getattr(self.zero, axis)
            delta = _wrap(getattr(self.pose, axis) - zero)
            step = max(abs(delta) * follow, floor) * strength
            new = zero if abs(delta) <= step else getattr(self.pose, axis) - math.copysign(step, delta)
            setattr(self.pose, axis, _wrap(new))

    def canvas_point(self, width: int, height: int) -> tuple:
        k = self.cfg.px_per_rad
        dx = _wrap(self.pose.yaw - self.zero.yaw) * k
        dy = _wrap(self.pose.pitch - self.zero.pitch) * k
        return (width * 0.5 + dx, height * 0.5 + dy)


class DistanceEstimator:
    """Условное удаление баллончика от холста (для художественного эффекта)."""

    def __init__(self, gain: float = 0.020, hold_time: float = 2.0) -> None:
        self.gain = gain
        self.hold_time = hold_time
        self.value = 0.0
        self.velocity = 0.0
        self._still_for = 0.0

    def update(self, axis_accel: float, dt: float) -> float:
        self._still_for = self._still_for + dt if abs(axis_accel) < 0.9 else 0.0
        self.velocity = self.velocity * 0.90 + axis_accel * dt
        self.value += self.velocity * self.gain
        if self._still_for > self.hold_time:  # без движения плавно возвращается к нулю
            self.value *= 0.90
            self.velocity *= 0.5
        self.value = max(-0.85, min(1.6, self.value))
        return self.value

    def reset(self) -> None:
        self.value = self.velocity = self._still_for = 0.0
