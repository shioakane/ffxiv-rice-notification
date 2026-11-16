#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bark 推送测试：发一条测试通知到你的 iPhone，验证 key 是否配对。

用法：python test_bark.py
先把 key 填到 config.py 的 BARK_KEY，或新建 bark.key 文件只写一行 key，
或设环境变量 BARK_KEY。key 在 Bark App 打开就能看到。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bark import push_bark, load_key  # noqa: E402


def main():
    print("=" * 50)
    print("  Bark 推送测试")
    print("=" * 50)
    key = load_key()
    if not key:
        print("[FAIL] 没找到 Bark key。")
        print("  1. 打开 iPhone 上的 Bark App，复制 key；")
        print("  2. 填到 config.py 的 BARK_KEY，或新建 bark.key 文件只写一行 key。")
        return 1
    print(f"[INFO] key 已读取（{key[:4]}...{key[-4:]}），正在发送测试推送...")
    ok, resp = push_bark(key, "Bark 测试", "如果你在手机上看到这条，说明推送链路通了，可以启动监控了。")
    if ok:
        print("[OK] 推送成功！请看手机是否收到通知。")
        print(f"  Bark 返回: {resp}")
        return 0
    print("[FAIL] 推送失败，请检查 key 是否正确、手机网络是否正常。")
    print(f"  错误: {resp}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
