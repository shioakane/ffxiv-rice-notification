#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试不同通知方式哪个在当前 Windows 上有效"""
import ctypes
import threading
import time
import winsound

def test_msgbox():
    """方式1: ctypes MessageBox（最可靠，一定弹窗）"""
    MB_ICONEXCLAMATION = 0x30
    MB_TOPMOST = 0x40000
    MB_SYSTEMMODAL = 0x1000
    t = threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(
        0, "MessageBox 通知测试 - 如果你看到这个框，说明通知正常！",
        "FF14 绝欧监控", MB_ICONEXCLAMATION | MB_TOPMOST | MB_SYSTEMMODAL
    ))
    t.daemon = True
    t.start()
    time.sleep(1)
    print("[OK] MessageBox 已弹出（非阻塞，独立线程）")

def test_sound():
    """方式2: 系统提示音"""
    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        print("[OK] 系统提示音已播放")
    except Exception as e:
        print(f"[FAIL] 提示音失败: {e}")

def test_plyer():
    """方式3: plyer"""
    try:
        from plyer import notification
        notification.notify(
            title="FF14 绝欧监控",
            message="plyer 通知测试",
            app_name="FF14 Rice Monitor",
            timeout=8,
        )
        print("[SENT] plyer 通知已发送（可能不会显示，Windows 通知中心被禁用时不弹）")
    except Exception as e:
        print(f"[FAIL] plyer 失败: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("  通知方式测试")
    print("=" * 50)
    test_sound()
    test_plyer()
    test_msgbox()
    print("\n请检查：")
    print("  1. 是否听到了提示音？")
    print("  2. 屏幕上是否弹出了消息框？")
    print("  3. Windows 通知中心是否有 plyer 的通知？")
    time.sleep(5)
