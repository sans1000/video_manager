# -*- coding: utf-8 -*-
"""文件哈希：三级校验里的第 2、3 级。

- quick_hash：文件大小 + 首块 + 尾块，用来快速排除「大小相同但内容不同」
- full_hash ：整文件流式哈希，用于最终确认
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

from . import util

HASH_NAMES = {
    "blake2b": "BLAKE2b-128",
    "md5": "MD5",
    "sha1": "SHA1",
    "sha256": "SHA256",
}


def new_hasher(algo: str):
    algo = (algo or "blake2b").lower()
    if algo == "blake2b":
        try:
            return hashlib.blake2b(digest_size=16)
        except Exception:
            return hashlib.md5()
    try:
        return hashlib.new(algo)
    except Exception:
        return hashlib.md5()


def _read_chunk(fh, size):
    return fh.read(size)


def quick_hash(path: str, chunk_size: int = 1048576, algo: str = "blake2b") -> str:
    """首块 + 尾块 + 文件大小的联合哈希。小文件只读一次。"""
    p = util.long_path(path)
    size = os.path.getsize(p)
    h = new_hasher(algo)
    h.update(str(size).encode("ascii"))
    with open(p, "rb") as fh:
        if size <= chunk_size * 2:
            h.update(fh.read())
        else:
            h.update(_read_chunk(fh, chunk_size))
            fh.seek(-chunk_size, os.SEEK_END)
            h.update(_read_chunk(fh, chunk_size))
    return h.hexdigest()


def full_hash(path: str, chunk_size: int = 1048576, algo: str = "blake2b") -> str:
    """整文件流式哈希。"""
    h = new_hasher(algo)
    with open(util.long_path(path), "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _job(task):
    path, mode, algo, chunk_size = task
    try:
        if mode == "quick":
            return path, quick_hash(path, chunk_size, algo), None
        return path, full_hash(path, chunk_size, algo), None
    except OSError as exc:
        return path, None, str(exc)


def hash_many(paths, mode: str = "quick", workers: int = 4,
              chunk_size: int = 1048576, algo: str = "blake2b",
              progress=None) -> dict:
    """并行哈希一批文件，返回 {path: digest}；读失败的文件不进结果。"""
    paths = list(paths)
    out, errors = {}, {}
    if not paths:
        return out
    tasks = [(p, mode, algo, chunk_size) for p in paths]
    done = 0
    workers = max(1, int(workers))
    if workers == 1 or len(paths) == 1:
        results = [_job(t) for t in tasks]
        for path, digest, err in results:
            done += 1
            if digest:
                out[path] = digest
            else:
                errors[path] = err
            if progress and (done % 25 == 0 or done == len(paths)):
                progress("%s哈希 %d/%d" % ("快速" if mode == "quick" else "全文",
                                           done, len(paths)))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for path, digest, err in pool.map(_job, tasks):
                done += 1
                if digest:
                    out[path] = digest
                else:
                    errors[path] = err
                if progress and (done % 25 == 0 or done == len(paths)):
                    progress("%s哈希 %d/%d" % ("快速" if mode == "quick" else "全文",
                                               done, len(paths)))
    return out


def hash_errors_last() -> dict:
    """预留：需要时把最近一次的错误带出来。"""
    return {}
