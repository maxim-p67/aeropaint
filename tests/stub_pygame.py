"""Минимальная заглушка pygame для проверки оболочки без графики.

Нужна только для тестов: позволяет прогнать главный цикл приложения
в окружении, где pygame не установлен, и поймать ошибки в собственном
коде проекта. Поведение pygame она не воспроизводит и для запуска
приложения не годится.
"""

import sys
import types

import numpy as np

SRCALPHA = 0x00010000
QUIT = 256
KEYDOWN = 768

_KEYS = {
    "K_ESCAPE": 27, "K_c": 99, "K_r": 114, "K_z": 122, "K_y": 121,
    "K_s": 115, "K_p": 112, "K_n": 110, "K_g": 103, "K_F1": 1073741882,
    "K_LEFTBRACKET": 91, "K_RIGHTBRACKET": 93,
    "K_1": 49, "K_2": 50, "K_3": 51, "K_4": 52,
    "K_5": 53, "K_6": 54, "K_7": 55, "K_8": 56,
}


class Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = int(x), int(y), int(w), int(h)

    def inflate(self, dx, dy):
        return Rect(self.x - dx // 2, self.y - dy // 2,
                    self.width + dx, self.height + dy)


class Surface:
    def __init__(self, size, flags=0):
        self.size = (int(size[0]), int(size[1]))
        self._array = np.zeros((self.size[0], self.size[1], 3), dtype=np.uint8)
        self._alpha = 255

    def get_size(self):
        return self.size

    def get_width(self):
        return self.size[0]

    def get_height(self):
        return self.size[1]

    def fill(self, colour):
        pass

    def blit(self, other, pos):
        pass

    def set_alpha(self, value):
        self._alpha = value

    def convert(self, *a):
        return self

    def convert_alpha(self, *a):
        return self


class _Draw:
    @staticmethod
    def rect(surface, colour, rect, width=0, border_radius=0):
        assert isinstance(rect, Rect), "ожидался Rect"

    @staticmethod
    def circle(surface, colour, centre, radius, width=0):
        assert len(centre) == 2


class _Font:
    def __init__(self, path, size):
        self.size = size

    def render(self, text, antialias, colour):
        # Ширина прикидывается, чтобы центрирование в интерфейсе считалось.
        return Surface((max(1, len(str(text)) * self.size // 2), self.size))


class _FontModule:
    Font = _Font

    @staticmethod
    def match_font(name, bold=False):
        return "/stub/font.ttf" if name == "dejavusans" else None

    @staticmethod
    def init():
        pass


class _Surfarray:
    @staticmethod
    def pixels3d(surface):
        return surface._array


class _Clock:
    def __init__(self):
        self._frames = 0

    def tick(self, fps):
        self._frames += 1
        return 1000 // max(1, fps)

    def get_fps(self):
        return 60.0


class _Time:
    Clock = _Clock


class _Display:
    _surface = None

    @staticmethod
    def set_caption(text):
        pass

    @staticmethod
    def set_mode(size, flags=0):
        _Display._surface = Surface(size)
        return _Display._surface

    @staticmethod
    def flip():
        pass


class _Event:
    queue = []

    @staticmethod
    def get():
        out = list(_Event.queue)
        _Event.queue.clear()
        return out

    @staticmethod
    def pump():
        pass

    @staticmethod
    def set_grab(value):
        pass


class _Mouse:
    delta = (0, 0)
    buttons = (False, False, False)

    @staticmethod
    def get_rel():
        return _Mouse.delta

    @staticmethod
    def get_pressed(num_buttons=3):
        return _Mouse.buttons

    @staticmethod
    def set_visible(value):
        pass


class _Joystick:
    count = 0

    @staticmethod
    def init():
        pass

    @staticmethod
    def get_count():
        return _Joystick.count


class _Mixer:
    @staticmethod
    def pre_init(*a, **k):
        pass

    @staticmethod
    def init(*a, **k):
        raise RuntimeError("звук в заглушке не поддерживается")

    @staticmethod
    def stop():
        pass


def install():
    """Подставить заглушку вместо pygame в sys.modules."""
    pg = types.ModuleType("pygame")
    pg.init = lambda: None
    pg.quit = lambda: None
    pg.get_init = lambda: True
    pg.Surface = Surface
    pg.Rect = Rect
    pg.SRCALPHA = SRCALPHA
    pg.QUIT = QUIT
    pg.KEYDOWN = KEYDOWN
    for name, code in _KEYS.items():
        setattr(pg, name, code)
    pg.draw = _Draw
    pg.font = _FontModule
    pg.surfarray = _Surfarray
    pg.time = _Time
    pg.display = _Display
    pg.event = _Event
    pg.mouse = _Mouse
    pg.joystick = _Joystick
    pg.mixer = _Mixer
    pg.version = types.SimpleNamespace(ver="stub", SDL=(2, 0, 0))

    sys.modules["pygame"] = pg
    sys.modules["pygame.font"] = _FontModule
    sys.modules["pygame.surfarray"] = _Surfarray
    return pg
