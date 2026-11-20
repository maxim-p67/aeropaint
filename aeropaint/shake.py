"""Встряхивание баллончика и состояние краски (чистая логика, без pygame)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ShakeConfig


@dataclass
class PaintQuality:
    """Как перемешанность краски влияет на распыление."""

    density: float      # множитель плотности пятна
    saturation: float   # множитель насыщенности
    sputter: float      # глубина случайных провалов подачи
    clogged: bool       # краска почти не идёт

    @property
    def is_clean(self) -> bool:
        return self.density >= 0.999


class ShakeDetector:
    """Считает взмахи: смена направления ускорения, превысившего порог."""

    def __init__(self, cfg: ShakeConfig | None = None) -> None:
        self.cfg = cfg or ShakeConfig()
        self._sign = 0
        self._last_t = -1e9
        self._time = 0.0
        self._armed = True   # ускорение должно успокоиться между взмахами
        self.total_strokes = 0

    def update(self, accel: float, dt: float) -> int:
        """Одна выборка. Возвращает 1, если засчитан взмах, иначе 0."""
        cfg = self.cfg
        self._time += dt
        since = self._time - self._last_t
        if since > cfg.max_stroke_interval:
            self._sign = 0  # серия закончилась, направление снова любое
        if abs(accel) < cfg.accel_threshold * 0.4:
            self._armed = True
        if abs(accel) < cfg.accel_threshold:
            return 0
        sign = 1 if accel > 0 else -1
        if sign == self._sign or since < cfg.min_stroke_interval or not self._armed:
            return 0
        self._armed, self._sign, self._last_t = False, sign, self._time
        self.total_strokes += 1
        return 1


class SprayCan:
    """Уровень перемешивания краски и вытекающее из него качество."""

    def __init__(self, cfg: ShakeConfig | None = None, mix: float = 0.0) -> None:
        self.cfg = cfg or ShakeConfig()
        self.detector = ShakeDetector(self.cfg)
        self.mix = mix

    def update_motion(self, accel: float, dt: float) -> int:
        strokes = self.detector.update(accel, dt)
        self.mix = min(1.0, self.mix + self.cfg.mix_per_stroke * strokes)
        return strokes

    def update_settling(self, dt: float, spraying: bool) -> None:
        decay = self.cfg.idle_decay + (self.cfg.spray_decay if spraying else 0.0)
        self.mix = max(0.0, self.mix - decay * dt)

    @property
    def needs_shaking(self) -> bool:
        return self.mix < self.cfg.warn_mix

    @property
    def strokes_to_good(self) -> int:
        return max(0, math.ceil((self.cfg.good_mix - self.mix) / self.cfg.mix_per_stroke))

    def quality(self) -> PaintQuality:
        c, m = self.cfg, self.mix
        if m >= c.good_mix:
            return PaintQuality(1.0, 1.0, 0.0, False)
        t = min(1.0, max(0.0, (c.good_mix - m) / c.good_mix))  # 0 - хорошо, 1 - совсем плохо
        density = 1.0 - 0.62 * t
        sputter = min(0.85, (c.warn_mix - m) / c.warn_mix) if m < c.warn_mix else 0.0
        clogged = m < c.clog_mix
        if clogged:
            density *= 0.25
        return PaintQuality(density, 1.0 - 0.45 * t, sputter, clogged)


def gravity_free_axis_accel(accel, gravity) -> float:
    """Линейное ускорение вдоль оси баллончика (Y): показания минус тяжесть.

    Если движение идёт в основном по другим осям, берётся полная величина
    ускорения со знаком проекции на Y, чтобы не терять взмахи.
    """
    lin = [a - g for a, g in zip(accel, gravity)]
    along = lin[1]
    magnitude = math.sqrt(sum(v * v for v in lin))
    if magnitude > abs(along) * 1.5:
        return math.copysign(magnitude, along if along != 0.0 else 1.0)
    return along


class GravityEstimator:
    """Медленно усреднённое направление силы тяжести."""

    def __init__(self, alpha: float = 0.04) -> None:
        self.alpha = alpha
        self.value: list | None = None

    def update(self, accel) -> list:
        if self.value is None:
            self.value = list(accel)
        else:
            self.value = [v + (a - v) * self.alpha for v, a in zip(self.value, accel)]
        return self.value
