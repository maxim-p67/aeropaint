"""Модель распыления: форма факела и нанесение следа (только numpy, без pygame)."""

from __future__ import annotations

import math
import random

import numpy as np

from .config import SprayConfig
from .shake import PaintQuality

_KERNEL_CACHE: dict = {}
GRAIN_VARIANTS = 8          # заготовок зернистости факела
MAX_STAMPS_PER_CALL = 256   # защита от зависания при резком взмахе


def _grain(size: int, seed: int) -> np.ndarray:
    """Пятнистый шум с зерном около трёх пикселей."""
    rng = np.random.default_rng(seed)
    small = rng.random(((size + 2) // 3 + 1,) * 2, dtype=np.float32)
    return np.repeat(np.repeat(small, 3, axis=0), 3, axis=1)[:size, :size]


def spray_kernel(radius: float, hardness: float, variant: int = -1) -> np.ndarray:
    """Профиль плотности факела: 1 в центре, плавно 0 на краю.

    variant >= 0 добавляет зернистую фактуру аэрозоля.
    """
    r = int(max(1, round(radius)))
    key = (r, round(float(hardness), 2), int(variant))
    if key in _KERNEL_CACHE:
        return _KERNEL_CACHE[key]

    size = 2 * r + 1
    coords = np.arange(size, dtype=np.float32) - r
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    dist = np.sqrt(xx * xx + yy * yy) / float(r)

    sigma = max(0.10, (1.0 - float(hardness)) * 0.55 + 0.13)
    kernel = np.exp(-(dist * dist) / (2.0 * sigma * sigma))
    edge = math.exp(-1.0 / (2.0 * sigma * sigma))  # на краю ровно 0, без круглой каймы
    kernel = np.clip((kernel - edge) / (1.0 - edge), 0.0, 1.0).astype(np.float32)
    if variant >= 0:
        kernel = (kernel * (0.45 + 0.55 * _grain(size, 7919 * r + variant))).astype(np.float32)
    kernel[dist >= 1.0] = 0.0

    if len(_KERNEL_CACHE) > 2000:
        _KERNEL_CACHE.clear()
    _KERNEL_CACHE[key] = kernel
    return kernel


def stamp(canvas: np.ndarray, x: float, y: float, kernel: np.ndarray, color, alpha: float) -> None:
    """Один отпечаток факела с альфа-смешиванием (canvas меняется на месте)."""
    if alpha <= 0.0:
        return
    h, w = canvas.shape[:2]
    k = kernel.shape[0]
    x0, y0 = int(round(x)) - k // 2, int(round(y)) - k // 2

    # Обрезка по границам холста.
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = k - max(0, x0 + k - w), k - max(0, y0 + k - h)
    if sx0 >= sx1 or sy0 >= sy1:
        return
    dx0, dy0 = max(0, x0), max(0, y0)

    a = np.clip(kernel[sy0:sy1, sx0:sx1] * float(alpha), 0.0, 1.0)[..., None]
    region = canvas[dy0:dy0 + sy1 - sy0, dx0:dx0 + sx1 - sx0]
    region *= 1.0 - a
    region += np.asarray(color, dtype=np.float32).reshape(1, 1, 3) * a


class SprayGun:
    def __init__(self, cfg: SprayConfig | None = None, seed: int | None = None) -> None:
        self.cfg = cfg or SprayConfig()
        self.rng = random.Random(seed)
        self._sputter_phase = 0.0
        self.dirty: tuple | None = None  # изменённый прямоугольник - для частичной перерисовки

    def mark_dirty(self, x0: float, y0: float, x1: float, y1: float) -> None:
        box = (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))
        d = self.dirty
        self.dirty = box if d is None else (min(d[0], box[0]), min(d[1], box[1]),
                                            max(d[2], box[2]), max(d[3], box[3]))

    def take_dirty(self) -> tuple | None:
        box, self.dirty = self.dirty, None
        return box

    def radius_for(self, nozzle: float, distance: float) -> float:
        """Радиус факела: сопло, увеличенное удалением от холста."""
        c = self.cfg
        return float(min(c.max_radius, max(c.min_radius,
                                           nozzle * (1.0 + c.distance_gain * max(-0.8, distance)))))

    def paint(self, canvas: np.ndarray, p0, p1, nozzle: float, distance: float,
              color, trigger: float, quality: PaintQuality, dt: float) -> int:
        """Нанести след от p0 до p1. Возвращает число отпечатков."""
        cfg = self.cfg
        if trigger <= 0.0:
            return 0
        radius = self.radius_for(nozzle, distance)

        # Недомешанная краска бледнее.
        col = np.asarray(color, dtype=np.float32)
        col = col * quality.saturation + float(col.mean()) * (1.0 - quality.saturation)

        alpha = (cfg.alpha_per_stamp * cfg.flow * float(trigger) * quality.density
                 / (1.0 + 0.85 * max(0.0, distance)))
        if quality.sputter > 0.0:  # неровная подача плохо перемешанной краски
            self._sputter_phase += dt * 17.0
            wave = 0.5 + 0.5 * math.sin(self._sputter_phase)
            alpha *= max(0.05, 1.0 - quality.sputter * (0.55 * wave + 0.45 * self.rng.random()))

        (x0, y0), (x1, y1) = p0, p1
        steps = min(MAX_STAMPS_PER_CALL,
                    int(math.hypot(x1 - x0, y1 - y0) / max(1.0, radius * cfg.spacing_ratio)))
        for i in range(steps + 1):
            t = i / steps if steps else 0.0
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            kernel = spray_kernel(radius, cfg.hardness, self.rng.randrange(GRAIN_VARIANTS))
            stamp(canvas, x, y, kernel, col, alpha)

        pad = radius * 1.2 + 6.0
        self.mark_dirty(min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad)
        return steps + 1
