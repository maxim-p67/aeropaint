"""Холст: изображение в памяти, отмена/повтор, сохранение, автосохранение."""

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
        self.width, self.height = self.cfg.width, self.cfg.height
        self.pixels = np.zeros((self.height, self.width, 3), dtype=np.float32)  # RGB 0..1
        self._undo: list = []
        self._redo: list = []
        self._last_autosave = time.time()
        self.clear()

    def clear(self) -> None:
        self.pixels[:] = np.asarray(self.cfg.background, dtype=np.float32)
        self._undo.clear()
        self._redo.clear()

    def to_uint8(self) -> np.ndarray:
        return (np.clip(self.pixels, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)

    # -- отмена и повтор: снимки хранятся сжатыми -----------------------------

    def _snapshot(self) -> bytes:
        return zlib.compress(self.to_uint8().tobytes(), 1)

    def _restore(self, blob: bytes) -> None:
        raw = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
        self.pixels[:] = raw.reshape(self.height, self.width, 3).astype(np.float32) / 255.0

    def begin_stroke(self) -> None:
        """Запомнить состояние перед новым мазком - один шаг отмены."""
        self._undo.append(self._snapshot())
        del self._undo[:-self.cfg.undo_depth]
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        return self._step(self._undo, self._redo)

    def redo(self) -> bool:
        return self._step(self._redo, self._undo)

    def _step(self, source: list, target: list) -> bool:
        if not source:
            return False
        target.append(self._snapshot())
        self._restore(source.pop())
        return True

    # -- файлы ----------------------------------------------------------------

    def export_image(self, path: str, quality: int = 92) -> str:
        from PIL import Image

        img = Image.fromarray(self.to_uint8(), mode="RGB")
        if path.lower().endswith((".jpg", ".jpeg")):
            img.save(path, quality=quality, subsampling=0)
        else:
            img.save(path)
        return path

    def save_project(self, path: str, meta: dict | None = None) -> str:
        """Файл проекта: 'AEPT', длина заголовка, заголовок JSON, сжатый растр."""
        header = {"format": "aeropaint-project", "version": 1,
                  "width": self.width, "height": self.height,
                  "saved_at": datetime.now().isoformat(timespec="seconds"),
                  "meta": meta or {}}
        head = json.dumps(header, ensure_ascii=False).encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(b"AEPT" + len(head).to_bytes(4, "little") + head)
            fh.write(zlib.compress(self.to_uint8().tobytes(), 6))
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

    def maybe_autosave(self, directory: str) -> str | None:
        """Раз в autosave_interval секунд пишет output/autosave.aept."""
        now = time.time()
        if now - self._last_autosave < self.cfg.autosave_interval:
            return None
        self._last_autosave = now
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "autosave.aept")
        self.save_project(path + ".tmp", {"autosave": True})
        os.replace(path + ".tmp", path)
        return path
