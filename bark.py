#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bark 推送模块：把消息推到用户 iPhone（Bark App）。

用法：
    from bark import push_bark, load_key
    ok, resp = push_bark(key, "标题", "正文")

key 的来源（按优先级）：
    1. 环境变量 BARK_KEY
    2. 同目录下 bark.key 文件（只写一行 key）
    3. config.py 里的 BARK_KEY

Bark 接口：POST https://api.day.app/{key}
    body: {"title": ..., "body": ..., "group": ..., "sound": ..., "isArchive": 1}
成功返回 {"code": 200, "message": "success", ...}。
"""
import os
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
BARK_API = "https://api.day.app"


def load_key():
    """按优先级找 Bark key，找不到返回空字符串。"""
    key = os.environ.get("BARK_KEY", "").strip()
    if key:
        return key
    kf = SCRIPT_DIR / "bark.key"
    if kf.exists():
        try:
            return kf.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    try:
        from config import BARK_KEY
        return (BARK_KEY or "").strip()
    except Exception:
        return ""


def push_bark(key, title, body, group="rice", sound="alarm", retries=3):
    """发一条 Bark 推送。失败自动重试。

    返回 (ok, resp)：ok 为 True 表示 Bark 返回 code 200；
    resp 是 Bark 返回的 JSON 或错误字符串。
    """
    url = f"{BARK_API}/{key}"
    payload = {
        "title": title,
        "body": body,
        "group": group,
        "isArchive": 1,
    }
    if sound:
        payload["sound"] = sound
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            data = r.json()
            if r.status_code == 200 and data.get("code") == 200:
                return True, data
            last_err = f"HTTP {r.status_code}: {data}"
        except Exception as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(2 * attempt)
    return False, last_err
