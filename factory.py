"""Выбор источника ввода.

Порядок перебора: сначала то, что указано в mapping.json, затем SDL,
затем сырые HID-отчёты, затем оси джойстика и в последнюю очередь мышь.
Мышь доступна всегда, поэтому приложение запускается при любом исходе.
"""

from __future__ import annotations

import json
import os

from .axes import AxesBackend
from .base import InputBackend
from .hid_raw import HidRawBackend
from .mouse import MouseBackend
from .sdl_sensor import SdlSensorBackend

MAPPING_FILENAME = "mapping.json"


def load_mapping(path: str | None = None) -> dict:
    path = path or MAPPING_FILENAME
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _build(kind: str, mapping: dict, px_per_rad: float) -> InputBackend | None:
    cfg = mapping.get(kind, {}) if isinstance(mapping, dict) else {}
    invert = cfg.get("invert", (1.0, 1.0, 1.0))

    if kind == "sdl":
        return SdlSensorBackend(
            index=cfg.get("index", 0),
            invert=invert,
            button_map=cfg.get("buttons"),
        )
    if kind == "hid":
        vid = cfg.get("vid")
        pid = cfg.get("pid")
        if vid is None or pid is None:
            return None
        return HidRawBackend(
            vid=vid, pid=pid,
            preset=cfg.get("preset", "switch_pro"),
            custom=cfg.get("custom"),
            invert=invert,
        )
    if kind == "axes":
        if not cfg.get("gyro_axes"):
            return None
        return AxesBackend(
            gyro_axes=cfg["gyro_axes"],
            accel_axes=cfg.get("accel_axes"),
            gyro_scale=cfg.get("gyro_scale", 8.0),
            accel_scale=cfg.get("accel_scale", 19.6),
            index=cfg.get("index", 0),
            button_map=cfg.get("buttons"),
        )
    if kind == "mouse":
        return MouseBackend(px_per_rad=px_per_rad)
    return None


def create_backend(preferred: str | None = None, mapping: dict | None = None,
                   px_per_rad: float = 900.0, log=print):
    """Подобрать работающий источник. Возвращает (источник, список попыток)."""
    mapping = mapping if mapping is not None else load_mapping()

    order = []
    if preferred:
        order.append(preferred)
    chosen = mapping.get("backend")
    if chosen and chosen not in order:
        order.append(chosen)
    for kind in ("sdl", "hid", "axes", "mouse"):
        if kind not in order:
            order.append(kind)

    attempts = []
    for kind in order:
        backend = _build(kind, mapping, px_per_rad)
        if backend is None:
            attempts.append((kind, "не настроен"))
            continue
        try:
            ok = backend.start()
        except Exception as exc:
            attempts.append((kind, f"ошибка: {exc}"))
            continue
        if ok:
            attempts.append((kind, "выбран"))
            log(f"Источник ввода: {backend.name} ({backend.description})")
            return backend, attempts
        note = getattr(backend, "_note", "") or "недоступен"
        attempts.append((kind, note))
        try:
            backend.close()
        except Exception:
            pass

    fallback = MouseBackend(px_per_rad=px_per_rad)
    try:
        fallback.start()
    except Exception as exc:
        raise RuntimeError(
            "не удалось подготовить ни один источник ввода; "
            f"последняя ошибка: {exc}"
        ) from exc
    return fallback, attempts
