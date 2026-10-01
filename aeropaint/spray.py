"""Модель распыления: форма факела, накопление краски, плевки и подтёки.

Работает поверх массивов numpy и не зависит от pygame, поэтому результат
можно проверить, сохранив холст в файл без запуска графической оболочки.
"""

from __future__ import annotations

import math
import random

import numpy as np

from .config import SprayConfig
from .shake import PaintQuality


_KERNEL_CACHE: dict = {}

# Число заранее просчитанных вариантов зернистости факела.
# Случайный выбор варианта на каждый отпечаток даёт характерную
# для аэрозоля крупинчатую фактуру, не считая шум в реальном времени.
GRAIN_VARIANTS = 8

# Предел числа отпечатков за один вызов paint.
MAX_STAMPS_PER_CALL = 256


def _grain(size: int, seed: int) -> np.ndarray:
    """Пятнистый шум с зерном примерно в три пикселя."""
    rng = np.random.default_rng(seed)
    small = rng.random(((size + 2) // 3 + 1, (size + 2) // 3 + 1), dtype=np.float32)
    big = np.repeat(np.repeat(small, 3, axis=0), 3, axis=1)[:size, :size]
    return big


def spray_kernel(radius: float, hardness: float, variant: int = -1) -> np.ndarray:
    """Профиль плотности факела: в центре 1, к краю плавно сходит к 0.

    variant = -1 даёт гладкий профиль, значения 0..GRAIN_VARIANTS-1 -
    тот же профиль с наложенной фактурой распыла.
    """
    r = int(max(1, round(radius)))
    key = (r, round(float(hardness), 2), int(variant))
    cached = _KERNEL_CACHE.get(key)
    if cached is not None:
        return cached

    size = 2 * r + 1
    coords = np.arange(size, dtype=np.float32) - r
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    dist = np.sqrt(xx * xx + yy * yy) / float(r)

    sigma = max(0.10, (1.0 - float(hardness)) * 0.55 + 0.13)
    kernel = np.exp(-(dist * dist) / (2.0 * sigma * sigma))

    # Гауссиана на границе факела обрывается на заметной величине, и голое
    # обрезание по радиусу дало бы круглую кайму. Смещаем и нормируем профиль
    # так, чтобы в центре он равнялся единице, а на радиусе ровно нулю.
    edge = math.exp(-1.0 / (2.0 * sigma * sigma))
    kernel = (kernel - edge) / (1.0 - edge)
    kernel = np.clip(kernel, 0.0, 1.0).astype(np.float32)

    if variant >= 0:
        # Фактура распыла: часть капель не долетает, часть ложится гуще.
        kernel = kernel * (0.45 + 0.55 * _grain(size, 7919 * r + variant))

    kernel = kernel.astype(np.float32)
    kernel[dist >= 1.0] = 0.0

    if len(_KERNEL_CACHE) > 2000:
        _KERNEL_CACHE.clear()
    _KERNEL_CACHE[key] = kernel
    return kernel


def stamp(
    canvas: np.ndarray,
    wet: np.ndarray,
    x: float,
    y: float,
    kernel: np.ndarray,
    color,
    alpha: float,
) -> None:
    """Нанести один отпечаток факела с альфа-смешиванием.

    canvas - массив (H, W, 3) значений 0..1, изменяется на месте;
    wet    - массив (H, W) накопленной "влажности", нужен для подтёков.
    """
    if alpha <= 0.0:
        return

    h, w = canvas.shape[:2]
    k = kernel.shape[0]
    r = k // 2

    x0 = int(round(x)) - r
    y0 = int(round(y)) - r
    x1 = x0 + k
    y1 = y0 + k

    # Отсечение по границам холста.
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    sx1 = k - max(0, x1 - w)
    sy1 = k - max(0, y1 - h)
    if sx0 >= sx1 or sy0 >= sy1:
        return

    dx0 = max(0, x0)
    dy0 = max(0, y0)
    dx1 = dx0 + (sx1 - sx0)
    dy1 = dy0 + (sy1 - sy0)

    sub = kernel[sy0:sy1, sx0:sx1]
    a = np.clip(sub * float(alpha), 0.0, 1.0)[..., None]

    region = canvas[dy0:dy1, dx0:dx1]
    col = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    region *= (1.0 - a)
    region += col * a

    wet[dy0:dy1, dx0:dx1] += sub * float(alpha)


class SprayGun:
    """Наносит непрерывный след и ведёт систему подтёков."""

    def __init__(self, cfg: SprayConfig | None = None, seed: int | None = None) -> None:
        self.cfg = cfg or SprayConfig()
        self.rng = random.Random(seed)
        self.drips: list = []
        self._sputter_phase = 0.0
        # Прямоугольник изменившейся области: оболочка обновляет на экране
        # только его, а не весь холст.
        self.dirty: tuple | None = None

    def mark_dirty(self, x0: float, y0: float, x1: float, y1: float) -> None:
        box = (int(math.floor(x0)), int(math.floor(y0)),
               int(math.ceil(x1)), int(math.ceil(y1)))
        if self.dirty is None:
            self.dirty = box
        else:
            a = self.dirty
            self.dirty = (min(a[0], box[0]), min(a[1], box[1]),
                          max(a[2], box[2]), max(a[3], box[3]))

    def take_dirty(self) -> tuple | None:
        box = self.dirty
        self.dirty = None
        return box

    # -- вспомогательное ------------------------------------------------------

    def radius_for(self, nozzle: float, distance: float) -> float:
        """Радиус факела с учётом сопла и удаления от холста."""
        cfg = self.cfg
        r = nozzle * (1.0 + cfg.distance_gain * max(-0.8, distance))
        return float(min(cfg.max_radius, max(cfg.min_radius, r)))

    def _apply_quality(self, color, quality: PaintQuality):
        """Недомешанная краска бледнее: пигмент не поднят со дна."""
        col = np.asarray(color, dtype=np.float32)
        if quality.saturation >= 0.999:
            return col
        grey = float(col.mean())
        return col * quality.saturation + grey * (1.0 - quality.saturation)

    # -- основной вызов -------------------------------------------------------

    def paint(
        self,
        canvas: np.ndarray,
        wet: np.ndarray,
        p0,
        p1,
        nozzle: float,
        distance: float,
        color,
        trigger: float,
        quality: PaintQuality,
        dt: float,
    ) -> int:
        """Нанести след от точки p0 до точки p1.

        Возвращает число поставленных отпечатков - используется в отладке.
        """
        cfg = self.cfg
        if trigger <= 0.0:
            return 0

        radius = self.radius_for(nozzle, distance)
        col = self._apply_quality(color, quality)

        # Чем дальше баллончик, тем более разрежённым становится напыление.
        distance_falloff = 1.0 / (1.0 + 0.85 * max(0.0, distance))

        base_alpha = (
            cfg.alpha_per_stamp
            * cfg.flow
            * float(trigger)
            * quality.density
            * distance_falloff
        )

        # Неровная подача плохо перемешанной краски.
        if quality.sputter > 0.0:
            self._sputter_phase += dt * 17.0
            wave = 0.5 + 0.5 * math.sin(self._sputter_phase)
            noise = self.rng.uniform(0.0, 1.0)
            dip = quality.sputter * (0.55 * wave + 0.45 * noise)
            base_alpha *= max(0.05, 1.0 - dip)

        x0, y0 = p0
        x1, y1 = p1
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)

        spacing = max(1.0, radius * cfg.spacing_ratio)
        steps = int(length / spacing)

        # Страховка от зависания: при очень резком движении точка нанесения
        # может уйти на тысячи пикселей за кадр. Ограничиваем число
        # отпечатков, жертвуя плотностью следа, но не частотой кадров.
        if steps > MAX_STAMPS_PER_CALL:
            steps = MAX_STAMPS_PER_CALL

        stamps = 0

        for i in range(steps + 1):
            t = 0.0 if steps == 0 else i / float(steps)
            x = x0 + dx * t
            y = y0 + dy * t
            kernel = spray_kernel(
                radius, cfg.hardness, self.rng.randrange(GRAIN_VARIANTS)
            )
            stamp(canvas, wet, x, y, kernel, col, base_alpha)
            stamps += 1

            if quality.spit_chance > 0.0 and self.rng.random() < quality.spit_chance:
                self._spit(canvas, wet, x, y, radius, col)

        # Плевки летят в стороне от оси, поэтому область берётся с запасом.
        pad = radius * 1.2 + 6.0
        self.mark_dirty(min(x0, x1) - pad, min(y0, y1) - pad,
                        max(x0, x1) + pad, max(y0, y1) + pad)

        self._maybe_drip(canvas, wet, p1, radius, col)
        return stamps

    def _spit(self, canvas, wet, x, y, radius, color) -> None:
        """Плевок: плотная капля непромешанной краски рядом с осью факела.

        Капля не бледнеет вместе с остальной краской: осевший пигмент как раз
        и вылетает такими сгустками, поэтому цвет берётся насыщенным.
        """
        ang = self.rng.uniform(0.0, 2.0 * math.pi)
        dist = self.rng.uniform(0.0, radius * 0.8)
        bx = x + math.cos(ang) * dist
        by = y + math.sin(ang) * dist
        br = max(3.0, radius * self.rng.uniform(0.14, 0.34))
        blob = spray_kernel(br, 0.88)
        stamp(canvas, wet, bx, by, blob, color, self.rng.uniform(0.7, 1.0))
        # Сгусток сразу делает это место влажным - отсюда часто бегут подтёки.
        wet[max(0, int(by) - 2): int(by) + 3, max(0, int(bx) - 2): int(bx) + 3] += 1.2

    # -- подтёки --------------------------------------------------------------

    def _maybe_drip(self, canvas, wet, point, radius, color) -> None:
        cfg = self.cfg
        if len(self.drips) >= cfg.max_drips:
            return
        x, y = int(point[0]), int(point[1])
        h, w = wet.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return
        if wet[y, x] < cfg.drip_threshold:
            return
        if self.rng.random() > cfg.drip_chance:
            return

        self.drips.append(
            {
                "x": float(x) + self.rng.uniform(-radius * 0.3, radius * 0.3),
                "y": float(y),
                "vy": self.rng.uniform(6.0, 16.0),
                "r": max(3.5, radius * self.rng.uniform(0.15, 0.28)),
                "life": self.rng.uniform(1.2, 3.0),
                "color": np.asarray(color, dtype=np.float32).copy(),
            }
        )
        wet[max(0, y - 4): y + 4, max(0, x - 4): x + 4] *= 0.5

    def update_drips(self, canvas: np.ndarray, wet: np.ndarray, dt: float) -> None:
        if not self.drips:
            return
        h, w = canvas.shape[:2]
        alive = []
        for d in self.drips:
            start_y = d["y"]
            d["life"] -= dt
            if d["life"] <= 0.0:
                continue
            d["vy"] = min(170.0, d["vy"] + 85.0 * dt)
            step = d["vy"] * dt
            kernel = spray_kernel(d["r"], 0.55)
            # Отпечатки ставятся чаще радиуса, иначе след получается пунктирным.
            passes = max(1, int(math.ceil(step / max(0.8, d["r"] * 0.35))))
            for _ in range(passes):
                d["y"] += step / passes
                if d["y"] >= h:
                    break
                stamp(canvas, wet, d["x"], d["y"], kernel, d["color"], 0.45)
            # Капля постепенно истощается и сужается.
            d["r"] *= 0.985
            if d["y"] < h and d["r"] > 1.2:
                alive.append(d)
            elif d["y"] < h:
                # В конце пути краска собирается в каплю на нижнем крае следа.
                bead = spray_kernel(max(3.0, d["r"] * 2.2), 0.6)
                stamp(canvas, wet, d["x"], d["y"], bead, d["color"], 0.55)

            pad = d["r"] * 2.6 + 4.0
            self.mark_dirty(d["x"] - pad, start_y - pad, d["x"] + pad, d["y"] + pad)
        self.drips = alive

    def clear_drips(self) -> None:
        self.drips = []


def dry(wet: np.ndarray, dt: float, rate: float = 0.55) -> None:
    """Краска подсыхает: накопленная влажность убывает."""
    wet *= max(0.0, 1.0 - rate * dt)
