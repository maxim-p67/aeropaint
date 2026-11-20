"""Гироскоп и акселерометр геймпада через SDL GameController Sensor API.

pygame эти функции не оборачивает, поэтому они вызываются через ctypes
из той же библиотеки SDL2, которую уже загрузил pygame.
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys

from .base import InputBackend, RateMeter, SprayState

SENSOR_ACCEL, SENSOR_GYRO = 1, 2
TRIGGER_AXIS = 5  # правый курок

# Кнопки геймпада -> действия приложения.
BUTTON_MAP = {"palette": 0, "undo": 1, "redo": 2, "calibrate": 3,
              "debug": 4, "save": 6, "size_down": 9, "size_up": 10}

POWER = {"empty": "разряжен", "low": "низкий", "medium": "средний", "full": "полный",
         "wired": "питание по кабелю", "max": "полный"}

_c_void, _c_int, _c_float = ctypes.c_void_p, ctypes.c_int, ctypes.c_float


def find_sdl2() -> ctypes.CDLL | None:
    """Найти SDL2, которую использует pygame (или системную)."""
    import pygame
    from ctypes.util import find_library

    base = os.path.dirname(os.path.abspath(pygame.__file__))
    patterns = {"win": "SDL2*.dll", "darwin": ".dylibs/libSDL2*.dylib"}
    pattern = next((p for k, p in patterns.items() if sys.platform.startswith(k)),
                   ".libs/libSDL2*.so*")
    candidates = glob.glob(os.path.join(base, pattern)) + [find_library("SDL2")]
    for path in filter(None, candidates):
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    return None


class SdlSensorBackend(InputBackend):
    name = "SDL Sensor"
    description = "гироскоп и акселерометр через SDL GameController"

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self.sdl = None
        self.controller = None
        self.joystick = None
        self._gyro = (_c_float * 3)()
        self._accel = (_c_float * 3)()
        self._rate = RateMeter()
        self.note = ""

    def _bind(self, sdl) -> None:
        """Объяснить ctypes, какие типы принимают и возвращают функции SDL."""
        sigs = {
            "SDL_GameControllerOpen": ([_c_int], _c_void),
            "SDL_GameControllerHasSensor": ([_c_void, _c_int], _c_int),
            "SDL_GameControllerSetSensorEnabled": ([_c_void, _c_int, _c_int], _c_int),
            "SDL_GameControllerGetSensorData": ([_c_void, _c_int, ctypes.POINTER(_c_float), _c_int], _c_int),
            "SDL_IsGameController": ([_c_int], _c_int),
            "SDL_GameControllerClose": ([_c_void], None),
        }
        for name, (args, res) in sigs.items():
            fn = getattr(sdl, name)  # AttributeError, если SDL старее 2.0.14
            fn.argtypes, fn.restype = args, res

    def start(self) -> bool:
        import pygame

        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() <= self.index:
            self.note = "геймпад не найден"
            return False
        self.joystick = pygame.joystick.Joystick(self.index)
        self.joystick.init()

        sdl = find_sdl2()
        if sdl is None:
            self.note = "библиотека SDL2 не найдена"
            return False
        try:
            self._bind(sdl)
        except AttributeError:
            self.note = "версия SDL2 старше 2.0.14, датчики недоступны"
            return False
        self.sdl = sdl

        if not sdl.SDL_IsGameController(self.index):
            self.note = "SDL не распознал устройство как геймпад"
            return False
        ctrl = sdl.SDL_GameControllerOpen(self.index)
        if not ctrl:
            self.note = "SDL не смог открыть геймпад"
            return False
        self.controller = _c_void(ctrl)

        if not all(sdl.SDL_GameControllerHasSensor(self.controller, s)
                   for s in (SENSOR_GYRO, SENSOR_ACCEL)):
            self.note = "контроллер не публикует датчики движения"
            self.close()
            return False
        for s in (SENSOR_GYRO, SENSOR_ACCEL):
            sdl.SDL_GameControllerSetSensorEnabled(self.controller, s, 1)
        self.note = "датчики включены"
        return True

    def poll(self, dt: float) -> SprayState:
        state = SprayState(connected=True, note=self.note)
        get = self.sdl.SDL_GameControllerGetSensorData
        get(self.controller, SENSOR_GYRO, self._gyro, 3)
        get(self.controller, SENSOR_ACCEL, self._accel, 3)
        state.gyro, state.accel = tuple(self._gyro), tuple(self._accel)

        js = self.joystick
        try:  # курок в SDL: от -1 (отпущен) до 1 (нажат)
            state.trigger = max(0.0, min(1.0, (js.get_axis(TRIGGER_AXIS) + 1.0) * 0.5))
        except Exception:
            pass
        count = js.get_numbuttons()
        state.buttons = {a: 0 <= i < count and bool(js.get_button(i)) for a, i in BUTTON_MAP.items()}
        state.buttons["spray"] = state.trigger > 0.06
        try:
            state.battery = POWER.get(js.get_power_level(), "неизвестно")
        except Exception:
            pass
        state.rate_hz = self._rate.tick()
        return state

    def rumble(self, strength: float, ms: int) -> None:
        try:
            self.joystick.rumble(strength, strength, ms)
        except Exception:
            pass

    def close(self) -> None:
        if self.sdl and self.controller:
            try:
                self.sdl.SDL_GameControllerClose(self.controller)
            except Exception:
                pass
        self.controller = None
