# -*- coding: utf-8 -*-
"""界面偏好：很小的一份持久化状态。

只存几个纯界面开关（比如「每行显示几个」），写在索引库旁边的
`var/gui_state.json` 里 —— 不动 vm_config.toml，那样会覆盖掉你手写的注释。
"""

from __future__ import annotations

import json
import os

from core import util

DEFAULTS = {
    "columns": 2,             # 窗口模式（非最大化）时每行显示几个
    "columns_maximized": 3,   # 全屏/最大化时每行显示几个
}


class Prefs(object):
    def __init__(self, path):
        self.path = path
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(util.long_path(self.path), "r", encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            for key in DEFAULTS:
                if key in saved:
                    self.data[key] = saved[key]

    def save(self):
        try:
            util.ensure_dir(os.path.dirname(os.path.abspath(self.path)))
            with open(util.long_path(self.path), "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass                                  # 写不进去也不该影响使用

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        if self.data.get(key) == value:
            return False
        self.data[key] = value
        self.save()
        return True
