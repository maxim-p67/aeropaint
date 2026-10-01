"""Главный цикл приложения AeroPaint."""

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
from .inputs.factory import create_backend, load_mapping
from .orientation import (CalibrationError, DistanceEstimator, OrientationFilter,
                          rotate_grip)
from .shake import GravityEstimator, SprayCan, gravity_free_axis_accel
from .spray import SprayGun, dry
from .ui import ALARM, ACCENT, GOOD, Fonts, Hud, PAPER, to255

OUTPUT_DIR = "output"


class AeroPaint:
    def __init__(self, cfg: AppConfig | None = None, backend_name: str | None = None) -> None:
        self.cfg = cfg or AppConfig()

        pygame.init()
        pygame.display.set_caption("AeroPaint - рисование баллончиком")

        self.canvas = Canvas(self.cfg.canvas)
        self.screen = pygame.display.set_mode((self.canvas.width, self.canvas.height))
        self.canvas_surface = pygame.Surface((self.canvas.width, self.canvas.height))
        self._blit_region(0, 0, self.canvas.width, self.canvas.height)

        self.fonts = Fonts()
        self.hud = Hud(self.fonts)

        self.gun = SprayGun(self.cfg.spray)
        self.can = SprayCan(self.cfg.shake)
        self.filter = OrientationFilter(self.cfg.orientation)
        self.gravity = GravityEstimator()
        self.distance = DistanceEstimator()
        self.edges = EdgeTracker()

        mapping = load_mapping()
        self.backend, self.attempts = create_backend(
            backend_name, mapping, self.cfg.orientation.px_per_rad)

        self.audio = CanAudio()
        self.audio.start()

        self.palette = list(DEFAULT_PALETTE)
        self.color_index = 0
        self.color = self.palette[0]
        self.hsv = list(colorsys.rgb_to_hsv(*self.color))
        self.nozzle = self.cfg.spray.base_radius

        self.palette_open = False
        self.debug = self.cfg.debug_overlay
        self.running = True
        self.spraying = False
        self.at_limit = False
        self.prev_point = None
        self.message = ""
        self.message_until = 0.0
        self.fps = 0.0
        self.idle_time = 0.0
        self._key_activity = False
        self._sticks_active = False

        # Порог разрыва следа: половина диагонали холста.
        diagonal = (self.canvas.width ** 2 + self.canvas.height ** 2) ** 0.5
        self._max_jump_sq = (diagonal * 0.5) ** 2

        if self.backend.name == "мышь":
            pygame.mouse.set_visible(False)
            pygame.event.set_grab(True)
            pygame.mouse.get_rel()

        self.notify("Y - калибровка, G - хват, курок - краска. "
                    "Сначала встряхните баллончик")

    # -- служебное ------------------------------------------------------------

    def notify(self, text: str, seconds: float = 3.5) -> None:
        self.message = text
        self.message_until = time.time() + seconds

    def _blit_region(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Перенести кусок холста из numpy в поверхность pygame."""
        x0 = max(0, min(self.canvas.width, x0))
        y0 = max(0, min(self.canvas.height, y0))
        x1 = max(0, min(self.canvas.width, x1))
        y1 = max(0, min(self.canvas.height, y1))
        if x1 <= x0 or y1 <= y0:
            return
        block = self.canvas.pixels[y0:y1, x0:x1]
        rgb = (np.clip(block, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        view = pygame.surfarray.pixels3d(self.canvas_surface)
        view[x0:x1, y0:y1] = np.transpose(rgb, (1, 0, 2))
        del view

    def refresh_canvas(self) -> None:
        self._blit_region(0, 0, self.canvas.width, self.canvas.height)

    # -- параметры инструмента ------------------------------------------------

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
        lo, hi = self.cfg.spray.min_radius, self.cfg.spray.max_radius
        self.nozzle = max(lo, min(hi, self.nozzle + delta))

    # -- действия -------------------------------------------------------------

    def calibrate(self) -> None:
        self.filter.begin_calibration()
        self.notify("Калибровка: положите баллончик и не трогайте его", 2.5)

    def save_png(self) -> None:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(OUTPUT_DIR, f"aeropaint_{stamp}.png")
        self.canvas.export_image(path)
        self.canvas.save_project(os.path.join(OUTPUT_DIR, f"aeropaint_{stamp}.aept"),
                                 {"nozzle": self.nozzle, "mix": self.can.mix})
        self.notify(f"Сохранено: {path}")

    def undo(self) -> None:
        if self.canvas.undo():
            self.gun.clear_drips()
            self.refresh_canvas()
            self.notify("Отменено")

    def redo(self) -> None:
        if self.canvas.redo():
            self.gun.clear_drips()
            self.refresh_canvas()
            self.notify("Возвращено")

    # -- обработка событий ----------------------------------------------------

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.handle_key(event)

    def handle_key(self, event) -> None:
        key = event.key
        self._key_activity = True
        if key == pygame.K_ESCAPE:
            self.running = False
        elif key == pygame.K_c:
            self.calibrate()
        elif key == pygame.K_r:
            self.filter.recenter()
            self.distance.reset()
            self.notify("Центр холста переустановлен")
        elif key == pygame.K_z:
            self.undo()
        elif key == pygame.K_y:
            self.redo()
        elif key == pygame.K_s:
            self.save_png()
        elif key == pygame.K_p:
            self.palette_open = not self.palette_open
        elif key == pygame.K_g:
            self.cycle_grip()
        elif key == pygame.K_F1:
            self.debug = not self.debug
        elif key == pygame.K_LEFTBRACKET:
            self.change_nozzle(-4.0)
        elif key == pygame.K_RIGHTBRACKET:
            self.change_nozzle(4.0)
        elif key == pygame.K_n:
            self.canvas.clear()
            self.gun.clear_drips()
            self.refresh_canvas()
            self.notify("Новый холст")
        elif pygame.K_1 <= key <= pygame.K_8:
            self.select_palette(key - pygame.K_1)

    def handle_actions(self, state) -> None:
        if self.edges.just_pressed(state, "calibrate"):
            self.calibrate()
        if self.edges.just_pressed(state, "undo"):
            self.undo()
        if self.edges.just_pressed(state, "redo"):
            self.redo()
        if self.edges.just_pressed(state, "save"):
            self.save_png()
        if self.edges.just_pressed(state, "debug"):
            self.debug = not self.debug
        if self.edges.just_pressed(state, "palette"):
            self.palette_open = not self.palette_open

        if self.palette_open:
            if self.edges.just_pressed(state, "size_up"):
                self.select_palette(self.color_index + 1)
            if self.edges.just_pressed(state, "size_down"):
                self.select_palette(self.color_index - 1)
        else:
            if state.pressed("size_up"):
                self.change_nozzle(1.5)
            if state.pressed("size_down"):
                self.change_nozzle(-1.5)

    def handle_palette_sticks(self) -> None:
        """Правка цвета мини-джойстиком при открытой палитре."""
        self._sticks_active = False
        if not self.palette_open or pygame.joystick.get_count() == 0:
            return
        js = pygame.joystick.Joystick(0)
        if js.get_numaxes() < 2:
            return

        def axis(i):
            v = js.get_axis(i)
            return v if abs(v) > 0.18 else 0.0

        changed = False
        dh, ds = axis(0), axis(1)
        if dh:
            self.hsv[0] = (self.hsv[0] + dh * 0.012) % 1.0
            changed = True
        if ds:
            self.hsv[1] = max(0.0, min(1.0, self.hsv[1] - ds * 0.012))
            changed = True
        if js.get_numaxes() >= 4:
            dv = axis(3)
            if dv:
                self.hsv[2] = max(0.05, min(1.0, self.hsv[2] - dv * 0.012))
                changed = True
        if changed:
            self._sticks_active = True
            self.apply_hsv()

    # -- один шаг -------------------------------------------------------------

    def step(self, dt: float) -> None:
        state = self.backend.poll(dt)
        self.handle_actions(state)
        self.handle_palette_sticks()

        # Поворот хвата применяется один раз к сырым показаниям, после чего
        # вся обработка ниже не знает, как именно держат геймпад.
        gyro, accel = self.apply_grip(state)

        # Движение баллончика: выделяем линейное ускорение и ищем взмахи.
        gravity = self.gravity.update(accel)
        axis_accel = gravity_free_axis_accel(accel, gravity)

        strokes = self.can.update_motion(axis_accel, dt)
        if strokes:
            self.hud.notify_shake()
            self.audio.rattle()
            self.backend.rumble(0.55, self.cfg.shake.rumble_ms)
            if self.can.mix >= self.cfg.shake.good_mix and self.can.mix - \
                    self.cfg.shake.mix_per_stroke < self.cfg.shake.good_mix:
                self.notify("Краска перемешана, можно рисовать")

        # Ориентация и положение точки нанесения.
        try:
            self.filter.update(gyro, accel, dt)
        except CalibrationError as exc:
            self.notify(f"Калибровка не удалась: {exc}", 4.0)

        # Угол удерживается в пределах холста, иначе точка уходит за экран
        # и возвращается только обратной отмоткой на тот же угол.
        self.at_limit = self.filter.clamp_to_canvas(
            self.canvas.width, self.canvas.height)

        distance = self.distance.update(axis_accel, dt)
        point = self.filter.canvas_point(self.canvas.width, self.canvas.height)

        trigger = 0.0 if (self.palette_open or self.filter.calibrating) else state.trigger
        self.audio.spray(trigger)

        was_spraying = self.spraying
        self.spraying = trigger > 0.06

        if self.spraying and not was_spraying:
            self.canvas.begin_stroke()
            self.prev_point = point
        if not self.spraying:
            self.prev_point = None

        # На краю холста краска не наносится: баллончик смотрит мимо стены.
        # Иначе при упоре в край пятно размазывалось бы по границе.
        inside = not self.at_limit

        # Резкий взмах может перебросить точку через весь холст. Соединять
        # такие положения следом неверно: это разрыв, а не движение кисти.
        start = self.prev_point or point
        jump = (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2
        if jump > self._max_jump_sq:
            start = point

        if self.spraying and inside:
            self.gun.paint(
                self.canvas.pixels, self.canvas.wet,
                start, point,
                self.nozzle, distance, self.color, trigger,
                self.can.quality(), dt,
            )
            self.prev_point = point

        # Любое использование органов управления сбрасывает таймер покоя.
        if (self.spraying or self.palette_open or self.filter.calibrating
                or any(state.buttons.values()) or self._key_activity
                or self._sticks_active):
            self.idle_time = 0.0
        else:
            self.idle_time += dt
        self._key_activity = False

        if self.idle_time >= self.cfg.orientation.auto_center_delay:
            self.filter.auto_center(dt)

        self.can.update_settling(dt, spraying=self.spraying)
        self.gun.update_drips(self.canvas.pixels, self.canvas.wet, dt)
        dry(self.canvas.wet, dt)

        box = self.gun.take_dirty()
        if box:
            self._blit_region(*box)

        saved = self.canvas.maybe_autosave(OUTPUT_DIR)
        if saved:
            self.notify("Автосохранение выполнено", 2.0)

        self.draw(state, point, distance, gyro, accel)

    # -- отрисовка ------------------------------------------------------------

    def apply_grip(self, state):
        """Привести показания датчиков к системе координат обычного хвата."""
        if not getattr(self.backend, "applies_grip", True):
            return state.gyro, state.accel
        angle = self.cfg.orientation.grip_rotation
        return rotate_grip(state.gyro, angle), rotate_grip(state.accel, angle)

    def cycle_grip(self) -> None:
        """Перебрать варианты хвата: 0, 90, 180, 270 градусов."""
        cfg = self.cfg.orientation
        cfg.grip_rotation = (cfg.grip_rotation + 90.0) % 360.0
        # Смена системы координат сбивает накопленные углы, поэтому
        # текущее направление сразу объявляется центром холста.
        self.filter.recenter()
        self.distance.reset()
        self.gravity = GravityEstimator()
        names = {0.0: "обычный", 90.0: "как баллончик",
                 180.0: "перевёрнутый", 270.0: "как баллончик, зеркально"}
        name = names.get(cfg.grip_rotation, "")
        self.notify(f"Хват: {int(cfg.grip_rotation)} градусов ({name})", 4.0)

    def draw(self, state, point, distance, gyro=None, accel=None) -> None:
        self.screen.blit(self.canvas_surface, (0, 0))

        radius = self.gun.radius_for(self.nozzle, distance)
        if not self.palette_open:
            self.hud.draw_crosshair(self.screen, point, radius, self.color,
                                    self.spraying, self.at_limit)

        self.hud.draw_can(self.screen, 24, 120, self.can, self.color)
        self.hud.draw_shake_prompt(self.screen, self.can)

        if self.filter.calibrating:
            pct = int(self.filter.calibration_progress * 100)
            img = self.fonts.big.render(f"Калибровка {pct} %", True, ACCENT)
            self.screen.blit(img, (self.screen.get_width() // 2 - img.get_width() // 2,
                                   self.screen.get_height() // 2 - 20))

        if self.palette_open:
            self.hud.draw_palette(self.screen, self.palette, self.color_index, self.hsv)

        if self.debug:
            self.hud.draw_debug(
                self.screen, self.debug_rows(state, point, distance, gyro, accel))

        self.hud.draw_status(self.screen, self.status_lines(state))
        pygame.display.flip()

    def status_lines(self, state):
        conn = (f"источник: {self.backend.name}", GOOD if state.connected else ALARM)
        lines = [conn]
        if state.battery not in ("неизвестно", "не применимо"):
            lines.append((f"заряд: {state.battery}", PAPER))
        lines.append((f"сопло: {int(self.nozzle)} px", PAPER))
        lines.append((f"цвет: ячейка {self.color_index + 1}", PAPER))
        mix_colour = GOOD if not self.can.needs_shaking else ALARM
        lines.append((f"перемешано: {int(self.can.mix * 100)} %", mix_colour))
        if time.time() < self.message_until:
            lines.append((self.message, ACCENT))
        elif state.note:
            lines.append((state.note, (150, 150, 158)))
        return lines

    def debug_rows(self, state, point, distance, gyro=None, accel=None):
        # Показываются уже повёрнутые значения: именно их видит фильтр.
        g = gyro if gyro is not None else state.gyro
        a = accel if accel is not None else state.accel
        return [
            ("частота отчётов", f"{state.rate_hz:.0f} Гц"),
            ("хват", f"{int(self.cfg.orientation.grip_rotation)} градусов"),
            ("кадров в секунду", f"{self.fps:.0f}"),
            ("гироскоп x", f"{g[0]:+.3f} рад/с"),
            ("гироскоп y", f"{g[1]:+.3f} рад/с"),
            ("гироскоп z", f"{g[2]:+.3f} рад/с"),
            ("ускорение", f"{a[0]:+.1f} {a[1]:+.1f} {a[2]:+.1f}"),
            ("рыскание", f"{self.filter.pose.yaw:+.3f} рад"),
            ("тангаж", f"{self.filter.pose.pitch:+.3f} рад"),
            ("смещение нуля", f"{self.filter.bias[1]:+.4f}"),
            ("точка", f"{point[0]:.0f}, {point[1]:.0f}"),
            ("удаление", f"{distance:+.2f}"),
            ("перемешивание", f"{self.can.mix:.2f}"),
            ("взмахов всего", str(self.can.detector.total_strokes)),
            ("подтёков", str(len(self.gun.drips))),
        ]

    # -- цикл -----------------------------------------------------------------

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            dt = clock.tick(self.cfg.target_fps) / 1000.0
            dt = min(dt, 0.05)
            self.fps = clock.get_fps()
            self.hud.update(dt)
            self.handle_events()
            if not self.running:
                break
            self.step(dt)

        self.audio.stop()
        self.backend.close()
        pygame.quit()
