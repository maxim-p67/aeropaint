"""Экранные элементы: баллончик, палитра, прицел, строка состояния, отладка."""

from __future__ import annotations

import colorsys
import math

import pygame

# Шрифт pygame по умолчанию без кириллицы - берём первый найденный системный.
FONT_CANDIDATES = ("dejavusans", "arial", "segoeui", "tahoma", "verdana",
                   "liberationsans", "freesans", "notosans")

INK, PAPER, PANEL = (28, 28, 32), (245, 243, 238), (24, 24, 28)
ACCENT, ALARM, GOOD = (235, 196, 60), (232, 84, 72), (96, 196, 124)
GREY, DIM = (168, 168, 176), (150, 150, 158)


def load_font(size: int, bold: bool = False):
    for name in FONT_CANDIDATES:
        path = pygame.font.match_font(name, bold=bold)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


class Fonts:
    def __init__(self) -> None:
        self.small, self.body = load_font(15), load_font(18)
        self.bold, self.big = load_font(18, bold=True), load_font(34, bold=True)


def to255(color) -> tuple:
    return tuple(int(max(0.0, min(1.0, c)) * 255) for c in color)


def panel(surface, rect, alpha=200, colour=PANEL) -> None:
    """Полупрозрачная тёмная подложка."""
    layer = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    layer.fill((*colour, alpha))
    surface.blit(layer, (rect[0], rect[1]))


class Hud:
    def __init__(self, fonts: Fonts) -> None:
        self.f = fonts
        self._pulse = 0.0
        self._shake_flash = 0.0

    def notify_shake(self) -> None:
        self._shake_flash = 1.0

    def update(self, dt: float) -> None:
        self._pulse += dt
        self._shake_flash = max(0.0, self._shake_flash - dt * 4.0)

    def _text(self, surface, font, text, colour, cx, y) -> None:
        """Текст, выровненный по центру относительно cx."""
        img = font.render(text, True, colour)
        surface.blit(img, (cx - img.get_width() // 2, y))

    def draw_can(self, surface, x, y, can, color) -> None:
        """Баллончик с уровнем перемешивания и шариком внутри."""
        w, h = 58, 150
        x += int(self._shake_flash * 4.0 * math.sin(self._pulse * 60.0))  # дрожит при взмахе
        panel(surface, (x - 12, y - 34, w + 24, h + 66), 190)
        pygame.draw.rect(surface, (58, 58, 64), pygame.Rect(x, y, w, h), border_radius=8)

        level = max(0.0, min(1.0, can.mix))
        fill_h = int((h - 8) * level)
        if fill_h > 0:
            pygame.draw.rect(surface, ALARM if can.needs_shaking else GOOD,
                             pygame.Rect(x + 4, y + h - 4 - fill_h, w - 8, fill_h), border_radius=6)

        pygame.draw.rect(surface, (90, 90, 98), pygame.Rect(x + 14, y - 16, w - 28, 16), border_radius=4)
        pygame.draw.rect(surface, to255(color), pygame.Rect(x + 6, y + 8, w - 12, 14), border_radius=3)

        ball_y = y + h - 16  # шарик лежит на дне, а при встряхивании мечется
        if self._shake_flash > 0.05:
            ball_y = y + 22 + int((h - 44) * (0.5 + 0.5 * math.sin(self._pulse * 43.0)))
        pygame.draw.circle(surface, (228, 228, 232), (x + w // 2, ball_y), 7)

        cx = x + w // 2
        self._text(surface, self.f.bold, f"{int(level * 100)} %", PAPER, cx, y + h + 8)
        self._text(surface, self.f.small, "перемешано", GREY, cx, y + h + 28)
        if can.needs_shaking:
            self._text(surface, self.f.small, f"взмахов: {can.strokes_to_good}", ALARM, cx, y - 32)

    def draw_shake_prompt(self, surface, can) -> None:
        if not can.needs_shaking:
            return
        alpha = int(130 + 90 * (0.5 + 0.5 * math.sin(self._pulse * 4.0)))
        cx = surface.get_width() // 2
        for font, text, colour, y in (
                (self.f.big, "ВСТРЯХНИТЕ БАЛЛОНЧИК", ALARM, 44),
                (self.f.body, "резкие движения вперёд-назад поднимают пигмент со дна", (150, 120, 118), 86)):
            img = font.render(text, True, colour)
            img.set_alpha(alpha)
            surface.blit(img, (cx - img.get_width() // 2, y))

    def draw_crosshair(self, surface, point, radius, color, spraying, at_limit=False) -> None:
        x, y = int(point[0]), int(point[1])
        r = max(3, int(radius))
        if at_limit:  # упёрлись в край: краска не идёт
            pygame.draw.circle(surface, ALARM, (x, y), r, 2)
            pygame.draw.circle(surface, ALARM, (x, y), 3)
            return
        pygame.draw.circle(surface, (*to255(color), 255), (x, y), r, 2 if spraying else 1)
        pygame.draw.circle(surface, INK, (x, y), 2)
        if spraying:
            pygame.draw.circle(surface, ACCENT, (x, y), r + 4, 1)

    def draw_status(self, surface, lines) -> None:
        h = surface.get_height()
        panel(surface, (0, h - 30, surface.get_width(), 30), 205)
        x = 14
        for text, colour in lines:
            img = self.f.small.render(text, True, colour)
            surface.blit(img, (x, h - 22))
            x += img.get_width() + 22

    def draw_palette(self, surface, palette, index, hsv) -> None:
        w, h = surface.get_size()
        box_w, box_h = 520, 240
        x, y = w // 2 - box_w // 2, h // 2 - box_h // 2
        panel(surface, (x, y, box_w, box_h), 232)
        surface.blit(self.f.bold.render("Палитра", True, PAPER), (x + 20, y + 16))

        cell, gap = 52, 10
        for i, colour in enumerate(palette):
            cx = x + 20 + i * (cell + gap)
            rect = pygame.Rect(cx, y + 52, cell, cell)
            pygame.draw.rect(surface, to255(colour), rect, border_radius=6)
            if i == index:
                pygame.draw.rect(surface, ACCENT, rect.inflate(8, 8), 2, border_radius=8)
            self._text(surface, self.f.small, str(i + 1), GREY, cx + cell // 2, y + 110)

        current = to255(colorsys.hsv_to_rgb(*hsv))
        for i, (name, value) in enumerate(zip(("тон", "насыщенность", "яркость"), hsv)):
            by = y + 140 + i * 30
            surface.blit(self.f.small.render(name, True, GREY), (x + 20, by))
            bar = pygame.Rect(x + 150, by + 4, 330, 12)
            pygame.draw.rect(surface, (58, 58, 64), bar, border_radius=6)
            pygame.draw.rect(surface, current, pygame.Rect(bar.x, bar.y, int(bar.width * value), bar.height),
                             border_radius=6)

        hint = "левый мини-джойстик - тон и насыщенность, LB/RB - ячейка, A - закрыть"
        surface.blit(self.f.small.render(hint, True, DIM), (x + 20, y + box_h - 26))

    def draw_debug(self, surface, rows) -> None:
        width, height = 330, 26 + 20 * len(rows)
        x, y = surface.get_width() - width - 16, 16
        panel(surface, (x, y, width, height), 214)
        surface.blit(self.f.bold.render("Отладка", True, ACCENT), (x + 14, y + 6))
        for i, (key, value) in enumerate(rows):
            ty = y + 28 + i * 20
            surface.blit(self.f.small.render(key, True, (160, 160, 168)), (x + 14, ty))
            img = self.f.small.render(str(value), True, PAPER)
            surface.blit(img, (x + width - 14 - img.get_width(), ty))
