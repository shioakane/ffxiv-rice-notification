#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通知测试：模拟发现老板队时发出的通知
用真实格式的招募信息测试 MessageBox + 提示音是否正常弹出
"""
import sys
import os
import time
import threading
import ctypes
import winsound

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rice_monitor import format_listing_detail, notify

# 模拟一条绝欧米茄老板队招募
mock_listing = {
    "id": 999999,
    "name": "测试老板",
    "description": "50w过本 绝欧 来5个打工的 全黑",
    "created_world": "拉诺西亚",
    "home_world": "紫水栈桥",
    "category": "HighEndDuty",
    "duty": "欧米茄绝境验证战",
    "min_item_level": 0,
    "slots_filled": 3,
    "slots_available": 8,
    "time_left": 3000,
    "datacenter": "陆行鸟",
}

# 模拟详情 API 返回
mock_detail = {
    "id": 999999,
    "slots": [
        {"filled": True, "role": "Tank", "role_id": 1, "job": "DRK", "job_id": [32]},
        {"filled": True, "role": "Healer", "role_id": 2, "job": "WHM", "job_id": [24]},
        {"filled": True, "role": "DPS", "role_id": 3, "job": "DNC", "job_id": [38]},
        {"filled": False, "role": "DPS", "role_id": 3, "job": "BLM SMN RDM PCT RPR SAM", "job_id": [30, 27, 35, 42, 39, 34]},
        {"filled": False, "role": "DPS", "role_id": 3, "job": "BLM SMN RDM PCT RPR SAM", "job_id": [30, 27, 35, 42, 39, 34]},
        {"filled": False, "role": "Tank", "role_id": 1, "job": "PLD WAR DRK GNB", "job_id": [19, 21, 32, 37]},
        {"filled": False, "role": "Healer", "role_id": 2, "job": "WHM SCH AST SGE", "job_id": [24, 28, 33, 40]},
        {"filled": False, "role": "Healer", "role_id": 2, "job": "WHM SCH AST SGE", "job_id": [24, 28, 33, 40]},
    ],
}

def main():
    print("=" * 60)
    print("  通知测试 - 模拟发现老板队")
    print("=" * 60)

    msg = format_listing_detail(mock_listing, mock_detail)
    print(f"通知内容预览:\n{'─' * 50}")
    print(msg)
    print(f"{'─' * 50}\n")

    print("3秒后发出通知...")
    time.sleep(3)

    notify("发现老板队！(欧米茄绝境验证战)", msg)

    print("\n通知已发出，等待5秒...")
    time.sleep(5)
    print("测试完成。你应该看到了 MessageBox 弹窗和听到了提示音。")

if __name__ == "__main__":
    main()
