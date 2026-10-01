#!/usr/bin/env python3
"""Диагностика геймпада: что именно доступно в Python на этой машине.

Скрипт по очереди проверяет три способа получить показания гироскопа
и акселерометра, показывает живые значения и записывает результат
в mapping.json, который потом читает само приложение.

Запуск:
    python probe_gamepad.py

Перед запуском включите геймпад и подключите его тем способом, которым
собираетесь пользоваться. Если геймпад умеет переключать режимы,
имеет смысл прогнать проверку в каждом: чаще всего датчики отдаются
в режиме Nintendo Switch.
"""

import json
import math
import os
import struct
import sys
import time

MAPPING_PATH = "mapping.json"
REPORT_PATH = "probe_report.txt"

FLYDIGI_VIDS = (0x04B4, 0x0483, 0x2563, 0x045E, 0x057E, 0x1949, 0x0079)

log_lines = []


def say(text=""):
    print(text)
    log_lines.append(str(text))


def rule(title):
    say()
    say("=" * 68)
    say(title)
    say("=" * 68)


def countdown(seconds, prompt):
    say(prompt)
    for i in range(seconds, 0, -1):
        print(f"  начинаем через {i}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 40, end="\r")


# ---------------------------------------------------------------------------
# 1. Что видит pygame
# ---------------------------------------------------------------------------

def probe_pygame():
    rule("1. Геймпад глазами pygame")
    try:
        import pygame
    except ImportError:
        say("pygame не установлен. Выполните: pip install -r requirements.txt")
        return None, None

    pygame.init()
    pygame.joystick.init()
    say(f"версия pygame: {pygame.version.ver}, SDL: {'.'.join(map(str, pygame.version.SDL))}")

    count = pygame.joystick.get_count()
    say(f"найдено устройств: {count}")
    if count == 0:
        say("Геймпад не обнаружен. Проверьте, включён ли он и видит ли его система.")
        return pygame, None

    js = pygame.joystick.Joystick(0)
    js.init()
    say(f"название: {js.get_name()}")
    say(f"GUID: {js.get_guid()}")
    say(f"осей: {js.get_numaxes()}, кнопок: {js.get_numbuttons()}, "
        f"крестовин: {js.get_numhats()}")
    try:
        say(f"заряд: {js.get_power_level()}")
    except Exception:
        pass
    return pygame, js


def watch_axes(pygame, js, seconds=8):
    """Показать, какие оси меняются при вращении геймпада."""
    rule("2. Поиск датчиков среди осей джойстика")
    say("Некоторые геймпады отдают гироскоп дополнительными осями.")
    countdown(3, "Возьмите геймпад и медленно поворачивайте его во все стороны.")

    n = js.get_numaxes()
    lo = [9.0] * n
    hi = [-9.0] * n
    end = time.time() + seconds
    while time.time() < end:
        pygame.event.pump()
        for i in range(n):
            v = js.get_axis(i)
            lo[i] = min(lo[i], v)
            hi[i] = max(hi[i], v)
        remaining = end - time.time()
        print(f"  осталось {remaining:3.0f} с", end="\r", flush=True)
        time.sleep(0.01)
    print(" " * 30, end="\r")

    say("ось   минимум  максимум  размах")
    moving = []
    for i in range(n):
        span = hi[i] - lo[i]
        mark = "  <-- меняется" if span > 0.25 else ""
        say(f"{i:>3}   {lo[i]:+7.3f}  {hi[i]:+7.3f}  {span:6.3f}{mark}")
        if span > 0.25:
            moving.append(i)

    say()
    if len(moving) > 4:
        say(f"Осей с заметным размахом: {moving}.")
        say("Часть из них - мини-джойстики и курки. Если среди них есть оси,")
        say("которые двигаются только от поворота корпуса, это и есть гироскоп.")
    else:
        say("Дополнительных осей движения не видно: гироскоп через оси не отдаётся.")
    return moving


# ---------------------------------------------------------------------------
# 3. SDL GameController Sensor API
# ---------------------------------------------------------------------------

def probe_sdl_sensors(pygame, seconds=8):
    rule("3. Датчики через SDL GameController Sensor API")
    import ctypes

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from aeropaint.inputs.sdl_sensor import (SDL_SENSOR_ACCEL, SDL_SENSOR_GYRO,
                                                 find_sdl2)
    except Exception as exc:
        say(f"не удалось загрузить модуль проверки: {exc}")
        return None

    sdl = find_sdl2()
    if sdl is None:
        say("библиотека SDL2 не найдена")
        return None
    say("библиотека SDL2 загружена")

    try:
        sdl.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
        sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
        sdl.SDL_GameControllerHasSensor.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GameControllerHasSensor.restype = ctypes.c_int
        sdl.SDL_GameControllerSetSensorEnabled.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        sdl.SDL_GameControllerGetSensorData.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        sdl.SDL_GameControllerGetSensorData.restype = ctypes.c_int
        sdl.SDL_IsGameController.argtypes = [ctypes.c_int]
        sdl.SDL_IsGameController.restype = ctypes.c_int
    except AttributeError:
        say("в этой сборке SDL2 нет функций работы с датчиками (нужна версия 2.0.14+)")
        return None

    if not sdl.SDL_IsGameController(0):
        say("SDL не считает устройство игровым контроллером")
        say("подсказка: помогает переключение геймпада в режим Switch или XInput")
        return None

    ctrl = sdl.SDL_GameControllerOpen(0)
    if not ctrl:
        say("SDL не смог открыть контроллер")
        return None
    ctrl = ctypes.c_void_p(ctrl)

    has_gyro = bool(sdl.SDL_GameControllerHasSensor(ctrl, SDL_SENSOR_GYRO))
    has_accel = bool(sdl.SDL_GameControllerHasSensor(ctrl, SDL_SENSOR_ACCEL))
    say(f"гироскоп доступен: {'да' if has_gyro else 'нет'}")
    say(f"акселерометр доступен: {'да' if has_accel else 'нет'}")

    if not (has_gyro and has_accel):
        say()
        say("Датчики через SDL недоступны. Попробуйте переключить геймпад")
        say("в режим Nintendo Switch: в нём контроллеры обычно публикуют IMU.")
        return None

    sdl.SDL_GameControllerSetSensorEnabled(ctrl, SDL_SENSOR_GYRO, 1)
    sdl.SDL_GameControllerSetSensorEnabled(ctrl, SDL_SENSOR_ACCEL, 1)

    gyro = (ctypes.c_float * 3)()
    accel = (ctypes.c_float * 3)()

    countdown(3, "Сейчас будут показаны живые значения. Поворачивайте и потряхивайте геймпад.")
    peak_g = [0.0, 0.0, 0.0]
    peak_a = 0.0
    samples = 0
    end = time.time() + seconds
    while time.time() < end:
        pygame.event.pump()
        sdl.SDL_GameControllerGetSensorData(ctrl, SDL_SENSOR_GYRO, gyro, 3)
        sdl.SDL_GameControllerGetSensorData(ctrl, SDL_SENSOR_ACCEL, accel, 3)
        for i in range(3):
            peak_g[i] = max(peak_g[i], abs(gyro[i]))
        mag = math.sqrt(sum(accel[i] ** 2 for i in range(3)))
        peak_a = max(peak_a, abs(mag - 9.80665))
        samples += 1
        print(f"  гиро {gyro[0]:+6.2f} {gyro[1]:+6.2f} {gyro[2]:+6.2f}   "
              f"ускор {accel[0]:+6.2f} {accel[1]:+6.2f} {accel[2]:+6.2f}",
              end="\r", flush=True)
        time.sleep(0.01)
    print(" " * 78, end="\r")

    say(f"выборок получено: {samples}")
    say(f"максимальная угловая скорость по осям: "
        f"{peak_g[0]:.2f}, {peak_g[1]:.2f}, {peak_g[2]:.2f} рад/с")
    say(f"максимальное линейное ускорение сверх тяжести: {peak_a:.1f} м/с^2")

    ok = max(peak_g) > 0.5
    if ok:
        say()
        say("Гироскоп работает. Приложение будет использовать именно этот путь.")
    else:
        say()
        say("Значения не менялись: датчики объявлены, но данных нет.")
    if peak_a < 6.0:
        say("Внимание: при встряхивании ускорение не превысило порог 6 м/с^2.")
        say("Либо потряхивание было слабым, либо порог стоит снизить")
        say("в aeropaint/config.py, параметр ShakeConfig.accel_threshold.")
    return {"available": ok, "peak_gyro": peak_g, "peak_linear_accel": peak_a}


# ---------------------------------------------------------------------------
# 4. Сырые HID-отчёты
# ---------------------------------------------------------------------------

def probe_hid(seconds=6):
    rule("4. Сырые HID-отчёты")
    try:
        import hid
    except ImportError:
        say("пакет hidapi не установлен, проверка пропущена")
        say("установить: pip install hidapi")
        return None

    try:
        devices = hid.enumerate()
    except Exception as exc:
        say(f"перечисление устройств не удалось: {exc}")
        return None

    say(f"устройств HID в системе: {len(devices)}")
    interesting = []
    for d in devices:
        name = f"{d.get('manufacturer_string') or ''} {d.get('product_string') or ''}".strip()
        vid, pid = d["vendor_id"], d["product_id"]
        if vid in FLYDIGI_VIDS or "flydigi" in name.lower() or "apex" in name.lower() \
                or "pro controller" in name.lower():
            interesting.append(d)
            say(f"  VID {vid:#06x}  PID {pid:#06x}  {name}  "
                f"usage_page={d.get('usage_page')}")

    if not interesting:
        say("Подходящих устройств не нашлось. Ниже первые десять для справки:")
        for d in devices[:10]:
            name = f"{d.get('manufacturer_string') or ''} {d.get('product_string') or ''}".strip()
            say(f"  VID {d['vendor_id']:#06x}  PID {d['product_id']:#06x}  {name}")
        return None

    target = interesting[0]
    vid, pid = target["vendor_id"], target["product_id"]
    say(f"\nчитаем отчёты устройства VID {vid:#06x} PID {pid:#06x}")

    try:
        device = hid.Device(vid, pid)
    except Exception as exc:
        say(f"открыть устройство не удалось: {exc}")
        say("в Windows это обычно значит, что устройство занято другим процессом")
        return None

    countdown(3, "Поворачивайте геймпад, чтобы было видно, какие байты меняются.")
    reports = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            data = device.read(64, timeout=100)
        except Exception:
            break
        if data:
            reports.append(bytes(data))
    device.close()

    say(f"получено отчётов: {len(reports)}")
    if not reports:
        say("Отчёты не приходят. Возможно, нужен запрос конфигурации устройства.")
        return None

    ids = {}
    for r in reports:
        ids.setdefault(r[0], 0)
        ids[r[0]] += 1
    say("идентификаторы отчётов: " +
        ", ".join(f"{i:#04x} ({n})" for i, n in sorted(ids.items())))

    main_id = max(ids, key=ids.get)
    same = [r for r in reports if r[0] == main_id]
    length = min(len(r) for r in same)
    say(f"разбираем отчёт {main_id:#04x}, длина {length} байт")

    spans = []
    for i in range(length):
        values = [r[i] for r in same]
        spans.append(max(values) - min(values))

    say("\nбайт : размах значений (больше - активнее меняется)")
    for start in range(0, length, 16):
        chunk = spans[start:start + 16]
        line = " ".join(f"{v:3d}" for v in chunk)
        say(f"{start:>3} : {line}")

    if main_id == 0x30 and length >= 49:
        say()
        say("Это стандартный полный отчёт Nintendo Switch Pro (0x30).")
        say("Формат известен, приложение разберёт его без настройки.")
        ax, ay, az, gx, gy, gz = struct.unpack_from("<6h", same[-1], 13 + 24)
        say(f"последний кадр: ускорение {ax} {ay} {az}, гироскоп {gx} {gy} {gz}")
        return {"vid": vid, "pid": pid, "preset": "switch_pro"}

    say()
    say("Формат отчёта нестандартный. Ориентируйтесь на строку размахов выше:")
    say("шесть подряд идущих активных байт - это, скорее всего, три числа")
    say("по два байта. Их смещение впишите в mapping.json в раздел hid.custom.")
    return {"vid": vid, "pid": pid, "preset": "custom"}


# ---------------------------------------------------------------------------
# Итог
# ---------------------------------------------------------------------------

def write_mapping(sdl_result, hid_result, moving_axes):
    rule("5. Что записано в mapping.json")
    mapping = {
        "_комментарий": "Файл создан probe_gamepad.py. Правьте вручную при необходимости.",
    }

    if sdl_result and sdl_result.get("available"):
        mapping["backend"] = "sdl"
        mapping["sdl"] = {"index": 0, "invert": [1.0, 1.0, 1.0]}
        say("Выбран источник: SDL. Это лучший вариант, ничего настраивать не нужно.")
    elif hid_result:
        mapping["backend"] = "hid"
        mapping["hid"] = {
            "vid": hid_result["vid"],
            "pid": hid_result["pid"],
            "preset": hid_result["preset"],
            "invert": [1.0, 1.0, 1.0],
        }
        if hid_result["preset"] == "custom":
            mapping["hid"]["custom"] = {
                "report_id": None,
                "gyro_offset": 0,
                "accel_offset": 0,
                "_подсказка": "впишите смещения активных байт из раздела 4",
            }
        say("Выбран источник: сырые HID-отчёты.")
    elif moving_axes and len(moving_axes) >= 3:
        mapping["backend"] = "axes"
        mapping["axes"] = {
            "index": 0,
            "gyro_axes": moving_axes[:3],
            "gyro_scale": 8.0,
            "_подсказка": "проверьте номера осей и при необходимости поправьте",
        }
        say("Выбран источник: оси джойстика. Номера осей стоит проверить вручную.")
    else:
        mapping["backend"] = "mouse"
        say("Датчики найти не удалось. Приложение запустится в режиме мыши:")
        say("движение мыши заменяет поворот баллончика, а быстрое потряхивание")
        say("мышью взбалтывает краску. Вся логика при этом работает полностью.")

    with open(MAPPING_PATH, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=2)
    say(f"\nфайл записан: {os.path.abspath(MAPPING_PATH)}")
    say(json.dumps(mapping, ensure_ascii=False, indent=2))


def main():
    say("Диагностика геймпада для AeroPaint")
    say(f"Python {sys.version.split()[0]} на {sys.platform}")

    pygame, js = probe_pygame()
    moving = []
    sdl_result = None

    if pygame and js:
        moving = watch_axes(pygame, js)
        sdl_result = probe_sdl_sensors(pygame)

    hid_result = None
    if not (sdl_result and sdl_result.get("available")):
        hid_result = probe_hid()

    write_mapping(sdl_result, hid_result, moving)

    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines))
    say(f"\nПолный протокол сохранён в {os.path.abspath(REPORT_PATH)}")
    say("Пришлите этот файл, если понадобится помощь с настройкой.")

    if pygame:
        pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
