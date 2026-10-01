"""Наглядная проверка модели распыления без запуска графической оболочки.

Рисует одинаковые мазки при разном уровне перемешивания краски
и сохраняет результат в PNG. Запуск: python -m tests.render_demo
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from aeropaint.canvas import Canvas
from aeropaint.config import CanvasConfig, ShakeConfig, SprayConfig
from aeropaint.shake import SprayCan
from aeropaint.spray import SprayGun, dry


def sweep(gun, canvas, y, color, quality, nozzle=34.0, seconds=1.6, distance=0.0):
    """Провести горизонтальный мазок слева направо."""
    dt = 1.0 / 60.0
    steps = int(seconds / dt)
    w = canvas.width
    x_prev = 90.0
    for i in range(steps):
        t = (i + 1) / steps
        x = 90.0 + (w - 180.0) * t
        gun.paint(canvas.pixels, canvas.wet, (x_prev, y), (x, y),
                  nozzle, distance, color, 1.0, quality, dt)
        gun.update_drips(canvas.pixels, canvas.wet, dt)
        dry(canvas.wet, dt)
        x_prev = x


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 1. Сравнение уровней перемешивания.
    cfg = CanvasConfig(width=1100, height=620)
    canvas = Canvas(cfg)
    gun = SprayGun(SprayConfig(), seed=42)

    levels = [1.00, 0.60, 0.35, 0.18, 0.06]
    colour = (0.10, 0.34, 0.80)
    for idx, mix in enumerate(levels):
        can = SprayCan(ShakeConfig(), mix=mix)
        y = 80.0 + idx * 120.0
        sweep(gun, canvas, y, colour, can.quality())
    canvas.export_image(os.path.join(out_dir, "01_mix_levels.png"))
    print("01_mix_levels.png  - сверху вниз: mix =", ", ".join(f"{m:.2f}" for m in levels))

    # 2. Влияние удаления от холста на размер и плотность пятна.
    canvas2 = Canvas(CanvasConfig(width=1100, height=420))
    gun2 = SprayGun(SprayConfig(), seed=7)
    good = SprayCan(ShakeConfig(), mix=1.0).quality()
    for idx, dist in enumerate([-0.5, 0.0, 0.7, 1.4]):
        sweep(gun2, canvas2, 70.0 + idx * 95.0, (0.08, 0.08, 0.09), good,
              nozzle=26.0, distance=dist, seconds=1.2)
    canvas2.export_image(os.path.join(out_dir, "02_distance.png"))
    print("02_distance.png    - сверху вниз: удаление -0.5, 0.0, 0.7, 1.4")

    # 3. Подтёки: долгое распыление в одну точку.
    canvas3 = Canvas(CanvasConfig(width=760, height=560))
    gun3 = SprayGun(SprayConfig(), seed=11)
    dt = 1.0 / 60.0
    for i in range(190):
        x = 250.0 + 40.0 * math.sin(i * 0.05)
        gun3.paint(canvas3.pixels, canvas3.wet, (x, 150), (x, 150),
                   30.0, 0.0, (0.72, 0.09, 0.14), 1.0, good, dt)
        gun3.update_drips(canvas3.pixels, canvas3.wet, dt)
    for _ in range(260):
        gun3.update_drips(canvas3.pixels, canvas3.wet, dt)
        dry(canvas3.wet, dt)
    canvas3.export_image(os.path.join(out_dir, "03_drips.png"))
    print(f"03_drips.png       - подтёков создано: {gun3.rng and 'да'}, "
          f"активных осталось {len(gun3.drips)}")

    # 4. Реалистичный сценарий: взболтали, порисовали, краска расслоилась.
    canvas4 = Canvas(CanvasConfig(width=1100, height=520))
    gun4 = SprayGun(SprayConfig(), seed=5)
    can = SprayCan(ShakeConfig(), mix=0.0)

    # Пользователь встряхивает баллончик.
    t = 0.0
    while t < 4.0:
        can.update_motion(15.0 * math.sin(2 * math.pi * 3.2 * t), 1.0 / 240.0)
        can.update_settling(1.0 / 240.0, spraying=False)
        t += 1.0 / 240.0
    print(f"04: после встряхивания mix={can.mix:.2f}, взмахов={can.detector.total_strokes}")

    dt = 1.0 / 60.0
    for row in range(4):
        y = 80.0 + row * 115.0
        x_prev = 90.0
        for i in range(110):
            x = 90.0 + 920.0 * (i + 1) / 110.0
            gun4.paint(canvas4.pixels, canvas4.wet, (x_prev, y), (x, y),
                       30.0, 0.0, (0.05, 0.05, 0.06), 1.0, can.quality(), dt)
            can.update_settling(dt, spraying=True)
            gun4.update_drips(canvas4.pixels, canvas4.wet, dt)
            dry(canvas4.wet, dt)
            x_prev = x
        print(f"    после полосы {row + 1}: mix={can.mix:.2f}")
    canvas4.export_image(os.path.join(out_dir, "04_session.png"))
    print("04_session.png     - непрерывное распыление расходует перемешивание")

    print("\nФайлы в", out_dir)


if __name__ == "__main__":
    main()
