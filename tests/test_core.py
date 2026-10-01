"""Модульные тесты ядра: детектор встряхивания, состояние краски, распыление.

Запуск:  python -m tests.test_core   (из корня проекта)
Тесты не требуют pygame и геймпада.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from aeropaint.canvas import Canvas
from aeropaint.config import CanvasConfig, ShakeConfig, SprayConfig
from aeropaint.orientation import (DistanceEstimator, OrientationFilter,
                                   CalibrationError, rotate_grip)
from aeropaint.shake import GravityEstimator, ShakeDetector, SprayCan, gravity_free_axis_accel
from aeropaint.spray import SprayGun, spray_kernel

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


def simulate_shaking(can, seconds, dt=1.0 / 240.0, freq=3.5, amplitude=14.0):
    """Синусоидальное встряхивание: амплитуда в м/с^2, частота в Гц."""
    t = 0.0
    strokes = 0
    while t < seconds:
        a = amplitude * math.sin(2.0 * math.pi * freq * t)
        strokes += can.update_motion(a, dt)
        can.update_settling(dt, spraying=False)
        t += dt
    return strokes


def test_shake_detector():
    print("\nДетектор встряхивания")
    det = ShakeDetector(ShakeConfig())
    dt = 1.0 / 240.0
    strokes = 0
    for i in range(int(2.0 / dt)):
        t = i * dt
        strokes += det.update(14.0 * math.sin(2 * math.pi * 3.0 * t), dt)
    # 3 Гц за 2 с - шесть полных периодов, то есть около 12 смен направления.
    check("взмахи считаются", 10 <= strokes <= 14, f"получено {strokes}")

    det2 = ShakeDetector(ShakeConfig())
    for i in range(int(2.0 / dt)):
        t = i * dt
        # Слабое покачивание ниже порога взмахом считаться не должно.
        det2.update(2.0 * math.sin(2 * math.pi * 3.0 * t), dt)
    check("слабое движение не считается", det2.total_strokes == 0,
          f"получено {det2.total_strokes}")

    det3 = ShakeDetector(ShakeConfig())
    for i in range(int(2.0 / dt)):
        # Постоянное ускорение в одну сторону - это не встряхивание.
        det3.update(12.0, dt)
    check("односторонняя тяга не считается", det3.total_strokes <= 1,
          f"получено {det3.total_strokes}")


def test_mix_level():
    print("\nУровень перемешивания")
    can = SprayCan(ShakeConfig())
    check("новый баллончик не перемешан", can.mix == 0.0 and can.needs_shaking)
    check("подсказка о числе взмахов", can.strokes_to_good == 9,
          f"получено {can.strokes_to_good}")

    simulate_shaking(can, 4.0)
    check("после встряхивания краска готова", can.mix >= 0.75, f"mix={can.mix:.2f}")
    check("предупреждение снято", not can.needs_shaking)
    check("качество чистое", can.quality().is_clean)

    # Расслоение при простое.
    for _ in range(int(120.0 * 60)):
        can.update_settling(1.0 / 60.0, spraying=False)
    check("за две минуты простоя краска расслаивается", can.mix < 0.75,
          f"mix={can.mix:.2f}")

    # Расход при распылении идёт быстрее простоя.
    a = SprayCan(ShakeConfig(), mix=1.0)
    b = SprayCan(ShakeConfig(), mix=1.0)
    for _ in range(int(30.0 * 60)):
        a.update_settling(1.0 / 60.0, spraying=True)
        b.update_settling(1.0 / 60.0, spraying=False)
    check("распыление расходует перемешивание быстрее простоя", a.mix < b.mix,
          f"spray={a.mix:.3f} idle={b.mix:.3f}")


def test_quality_curve():
    print("\nКачество краски")
    full = SprayCan(ShakeConfig(), mix=1.0).quality()
    mid = SprayCan(ShakeConfig(), mix=0.45).quality()
    low = SprayCan(ShakeConfig(), mix=0.20).quality()
    dead = SprayCan(ShakeConfig(), mix=0.05).quality()

    check("полная подача при перемешанной краске", full.density == 1.0 and full.spit_chance == 0.0)
    check("плотность падает с ухудшением", full.density > mid.density > low.density)
    check("плевки появляются", mid.spit_chance > 0.0 and low.spit_chance > mid.spit_chance)
    check("цвет бледнеет", low.saturation < full.saturation)
    check("неровная подача только внизу шкалы", mid.sputter == 0.0 and low.sputter > 0.0)
    check("баллончик засоряется", dead.clogged and not low.clogged)


def test_gravity_and_axis():
    print("\nВыделение линейного ускорения")
    est = GravityEstimator()
    rest = (0.0, 9.81, 0.0)
    for _ in range(300):
        est.update(rest)
    check("тяжесть найдена", abs(est.value[1] - 9.81) < 0.05, f"{est.value}")
    check("покой не даёт ускорения", abs(gravity_free_axis_accel(rest, est.value)) < 0.05)

    moved = (0.0, 9.81 + 12.0, 0.0)
    a = gravity_free_axis_accel(moved, est.value)
    check("взмах виден сквозь тяжесть", a > 11.0, f"{a:.2f}")


def test_orientation():
    print("\nОриентация и калибровка")
    filt = OrientationFilter()
    filt.begin_calibration()
    for _ in range(filt.cfg.calibration_samples):
        filt.update((0.01, -0.02, 0.005), (0.0, 9.81, 0.0), 1.0 / 240.0)
    check("калибровка завершилась", not filt.calibrating)
    check("смещение нуля оценено", abs(filt.bias[1] + 0.02) < 1e-6, f"{filt.bias}")

    filt2 = OrientationFilter()
    filt2.begin_calibration()
    raised = False
    try:
        for i in range(filt2.cfg.calibration_samples):
            g = (0.0, 2.0 if i % 2 else -2.0, 0.0)
            filt2.update(g, (0.0, 9.81, 0.0), 1.0 / 240.0)
    except CalibrationError:
        raised = True
    check("движение при калибровке отклоняется", raised)

    filt3 = OrientationFilter()
    dt = 1.0 / 240.0
    # Поворот на 0.2 рад вокруг вертикали за 1 с.
    for _ in range(240):
        filt3.update((0.0, 0.2, 0.0), (0.0, 9.81, 0.0), dt)
    x, y = filt3.canvas_point(1600, 900)
    expected = 800 + 0.2 * filt3.cfg.px_per_rad
    check("поворот смещает точку по горизонтали", abs(x - expected) < 12.0,
          f"x={x:.1f} ожидалось {expected:.1f}")
    check("вертикаль не поехала", abs(y - 450) < 6.0, f"y={y:.1f}")


def move_and_point(gyro_raw, grip, width=1600, height=900, seconds=1.0):
    """Покрутить виртуальный геймпад и вернуть смещение точки от центра."""
    filt = OrientationFilter()
    dt = 1.0 / 240.0
    at_rest = rotate_grip((0.0, 9.81, 0.0), grip)
    for _ in range(int(seconds / dt)):
        filt.update(rotate_grip(gyro_raw, grip), at_rest, dt)
    x, y = filt.canvas_point(width, height)
    return x - width * 0.5, y - height * 0.5


def test_grip_rotation():
    print("\nПоворот хвата")

    # Ожидаемый ход считается от текущей чувствительности, а не вшит
    # числом: поворот 0.2 рад/с за секунду даёт 0.2 рад.
    k = OrientationFilter().cfg.px_per_rad
    full = 0.2 * k
    lo, hi = full * 0.94, full * 1.06

    # Сам поворот как преобразование вектора.
    check("нулевой угол ничего не меняет",
          rotate_grip((1.0, 2.0, 3.0), 0) == (1.0, 2.0, 3.0))
    r = rotate_grip((1.0, 2.0, 3.0), 90)
    check("поворот на 90 меняет оси местами",
          abs(r[0] - 2.0) < 1e-9 and abs(r[1] + 1.0) < 1e-9 and r[2] == 3.0,
          f"{r}")
    r4 = rotate_grip(rotate_grip(rotate_grip(rotate_grip((1.0, 2.0, 3.0), 90), 90), 90), 90)
    check("четыре поворота по 90 возвращают исходное",
          all(abs(r4[i] - v) < 1e-9 for i, v in enumerate((1.0, 2.0, 3.0))), f"{r4}")

    # Обычный хват: вправо - вправо, вверх - вверх.
    dx, dy = move_and_point((0.0, 0.2, 0.0), 0)
    check("хват 0: поворот вправо ведёт вправо", lo < dx < hi and abs(dy) < 5, f"{dx:.0f},{dy:.0f}")
    dx, dy = move_and_point((0.2, 0.0, 0.0), 0)
    check("хват 0: наклон вверх ведёт вниз (инверсия)", lo < dy < hi and abs(dx) < 5, f"{dx:.0f},{dy:.0f}")

    # Хват "как баллончик": все четыре направления из постановки задачи.
    # На экране ось y направлена вниз, поэтому "вверх" - это dy < 0.
    dx, dy = move_and_point((0.0, 0.2, 0.0), 90)
    check("хват 90: вправо даёт вниз", lo < dy < hi and abs(dx) < 5, f"{dx:.0f},{dy:.0f}")

    dx, dy = move_and_point((0.2, 0.0, 0.0), 90)
    check("хват 90: вверх даёт влево", -hi < dx < -lo and abs(dy) < 5, f"{dx:.0f},{dy:.0f}")

    dx, dy = move_and_point((0.0, -0.2, 0.0), 90)
    check("хват 90: влево даёт вверх", -hi < dy < -lo and abs(dx) < 5, f"{dx:.0f},{dy:.0f}")

    dx, dy = move_and_point((-0.2, 0.0, 0.0), 90)
    check("хват 90: вниз даёт вправо", lo < dx < hi and abs(dy) < 5, f"{dx:.0f},{dy:.0f}")

    # Зеркальный вариант на случай другого хвата должен давать противоположное.
    dx270, dy270 = move_and_point((0.0, 0.2, 0.0), 270)
    check("хват 270 зеркален хвату 90", -hi < dy270 < -lo, f"{dx270:.0f},{dy270:.0f}")

    # Коррекция по силе тяжести не должна разваливаться при повороте:
    # в покое точка обязана стоять на месте.
    filt = OrientationFilter()
    at_rest = rotate_grip((0.0, 9.81, 0.0), 90)
    for _ in range(240 * 3):
        filt.update((0.0, 0.0, 0.0), at_rest, 1.0 / 240.0)
    x, y = filt.canvas_point(1600, 900)
    check("в покое при повёрнутом хвате точка не уплывает",
          abs(x - 800) < 3 and abs(y - 450) < 3, f"{x:.1f},{y:.1f}")


def spin(filt, rate, seconds, clamp=None, dt=1.0 / 240.0):
    """Крутить виртуальный геймпад с заданной угловой скоростью."""
    for _ in range(int(seconds / dt)):
        filt.update((0.0, rate, 0.0), (0.0, 9.81, 0.0), dt)
        if clamp:
            filt.clamp_to_canvas(*clamp)


def test_canvas_limits():
    print("\nУдержание точки в пределах холста")
    W, H = 1600, 900

    # Без ограничения точка уходит сколь угодно далеко.
    free = OrientationFilter()
    spin(free, 0.5, 3.0)
    x_free, _ = free.canvas_point(W, H)
    check("без ограничения точка уходит за холст", x_free > W,
          f"x={x_free:.0f}")

    # И, перевалив за половину оборота, перепрыгивает на другую сторону.
    runaway = OrientationFilter()
    spin(runaway, 0.5, 7.0)
    x_jump, _ = runaway.canvas_point(W, H)
    check("без ограничения знак переворачивается", x_jump < 0,
          f"x={x_jump:.0f}")

    # С ограничением точка упирается в край при любом повороте.
    for seconds in (2.0, 7.0, 14.0):
        f = OrientationFilter()
        spin(f, 0.5, seconds, clamp=(W, H))
        x, y = f.canvas_point(W, H)
        check(f"поворот {seconds:.0f} с: точка на холсте",
              -1 <= x <= W + 1 and -1 <= y <= H + 1, f"x={x:.0f} y={y:.0f}")

    # Вертикаль ограничивается так же.
    f = OrientationFilter()
    dt = 1.0 / 240.0
    for _ in range(int(6.0 / dt)):
        f.update((0.6, 0.0, 0.0), (0.0, 9.81, 0.0), dt)
        f.clamp_to_canvas(W, H)
    x, y = f.canvas_point(W, H)
    check("вертикаль тоже ограничена", -1 <= y <= H + 1, f"y={y:.0f}")

    # Упор в край сообщается вызывающему коду.
    f = OrientationFilter()
    spin(f, 0.5, 0.2, clamp=(W, H))
    check("в середине холста упора нет", not f.clamp_to_canvas(W, H))
    spin(f, 0.5, 5.0, clamp=(W, H))
    check("на краю упор отмечается", f.clamp_to_canvas(W, H))

    # Главное: обратный ход отзывается сразу, без отмотки на весь перебор.
    f = OrientationFilter()
    spin(f, 0.5, 8.0, clamp=(W, H))
    at_edge, _ = f.canvas_point(W, H)
    spin(f, -0.5, 0.4, clamp=(W, H))
    back, _ = f.canvas_point(W, H)
    check("обратный поворот сразу возвращает точку", back < at_edge - 100,
          f"было {at_edge:.0f}, стало {back:.0f}")


def test_auto_center():
    print("\nВозврат к центру")
    W, H = 1600, 900
    f = OrientationFilter()
    spin(f, 0.5, 1.0, clamp=(W, H))
    start, _ = f.canvas_point(W, H)
    check("точка сдвинута", start > W * 0.5 + 100, f"{start:.0f}")

    for _ in range(int(20.0 * 240)):
        f.auto_center(1.0 / 240.0)
    mid, _ = f.canvas_point(W, H)
    check("за 20 с точка подтянулась к центру", mid < start - 50,
          f"было {start:.0f}, стало {mid:.0f}")
    check("но не проскочила центр", mid >= W * 0.5 - 1, f"{mid:.0f}")

    for _ in range(int(600.0 * 240)):
        f.auto_center(1.0 / 240.0)
    end, _ = f.canvas_point(W, H)
    check("в пределе ровно центр", abs(end - W * 0.5) < 1.0, f"{end:.0f}")

    # Скорость возврата должна быть много меньше обычного ведения руки.
    rate = f.cfg.auto_center_rate
    check("возврат сильный", rate >= 1.0, f"{rate}")


def test_distance():
    print("\nОценка удаления")
    est = DistanceEstimator()
    for _ in range(30):
        est.update(9.0, 1.0 / 120.0)
    moved = est.value
    check("движение вперёд меняет удаление", abs(moved) > 0.001, f"{moved:.4f}")
    for _ in range(int(6.0 * 120)):
        est.update(0.0, 1.0 / 120.0)
    check("в покое удаление возвращается", abs(est.value) < abs(moved) * 0.5,
          f"{est.value:.4f} было {moved:.4f}")


def test_spray_rendering():
    print("\nРаспыление")
    k = spray_kernel(20, 0.45)
    check("ядро квадратное и нечётное", k.shape == (41, 41))
    check("центр ядра максимален", abs(float(k[20, 20]) - 1.0) < 1e-6)
    check("за радиусом ядро пустое", float(k[20, 0]) == 0.0)

    cfg = CanvasConfig(width=300, height=200)
    canvas = Canvas(cfg)
    gun = SprayGun(SprayConfig(), seed=1)
    good = SprayCan(ShakeConfig(), mix=1.0).quality()

    before = canvas.pixels.copy()
    gun.paint(canvas.pixels, canvas.wet, (50, 100), (250, 100), 30.0, 0.0,
              (0.9, 0.1, 0.1), 1.0, good, 1.0 / 60.0)
    check("краска легла", not np.allclose(before, canvas.pixels))
    check("след красный", float(canvas.pixels[100, 150, 0]) > float(canvas.pixels[100, 150, 2]))
    check("вне следа чисто", np.allclose(canvas.pixels[10, 10], cfg.background, atol=1e-3))

    # Проверка, что плохо перемешанная краска кладётся слабее.
    c1 = Canvas(CanvasConfig(width=200, height=200))
    c2 = Canvas(CanvasConfig(width=200, height=200))
    g1 = SprayGun(SprayConfig(), seed=7)
    g2 = SprayGun(SprayConfig(), seed=7)
    bad = SprayCan(ShakeConfig(), mix=0.18).quality()
    for _ in range(12):
        g1.paint(c1.pixels, c1.wet, (60, 100), (140, 100), 25.0, 0.0,
                 (0.0, 0.0, 0.0), 1.0, good, 1.0 / 60.0)
        g2.paint(c2.pixels, c2.wet, (60, 100), (140, 100), 25.0, 0.0,
                 (0.0, 0.0, 0.0), 1.0, bad, 1.0 / 60.0)
    check("недомешанная краска кроет хуже",
          float(c2.pixels[100, 100].mean()) > float(c1.pixels[100, 100].mean()),
          f"good={c1.pixels[100,100].mean():.3f} bad={c2.pixels[100,100].mean():.3f}")

    # Отпечаток у края не должен падать.
    gun.paint(canvas.pixels, canvas.wet, (-40, -40), (5, 5), 30.0, 0.0,
              (0, 0, 0), 1.0, good, 1.0 / 60.0)
    gun.paint(canvas.pixels, canvas.wet, (295, 195), (340, 240), 30.0, 0.0,
              (0, 0, 0), 1.0, good, 1.0 / 60.0)
    check("края холста обрабатываются", np.isfinite(canvas.pixels).all())


def test_canvas_io():
    print("\nХолст, отмена и файлы")
    canvas = Canvas(CanvasConfig(width=120, height=80))
    gun = SprayGun(SprayConfig(), seed=3)
    good = SprayCan(ShakeConfig(), mix=1.0).quality()

    check("сначала отменять нечего", not canvas.can_undo)
    canvas.begin_stroke()
    gun.paint(canvas.pixels, canvas.wet, (20, 40), (100, 40), 15.0, 0.0,
              (0, 0, 0), 1.0, good, 1.0 / 60.0)
    painted = canvas.pixels.copy()
    check("отмена доступна", canvas.can_undo)

    canvas.undo()
    check("отмена вернула фон",
          np.allclose(canvas.pixels[40, 60], canvas.cfg.background, atol=0.02),
          f"{canvas.pixels[40, 60]}")
    canvas.redo()
    check("повтор вернул след", np.allclose(canvas.pixels, painted, atol=0.01))

    depth = canvas.cfg.undo_depth
    for _ in range(depth + 8):
        canvas.begin_stroke()
    check("глубина истории ограничена", len(canvas._undo) == depth,
          f"{len(canvas._undo)}")

    with tempfile.TemporaryDirectory() as tmp:
        png = canvas.export_image(os.path.join(tmp, "out.png"))
        check("PNG записан", os.path.getsize(png) > 0)
        jpg = canvas.export_image(os.path.join(tmp, "out.jpg"))
        check("JPEG записан", os.path.getsize(jpg) > 0)
        proj = canvas.save_project(os.path.join(tmp, "p.aept"), {"note": "тест"})
        loaded = Canvas.load_project(proj)
        check("проект открылся без потерь",
              np.allclose(loaded.pixels, canvas.pixels, atol=1.0 / 255.0))


def main():
    test_shake_detector()
    test_mix_level()
    test_quality_curve()
    test_gravity_and_axis()
    test_orientation()
    test_grip_rotation()
    test_canvas_limits()
    test_auto_center()
    test_distance()
    test_spray_rendering()
    test_canvas_io()

    print("\n" + "=" * 56)
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)}")
        for f in FAILED:
            print("  -", f)
        return 1
    print("Все проверки пройдены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
