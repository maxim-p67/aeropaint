"""Звук баллончика, синтезируемый на лету.

Звуковых файлов в проекте нет: шипение распыла и стук шарика
при встряхивании считаются numpy прямо при запуске. Если звуковое
устройство недоступно, весь модуль тихо отключается.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 44100


def _to_sound(samples: np.ndarray):
    import pygame

    data = np.clip(samples, -1.0, 1.0)
    stereo = np.ascontiguousarray(
        np.stack([data, data], axis=1) * 32767.0
    ).astype(np.int16)
    return pygame.sndarray.make_sound(stereo)


def _hiss(seconds: float = 1.0) -> np.ndarray:
    """Шипение: белый шум, приглушённый в верхних частотах."""
    rng = np.random.default_rng(4)
    noise = rng.normal(0.0, 0.35, int(SAMPLE_RATE * seconds)).astype(np.float32)

    # Простой однополюсный фильтр нижних частот убирает песок из звука.
    out = np.empty_like(noise)
    acc = 0.0
    a = 0.30
    for i in range(noise.size):
        acc = acc * (1.0 - a) + noise[i] * a
        out[i] = acc

    # Сшивка краёв, чтобы зацикленный звук не щёлкал.
    fade = int(SAMPLE_RATE * 0.02)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    out[:fade] *= ramp
    out[-fade:] *= ramp[::-1]
    return out * 2.2


def _rattle() -> np.ndarray:
    """Стук шарика внутри баллончика: короткий затухающий всплеск."""
    rng = np.random.default_rng(11)
    n = int(SAMPLE_RATE * 0.055)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    envelope = np.exp(-t * 90.0)
    body = np.sin(2.0 * np.pi * 220.0 * t) * 0.5
    click = rng.normal(0.0, 1.0, n).astype(np.float32) * 0.5
    return (body + click) * envelope * 0.7


class CanAudio:
    """Звуковое сопровождение. При любой ошибке просто молчит."""

    def __init__(self) -> None:
        self.enabled = False
        self._hiss_channel = None
        self._rattle = None
        self._hiss_sound = None

    def start(self) -> bool:
        try:
            import pygame

            pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
            pygame.mixer.init(SAMPLE_RATE, -16, 2, 512)
            self._hiss_sound = _to_sound(_hiss())
            self._rattle = _to_sound(_rattle())
            self._hiss_channel = pygame.mixer.Channel(0)
            self.enabled = True
        except Exception:
            self.enabled = False
        return self.enabled

    def spray(self, intensity: float) -> None:
        """Поддерживать шипение с громкостью по нажатию курка."""
        if not self.enabled:
            return
        try:
            if intensity <= 0.02:
                if self._hiss_channel.get_busy():
                    self._hiss_channel.stop()
                return
            if not self._hiss_channel.get_busy():
                self._hiss_channel.play(self._hiss_sound, loops=-1)
            self._hiss_channel.set_volume(min(1.0, 0.15 + 0.6 * intensity))
        except Exception:
            self.enabled = False

    def rattle(self) -> None:
        if not self.enabled:
            return
        try:
            self._rattle.play()
        except Exception:
            self.enabled = False

    def stop(self) -> None:
        if not self.enabled:
            return
        try:
            import pygame

            pygame.mixer.stop()
        except Exception:
            pass
