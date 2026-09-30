# -*- coding: utf-8 -*-
"""后台任务：把 core 里的耗时操作放到 QThread 里跑，别冻住界面。

core 的函数本来就接受一个 progress 回调，这里直接把回调接到 Qt 信号上，
所以 core 一行都不用改。
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal


class Job(QThread):
    """跑一个耗时函数，进度用信号回传。"""

    progressed = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func, parent=None, **kwargs):
        super(Job, self).__init__(parent)
        self._func = func
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._func(progress=self.progressed.emit, **self._kwargs)
        except Exception as exc:                        # noqa: BLE001
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
            return
        self.done.emit(result)
