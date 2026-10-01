"""Холст: хранение изображения, отмена действий, сохранение и загрузка."""

from __future__ import annotations

import json
import os
import time
import zlib
from datetime import datetime

import numpy as np

from .config import CanvasConfig


class Canvas:
    def __init__(self, cfg: CanvasConfig | None = None) -> None:
        self.cfg = cfg or CanvasConfig()
        self.width = self.cfg.width
        self.height = self.cfg.height
        self.pixels = np.zeros((self.height, self.width, 3), dtype=np.float32)
        self.wet = np.zeros((self.height, self.width), dtype=np.float32)
        self._undo: list = []
        self._redo: list = []
        self._last_autosave = time.time()
        self.clear()

    # -- содержимое -----------------------------------------------------------

    def clear(self) -> None:
        self.pixels[:] = np.asarray(self.cfg.background, dtype=np.float32)
        self.wet[:] = 0.0
        self._undo.clear()
        self._redo.clear()

    def to_uint8(self) -> np.ndarray:
        return (np.clip(self.pixels, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)

    # -- отмена и повтор ------------------------------------------------------
    # Снимки сжимаются: пустой холст занимает единицы килобайт,
    # поэтому двадцать с лишним шагов истории помещаются в память.

    def _snapshot(self) -> bytes:
        return zlib.compress(self.to_uint8().tobytes(), 1)

    def _restore(self, blob: bytes) -> None:
        raw = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
        self.pixels[:] = raw.reshape(self.height, self.width, 3).astype(np.float32) / 255.0

    def begin_stroke(self) -> None:
        """Запомнить состояние перед нанесением - одна операция отмены."""
        self._undo.append(self._snapshot())
        if len(self._undo) > self.cfg.undo_depth:
            self._undo.pop(0)
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self.wet[:] = 0.0
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self.wet[:] = 0.0
        return True

    # -- файлы ----------------------------------------------------------------

    def export_image(self, path: str, quality: int = 92) -> str:
        from PIL import Image

        img = Image.fromarray(self.to_uint8(), mode="RGB")
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            img.save(path, quality=quality, subsampling=0)
        else:
            img.save(path)
        return path

    def save_project(self, path: str, meta: dict | None = None) -> str:
        """Формат проекта: сжатый растр плюс заголовок с параметрами сеанса."""
        header = {
            "format": "aeropaint-project",
            "version": 1,
            "width": self.width,
            "height": self.height,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "meta": meta or {},
        }
        blob = zlib.compress(self.to_uint8().tobytes(), 6)
        head = json.dumps(header, ensure_ascii=False).encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(b"AEPT")
            fh.write(len(head).to_bytes(4, "little"))
            fh.write(head)
            fh.write(blob)
        return path

    @classmethod
    def load_project(cls, path: str) -> "Canvas":
        with open(path, "rb") as fh:
            if fh.read(4) != b"AEPT":
                raise ValueError("файл не является проектом AeroPaint")
            head_len = int.from_bytes(fh.read(4), "little")
            header = json.loads(fh.read(head_len).decode("utf-8"))
            raw = np.frombuffer(zlib.decompress(fh.read()), dtype=np.uint8)

        cfg = CanvasConfig(width=header["width"], height=header["height"])
        canvas = cls(cfg)
        canvas.pixels[:] = raw.reshape(cfg.height, cfg.width, 3).astype(np.float32) / 255.0
        return canvas

    # -- автосохранение -------------------------------------------------------

    def maybe_autosave(self, directory: str) -> str | None:
        now = time.time()
        if now - self._last_autosave < self.cfg.autosave_interval:
            return None
        self._last_autosave = now
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "autosave.aept")
        tmp = path + ".tmp"
        self.save_project(tmp, {"autosave": True})
        os.replace(tmp, path)
        return path
