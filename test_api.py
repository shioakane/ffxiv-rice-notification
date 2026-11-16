#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单次测试脚本 v2：
- 调用列表 API，打印当前所有绝欧招募
- 使用智能正则 is_rice_party 判断老板队
- 对老板队调用详情 API 获取 slots，展示阵容
"""
import sys
import os
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    API_URL, PER_PAGE, DUTIES, USER_AGENT,
    CHINESE_DCS, FETCH_DETAIL_SLOTS,
)
from rice_monitor import (
    fetch_listings, fetch_detail,
    is_rice_party, get_rice_reason,
    format_listing_detail, format_slots,
)


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 单次测试 v2 - 绝欧米茄招募查询")
    print(f"  API: {API_URL}")
    print(f"  Duty: {DUTIES}")
    print(f"  UA: {USER_AGENT}")
    print(f"  中文DC: {CHINESE_DCS}")
    print(f"  智能米检测: is_rice_party() (正则模式)")
    print(f"  详情 API: {'开启' if FETCH_DETAIL_SLOTS else '关闭'}")
    print("=" * 70)

    listings = fetch_listings(DUTIES)
    print(f"API 返回 {len(listings)} 条招募\n")

    if not listings:
        print("没有招募数据或 API 请求失败")
        return

    rice_listings = []
    cn_listings = []

    for i, item in enumerate(listings, 1):
        lid = item.get("id", "")
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        dc = item.get("datacenter", "")
        duty = item.get("duty", "")
        home = item.get("home_world", "")
        slots_filled = item.get("slots_filled", 0)
        slots_available = item.get("slots_available", 0)
        time_left = item.get("time_left", 0)

        is_cn = dc in CHINESE_DCS
        is_rice_match = is_rice_party(desc)
        reasons = get_rice_reason(desc) if is_rice_match else []

        flag = ""
        if is_cn and is_rice_match:
            flag = " *** [老板队] ***"
            rice_listings.append(item)
        if is_cn:
            cn_listings.append(item)

        print(f"[{i}] id={lid} {duty} ({dc}){flag}")
        print(f"    队长: {name}@{home}  [{slots_filled}/{slots_available}人]  剩余: {int(time_left/60)}分钟")
        print(f"    描述: {desc[:120]}")
        if reasons:
            print(f"    米匹配原因: {reasons}")
        print()

    print("=" * 70)
    print(f"总计: {len(listings)} 条绝欧招募")
    print(f"  中文DC: {len(cn_listings)} 条")
    print(f"  老板队(智能检测): {len(rice_listings)} 条")

    if rice_listings:
        print("\n" + "=" * 70)
        print(">>> 老板队详情（含 slots 阵容） <<<")
        print("=" * 70)
        for item in rice_listings:
            detail = None
            if FETCH_DETAIL_SLOTS:
                detail = fetch_detail(item.get("id"))

            print(f"\n{'─' * 50}")
            print(format_listing_detail(item, detail))
            if detail and detail.get("slots"):
                slots = detail["slots"]
                print(f"\n  Slots 明细:")
                for idx, s in enumerate(slots, 1):
                    filled = s.get("filled", False)
                    role = s.get("role", "?")
                    job = s.get("job", "?")
                    job_ids = s.get("job_id", [])
                    if filled:
                        print(f"    [{idx}] 已有: {job} ({role}) job_id={job_ids}")
                    else:
                        print(f"    [{idx}] 空位: {role} 可接受: {job} job_id={job_ids}")

    print(f"\n[{datetime.now():%H:%M:%S}] 测试完成")


if __name__ == "__main__":
    main()
