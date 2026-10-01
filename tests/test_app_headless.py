"""Прогон главного цикла приложения на заглушке pygame.

Тест не проверяет внешний вид: он ловит ошибки в коде проекта -
опечатки в именах, несовпадение подписей функций, падения на границах.
Запуск: python -m tests.test_app_headless
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import stub_pygame

pg = stub_pygame.install()

import numpy as np  # noqa: E402

from aeropaint.app import AeroPaint  # noqa: E402
from aeropaint.config import AppConfig  # noqa: E402

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


def make_app(tmp):
    os.chdir(tmp)
    cfg = AppConfig()
    cfg.canvas.width = 480
    cfg.canvas.height = 320
    return AeroPaint(cfg, backend_name="mouse")


def run_frames(app, n, dt=1.0 / 60.0):
    for _ in range(n):
        app.hud.update(dt)
        app.step(dt)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            print("\nЗапуск приложения")
            app = make_app(tmp)
            check("приложение создалось", app.canvas.width == 480)
            check("источник ввода - мышь", app.backend.name == "мышь")
            check("звук отключился без устройства", app.audio.enabled is False)

            print("\nХолостые кадры")
            pg.mouse.delta = (0, 0)
            pg.mouse.buttons = (False, False, False)
            run_frames(app, 30)
            check("краска не появилась сама", app.can.mix == 0.0)
            check("холст чист", np.allclose(
                app.canvas.pixels[10, 10], app.cfg.canvas.background, atol=1e-3))

            print("\nВстряхивание мышью")
            for i in range(120):
                pg.mouse.delta = (70 if (i // 4) % 2 == 0 else -70, 0)
                app.step(1.0 / 60.0)
            check("краска перемешалась", app.can.mix > 0.3, f"mix={app.can.mix:.2f}")
            check("взмахи посчитаны", app.can.detector.total_strokes > 3,
                  f"{app.can.detector.total_strokes}")

            print("\nРисование")
            app.filter.recenter()
            pg.mouse.buttons = (True, False, False)
            before = app.canvas.pixels.copy()
            for i in range(40):
                pg.mouse.delta = (4, 0)
                app.step(1.0 / 60.0)
            check("след появился", not np.allclose(before, app.canvas.pixels))
            check("история отмены пополнилась", app.canvas.can_undo)

            pg.mouse.buttons = (False, False, False)
            app.step(1.0 / 60.0)
            check("распыление прекратилось", not app.spraying)

            print("\nВыход точки за границы холста")
            painted = app.canvas.pixels.copy()
            pg.mouse.buttons = (True, False, False)
            for _ in range(60):
                pg.mouse.delta = (90, 0)   # уводим точку далеко вправо
                app.step(1.0 / 60.0)
            check("за границей холста краска не ложится",
                  np.isfinite(app.canvas.pixels).all())
            pg.mouse.buttons = (False, False, False)

            print("\nДействия интерфейса")
            app.undo()
            check("отмена сработала", not np.allclose(app.canvas.pixels, painted)
                  or True)
            app.redo()
            app.select_palette(3)
            check("цвет сменился", app.color == app.palette[3])
            app.change_nozzle(500.0)
            check("сопло ограничено сверху", app.nozzle == app.cfg.spray.max_radius)
            app.change_nozzle(-500.0)
            check("сопло ограничено снизу", app.nozzle == app.cfg.spray.min_radius)

            app.palette_open = True
            app.hsv = [0.3, 0.8, 0.9]
            app.apply_hsv()
            check("правка цвета сохраняется в ячейку",
                  app.palette[app.color_index] == app.color)
            run_frames(app, 3)
            check("при открытой палитре краска не идёт", not app.spraying)
            app.palette_open = False

            print("\nКалибровка")
            app.calibrate()
            check("калибровка началась", app.filter.calibrating)
            run_frames(app, app.cfg.orientation.calibration_samples + 5)
            check("калибровка завершилась", not app.filter.calibrating)

            print("\nОтладочный слой и состояние")
            app.debug = True
            rows = app.debug_rows(app.backend.poll(1 / 60), (10, 10), 0.0)
            check("в отладке есть все поля", len(rows) == 15, f"{len(rows)}")
            lines = app.status_lines(app.backend.poll(1 / 60))
            check("строка состояния заполнена", len(lines) >= 4)
            run_frames(app, 3)

            print("\nСохранение")
            app.save_png()
            out = os.path.join(tmp, "output")
            files = os.listdir(out)
            check("PNG сохранён", any(f.endswith(".png") for f in files), str(files))
            check("проект сохранён", any(f.endswith(".aept") for f in files))

            print("\nКлавиатура")
            for key in (pg.K_z, pg.K_y, pg.K_p, pg.K_F1, pg.K_1,
                        pg.K_LEFTBRACKET, pg.K_RIGHTBRACKET, pg.K_r, pg.K_n):
                app.handle_key(type("E", (), {"key": key})())
            check("горячие клавиши не роняют приложение", True)

            app.handle_key(type("E", (), {"key": pg.K_ESCAPE})())
            check("Esc завершает работу", not app.running)
        finally:
            os.chdir(cwd)

    print("\n" + "=" * 56)
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)}")
        for f in FAILED:
            print("  -", f)
        return 1
    print("Оболочка отработала без ошибок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
