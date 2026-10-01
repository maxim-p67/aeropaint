#!/usr/bin/env python3
"""Запуск AeroPaint.

Примеры:
    python run.py                     автоподбор источника ввода
    python run.py --backend mouse     принудительно мышь
    python run.py --backend sdl       принудительно датчики через SDL
    python run.py --size 1920x1080    размер холста
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="AeroPaint")
    parser.add_argument("--backend", choices=("sdl", "hid", "axes", "mouse"),
                        help="источник данных о движении")
    parser.add_argument("--size", help="размер холста, например 1600x900")
    parser.add_argument("--debug", action="store_true",
                        help="сразу показать отладочный слой")
    parser.add_argument("--sensitivity", type=float,
                        help="пикселей холста на радиан поворота")
    parser.add_argument("--grip", type=int, choices=(0, 90, 180, 270),
                        help="поворот хвата в градусах: 0 - обычный, "
                             "90 - как баллончик (по умолчанию)")
    args = parser.parse_args()

    try:
        import pygame  # noqa: F401
    except ImportError:
        print("Не установлен pygame. Выполните: pip install -r requirements.txt")
        return 1

    from aeropaint.app import AeroPaint
    from aeropaint.config import AppConfig

    cfg = AppConfig()
    if args.size:
        try:
            w, h = args.size.lower().split("x")
            cfg.canvas.width, cfg.canvas.height = int(w), int(h)
        except ValueError:
            print("Размер задаётся как ШИРИНАxВЫСОТА, например 1600x900")
            return 1
    if args.sensitivity:
        cfg.orientation.px_per_rad = args.sensitivity
    if args.grip is not None:
        cfg.orientation.grip_rotation = float(args.grip)
    cfg.debug_overlay = args.debug

    AeroPaint(cfg, backend_name=args.backend).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
