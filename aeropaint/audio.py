"""Звук баллончика, синтезируемый при запуске (звуковых файлов нет).

Если звуковое устройство недоступно, модуль молча отключается.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 44100


def _to_sound(samples: np.ndarray):
    import pygame

    data = np.clip(samples, -1.0, 1.0)
    stereo = np.ascontiguousarray(np.stack([data, data], axis=1) * 32767.0).astype(np.int16)
    return pygame.sndarray.make_sound(stereo)


def _hiss(seconds: float = 1.0) -> np.ndarray:
    """Шипение: белый шум, пропущенный через фильтр нижних частот."""
    noise = np.random.default_rng(4).normal(0.0, 0.35, int(SAMPLE_RATE * seconds)).astype(np.float32)
    out = np.empty_like(noise)
    acc = 0.0
    for i, v in enumerate(noise):
        acc = acc * 0.70 + v * 0.30
        out[i] = acc
    fade = int(SAMPLE_RATE * 0.02)  # плавные края, чтобы зацикленный звук не щёлкал
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    out[:fade] *= ramp
    out[-fade:] *= ramp[::-1]
    return out * 2.2


def _rattle() -> np.ndarray:
    """Стук шарика: короткий затухающий всплеск."""
    n = int(SAMPLE_RATE * 0.055)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    click = np.random.default_rng(11).normal(0.0, 1.0, n).astype(np.float32) * 0.5
    return (np.sin(2.0 * np.pi * 220.0 * t) * 0.5 + click) * np.exp(-t * 90.0) * 0.7


class CanAudio:
    def __init__(self) -> None:
        self.enabled = False
        self._channel = None
        self._hiss = None
        self._rattle = None

    def start(self) -> bool:
        try:
            import pygame

            pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
            pygame.mixer.init(SAMPLE_RATE, -16, 2, 512)
            self._hiss, self._rattle = _to_sound(_hiss()), _to_sound(_rattle())
            self._channel = pygame.mixer.Channel(0)
            self.enabled = True
        except Exception:
            self.enabled = False
        return self.enabled

    def spray(self, intensity: float) -> None:
        """Шипение, громкость зависит от нажатия курка."""
        if not self.enabled:
            return
        try:
            if intensity <= 0.02:
                self._channel.stop()
                return
            if not self._channel.get_busy():
                self._channel.play(self._hiss, loops=-1)
            self._channel.set_volume(min(1.0, 0.15 + 0.6 * intensity))
        except Exception:
            self.enabled = False

    def rattle(self) -> None:
        if self.enabled:
            try:
                self._rattle.play()
            except Exception:
                self.enabled = False

    def stop(self) -> None:
        if self.enabled:
            try:
                import pygame

                pygame.mixer.stop()
            except Exception:
                pass
