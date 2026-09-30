# -*- coding: utf-8 -*-
"""报表：控制台统计、CSV 导出、HTML 索引页。"""

from __future__ import annotations

import csv
import html
import os

from . import util

INDEX_HEADERS = ["序号", "文件名", "类型", "标签", "大小", "时长", "分辨率", "视频编码",
                 "音频编码", "码率", "封面", "修改时间", "所在目录", "完整路径"]

DUPES_HEADERS = ["重复组", "文件大小", "重复份数", "可回收", "判定", "建议保留",
                 "完整路径"]


def mtime_text(ts):
    if not ts:
        return "-"
    import datetime
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "-"


def _bitrate_text(value):
    if not value:
        return "-"
    try:
        return "%.2f Mbps" % (float(value) / 1000000.0)
    except (TypeError, ValueError):
        return "-"


def row_to_columns(row, index=None):
    """把一条数据库记录转成报表列。"""
    height = row.get("height")
    width = row.get("width")
    resolution = "%sx%s" % (width, height) if width and height else "-"
    cover = "有" if row.get("has_cover") else ("-" if row.get("probe_state") != 1 else "无")
    directory = os.path.dirname(row.get("path") or "")
    return [
        index if index is not None else row.get("id"),
        row.get("name") or "",
        row.get("kind") or "",
        row.get("tags") or "",
        util.human_size(row.get("size")),
        util.human_duration(row.get("duration")),
        resolution,
        row.get("vcodec") or "-",
        row.get("acodec") or "-",
        _bitrate_text(row.get("bitrate")),
        cover,
        mtime_text(row.get("mtime")),
        directory,
        row.get("path") or "",
    ]


# ------------------------------------------------------------------ CSV

def write_csv(path, headers, rows):
    util.ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(util.long_path(path), "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return path


def export_index_csv(rows, path):
    data = [row_to_columns(r, i) for i, r in enumerate(rows, 1)]
    return write_csv(path, INDEX_HEADERS, data)


def export_dupes_csv(groups, path):
    data = []
    for gi, group in enumerate(groups, 1):
        for row in group["files"]:
            is_keeper = row["id"] == group["keeper_id"]
            data.append([
                gi,
                util.human_size(group["size"]),
                group["count"],
                util.human_size(group["wasted"]) if is_keeper else "",
                "保留" if is_keeper else "重复",
                group["keeper_path"] if is_keeper else "",
                row["path"],
            ])
    return write_csv(path, DUPES_HEADERS, data)


# ------------------------------------------------------------------ 控制台统计

def format_stats(st):
    lines = []
    lines.append("索引概况")
    lines.append("  文件总数 : %d 个，合计 %s"
                 % (st["total"], util.human_size(st["total_size"])))
    for kind, n, size in st["by_kind"]:
        lines.append("    - %-6s %6d 个  %s" % (kind, n, util.human_size(size)))
    lines.append("  视频合计 : %d 个，%s，总时长 %s"
                 % (st["video_count"], util.human_size(st["video_size"]),
                    util.human_duration(st["video_duration"])))
    if st["by_ext"]:
        lines.append("  按扩展名 :")
        for ext, n, size in st["by_ext"][:12]:
            lines.append("    - %-8s %6d 个  %s" % (ext or "无", n, util.human_size(size)))
    if st["by_year"]:
        lines.append("  按年份   :")
        for year, n, size in st["by_year"][:15]:
            lines.append("    - %-6s %6d 个  %s" % (year, n, util.human_size(size)))
    if st["by_resolution"]:
        lines.append("  分辨率 Top:")
        for res, n in st["by_resolution"][:10]:
            lines.append("    - %-12s %6d 个" % (res, n))
    lines.append("  无内嵌封面 : %d 个" % st["no_cover"])
    lines.append("  探测失败   : %d 个" % st["probe_failed"])
    lines.append("  已消失     : %d 个（索引里有、磁盘上已找不到）" % st["missing"])
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
         margin: 0; padding: 24px; background: #f5f6f8; color: #1c1e21; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #6b7280; font-size: 13px; margin-bottom: 16px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
          padding: 12px 16px; min-width: 132px; }
  .card b { display: block; font-size: 20px; margin-bottom: 2px; }
  .card span { color: #6b7280; font-size: 12px; }
  .toolbar { margin-bottom: 10px; display: flex; gap: 10px; align-items: center; }
  input[type=search] { padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 8px;
                       min-width: 280px; font-size: 13px; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }
  th, td { border-bottom: 1px solid #eceef1; padding: 6px 10px; text-align: left;
           white-space: nowrap; }
  th { position: sticky; top: 0; background: #fafbfc; cursor: pointer; user-select: none; }
  th:hover { background: #f0f2f5; }
  td.name { max-width: 520px; overflow: hidden; text-overflow: ellipsis; }
  td.tags { color: #7c3aed; font-size: 12px; }
  td.muted { color: #6b7280; }
  tr:hover td { background: #f9fafb; }
  a { color: #2563eb; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .foot { margin-top: 14px; color: #9ca3af; font-size: 12px; }
  mark { background: #fde68a; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="sub">生成时间 __GENERATED__ ｜ 数据源 __SOURCE__ ｜ 共 __COUNT__ 条</div>
<div class="cards">__CARDS__</div>
<div class="toolbar">
  <input type="search" id="q" placeholder="按文件名 / 路径 / 编码过滤…">
  <span class="sub" id="hint"></span>
</div>
<table id="t">
<thead><tr>__HEAD__</tr></thead>
<tbody>
__ROWS__
</tbody>
</table>
<div class="foot">本页面为只读索引，点击文件名可在资源管理器中打开。</div>
<script>
(function () {
  var table = document.getElementById('t');
  var tbody = table.tBodies[0];
  var rows = Array.prototype.slice.call(tbody.rows);
  var q = document.getElementById('q');
  var hint = document.getElementById('hint');

  function filter() {
    var needle = q.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (tr) {
      var hit = !needle || tr.innerText.toLowerCase().indexOf(needle) >= 0;
      tr.style.display = hit ? '' : 'none';
      if (hit) { shown++; }
    });
    hint.textContent = needle ? ('匹配 ' + shown + ' / ' + rows.length + ' 条') : '';
  }
  q.addEventListener('input', filter);

  var dir = 1;
  table.tHead.rows[0].querySelectorAll('th').forEach(function (th, index) {
    th.addEventListener('click', function () {
      var numeric = index === 0 || index === 3 || index === 4;
      rows.sort(function (a, b) {
        var x = a.cells[index].innerText, y = b.cells[index].innerText;
        if (numeric) {
          var nx = parseFloat(x.replace(/[^0-9.]/g, '')) || 0;
          var ny = parseFloat(y.replace(/[^0-9.]/g, '')) || 0;
          return (nx - ny) * dir;
        }
        return x.localeCompare(y, 'zh') * dir;
      });
      dir = -dir;
      rows.forEach(function (tr) { tbody.appendChild(tr); });
    });
  });
})();
</script>
</body>
</html>
"""


def _cards(st):
    if not st:
        return ""
    items = [
        ("文件总数", "%d" % st["total"]),
        ("占用空间", util.human_size(st["total_size"])),
        ("视频数量", "%d" % st["video_count"]),
        ("视频时长", util.human_duration(st["video_duration"])),
        ("无封面", "%d" % st["no_cover"]),
        ("探测失败", "%d" % st["probe_failed"]),
    ]
    return "".join('<div class="card"><b>%s</b><span>%s</span></div>'
                   % (html.escape(v), html.escape(k)) for k, v in items)


def render_html(rows, path, title="视频索引", stats=None, source="", headers=None):
    headers = headers or INDEX_HEADERS
    # 按列名定位特殊列，避免以后加列时下标串位
    name_idx = headers.index("文件名")
    path_idx = headers.index("完整路径")
    dir_idx = headers.index("所在目录")
    tags_idx = headers.index("标签") if "标签" in headers else -1
    muted_idx = {dir_idx, path_idx}

    body = []
    for i, row in enumerate(rows, 1):
        cols = row_to_columns(row, i)
        cells = []
        for j, value in enumerate(cols):
            text = html.escape(str(value))
            if j == name_idx:
                cells.append('<td class="name"><a href="%s" title="%s">%s</a></td>'
                             % (html.escape(util.file_uri(cols[path_idx])), text, text))
            elif j == tags_idx:
                cells.append('<td class="tags">%s</td>' % text)
            elif j in muted_idx:
                cells.append('<td class="muted">%s</td>' % text)
            else:
                cells.append("<td>%s</td>" % text)
        body.append("<tr>%s</tr>" % "".join(cells))

    page = (HTML_TEMPLATE
            .replace("__TITLE__", html.escape(title))
            .replace("__GENERATED__", util.now_stamp())
            .replace("__SOURCE__", html.escape(source or "-"))
            .replace("__COUNT__", str(len(rows)))
            .replace("__CARDS__", _cards(stats))
            .replace("__HEAD__", "".join("<th>%s</th>" % html.escape(h) for h in headers))
            .replace("__ROWS__", "\n".join(body)))
    util.ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(util.long_path(path), "w", encoding="utf-8") as fh:
        fh.write(page)
    return path
