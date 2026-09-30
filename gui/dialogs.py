# -*- coding: utf-8 -*-
"""共用的几个小对话框。"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCompleter, QInputDialog, QLineEdit, QMessageBox


def ask_tag(parent, title, label, suggestions=None):
    """输入标签名，支持逗号分隔多个，带补全。返回标签列表（可能为空）。"""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setInputMode(QInputDialog.TextInput)
    dialog.resize(460, dialog.height())
    names = list(suggestions or [])
    if names:
        completer = QCompleter(names, dialog)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        # 注意：QInputDialog **没有** setCompleter（那是 QComboBox / QLineEdit 的），
        # 补全得装到它内部那个输入框上。
        # 这里以前写的是 dialog.setCompleter(completer)：只要索引库里已经有标签，
        # 就会 AttributeError，而 PyQt5 会把槽里未捕获的异常升级成 qFatal()
        # —— 于是右键点「打标签…」整个程序当场闪退（连错误框都没有）。
        edit = dialog.findChild(QLineEdit)
        if edit is not None:
            edit.setCompleter(completer)
    if not dialog.exec_():
        return []
    text = dialog.textValue().strip()
    if not text:
        return []
    from core import util
    parts = text.replace("，", ",").replace("、", ",").split(",")
    return [util.normalize_tag(p) for p in parts if util.normalize_tag(p)]


def ask_text(parent, title, label, default=""):
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip() if ok else ""


def confirm(parent, title, text, default_no=True):
    buttons = QMessageBox.Yes | QMessageBox.No
    answer = QMessageBox.question(
        parent, title, text, buttons,
        QMessageBox.No if default_no else QMessageBox.Yes)
    return answer == QMessageBox.Yes
