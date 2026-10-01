"""Показания датчиков через SDL GameController Sensor API.

Это основной путь. SDL умеет отдавать гироскоп и акселерометр для
контроллеров, которые публикуют их по HID: в частности, для геймпадов
в режиме Nintendo Switch Pro. Flydigi Apex 5 такой режим поддерживает.

pygame не оборачивает этот участок SDL, поэтому нужные функции
вызываются через ctypes в той же библиотеке SDL2, которую уже загрузил
pygame: отдельного экземпляра SDL при этом не создаётся.
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys

from .base import InputBackend, RateMeter, SprayState

SDL_SENSOR_ACCEL = 1
SDL_SENSOR_GYRO = 2

# Раскладка кнопок SDL GameController - одинаковая для всех контроллеров,
# которые SDL распознал, поэтому её можно задать константой.
DEFAULT_BUTTON_MAP = {
    "palette": 0,     # A
    "undo": 1,        # B
    "redo": 2,        # X
    "calibrate": 3,   # Y
    "size_down": 9,   # LB
    "size_up": 10,    # RB
    "debug": 4,       # Back / View
    "save": 6,        # Start / Menu
}

SDL_CONTROLLER_AXIS_TRIGGERRIGHT = 5


def find_sdl2() -> ctypes.CDLL | None:
    """Найти ту библиотеку SDL2, которую использует pygame."""
    candidates: list = []
    try:
        import pygame

        base = os.path.dirname(os.path.abspath(pygame.__file__))
        if sys.platform.startswith("win"):
            candidates += glob.glob(os.path.join(base, "SDL2*.dll"))
        elif sys.platform == "darwin":
            candidates += glob.glob(os.path.join(base, ".dylibs", "libSDL2*.dylib"))
        else:
            candidates += glob.glob(os.path.join(base, ".libs", "libSDL2*.so*"))
    except Exception:
        pass

    from ctypes.util import find_library

    system = find_library("SDL2")
    if system:
        candidates.append(system)

    for path in candidates:
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    return None


class SdlSensorBackend(InputBackend):
    name = "SDL Sensor"
    description = "гироскоп и акселерометр через SDL GameController"

    def __init__(self, index: int = 0, invert=(1.0, 1.0, 1.0),
                 button_map: dict | None = None) -> None:
        self.index = index
        self.invert = tuple(float(v) for v in invert)
        self.button_map = dict(button_map or DEFAULT_BUTTON_MAP)
        self.sdl = None
        self.controller = None
        self.joystick = None
        self._gyro_buf = (ctypes.c_float * 3)()
        self._accel_buf = (ctypes.c_float * 3)()
        self._rate = RateMeter()
        self._note = ""

    # -- подключение ----------------------------------------------------------

    def _bind(self, sdl) -> None:
        sdl.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
        sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
        sdl.SDL_GameControllerHasSensor.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GameControllerHasSensor.restype = ctypes.c_int
        sdl.SDL_GameControllerSetSensorEnabled.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        sdl.SDL_GameControllerSetSensorEnabled.restype = ctypes.c_int
        sdl.SDL_GameControllerGetSensorData.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        sdl.SDL_GameControllerGetSensorData.restype = ctypes.c_int
        sdl.SDL_IsGameController.argtypes = [ctypes.c_int]
        sdl.SDL_IsGameController.restype = ctypes.c_int
        sdl.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
        sdl.SDL_GameControllerClose.restype = None
        sdl.SDL_GetError.restype = ctypes.c_char_p

    def start(self) -> bool:
        import pygame

        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() <= self.index:
            self._note = "геймпад не найден"
            return False

        self.joystick = pygame.joystick.Joystick(self.index)
        self.joystick.init()

        sdl = find_sdl2()
        if sdl is None:
            self._note = "библиотека SDL2 не найдена"
            return False
        try:
            self._bind(sdl)
        except AttributeError:
            self._note = "версия SDL2 старше 2.0.14, датчики недоступны"
            return False
        self.sdl = sdl

        if not sdl.SDL_IsGameController(self.index):
            self._note = "SDL не распознал устройство как геймпад"
            return False

        ctrl = sdl.SDL_GameControllerOpen(self.index)
        if not ctrl:
            self._note = "SDL не смог открыть геймпад"
            return False
        self.controller = ctypes.c_void_p(ctrl)

        has_gyro = bool(sdl.SDL_GameControllerHasSensor(self.controller, SDL_SENSOR_GYRO))
        has_accel = bool(sdl.SDL_GameControllerHasSensor(self.controller, SDL_SENSOR_ACCEL))
        if not (has_gyro and has_accel):
            self._note = "контроллер не публикует датчики движения"
            self.close()
            return False

        sdl.SDL_GameControllerSetSensorEnabled(self.controller, SDL_SENSOR_GYRO, 1)
        sdl.SDL_GameControllerSetSensorEnabled(self.controller, SDL_SENSOR_ACCEL, 1)
        self._note = "датчики включены"
        return True

    # -- опрос ----------------------------------------------------------------

    def poll(self, dt: float) -> SprayState:
        import pygame

        state = SprayState(connected=True, has_motion=True)

        self.sdl.SDL_GameControllerGetSensorData(
            self.controller, SDL_SENSOR_GYRO, self._gyro_buf, 3)
        self.sdl.SDL_GameControllerGetSensorData(
            self.controller, SDL_SENSOR_ACCEL, self._accel_buf, 3)

        state.gyro = tuple(self._gyro_buf[i] * self.invert[i] for i in range(3))
        state.accel = tuple(self._accel_buf[i] for i in range(3))

        js = self.joystick
        try:
            # Курок в SDL даёт значение от -1 (отпущен) до 1 (нажат).
            raw = js.get_axis(SDL_CONTROLLER_AXIS_TRIGGERRIGHT)
            state.trigger = max(0.0, min(1.0, (raw + 1.0) * 0.5))
        except Exception:
            state.trigger = 0.0

        buttons = {}
        count = js.get_numbuttons()
        for action, idx in self.button_map.items():
            buttons[action] = bool(js.get_button(idx)) if 0 <= idx < count else False
        buttons["spray"] = state.trigger > 0.06
        state.buttons = buttons

        try:
            state.battery = _POWER.get(js.get_power_level(), "неизвестно")
        except Exception:
            pass

        state.rate_hz = self._rate.tick()
        state.note = self._note
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


_POWER = {
    "empty": "разряжен",
    "low": "низкий",
    "medium": "средний",
    "full": "полный",
    "wired": "питание по кабелю",
    "max": "полный",
    "unknown": "неизвестно",
}
