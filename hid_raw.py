"""Чтение датчиков напрямую из HID-отчётов контроллера.

Запасной путь на случай, если SDL не отдаёт датчики. Работает в двух режимах:

* preset "switch_pro" - разбор стандартного полного отчёта 0x30, который
  контроллер выдаёт в режиме Nintendo Switch Pro. Формат общеизвестен:
  три кадра инерциальных данных по 12 байт в каждом отчёте;
* preset "custom"     - смещения байтов, масштабы и знаки берутся из
  mapping.json. Их подбирает диагностический скрипт probe_gamepad.py.

Зависит от пакета hidapi. Если он не установлен, источник просто
сообщает о своей недоступности.
"""

from __future__ import annotations

import math
import struct
import threading
import time

from .base import InputBackend, RateMeter, SprayState

# Масштабы штатной калибровки контроллера Switch Pro.
SWITCH_ACCEL_LSB_G = 0.000244          # g на единицу
SWITCH_GYRO_LSB_DPS = 0.06103          # град/с на единицу
G = 9.80665


def _open_hid():
    try:
        import hid  # noqa: F401

        return hid
    except Exception:
        return None


class HidRawBackend(InputBackend):
    name = "HID"
    description = "разбор сырых отчётов контроллера"

    def __init__(self, vid: int, pid: int, preset: str = "switch_pro",
                 custom: dict | None = None, invert=(1.0, 1.0, 1.0)) -> None:
        self.vid = int(vid)
        self.pid = int(pid)
        self.preset = preset
        self.custom = custom or {}
        self.invert = tuple(float(v) for v in invert)

        self.device = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._latest = SprayState()
        self._rate = RateMeter()
        self._packet_number = 0
        self._note = ""

    # -- подключение ----------------------------------------------------------

    def start(self) -> bool:
        hid = _open_hid()
        if hid is None:
            self._note = "пакет hidapi не установлен"
            return False
        try:
            self.device = hid.Device(self.vid, self.pid)
        except Exception as exc:
            self._note = f"устройство не открылось: {exc}"
            return False

        self.device.nonblocking = False
        if self.preset == "switch_pro":
            self._init_switch_pro()

        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self._note = f"чтение отчётов, формат {self.preset}"
        return True

    def _send_subcommand(self, sub_id: int, args: bytes) -> None:
        """Команда контроллеру Switch Pro: отчёт 0x01 с нейтральной вибрацией."""
        self._packet_number = (self._packet_number + 1) & 0x0F
        rumble = bytes([0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40])
        packet = bytes([0x01, self._packet_number]) + rumble + bytes([sub_id]) + args
        packet = packet.ljust(64, b"\x00")
        try:
            self.device.write(packet)
            time.sleep(0.05)
        except Exception:
            pass

    def _init_switch_pro(self) -> None:
        # Включить инерциальные датчики и перевести контроллер
        # в режим полных отчётов 0x30 с частотой 60 Гц.
        self._send_subcommand(0x40, bytes([0x01]))
        self._send_subcommand(0x03, bytes([0x30]))

    # -- фоновое чтение -------------------------------------------------------

    def _reader(self) -> None:
        while self._running:
            try:
                data = self.device.read(64, timeout=200)
            except Exception:
                time.sleep(0.05)
                continue
            if not data:
                continue
            parsed = self._parse(bytes(data))
            if parsed is None:
                continue
            gyro, accel = parsed
            with self._lock:
                self._latest.connected = True
                self._latest.has_motion = True
                self._latest.gyro = gyro
                self._latest.accel = accel
            self._rate.tick()

    def _parse(self, data: bytes):
        if self.preset == "switch_pro":
            return self._parse_switch(data)
        return self._parse_custom(data)

    def _parse_switch(self, data: bytes):
        if len(data) < 49 or data[0] != 0x30:
            return None
        # Берётся последний из трёх кадров - он самый свежий.
        offset = 13 + 24
        ax, ay, az, gx, gy, gz = struct.unpack_from("<6h", data, offset)
        accel = (ax * SWITCH_ACCEL_LSB_G * G,
                 ay * SWITCH_ACCEL_LSB_G * G,
                 az * SWITCH_ACCEL_LSB_G * G)
        k = SWITCH_GYRO_LSB_DPS * math.pi / 180.0
        gyro = (gx * k * self.invert[0],
                gy * k * self.invert[1],
                gz * k * self.invert[2])
        return gyro, accel

    def _parse_custom(self, data: bytes):
        c = self.custom
        report_id = c.get("report_id")
        if report_id is not None and (not data or data[0] != report_id):
            return None
        try:
            fmt = "<3h"
            gx, gy, gz = struct.unpack_from(fmt, data, c["gyro_offset"])
            ax, ay, az = struct.unpack_from(fmt, data, c["accel_offset"])
        except (KeyError, struct.error):
            return None
        gs = c.get("gyro_scale", SWITCH_GYRO_LSB_DPS * math.pi / 180.0)
        acs = c.get("accel_scale", SWITCH_ACCEL_LSB_G * G)
        gyro = (gx * gs * self.invert[0], gy * gs * self.invert[1], gz * gs * self.invert[2])
        accel = (ax * acs, ay * acs, az * acs)
        return gyro, accel

    # -- интерфейс ------------------------------------------------------------

    def poll(self, dt: float) -> SprayState:
        with self._lock:
            state = SprayState(
                connected=self._latest.connected,
                has_motion=self._latest.has_motion,
                gyro=self._latest.gyro,
                accel=self._latest.accel,
            )
        state.rate_hz = self._rate.value
        state.note = self._note

        # Кнопки и курок берутся из pygame: разбирать их из сырых отчётов
        # ради тех же значений смысла нет.
        try:
            import pygame

            if pygame.joystick.get_count():
                js = pygame.joystick.Joystick(0)
                if not js.get_init():
                    js.init()
                raw = js.get_axis(5) if js.get_numaxes() > 5 else -1.0
                state.trigger = max(0.0, min(1.0, (raw + 1.0) * 0.5))
                from .sdl_sensor import DEFAULT_BUTTON_MAP

                count = js.get_numbuttons()
                state.buttons = {
                    a: (bool(js.get_button(i)) if 0 <= i < count else False)
                    for a, i in DEFAULT_BUTTON_MAP.items()
                }
                state.buttons["spray"] = state.trigger > 0.06
        except Exception:
            pass
        return state

    def rumble(self, strength: float, ms: int) -> None:
        try:
            import pygame

            if pygame.joystick.get_count():
                pygame.joystick.Joystick(0).rumble(strength, strength, ms)
        except Exception:
            pass

    def close(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
        self.device = None
