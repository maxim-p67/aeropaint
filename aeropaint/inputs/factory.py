"""Выбор источника ввода: геймпад через SDL, а если не вышло - мышь."""

from __future__ import annotations

from .base import InputBackend
from .mouse import MouseBackend
from .sdl_sensor import SdlSensorBackend


def create_backend(preferred: str | None = None, px_per_rad: float = 900.0, log=print):
    """Вернуть (источник, список попыток). Мышь доступна всегда."""
    kinds = ["mouse"] if preferred == "mouse" else ["sdl", "mouse"]
    attempts = []
    for kind in kinds:
        backend: InputBackend = (MouseBackend(px_per_rad) if kind == "mouse"
                                 else SdlSensorBackend())
        try:
            ok = backend.start()
        except Exception as exc:
            attempts.append((kind, f"ошибка: {exc}"))
            continue
        if ok:
            attempts.append((kind, "выбран"))
            log(f"Источник ввода: {backend.name} ({backend.description})")
            return backend, attempts
        attempts.append((kind, getattr(backend, "note", "") or "недоступен"))
        backend.close()
    raise RuntimeError("не удалось подготовить ни один источник ввода")
