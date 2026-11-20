"""Главный цикл AeroPaint: опрос геймпада -> расчёт точки -> рисование -> экран."""

from __future__ import annotations

import colorsys
import os
import time
from datetime import datetime

import numpy as np
import pygame

from .audio import CanAudio
from .canvas import Canvas
from .config import DEFAULT_PALETTE, AppConfig
from .inputs.base import EdgeTracker
from .inputs.factory import create_backend
from .orientation import CalibrationError, DistanceEstimator, OrientationFilter, rotate_grip
from .shake import GravityEstimator, SprayCan, gravity_free_axis_accel
from .spray import SprayGun
from .ui import ACCENT, ALARM, GOOD, PAPER, Fonts, Hud

OUTPUT_DIR = "output"
GRIP_NAMES = {0: "обычный", 90: "как баллончик", 180: "перевёрнутый", 270: "как баллончик, зеркально"}


class AeroPaint:
    def __init__(self, cfg: AppConfig | None = None, backend_name: str | None = None) -> None:
        self.cfg = cfg or AppConfig()
        pygame.init()
        pygame.display.set_caption("AeroPaint - рисование баллончиком")

        self.canvas = Canvas(self.cfg.canvas)
        w, h = self.canvas.width, self.canvas.height
        self.screen = pygame.display.set_mode((w, h))
        self.canvas_surface = pygame.Surface((w, h))  # копия холста в формате pygame
        self._blit_region(0, 0, w, h)

        self.hud = Hud(Fonts())
        self.gun = SprayGun(self.cfg.spray)
        self.can = SprayCan(self.cfg.shake)
        self.filter = OrientationFilter(self.cfg.orientation)
        self.gravity = GravityEstimator()
        self.distance = DistanceEstimator()
        self.edges = EdgeTracker()
        self.backend, _ = create_backend(backend_name, self.cfg.orientation.px_per_rad)
        self.audio = CanAudio()
        self.audio.start()

        self.palette = list(DEFAULT_PALETTE)
        self.color_index = 0
        self.set_color(self.palette[0])
        self.nozzle = self.cfg.spray.base_radius

        self.palette_open = False
        self.debug = self.cfg.debug_overlay
        self.running = True
        self.spraying = False
        self.at_limit = False        # точка упёрлась в край холста
        self.prev_point = None
        self.message, self.message_until = "", 0.0
        self.fps = 0.0
        self.idle_time = 0.0         # секунд без использования органов управления
        self._key_activity = False
        self._sticks_active = False
        # Прыжок точки больше половины диагонали - не движение кисти, а разрыв.
        self._max_jump_sq = ((w * w + h * h) ** 0.5 * 0.5) ** 2

        if self.backend.name == "мышь":
            pygame.mouse.set_visible(False)
            pygame.event.set_grab(True)
            pygame.mouse.get_rel()
        self.notify("G - хват, C - калибровка, курок - краска. Сначала встряхните баллончик")

    # -- служебное ------------------------------------------------------------

    def notify(self, text: str, seconds: float = 3.5) -> None:
        self.message, self.message_until = text, time.time() + seconds

    def _blit_region(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Перенести кусок холста из numpy в поверхность pygame."""
        x0, x1 = (max(0, min(self.canvas.width, v)) for v in (x0, x1))
        y0, y1 = (max(0, min(self.canvas.height, v)) for v in (y0, y1))
        if x1 <= x0 or y1 <= y0:
            return
        block = self.canvas.pixels[y0:y1, x0:x1]
        rgb = (np.clip(block, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        view = pygame.surfarray.pixels3d(self.canvas_surface)
        view[x0:x1, y0:y1] = np.transpose(rgb, (1, 0, 2))
        del view

    def refresh_canvas(self) -> None:
        self._blit_region(0, 0, self.canvas.width, self.canvas.height)

    # -- инструмент -----------------------------------------------------------

    def set_color(self, rgb) -> None:
        self.color = tuple(float(c) for c in rgb)
        self.hsv = list(colorsys.rgb_to_hsv(*self.color))

    def select_palette(self, index: int) -> None:
        self.color_index = index % len(self.palette)
        self.set_color(self.palette[self.color_index])

    def apply_hsv(self) -> None:
        self.color = colorsys.hsv_to_rgb(*self.hsv)
        self.palette[self.color_index] = self.color

    def change_nozzle(self, delta: float) -> None:
        c = self.cfg.spray
        self.nozzle = max(c.min_radius, min(c.max_radius, self.nozzle + delta))

    # -- действия -------------------------------------------------------------

    def calibrate(self) -> None:
        self.filter.begin_calibration()
        self.notify("Калибровка: положите баллончик и не трогайте его", 2.5)

    def save_png(self) -> None:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        base = os.path.join(OUTPUT_DIR, f"aeropaint_{datetime.now():%Y%m%d_%H%M%S}")
        self.canvas.export_image(base + ".png")
        self.canvas.save_project(base + ".aept", {"nozzle": self.nozzle, "mix": self.can.mix})
        self.notify(f"Сохранено: {base}.png")

    def _history(self, step, text: str) -> None:
        if step():
            self.refresh_canvas()
            self.notify(text)

    def undo(self) -> None:
        self._history(self.canvas.undo, "Отменено")

    def redo(self) -> None:
        self._history(self.canvas.redo, "Возвращено")

    def new_canvas(self) -> None:
        self.canvas.clear()
        self.refresh_canvas()
        self.notify("Новый холст")

    def recenter(self) -> None:
        self.filter.recenter()
        self.distance.reset()
        self.notify("Центр холста переустановлен")

    def cycle_grip(self) -> None:
        """Хват: 0 -> 90 -> 180 -> 270 градусов."""
        cfg = self.cfg.orientation
        cfg.grip_rotation = (cfg.grip_rotation + 90.0) % 360.0
        # Смена системы координат сбивает углы - текущее направление становится центром.
        self.filter.recenter()
        self.distance.reset()
        self.gravity = GravityEstimator()
        deg = int(cfg.grip_rotation)
        self.notify(f"Хват: {deg} градусов ({GRIP_NAMES.get(deg, '')})", 4.0)

    # -- ввод -----------------------------------------------------------------

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.handle_key(event)

    def handle_key(self, event) -> None:
        key = event.key
        self._key_activity = True
        actions = {
            pygame.K_c: self.calibrate, pygame.K_r: self.recenter,
            pygame.K_z: self.undo, pygame.K_y: self.redo,
            pygame.K_s: self.save_png, pygame.K_g: self.cycle_grip,
            pygame.K_n: self.new_canvas,
            pygame.K_p: lambda: setattr(self, "palette_open", not self.palette_open),
            pygame.K_F1: lambda: setattr(self, "debug", not self.debug),
            pygame.K_LEFTBRACKET: lambda: self.change_nozzle(-4.0),
            pygame.K_RIGHTBRACKET: lambda: self.change_nozzle(4.0),
            pygame.K_ESCAPE: lambda: setattr(self, "running", False),
        }
        if key in actions:
            actions[key]()
        elif pygame.K_1 <= key <= pygame.K_8:
            self.select_palette(key - pygame.K_1)

    def handle_actions(self, state) -> None:
        """Кнопки геймпада (срабатывают один раз в момент нажатия)."""
        just = lambda name: self.edges.just_pressed(state, name)  # noqa: E731
        for name, action in (("calibrate", self.calibrate), ("undo", self.undo),
                             ("redo", self.redo), ("save", self.save_png)):
            if just(name):
                action()
        if just("debug"):
            self.debug = not self.debug
        if just("palette"):
            self.palette_open = not self.palette_open

        if self.palette_open:  # в палитре LB/RB листают ячейки
            if just("size_up"):
                self.select_palette(self.color_index + 1)
            if just("size_down"):
                self.select_palette(self.color_index - 1)
        else:                  # иначе меняют размер сопла, пока зажаты
            if state.pressed("size_up"):
                self.change_nozzle(1.5)
            if state.pressed("size_down"):
                self.change_nozzle(-1.5)

    def handle_palette_sticks(self) -> None:
        """Правка цвета левым мини-джойстиком при открытой палитре."""
        self._sticks_active = False
        if not self.palette_open or pygame.joystick.get_count() == 0:
            return
        js = pygame.joystick.Joystick(0)
        if js.get_numaxes() < 2:
            return

        def axis(i):
            v = js.get_axis(i)
            return v if abs(v) > 0.18 else 0.0  # мёртвая зона

        dh, ds = axis(0), axis(1)
        dv = axis(3) if js.get_numaxes() >= 4 else 0.0
        if dh or ds or dv:
            self._sticks_active = True
            self.hsv[0] = (self.hsv[0] + dh * 0.012) % 1.0
            self.hsv[1] = max(0.0, min(1.0, self.hsv[1] - ds * 0.012))
            self.hsv[2] = max(0.05, min(1.0, self.hsv[2] - dv * 0.012)) if dv else self.hsv[2]
            self.apply_hsv()

    # -- один кадр ------------------------------------------------------------

    def step(self, dt: float) -> None:
        state = self.backend.poll(dt)
        self.handle_actions(state)
        self.handle_palette_sticks()

        gyro, accel = self.apply_grip(state)

        # Взмахи: из ускорения вычитаем силу тяжести и ищем смены направления.
        axis_accel = gravity_free_axis_accel(accel, self.gravity.update(accel))
        mix_before = self.can.mix
        if self.can.update_motion(axis_accel, dt):
            self.hud.notify_shake()
            self.audio.rattle()
            self.backend.rumble(0.55, self.cfg.shake.rumble_ms)
            good = self.cfg.shake.good_mix
            if mix_before < good <= self.can.mix:
                self.notify("Краска перемешана, можно рисовать")

        # Ориентация -> точка на холсте.
        try:
            self.filter.update(gyro, accel, dt)
        except CalibrationError as exc:
            self.notify(f"Калибровка не удалась: {exc}", 4.0)
        self.at_limit = self.filter.clamp_to_canvas(self.canvas.width, self.canvas.height)
        distance = self.distance.update(axis_accel, dt)
        point = self.filter.canvas_point(self.canvas.width, self.canvas.height)

        # Рисование.
        trigger = 0.0 if (self.palette_open or self.filter.calibrating) else state.trigger
        self.audio.spray(trigger)
        was_spraying, self.spraying = self.spraying, trigger > 0.06
        if self.spraying and not was_spraying:
            self.canvas.begin_stroke()
            self.prev_point = point
        if not self.spraying:
            self.prev_point = None

        start = self.prev_point or point
        if (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2 > self._max_jump_sq:
            start = point  # резкий взмах: след не соединяем
        if self.spraying and not self.at_limit:  # у края краска не идёт
            self.gun.paint(self.canvas.pixels, start, point, self.nozzle, distance,
                           self.color, trigger, self.can.quality(), dt)
            self.prev_point = point

        self.update_auto_center(state, dt)
        self.can.update_settling(dt, spraying=self.spraying)

        box = self.gun.take_dirty()
        if box:
            self._blit_region(*box)
        if self.canvas.maybe_autosave(OUTPUT_DIR):
            self.notify("Автосохранение выполнено", 2.0)
        self.draw(state, point, distance, gyro, accel)

    def update_auto_center(self, state, dt: float) -> None:
        """Плавный возврат к центру после бездействия (3 с, у края - 10 с).

        Бездействие - это и неподвижный геймпад: стоит им повести, возврат
        прекращается, а отсчёт начинается заново."""
        c = self.cfg.orientation
        moving = self.filter.rate > c.accel_still_rate
        if (self.spraying or self.palette_open or self.filter.calibrating or moving
                or any(state.buttons.values()) or self._key_activity or self._sticks_active):
            self.idle_time = 0.0
        else:
            self.idle_time += dt
        self._key_activity = False

        delay = c.auto_center_delay_offscreen if self.at_limit else c.auto_center_delay
        if self.idle_time >= delay:
            t = min(1.0, (self.idle_time - delay) / max(1e-3, c.auto_center_ramp))
            self.filter.auto_center(dt, t * t * (3.0 - 2.0 * t))  # плавное нарастание силы

    def apply_grip(self, state):
        """Привести показания к системе координат выбранного хвата."""
        if not self.backend.applies_grip:
            return state.gyro, state.accel
        angle = self.cfg.orientation.grip_rotation
        return rotate_grip(state.gyro, angle), rotate_grip(state.accel, angle)

    # -- отрисовка ------------------------------------------------------------

    def draw(self, state, point, distance, gyro=None, accel=None) -> None:
        screen = self.screen
        screen.blit(self.canvas_surface, (0, 0))
        if not self.palette_open:
            self.hud.draw_crosshair(screen, point, self.gun.radius_for(self.nozzle, distance),
                                    self.color, self.spraying, self.at_limit)
        self.hud.draw_can(screen, 24, 120, self.can, self.color)
        self.hud.draw_shake_prompt(screen, self.can)

        if self.filter.calibrating:
            img = self.hud.f.big.render(
                f"Калибровка {int(self.filter.calibration_progress * 100)} %", True, ACCENT)
            screen.blit(img, (screen.get_width() // 2 - img.get_width() // 2,
                              screen.get_height() // 2 - 20))
        if self.palette_open:
            self.hud.draw_palette(screen, self.palette, self.color_index, self.hsv)
        if self.debug:
            self.hud.draw_debug(screen, self.debug_rows(state, point, distance, gyro, accel))
        self.hud.draw_status(screen, self.status_lines(state))
        pygame.display.flip()

    def status_lines(self, state):
        lines = [(f"источник: {self.backend.name}", GOOD if state.connected else ALARM)]
        if state.battery not in ("неизвестно", "не применимо"):
            lines.append((f"заряд: {state.battery}", PAPER))
        lines += [(f"сопло: {int(self.nozzle)} px", PAPER),
                  (f"цвет: ячейка {self.color_index + 1}", PAPER),
                  (f"перемешано: {int(self.can.mix * 100)} %", ALARM if self.can.needs_shaking else GOOD)]
        if time.time() < self.message_until:
            lines.append((self.message, ACCENT))
        elif state.note:
            lines.append((state.note, (150, 150, 158)))
        return lines

    def debug_rows(self, state, point, distance, gyro=None, accel=None):
        g = gyro if gyro is not None else state.gyro
        a = accel if accel is not None else state.accel
        pose = self.filter.pose
        return [
            ("частота отчётов", f"{state.rate_hz:.0f} Гц"),
            ("хват", f"{int(self.cfg.orientation.grip_rotation)} градусов"),
            ("кадров в секунду", f"{self.fps:.0f}"),
            ("гироскоп x", f"{g[0]:+.3f} рад/с"),
            ("гироскоп y", f"{g[1]:+.3f} рад/с"),
            ("гироскоп z", f"{g[2]:+.3f} рад/с"),
            ("ускорение", f"{a[0]:+.1f} {a[1]:+.1f} {a[2]:+.1f}"),
            ("рыскание", f"{pose.yaw:+.3f} рад"),
            ("тангаж", f"{pose.pitch:+.3f} рад"),
            ("смещение нуля", f"{self.filter.bias[1]:+.4f}"),
            ("точка", f"{point[0]:.0f}, {point[1]:.0f}"),
            ("удаление", f"{distance:+.2f}"),
            ("перемешивание", f"{self.can.mix:.2f}"),
            ("взмахов всего", str(self.can.detector.total_strokes)),
        ]

    # -- главный цикл ---------------------------------------------------------

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            dt = min(clock.tick(self.cfg.target_fps) / 1000.0, 0.05)
            self.fps = clock.get_fps()
            self.hud.update(dt)
            self.handle_events()
            if not self.running:
                break
            self.step(dt)
        self.audio.stop()
        self.backend.close()
        pygame.quit()
