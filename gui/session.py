# -*- coding: utf-8 -*-
"""Session：整个界面共享的唯一资源与事件总线。

重构前这些散落在 MainWindow 里（Engine、缩略图线程池、标签操作），
现在收敛到一个对象，好处是：

1. 每个标签页只持有「自己的查询状态」，共享资源从 Session 拿；
2. 标签变了只发一个信号，所有标签页/侧栏/详情面板各自刷新，
   不用 MainWindow 挨个去通知；
3. 长任务（扫描）统一在这里跑，界面只订阅进度。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import QObject, pyqtSignal

from core import tags as tags_mod

from .prefs import Prefs
from .thumbs import ThumbnailCache, ThumbnailLoader
from .workers import Job


class Session(QObject):
    # 标签集合或「文件-标签」关联变了（打标签、改名、删除、导入、撤销）
    tagsChanged = pyqtSignal()
    # 索引库内容变了（扫描完成）
    libraryChanged = pyqtSignal()
    # 状态栏文字
    status = pyqtSignal(str)
    # 长任务进度
    progress = pyqtSignal(str)
    # 是否正在跑长任务（用于禁用按钮、显示进度条）
    busy = pyqtSignal(bool)
    # 出错
    failed = pyqtSignal(str)

    def __init__(self, engine, parent=None):
        super(Session, self).__init__(parent)
        self.engine = engine
        cache_dir = os.path.join(os.path.dirname(engine.cfg.db_path), "thumbs")
        self.thumbs = ThumbnailLoader(ThumbnailCache(engine.cfg.ffmpeg, cache_dir))
        # 纯界面偏好（每行显示几个之类），写在索引库旁边
        self.prefs = Prefs(os.path.join(os.path.dirname(engine.cfg.db_path),
                                        "gui_state.json"))
        self._job = None

    # ---------------------------------------------------------- 元信息

    @property
    def roots(self):
        return self.engine.cfg.roots

    @property
    def root_label(self):
        return "、".join(self.engine.cfg.roots) or "（未设置）"

    def is_busy(self):
        return self._job is not None and self._job.isRunning()

    # ---------------------------------------------------------- 标签

    def _after_tag_change(self, message):
        """标签写操作统一出口：导出备份 + 广播。"""
        tags_mod.export_csv(self.engine.conn, self.engine.cfg.tags_backup)
        self.tagsChanged.emit()
        if message:
            self.status.emit(message)

    def add_tags(self, file_ids, names):
        result = self.engine.add_tags(file_ids, names)
        self._after_tag_change(
            "已给 %d 个文件添加标签：%s（新增 %d 条关联）"
            % (result["files"], "、".join(result["tags"]), result["added"]))
        return result

    def remove_tags(self, file_ids, names):
        result = self.engine.remove_tags(file_ids, names)
        self._after_tag_change("已移除 %d 条标签关联。" % result["removed"])
        return result

    def rename_tag(self, old, new):
        info = self.engine.rename_tag(old, new)
        if not info:
            self.failed.emit("标签不存在：%s" % old)
            return None
        if info["merged"]:
            message = ("已把「%s」合并进「%s」，迁移 %d 条关联"
                       % (info["name"], info["new"], info["moved"]))
        else:
            message = "标签已改名为「%s」" % info["new"]
        self._after_tag_change(message)
        return info

    def delete_tag(self, name):
        info = self.engine.delete_tag(name)
        if info:
            self._after_tag_change("已删除标签「%s」，解除 %d 个文件的关联"
                                   % (info["name"], info["unlinked"]))
        return info

    def undo_tag(self):
        done = tags_mod.undo(self.engine.conn, 1)
        if done:
            self._after_tag_change(done[0])
        else:
            self.status.emit("没有可撤销的标签操作。")
        return done

    def export_tags(self, path):
        info = tags_mod.export_csv(self.engine.conn, path)
        self.status.emit("已导出 %d 条标签关联到 %s" % (info["rows"], info["path"]))
        return info

    def import_tags(self, path, mode="merge"):
        info = tags_mod.import_csv(self.engine.conn, path, mode=mode)
        self._after_tag_change(
            "已导入：新增 %d 条关联，涉及 %d 个文件%s"
            % (info["added"], info["files"],
               "，%d 条找不到文件" % info["missing_paths"] if info["missing_paths"] else ""))
        return info

    def tag_names(self):
        return self.engine.all_tag_names()

    # ---------------------------------------------------------- 扫描

    def start_scan(self):
        if self.is_busy():
            self.status.emit("扫描还在进行中…")
            return False
        self.busy.emit(True)
        self.status.emit("正在扫描…")
        # 走 scan_in_own_connection：工作线程要自己开一个 sqlite 连接
        self._job = Job(self.engine.scan_in_own_connection, self)
        self._job.progressed.connect(self.progress.emit)
        self._job.done.connect(self._on_scan_done)
        self._job.failed.connect(self._on_scan_failed)
        self._job.start()
        return True

    def _on_scan_done(self, summary):
        self.busy.emit(False)
        self.status.emit(
            "扫描完成：共 %d 个文件（新增 %d，有变化 %d，未变 %d，消失 %d），"
            "探测 %d 个（失败 %d），标签重接 %d 个"
            % (summary["files"], summary["new"], summary["updated"],
               summary["unchanged"], summary["missing"], summary["probed"],
               summary["probe_failed"], summary["relinked"]))
        self.libraryChanged.emit()
        self.tagsChanged.emit()

    def _on_scan_failed(self, message):
        self.busy.emit(False)
        self.status.emit("扫描失败：%s" % message)
        self.failed.emit(message)

    # ---------------------------------------------------------- 清理

    def shutdown(self):
        """退出时收尾。

        先把长任务的信号断开再关连接 —— 否则扫描刚结束、done 还在事件队列里
        时就关窗口，回调会在连接关掉之后才跑，报「Cannot operate on a closed
        database」。
        """
        if self._job is not None:
            for signal in (self._job.done, self._job.failed, self._job.progressed):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
            if self._job.isRunning():
                self._job.terminate()
                self._job.wait(2000)
            self._job = None
        self.thumbs.shutdown()
        self.engine.close()
