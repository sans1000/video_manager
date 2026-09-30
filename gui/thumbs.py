# -*- coding: utf-8 -*-
"""缩略图：ffmpeg 抽一帧存到磁盘缓存，再交给界面显示。

三个要点：
1. 抽帧在工作线程里做，绝不阻塞界面；
2. 磁盘缓存，键里带 size + mtime，文件一变自动失效；
3. 同一张图重复请求只做一次（正在生成的任务记在 in-flight 集合里）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from core import util

# Windows 下别弹黑框
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

THUMB_WIDTH = 360


def thumb_key(row, width=THUMB_WIDTH):
    """缓存键：路径 + 大小 + 修改时间 + 抽帧宽度。

    带上宽度是因为卡片尺寸随窗口变化 —— 同一部片子在大窗口和小窗口下
    需要不同分辨率的缩略图，键里不带宽度就会互相串图。
    """
    raw = "%s|%s|%s|%d" % (row.get("path"), row.get("size"),
                           row.get("mtime"), int(width or THUMB_WIDTH))
    return hashlib.blake2b(raw.encode("utf-8", "replace"), digest_size=16).hexdigest()


class ThumbnailCache(object):
    """抽帧 + 缓存。线程安全（同一个键只会有一个线程在生成）。"""

    def __init__(self, ffmpeg, cache_dir):
        self.ffmpeg = ffmpeg or ""
        self.cache_dir = cache_dir
        self._locks = {}
        self._guard = threading.Lock()
        try:
            util.ensure_dir(cache_dir)
        except OSError:
            pass

    def path_for(self, row, width=THUMB_WIDTH):
        return os.path.join(self.cache_dir, thumb_key(row, width) + ".jpg")

    def _lock_for(self, key):
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def ensure(self, row, width=THUMB_WIDTH):
        """返回缩略图路径；失败返回 None。可安全地在工作线程里调用。"""
        if not self.ffmpeg:
            return None
        out = self.path_for(row, width)
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out

        lock = self._lock_for(out)
        with lock:
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                return out                      # 别的线程刚做好
            if self._extract(row, out, width):
                return out
            return None

    def _extract(self, row, out, width=THUMB_WIDTH):
        duration = row.get("duration") or 0
        # 取 1/4 处那一帧：比第一帧有信息量，又不像正中间那样可能是黑场
        seek = max(1.0, float(duration) * 0.25) if duration else 1.0
        tmp = out + ".part.jpg"
        cmd = [
            self.ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", "%.3f" % seek,
            "-i", util.long_path(row["path"]),
            "-frames:v", "1",
            "-vf", "scale=%d:-2" % int(width or THUMB_WIDTH),
            "-q:v", "4",
            "-y", util.long_path(tmp),
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=60,
                                  creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            self._cleanup(tmp)
            return False
        if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            self._cleanup(tmp)
            return False
        try:
            os.replace(tmp, out)                # 原子替换，避免半张图被读到
        except OSError:
            self._cleanup(tmp)
            return False
        return True

    @staticmethod
    def _cleanup(path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


class ThumbSignals(QObject):
    done = pyqtSignal(int, str)                 # file_id, 图片路径（空 = 失败）


class ThumbTask(QRunnable):
    def __init__(self, cache, row, signals, width=THUMB_WIDTH):
        super(ThumbTask, self).__init__()
        self.cache = cache
        self.row = row
        self.signals = signals
        self.width = width
        self.setAutoDelete(True)

    def run(self):
        try:
            path = self.cache.ensure(self.row, self.width)
        except Exception:
            path = None
        try:
            self.signals.done.emit(int(self.row["id"]), path or "")
        except RuntimeError:
            pass                                # 窗口已销毁


class ThumbnailLoader(object):
    """把抽帧任务丢进线程池，并在 in-flight 里去重。"""

    def __init__(self, cache, max_threads=3):
        self.cache = cache
        self.signals = ThumbSignals()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(1, int(max_threads)))
        self._inflight = set()
        self._guard = threading.Lock()

    def request(self, row, width=THUMB_WIDTH):
        file_id = int(row["id"])
        if not self.cache.ffmpeg:
            return False
        cached = self.cache.path_for(row, width)
        if os.path.isfile(cached) and os.path.getsize(cached) > 0:
            self.signals.done.emit(file_id, cached)
            return True
        # 同一个文件在不同尺寸下算两个任务，键里带上宽度
        key = (file_id, int(width or THUMB_WIDTH))
        with self._guard:
            if key in self._inflight:
                return False
            self._inflight.add(key)
        task = ThumbTask(self.cache, row, self.signals, width)
        self.pool.start(task)
        return True

    def release(self, file_id):
        with self._guard:
            for key in [k for k in self._inflight if k[0] == int(file_id)]:
                self._inflight.discard(key)

    def shutdown(self):
        try:
            self.pool.clear()
            self.pool.waitForDone(3000)
        except Exception:
            pass
