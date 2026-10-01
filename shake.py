"""Встряхивание баллончика и состояние краски.

Модель повторяет поведение настоящего аэрозольного баллончика:

* пигмент оседает, поэтому перед работой баллончик надо взболтать;
* каждый взмах поднимает уровень перемешивания;
* перемешивание постепенно теряется само и расходуется при распылении;
* плохо перемешанная краска ложится бледно, неровно и плюётся каплями.

Модуль не зависит ни от pygame, ни от numpy: это чистая логика,
которую можно проверить модульными тестами.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ShakeConfig


@dataclass
class PaintQuality:
    """Как перемешанность краски влияет на распыление."""

    density: float       # множитель плотности пятна
    saturation: float    # множитель насыщенности цвета
    spit_chance: float   # вероятность выплёвывания капли за отпечаток
    sputter: float       # глубина случайных провалов подачи, 0..1
    clogged: bool        # краска почти не идёт, только плевки

    @property
    def is_clean(self) -> bool:
        return self.spit_chance <= 0.0 and self.density >= 0.999


class ShakeDetector:
    """Считает взмахи по знакопеременному ускорению вдоль оси баллончика.

    Взмахом считается смена направления движения, при которой модуль
    линейного ускорения превысил порог. Подряд идущие взмахи образуют
    серию; пауза дольше max_stroke_interval серию завершает.
    """

    def __init__(self, cfg: ShakeConfig | None = None) -> None:
        self.cfg = cfg or ShakeConfig()
        self._last_sign = 0
        self._last_stroke_t = -1e9
        self._time = 0.0
        # Взмах засчитывается только после того, как ускорение успело
        # вернуться к покою. Без этого постоянная тяга в одну сторону
        # (например, поднос баллончика к холсту) считалась бы взмахами.
        self._armed = True
        self.total_strokes = 0
        self.burst_strokes = 0

    def reset(self) -> None:
        self._last_sign = 0
        self._last_stroke_t = -1e9
        self._armed = True
        self.burst_strokes = 0

    def update(self, axis_accel: float, dt: float) -> int:
        """Обработать одну выборку. Возвращает число взмахов за вызов (0 или 1).

        axis_accel - линейное ускорение вдоль продольной оси баллончика,
        то есть показания акселерометра с вычтенной силой тяжести.
        """
        self._time += dt
        cfg = self.cfg

        since = self._time - self._last_stroke_t
        if since > cfg.max_stroke_interval:
            # Серия закончилась: следующий взмах может быть в любую сторону.
            self._last_sign = 0
            self.burst_strokes = 0

        if abs(axis_accel) < cfg.accel_threshold * 0.4:
            # Движение затухло - можно засчитывать следующий взмах.
            self._armed = True

        if abs(axis_accel) < cfg.accel_threshold:
            return 0

        sign = 1 if axis_accel > 0 else -1
        if sign == self._last_sign:
            return 0
        if since < cfg.min_stroke_interval:
            return 0
        if not self._armed:
            return 0

        self._armed = False
        self._last_sign = sign
        self._last_stroke_t = self._time
        self.total_strokes += 1
        self.burst_strokes += 1
        return 1


class SprayCan:
    """Состояние баллончика: уровень перемешивания и качество краски."""

    def __init__(self, cfg: ShakeConfig | None = None, mix: float = 0.0) -> None:
        self.cfg = cfg or ShakeConfig()
        self.detector = ShakeDetector(self.cfg)
        self.mix = mix
        self.last_stroke_at = -1e9
        self._time = 0.0

    # -- обновление состояния -------------------------------------------------

    def update_motion(self, axis_accel: float, dt: float) -> int:
        """Скормить баллончику очередную выборку акселерометра."""
        self._time += dt
        strokes = self.detector.update(axis_accel, dt)
        if strokes:
            self.mix = min(1.0, self.mix + self.cfg.mix_per_stroke * strokes)
            self.last_stroke_at = self._time
        return strokes

    def update_settling(self, dt: float, spraying: bool) -> None:
        """Учесть расслоение краски за прошедшее время."""
        decay = self.cfg.idle_decay
        if spraying:
            decay += self.cfg.spray_decay
        self.mix = max(0.0, self.mix - decay * dt)

    # -- производные величины -------------------------------------------------

    @property
    def needs_shaking(self) -> bool:
        return self.mix < self.cfg.warn_mix

    @property
    def strokes_to_good(self) -> int:
        """Сколько взмахов осталось до ровной подачи."""
        missing = self.cfg.good_mix - self.mix
        if missing <= 0:
            return 0
        return int(math.ceil(missing / self.cfg.mix_per_stroke))

    def quality(self) -> PaintQuality:
        cfg = self.cfg
        m = self.mix

        if m >= cfg.good_mix:
            return PaintQuality(1.0, 1.0, 0.0, 0.0, False)

        # Линейная доля "недомешанности" от 0 (хорошо) до 1 (совсем плохо).
        t = (cfg.good_mix - m) / cfg.good_mix
        t = min(1.0, max(0.0, t))

        density = 1.0 - 0.62 * t
        saturation = 1.0 - 0.45 * t
        spit = 0.0
        sputter = 0.0

        if m < cfg.good_mix * 0.8:
            # Чем хуже перемешана краска, тем чаще летят капли.
            spit = 0.020 + 0.085 * t * t

        if m < cfg.warn_mix:
            sputter = min(0.85, (cfg.warn_mix - m) / cfg.warn_mix)

        clogged = m < cfg.clog_mix
        if clogged:
            density *= 0.25
            spit = max(spit, 0.22)

        return PaintQuality(density, saturation, spit, sputter, clogged)


def gravity_free_axis_accel(accel, gravity) -> float:
    """Линейное ускорение вдоль продольной оси баллончика.

    accel   - сырые показания акселерометра, м/с^2;
    gravity - низкочастотная оценка направления силы тяжести.

    Продольной считается ось Y устройства: именно вдоль неё движется
    рука при встряхивании баллончика. Проекция берётся на направление
    оставшегося после вычитания тяжести линейного ускорения, что делает
    детектор нечувствительным к тому, как именно повёрнут баллончик.
    """
    lin = [accel[i] - gravity[i] for i in range(3)]

    # Ось баллончика в системе координат устройства.
    axis = (0.0, 1.0, 0.0)
    along = sum(lin[i] * axis[i] for i in range(3))

    # Если баллончик держат необычно, значимая часть движения приходится
    # на другие оси; берём величину полного линейного ускорения со знаком
    # проекции на продольную ось, чтобы не терять взмахи.
    magnitude = math.sqrt(sum(v * v for v in lin))
    if magnitude > abs(along) * 1.5:
        return math.copysign(magnitude, along if along != 0.0 else 1.0)
    return along


class GravityEstimator:
    """Низкочастотная оценка направления силы тяжести по акселерометру."""

    def __init__(self, alpha: float = 0.04) -> None:
        self.alpha = alpha
        self.value = [0.0, 0.0, 0.0]
        self._initialised = False

    def update(self, accel) -> list:
        if not self._initialised:
            self.value = list(accel)
            self._initialised = True
            return self.value
        a = self.alpha
        for i in range(3):
            self.value[i] = self.value[i] * (1.0 - a) + accel[i] * a
        return self.value
