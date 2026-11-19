#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FF14 绝欧米茄老板队招募监控 (v2)
- 基于正则的智能"米"关键词检测（参考 is_rice.py）
- 多级 Windows 通知（MessageBox 保底，一定弹窗）
- 调用详情 API 获取 slots 职业信息，格式化输出
"""
import sys
import os
import re
import unicodedata
import time
import json
import ctypes
import logging
import threading
import winsound
from pathlib import Path
from datetime import datetime
from typing import Set, List, Dict, Any, Optional

import requests

# 同目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    API_URL, DETAIL_API_URL, PER_PAGE, DUTIES, POLL_INTERVAL,
    USER_AGENT, CHINESE_DCS,
    SEEN_FILE, LOG_FILE, PLAY_SOUND,
    FETCH_DETAIL_SLOTS,
    BARK_GROUP, BARK_SOUND, BARK_RETRY,
    NOTIFY_BARK, NOTIFY_PC,
    REPEAT_NOTIFY, REPEAT_NOTIFY_MINUTES,
)

# ========== 日志配置 ==========
SCRIPT_DIR = Path(__file__).parent
LOG_PATH = SCRIPT_DIR / LOG_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("rice_monitor")


# ========== 职业 ID 映射 ==========
JOB_MAP = {
    19: "骑士/PLD", 21: "战士/WAR", 32: "暗骑/DRK", 37: "枪刃/GNB",
    24: "白魔/WHM", 28: "学者/SCH", 33: "占星/AST", 40: "贤者/SGE",
    38: "舞者/DNC", 31: "机工/MCH", 23: "诗人/BRD",
    27: "召唤/SMN", 30: "黑魔/BLM", 35: "赤魔/RDM",
    42: "绘灵/PCT", 39: "钐镰/RPR", 34: "武士/SAM",
    25: "黑骑/DRG", 22: "忍者/NIN", 20: "武僧/MNK",
    41: "绝枪/SAG",
    6: "白魔/WHM", 26: "学者/SCH",
}
ROLE_MAP = {0: "自由", 1: "T", 2: "奶", 3: "DPS"}


# ========== "米"检测逻辑（v2.4，2026-11-20 第五批样本外验证复测，来源见 rice_regex/v2_4.py） ==========
STRONG_V4 = [
    # v2.4: 时薪/小時薪/每小时/每小時 从强关键词移除（P1：拿着三百万时薪吐槽、1k2时薪跪下请安
    # 是裸词提及，非招募；时薪信号改由 HOURLY 的数字分支承担：N时薪 / 时薪N / 每小时N。
    # 三批 52807/72280/79370 的搞转时薪仍由 搞转/过立结/价格兜底，全五批验证无回归。）
    "全需", "全贪", "黑CD", "黑cd",
    "老板躺", "老闆躺", "躺黑", "后躺", "後躺",
    "过本结", "过立结", "过本結", "過本結", "搞转", "搞轉",
    "郭立姐", "郭丽姐", "郭立姊", "郭立杰",
    "搞扣", "搞赔", "搞賠",
    "代打", "代练", "代練",
    "立结",
    "低保", "底薪",
]
# 从强关键词降级：老板队/老闆隊/老板刷/全黑 -> 见 BOSS_WORDS(需组合)；赔钱/扣钱/扣工资 -> 见 DEDUCT_CTX(需组合)

# 价格 token: 数字+单位（万/W/k/K/千，含 kw 组合），或 数字/CD|饭|小时 形式，或 裸数字>=100（仅用于雇佣/狗组合）
# v2.2: 价格数字首位非零（0w 不是价格）；支持小数 k（2.5k=2500万、1.5kw）
PRICE_UNIT = re.compile(r'[1-9]\d{0,3}(?:\.\d{1,2})?\s*[kK千]?[wW万萬]|[1-9]\d{0,3}(?:\.\d{1,2})?\s*[kK千](?![pP])|[1-9]\d{0,3}(?:\.\d{1,2})?\s*VV')  # v2.4: VV 为 w 的双 v 变体（2000vv）
# v2.2: 价格 >0；支持小数 k；分支 2 允许“一个 CD”（2600w一个cd、2.5k一个cd）
PRICE_PER_CD = re.compile(
    r'([1-9]\d{0,3}(?:\.\d{1,2})?)\s*[kK千]?[wW万萬]?\s*\d?\s*/\s*[cC][dD]'
    r'|([1-9]\d{0,3}(?:\.\d{1,2})?)\s*(?:[kK千]?[wW万萬]|[kK千])\s*(?:一|1)\s*(?:个)?\s*[cC][dD]'
    r'|([1-9]\d{0,3}(?:\.\d{1,2})?)\s*(?:[kK千]?[wW万萬]|[kK千])\s*打(?:一|1)(?:个)?\s*[cC][dD]'
    r'|([1-9]\d{2,3})\s*(?:一|1)?\s*[cC][dD]'
    r'|([1-9]\d{0,3}(?:\.\d{1,2})?)\s*(?:[kK千]?[wW万萬]|VV|[kK千])\s*[cC][dD]'  # v2.4: 1800WCD（价+单位+CD 直连，无 一/打/斜杠）
    r'|([1-9]\d{0,3}(?:\.\d{1,2})?)\s*(?:[kK千]?[wW万萬]|VV|[kK千])\s*打满\s*(?:一|1|两|二|2)?\s*(?:个)?\s*[cC][dD]'  # v2.4: 2000w打满1cd
    r'|打满\s*(?:一|1|两|二|2)?\s*(?:个)?\s*[cC][dD]\s*([1-9]\d{0,3}(?:\.\d{1,2})?)\s*(?:[kK千]?[wW万萬]|VV|[kK千])')  # v2.4: 打满1cd 2000w
# v2.2: 价格 >0
PRICE_PER_MEAL = re.compile(
    r'(?:一|1)\s*[飯饭]\s*[1-9]\d*(?:\.\d{1,2})?\s*[wW万萬]'
    r'|[1-9]\d*(?:\.\d{1,2})?\s*[wW万萬]\s*/\s*(?:一|1)\s*[飯饭]'
    r'|[1-9]\d{1,3}\s*/\s*[飯饭]'
    r'|(?:一|1)\s*[飯饭]\s*[1-9]\d{1,2}(?!\d)')
# v2.2: 价格 >0
PRICE_PER_CLEAR = re.compile(r'(?:通关|通關|过本|過本|渡劫)\s*[1-9]\d*(?:\.\d{1,2})?\s*[wW万萬]')
# v2.2 新增：按把计价（1kw一把、500w一把、5w来扎啤+1把）
# 价 + （来/招/找 + 任意短词 + +1）+ 一把；把 后须是词界（防 一把抓/一把手）
PRICE_PER_RUN = re.compile(
    r'[1-9]\d{0,3}(?:\.\d{1,2})?\s*(?:[kK千]?[wW万萬]|[kK千])\s*'
    r'(?:(?:来|招|找)\s*(?:一|1)?\s*(?:个|位)?\s*\S{1,4}?\s*(?:\+\s*)?)?'
    r'(?:一|1)?\s*把(?![\u4e00-\u9fa5a-zA-Z0-9])')
# v2.2 新增：价 + 打到 + 时间（1600打到10点、1800打到10点）
# 价首位非零（防 21:00 的 00 被当价）且价前不是 : 或数字（防 21:00 截断）
PRICE_UNTIL = re.compile(r'(?<![:：\d])[1-9]\d{1,3}\s*[W万萬K千]?\s*打到\s*\d{1,2}(?:点|:\d{2})')
# v2.2 时薪改动：
#  - 改在未去空格原文上匹配（与 SPLIT 同理）：800/h 6p 里 H 后是空格不是 6P，前瞻不再失效
#  - /h 类分支前瞻 (?![0-9]) -> (?![1-4](?![0-9]))：H 后跟 5-9/多位数/字母仍是时薪，只有 H1-H4 职业码被挡
#  - 新增 打满 X 小时（3200w打满两小时）、俩小时（600w俩小时）、每 h（400w每h）、
#    w 时（1400w时，要求带 w，防 1000时=14:00）、/时（1200/时）、/30min（500/30min）
HOURLY_V4 = re.compile(
    r'(\d{1,4})\s*[wW万萬kK千]?\s*/\s*(?:[hH](?![1-4](?![0-9]))|小时|小時)'   # 1000/h, 800/h 6p
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*1\s*(?:小时|小時)'                    # 400W/1小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*1\s*[hH](?![1-4](?![0-9]))'           # 1000/1h
    r'|(\d{1,4})\s*[wW万萬K千]\s*.?.?(?:一|1)\s*(?:小时|小時)'             # 500w一小时/1200W黄金一小时
    r'|(\d{1,4})\s*(?:[kK千][wW万萬]?|[wW万萬K千])\s*打(?:一|1)(?:个)?(?:半\s*)?(?:小时|小時|[hH])'  # 1000w打一小时
    r'|(\d{1,4})\s*[wW万萬]\s*半小时'                                       # 500w半小时
    r'|(\d{1,4})\s*[wW万萬kK千]\s*打\s*半\s*(?:小时|小時|[hH])'             # 500w打半小时
    r'|(\d{3,4})\s*打(?:一|1)(?:个)?(?:半\s*)?(?:小时|小時|[hH])'           # 1000打一小时（裸数字）
    r'|(\d{3,4})\s*(?:一|1)\s*(?:小时|小時)'                               # 1000一小时（裸数字）
    r'|(\d{2,4})(?!\d)\s*[wW万萬kK千]?\s*1\s*[hH](?![1-4](?![0-9]))'         # 1200w1h
    r'|(\d{1,4})\s*[kK千][wW万]?\s*[hH](?![1-4](?![0-9]))'                   # 1kw h
    r'|(\d{1,4})\s*[wW万萬]?\s*时薪'                                        # 1000w时薪
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*打满\s*(?:一|1|两|二|2|三|3|半)?\s*(?:个)?\s*(?:小时|小時|[hH])'  # 3200w打满两小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*俩\s*(?:小时|小時|[hH])'                   # 600w俩小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*每\s*[hH](?![1-4](?![0-9]))'               # 400w每h
    r'|(\d{1,4})\s*[wW万萬kK千]\s*时(?!间|候)'                                # 1400w时（要求带 w）
    r'|(\d{1,4})\s*[kK千][wW万萬]?\s*时(?!间|候)'                          # v2.4: 1kw时
    r'|(\d{1,4})\s*[kK千][wW万萬]?\s*(?:一|1)?\s*(?:小时|小時)'             # v2.4: 1kw 1小时 / 1kw一小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*时(?!间|候)'                           # 1200/时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*\d{1,2}\s*(?:min|MIN|分钟|分)'         # 500/30min
    r'|(?:时薪|小時薪|時薪)\s*[1-9]\d{0,3}(?!\d)'                            # v2.4: 时薪1000（数字在后；v2.4 时薪移出强关键词后由本分支兜底）
    r'|(?:每小时|每小時)\s*[1-9]\d{0,3}(?!\d)'                               # v2.4: 每小时1000
    r'|(\d{3,4})\s*半(?:小时|小時)'                                          # v2.4: 500半小时（裸数字+半小时）
)
# 人数拆分：要求前后不是数字/= /pP（排除 P2P3 阶段、7=1+P2 合并）；在未去空格原文上匹配
SPLIT_V4 = re.compile(r'(?<![\d=pP])[1-7][pP][1-7](?![\d])')
# 价格+次数：要求带单位，或首数字>=100；v2.2：价格支持小数，须 >0
# v2.4: 数字前不得紧贴字母/数字（去空格粘连：H2 20+ 不再拼成 H220+10；全五批验证无 TP 依赖字母前数字）
PRICE_PLUS_RUN_V4 = re.compile(r'(?:^|[^0-9A-Za-z:])(\d{1,4}(?:\.\d{1,2})?)\s*([kK千]?[wW万萬]?)\s*[+＋]\s*(\d{1,2})(?:把|次|局)?')
CLEAR_V4 = ["过本", "過本", "渡劫", "通关", "通關", "包过", "包過", "复建"]
# v2.2: 全黑 从强关键词降级到此（组合词：需价格配合）
BOSS_WORDS = ["老板", "老闆", "打工", "躺", "老板队", "老闆隊", "老板刷", "全黑"]
HIRE_WORDS = ["陪练", "陪練", "傭兵", "佣兵", "代打", "代练", "代練", "代班"]
DEDUCT_STRONG = ["搞扣", "搞赔", "搞賠"]
DEDUCT_CTX = ["扣工资", "扣錢", "扣钱", "赔钱", "賠錢"]
STAGE_DEDUCT_V4 = re.compile(
    r'(?:[pP]?\s*)?([1-9])\s*[kK扣]\s*(?:[pP]?\s*)?([1-9])\s*[pP赔賠](?!\s*/\s*[cC][dD])'
    r'|(?:[pP]\s*)?([1-9])\s*扣\s*(?:[pP]\s*)?([1-9])\s*赔')
REQ_STRIP = [
    re.compile(r'过本(经验|记录|配置)'),
    re.compile(r'(内含|内置|内有|已有)[^，,。]{0,8}?(过本|通關|通关)'),
    re.compile(r'(要求|需要|需)[^，,。]{0,12}?(过本|通關|通关|渡劫)'),
    # v2.2: 进度目标表述不是付费过本（1000h 争取过本 … 7P1 是进度队；全三批仅此一例）
    re.compile(r'(争取|想|求|目标|為了|为了)过本'),
]
RANT_SENSITIVE = ["低保", "底薪", "时薪", "小時薪", "每小时", "每小時"]
RANT_RE = re.compile(r'骗[^，,。]{0,8}?(不给|不給)')
# v2.2: 嘲讽标记（还寄吧 500w+1 穷逼开什么队 是嘲讽帖，非招募）
RANT_MOCK = ["穷逼", "寄吧", "开什么队", "避雷", "挂人"]  # v2.4: 避雷/挂人帖（76684）

def _norm(desc):
    return unicodedata.normalize('NFKC', desc or '')
def _flat(desc):
    return _norm(desc).upper().replace(' ', '').replace('　', '')
def _has_rant(raw):
    t = _flat(raw)
    if "不想活了" in t or "藏亲友" in t: return True
    if any(m in t for m in RANT_MOCK): return True
    if RANT_RE.search(t): return True
    return False

def _has_price_unit(text):
    return bool(PRICE_UNIT.search(text))
def _has_price_plus_run(text):
    m = PRICE_PLUS_RUN_V4.search(text)
    if not m: return False
    num, unit = float(m.group(1)), m.group(2)
    return num > 0 and (bool(unit) or num >= 100)
TIME_STRIP = [re.compile(r'\d{1,2}:\d{2}'), re.compile(r'\d{3,4}\s*-\s*\d{3,4}'), re.compile(r'\d{7,}')]
BARE_NUM = re.compile(r'(?<![A-Za-z0-9+])\d{3,4}(?![A-Za-z0-9:])')
def _bare_num(win):
    win2 = win
    for pat in TIME_STRIP: win2 = pat.sub('', win2)
    for m in BARE_NUM.finditer(win2):
        if re.fullmatch(r'3+', m.group(0)): continue
        if m.group(0) == '1211': continue
        if m.group(0).startswith('33') and win2[m.end():m.end()+1] == '+': continue
        return True
    return False
# v2.4: 雇佣窗口 16 -> 28（3238：招h1陪练…打半个小时，400w，价在 27 字外；
# 6896：9-11點 70萬，先剔时间段再取窗口）。窗口内先去掉电话/QQ 长串与时间段。
HIRE_WINDOW_V4 = 28
def _hire_with_price(raw):
    t = _flat(raw)
    for pat in TIME_STRIP: t = pat.sub('', t)
    t = _strip_time_ranges(t)
    for w in HIRE_WORDS:
        for m in re.finditer(re.escape(w), t):
            lo, hi = max(0, m.start()-HIRE_WINDOW_V4), m.end()+HIRE_WINDOW_V4
            win = t[lo:hi]
            if (PRICE_UNIT.search(win) or PRICE_PER_CD.search(win) or PRICE_PER_MEAL.search(win)
                    or _bare_num(win) or re.search(r'[1-9]\d*\s*/\s*(?:飯|CD|小时|小時)', win)
                    or re.search(r'\d{3,4}\s*[hH](?![A-Za-z0-9])', win)):
                return True
    return False
def _short_hire_with_price(raw):
    t = _flat(raw)
    return bool(re.search(r'\d{3,4}\s*陪(?![练練])', t))
def _hire_negotiable(raw):
    t = _flat(raw)
    # v2.4: 进队谈价也是议价（71562 价格进队谈；80105 价不对进队说）
    return any(w in t for w in HIRE_WORDS) and bool(re.search(r'价格(?:可议|可談|可谈|面议|详谈|进队(?:谈|说))|价不对进队(?:说|谈)', t))
def _dog_with_price(raw):
    # v2.2: 先去掉 QQ/电话等 7 位以上长串，避免 ±10 字窗口把 792061652 截断成 7920 当价格
    t = re.sub(r'\d{7,}', '', _flat(raw))
    if re.search(r'狗\s*[+＋]\s*\d+', t): return True
    for m in re.finditer('狗', t):
        lo, hi = max(0, m.start()-10), m.end()+10
        win = t[lo:hi]
        # v2.4: 机制代码（332后116）不是价格，先去掉再判裸数字
        win = re.sub(r'\d{3}\s*(?:后|前|左|右)\s*\d{3}', '', win)
        if PRICE_UNIT.search(win) or _bare_num(win):
            return True
    return False
def _photo_entry_with_price(raw):
    t = _flat(raw)
    if not re.search(r'\d{1,4}\s*[W万萬]\s*(?:一|1)?\s*个\s*人', t):
        return False
    return ('进本' in t) or ('拍照' in t)
def _looks_like_time(num):
    if len(num) == 4:
        hh, mm = int(num[:2]), int(num[2:])
    elif len(num) == 3:
        hh, mm = int(num[0]), int(num[1:])
    else:
        return False
    return 0 <= hh <= 23 and 0 <= mm <= 59
# v2.4: 剔除时间段，避免时间数字拼成价格（晚8-11+1cd -> 111CD；2030-2200 -> 裸价）
TIME_RANGE_SIMPLE = re.compile(r'\d{1,2}\s*[-~～]\s*\d{1,2}(?=\s*(?:点|點|来|招|找|，|,|、|。|$))')  # v2.4: 前瞻补 點（9-11點 与 9-11点 同为时间段）
TIME_RANGE_GLUED_CD = re.compile(r'\d{1,2}\s*[-~～]\s*\d{1,2}(?=\d\s*[cC][dD])')
TIME_RANGE_HHMM = re.compile(r'(\d{3,4})\s*[-~～]\s*(\d{3,4})(?![wW万萬kK千])')
TIME_RANGE_HM = re.compile(r'\d{1,2}:\d{2}\s*[-~～]\s*\d{1,2}:\d{2}')
def _strip_time_ranges(t):
    t = TIME_RANGE_SIMPLE.sub('', t)
    t = TIME_RANGE_GLUED_CD.sub('', t)
    def _hhmm_repl(m):
        # 只有两边都是合法钟点才当时间段（2000-3000w 的 3000 不是钟点，不剔；1800-2000w 有 w 也不剔）
        if _looks_like_time(m.group(1)) and _looks_like_time(m.group(2)):
            return ''
        return m.group(0)
    t = TIME_RANGE_HHMM.sub(_hhmm_repl, t)
    t = TIME_RANGE_HM.sub('', t)
    return t
# v2.4: 问价语境（想问一下/多少钱，且无招募动作+职业）不是招募
INQUIRY_RE = re.compile(r'想问|问一下|问下|多少钱|怎么收费|什么价格|什么价')
def _is_price_inquiry(text):
    if not INQUIRY_RE.search(text):
        return False
    if re.search(r'(?:招|来|找|缺|锁)\s*(?:一|1)?\s*(?:个|位)?\s*(?:D\d|H\d|MT|ST|非\w{0,3}[dD]\d)', text):
        return False
    return True
# v2.4: 非老板+数字 = 结算条款（非老板问题p5前33；非老板原因33）。
# “非老板”是过错归属条款，只存在于付费队；全五批 51 条含 非老板 的帖子全是真老板队、零误伤。
# 数字须是 2-4 位独立数字（33w 赔款），防 QQ/电话长串与 p5 这类 1 位职业码。
NONBOSS_SETTLEMENT_RE = re.compile(r'非老板(?:原因|问题)?[^，,。]{0,12}?(?<!\d)\d{2,4}(?!\d)')
def _nonboss_settlement(raw):
    return bool(NONBOSS_SETTLEMENT_RE.search(_flat(raw)))
# v2.4: 裸 N时 + 结算锚点按小时计（300时 过本+100）。
# 单独的 N时 是钟点（一批 1400时=14:00），须有结算锚点才按时薪计；
# 锚点是结算词（立结/搞赔/搞扣/搞转/过本结）或 过本+2 位以上数字（过本+100 是结账加价；
# 过本+1队 / 过本+8 是固定队模板，1 位数字不触发）。
SETTLEMENT_ANCHOR_RE = re.compile(r'立结|搞赔|搞賠|搞扣|搞转|搞轉|过本结|过本結|過本結|过本\s*\+\s*[1-9]\d{1,3}')
BARE_HOURLY_RE = re.compile(r'(\d{2,4})\s*时(?!间|候)')
def _hourly_bare_with_settlement(raw):
    t = _flat(raw)
    return bool(BARE_HOURLY_RE.search(t) and SETTLEMENT_ANCHOR_RE.search(t))
def _price_recruit(raw):
    t = _flat(raw)
    if '拿' in t and re.search(r'拿\d+\s*[W万萬]', t):
        return False
    # v2.2: 职业表加 非…d 码（非镰d2、非镰黑d2）与 熟练工
    job = r'(?:非\w{0,3}[dD]\d|D\d|H\d|MT|ST|非\w{0,2}|[A-Z]{1,3}|熟练工)'
    price = r'[1-9]\d{0,3}(?:\.\d{1,2})?\s*(?:[kK千]?[wW万萬]|VV|[kK千])'  # v2.4: VV 为 w 的双 v 变体
    # v2.4: 先去掉时间段（8-10、9-11点、2030-2200、11:10-1:10）与 +N / N+ 人数（+1、8+、10+），再匹配价…职业
    t2 = _strip_time_ranges(t)
    t2 = re.sub(r'\+\s*\d{1,2}(?!\d)', '', t2)  # v2.4: +N 后不能再跟数字（+1 300w 展平后是 +1300W，+13 不是标记）
    t2 = re.sub(r'\d{1,2}\s*\+', '', t2)  # v2.4: 8+ / 10+ 类进度标记（300w 8+ st、350w 来10+）
    # 单位价 + 招/来/找 + 职业，中间允许 ≤12 字间隔（2kw，9-11，p4开荒来h2）
    if re.search(price + r'\s*.{0,12}?(?:来|招|找)\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2):
        return True
    # 裸价 + 招/来 + 职业（3000招一H2）；裸数字读作时间时除外（1800招非黑d1）
    m = re.search(r'(\d{3,4})\s*(?:来|招)\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2)
    if m and not _looks_like_time(m.group(1)):
        return True
    # v2.2: 裸价 + 找 + 职业（1600找非镰d2）；找 不受时间排除（全三批仅此一例，招/来仍受排除）
    if re.search(r'\d{3,4}\s*找\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2):
        return True
    # 尾价——招/来/找 + 职业 … 价格在后（来个D4 1600W）
    if re.search(r'(?:来|招|找)\s*(?:一|1)?\s*(?:个|位)?\s*' + job + r'[^\d]{0,16}?\d{1,4}(?:\.\d{1,2})?\s*(?:[kK千]?[wW万萬]|[kK千])', t2):
        return True
    # v2.2: 价 … 职业（300w，+1，ymd，武士d1；价在前、中间隔 +1/ymd/职业名，≤10 字无数字）
    if re.search(price + r'\s*[^\d]{0,10}?(?:D\d|H\d|MT|ST)', t2):
        return True
    # v2.4: 价 + 加一（1kw加一）；后跟汉字会把 一 扩成别的词（加一根），用前瞻挡住
    if re.search(price + r'\s*加\s*(?:一|1|壹)\s*(?:个|位|人)?(?![\u4e00-\u9fa5])', t2):
        return True
    return False

CHATTER = ["刚刚", "少给", "少給"]
def is_rice_party(desc):
    if not desc: return False
    if any(c in desc for c in CHATTER): return False
    raw = _norm(desc)
    text = _flat(raw)
    # v2.4: 反向要钱 / 讽刺语境不是老板队（v我50、赔老板；全 4945 条数据里各只出现 1 次，均在第四批 FP 里）
    if re.search(r'V\s*我\s*\d+', text): return False
    if re.search(r'(?<![\d扣搞])[赔賠]老板', text): return False  # v2.4: 赔前是数字/扣/搞时是扣赔机制（如 5扣6赔老板d1），不是反向付钱
    rant = _has_rant(raw)
    strong_hits = [k for k in STRONG_V4 if k.upper() in text]
    if strong_hits:
        # v2.4: 问价语境下降权时薪类强关键词（想问一下，现在时薪多少钱）
        if _is_price_inquiry(text):
            # v2.4: 问价语境下降权扩展到 躺黑/老板躺（3006 躺黑现在多少钱；时薪类已移出强关键词）
            strong_hits = [k for k in strong_hits if k not in ("躺黑", "老板躺", "老闆躺")]
        if strong_hits and not (rant and all(k in RANT_SENSITIVE for k in strong_hits)):
            return True
    if HOURLY_V4.search(raw): return True   # v2.2: 时薪在未去空格原文上匹配
    if _hourly_bare_with_settlement(raw): return True   # v2.4: 裸 N时 + 结算锚点（300时 过本+100）
    # v2.4: 按 CD 价用剔除时间段后的文本（晚8-111cd 不再拼成 111CD）
    if PRICE_PER_CD.search(_strip_time_ranges(text)) or PRICE_PER_MEAL.search(text) or PRICE_PER_CLEAR.search(text): return True
    if PRICE_PER_RUN.search(text): return True   # v2.2 新增：按把计价
    if PRICE_UNTIL.search(text): return True     # v2.2 新增：价 + 打到 + 时间
    if _has_price_plus_run(text) and not rant: return True   # v2.2: 吐槽语境下价格+次数不触发
    if _hire_with_price(raw) or _short_hire_with_price(raw) or _hire_negotiable(raw) or _dog_with_price(raw): return True
    if _photo_entry_with_price(raw): return True
    if _price_recruit(raw): return True
    has_price = _has_price_unit(text)
    t2 = text
    for pat in REQ_STRIP: t2 = pat.sub('', t2)
    has_clear = any(k.upper() in t2 for k in CLEAR_V4)
    has_boss = any(k in text for k in BOSS_WORDS)
    has_split = bool(SPLIT_V4.search(raw))
    has_deduct = any(k.upper() in text for k in DEDUCT_STRONG) or bool(STAGE_DEDUCT_V4.search(text))
    has_deduct_ctx = any(k.upper() in text for k in DEDUCT_CTX)
    if _nonboss_settlement(raw): return True   # v2.4: 非老板+数字结算条款（非老板问题p5前33）
    if has_deduct: return True
    if has_deduct_ctx and has_price: return True
    if has_split and (has_price or has_clear): return True
    if has_price and has_clear: return True
    if has_price and has_boss: return True
    return False

def get_rice_reason(desc: str) -> List[str]:
    """返回匹配到的原因列表（v2.4 规则，用于日志展示，与 is_rice_party 同口径）。"""
    if not desc:
        return []
    raw = _norm(desc)
    text = _flat(raw)
    # v2.3: 反向要钱/讽刺语境直接排除，不报原因
    if re.search(r'V\s*我\s*\d+', text): return []
    if re.search(r'(?<![\d扣搞])[赔賠]老板', text): return []
    reasons = []
    strong_hits = [k for k in STRONG_V4 if k.upper() in text]
    if strong_hits:
        # v2.4: 时薪已移出强关键词（问价语境误伤），此过滤现为无操作，保留结构
        if _is_price_inquiry(text):
            strong_hits = [k for k in strong_hits if k not in ("时薪", "小時薪", "每小时", "每小時")]
        if strong_hits and not (_has_rant(raw) and all(k in RANT_SENSITIVE for k in strong_hits)):
            reasons.append(f"强关键词={strong_hits}")
    m = HOURLY_V4.search(raw)  # v2.2: 时薪在未去空格原文上匹配
    if m:
        reasons.append(f"时薪={m.group()}")
    for name, pat in (("按CD", PRICE_PER_CD), ("按饭", PRICE_PER_MEAL), ("按通关", PRICE_PER_CLEAR)):
        # v2.3: 按 CD 价用剔除时间段后的文本
        mm = pat.search(_strip_time_ranges(text) if name == "按CD" else text)
        if mm:
            reasons.append(f"{name}={mm.group()}")
    mm = PRICE_PER_RUN.search(text)  # v2.2 新增：按把计价
    if mm:
        reasons.append(f"按把={mm.group()}")
    mm = PRICE_UNTIL.search(text)  # v2.2 新增：价 + 打到 + 时间
    if mm:
        reasons.append(f"价+打到={mm.group()}")
    if _has_price_plus_run(text) and not _has_rant(raw):  # v2.2: 吐槽语境下价格+次数不触发
        reasons.append(f"价格+次数={PRICE_PLUS_RUN_V4.search(text).group()}")
    if _hire_with_price(raw):
        reasons.append("雇佣+价=陪练/佣兵/代打/代班+价格")
    if _short_hire_with_price(raw):
        reasons.append("陪简写+价=裸数字+陪")
    if _hire_negotiable(raw):
        reasons.append("雇佣+议价=价格可议")
    if _dog_with_price(raw):
        reasons.append("狗+价")
    if _photo_entry_with_price(raw):
        reasons.append("付费进本拍照")
    if _price_recruit(raw):
        reasons.append("裸价招募")
    if _has_price_unit(text):
        reasons.append(f"价格={PRICE_UNIT.search(text).group()}")
    # v2.4 新增：非老板+结算、裸 N 时+结算（原因一致性：仅靠这两个 helper 判真的帖子也要有原因）
    if _nonboss_settlement(raw):
        reasons.append("非老板+结算")
    if _hourly_bare_with_settlement(raw):
        reasons.append("裸N时+结算")
    t2 = text
    for pat in REQ_STRIP:
        t2 = pat.sub('', t2)
    clear_hits = [k for k in CLEAR_V4 if k.upper() in t2]
    if clear_hits:
        reasons.append(f"过本={clear_hits}")
    boss_hits = [k for k in BOSS_WORDS if k in text]
    if boss_hits:
        reasons.append(f"老板/打工={boss_hits}")
    sm = SPLIT_V4.search(raw)  # 拆分在未去空格原文上匹配
    if sm:
        reasons.append(f"人数拆分={sm.group()}")
    if any(k.upper() in text for k in DEDUCT_STRONG) or STAGE_DEDUCT_V4.search(text):
        reasons.append("扣赔机制")
    return reasons

# ========== 工具函数 ==========
def load_seen() -> Dict[int, float]:
    """加载已通知过的招募 ID -> 上次提醒时间（epoch 秒）。
    兼容旧格式：旧版存的是纯 ID 列表，读到后按当前时间戳转换
    （升级后旧招募会在间隔到期后才再次提醒，不会一启动就刷屏）。"""
    path = SCRIPT_DIR / SEEN_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 seen 文件失败: {e}，重新开始")
        return {}
    if isinstance(data, dict):
        # 新格式 {id: 上次提醒时间}
        return {int(k): float(v) for k, v in data.items()}
    if isinstance(data, list):
        # 旧格式 [id, id, ...]，按当前时间转换
        now = time.time()
        return {int(i): now for i in data}
    return {}


def save_seen(seen: Dict[int, float]):
    """保存已通知过的招募 ID 及上次提醒时间（只保留最近提醒的 2000 个）"""
    path = SCRIPT_DIR / SEEN_FILE
    recent = dict(sorted(seen.items(), key=lambda kv: kv[1])[-2000:])
    path.write_text(json.dumps(recent), encoding="utf-8")


def should_notify(lid: int, seen: Dict[int, float], now: float) -> bool:
    """这条招募现在该不该提醒：没提醒过 → 提醒；提醒过且开关开了、
    距上次提醒已超过 REPEAT_NOTIFY_MINUTES 分钟 → 再提醒一次。"""
    last = seen.get(lid)
    if last is None:
        return True
    if not REPEAT_NOTIFY:
        return False
    return (now - last) >= REPEAT_NOTIFY_MINUTES * 60


def fetch_listings(duties: List[int]) -> List[Dict[str, Any]]:
    """从 xivpf API 获取招募列表"""
    params = {
        "per_page": PER_PAGE,
        "duty": ",".join(map(str, duties)),
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(API_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("data", [])
    except requests.exceptions.Timeout:
        logger.error("API 请求超时")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.error(f"API 连接失败: {e}")
        return []
    except Exception as e:
        logger.error(f"API 请求异常: {e}")
        return []


def fetch_detail(listing_id: int) -> Optional[Dict[str, Any]]:
    """调用详情 API 获取 slots 信息"""
    url = DETAIL_API_URL.format(id=listing_id)
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"获取详情失败 id={listing_id}: {e}")
        return None


def format_slots(slots: List[Dict]) -> str:
    """格式化 slots 数组为可读字符串"""
    if not slots:
        return "（无 slots 信息）"

    filled_jobs = []
    open_slots = []

    for s in slots:
        if s.get("filled"):
            job = s.get("job", "?")
            role = ROLE_MAP.get(s.get("role_id", 0), s.get("role", "?"))
            filled_jobs.append(f"{job}({role})")
        else:
            role = ROLE_MAP.get(s.get("role_id", 0), s.get("role") or "自由")
            # 空位的 job 字段是可接受职业列表
            acceptable = s.get("job", "")
            if acceptable and len(acceptable) > 15:
                acceptable = acceptable[:15] + "…"
            open_slots.append(f"空位({role}{'/' + acceptable if acceptable else ''})")

    parts = []
    if filled_jobs:
        parts.append(f"已有: {', '.join(filled_jobs)}")
    if open_slots:
        parts.append(f"招: {', '.join(open_slots)}")

    return " | ".join(parts) if parts else "（无 slots 信息）"


def format_bark_body(item: Dict[str, Any]) -> str:
    """手机推送正文（三行，短格式）：
    第 1 行：描述
    第 2 行：大区-队长@服务器 人数
    第 3 行：接收时间 + 剩余时间
    """
    dc = item.get("datacenter", "")
    name = item.get("name", "")
    home = item.get("home_world", "")
    desc = (item.get("description", "") or "").strip()
    slots_filled = item.get("slots_filled", 0)
    slots_available = item.get("slots_available", 0)
    time_left = item.get("time_left", 0)
    mins_left = int(time_left / 60) if time_left else 0
    received = datetime.now().strftime("%m-%d %H:%M")
    lines = [
        desc,
        f"{dc}-{name}@{home} {slots_filled}/{slots_available}",
        f"接收 {received}｜剩余约 {mins_left} 分钟",
    ]
    return "\n".join(lines)


def format_listing_detail(item: Dict[str, Any], detail: Optional[Dict]) -> str:
    """格式化招募信息为完整通知消息（含 slots）"""
    duty = item.get("duty", "")
    dc = item.get("datacenter", "")
    name = item.get("name", "")
    home = item.get("home_world", "")
    desc = item.get("description", "") or ""
    slots_filled = item.get("slots_filled", 0)
    slots_available = item.get("slots_available", 0)
    time_left = item.get("time_left", 0)
    mins_left = int(time_left / 60) if time_left else 0

    lines = [
        f"副本: {duty}",
        f"大区: {dc} | 队长: {name}@{home}",
        f"人数: {slots_filled}/{slots_available} | 剩余: {mins_left}分钟",
        f"描述: {desc}",
    ]

    if detail and FETCH_DETAIL_SLOTS:
        slots = detail.get("slots", [])
        if slots:
            lines.append(f"阵容: {format_slots(slots)}")

    return "\n".join(lines)


def format_listing_console(item: Dict[str, Any], detail: Optional[Dict]) -> str:
    """格式化招募信息为控制台日志"""
    lid = item.get("id", "")
    duty = item.get("duty", "")
    dc = item.get("datacenter", "")
    name = item.get("name", "")
    home = item.get("home_world", "")
    desc = item.get("description", "") or ""
    reasons = get_rice_reason(desc)

    msg = (
        f"[新老板队] id={lid} | {duty}({dc}) | "
        f"{name}@{home} | 米={reasons}\n"
        f"  描述: {desc[:120]}"
    )

    if detail and FETCH_DETAIL_SLOTS:
        slots = detail.get("slots", [])
        if slots:
            msg += f"\n  阵容: {format_slots(slots)}"

    return msg


# ========== 通知 ==========
def play_alert_sound():
    """播放系统提示音（连续三声）"""
    if not PLAY_SOUND:
        return
    try:
        for _ in range(3):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.15)
    except Exception:
        pass


def show_msgbox(title: str, message: str):
    """非阻塞 MessageBox（最可靠的通知方式，一定弹窗）"""
    MB_ICONEXCLAMATION = 0x30
    MB_TOPMOST = 0x40000
    MB_SYSTEMMODAL = 0x1000
    # 截断消息（MessageBox 有长度限制）
    if len(message) > 500:
        message = message[:500] + "..."
    t = threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(
        0, message, title, MB_ICONEXCLAMATION | MB_TOPMOST | MB_SYSTEMMODAL
    ))
    t.daemon = True
    t.start()


def notify(title: str, message: str,
           bark_title: Optional[str] = None, bark_body: Optional[str] = None):
    """发送通知。PC 通知和手机通知是两个独立开关（config 里的 NOTIFY_PC / NOTIFY_BARK），
    可多选。bark_title / bark_body 不传时，手机推送沿用 PC 版的标题和正文。"""
    logger.info(f"通知: {title}")

    # ---- PC 通知：弹窗 + 提示音 + toast ----
    if NOTIFY_PC:
        # 播放提示音
        play_alert_sound()
        # MessageBox 弹窗（最可靠）
        show_msgbox(title, message)

        # 同时尝试 plyer toast（如果 Windows 通知中心开着就能看到）
        try:
            from plyer import notification as plyer_notif
            plyer_notif.notify(
                title=title,
                message=message[:200],
                app_name="FF14 Rice Monitor",
                timeout=10,
            )
        except Exception:
            pass

    # ---- 手机通知：Bark 推送到 iPhone（电脑当 server，手机只做接收）----
    if NOTIFY_BARK:
        try:
            from bark import push_bark, load_key
            key = load_key()
            if not key:
                logger.warning("Bark 未配置 key，跳过手机推送（把 key 填到 config.py 的 BARK_KEY，或新建 bark.key 文件）")
            else:
                ok, resp = push_bark(key, bark_title or title, bark_body or message,
                                     group=BARK_GROUP, sound=BARK_SOUND,
                                     retries=BARK_RETRY)
                if ok:
                    logger.info(f"Bark 推送成功: {resp}")
                else:
                    logger.error(f"Bark 推送失败: {resp}")
        except Exception as e:
            logger.error(f"Bark 推送异常: {e}")


# ========== 主循环 ==========
def main():
    # 初始化
    seen = load_seen()
    logger.info("=" * 60)
    logger.info("FF14 绝欧米茄老板队监控 v2 启动")
    logger.info(f"  副本 ID: {DUTIES}")
    logger.info(f"  轮询间隔: {POLL_INTERVAL}s")
    logger.info(f"  中文 DC: {CHINESE_DCS}")
    logger.info(f"  通知开关: PC 通知({'开' if NOTIFY_PC else '关'}) / 手机通知 Bark({'开' if NOTIFY_BARK else '关'})")
    logger.info(f"  重复提醒: {'开' if REPEAT_NOTIFY else '关'}" +
                (f"（同一条招募隔 {REPEAT_NOTIFY_MINUTES} 分钟再提醒）" if REPEAT_NOTIFY else ""))
    logger.info(f"  详情 API: {'开启' if FETCH_DETAIL_SLOTS else '关闭'}")
    logger.info(f"  已记录 {len(seen)} 条历史招募 ID")
    logger.info("=" * 60)

    # 首次启动时，先把当前所有已有的招募加入 seen（不通知旧招募）
    if not seen:
        logger.info("首次启动：初始化已有招募列表（不通知旧招募）...")
        initial_listings = fetch_listings(DUTIES)
        now = time.time()
        for item in initial_listings:
            dc = item.get("datacenter", "")
            if dc in CHINESE_DCS:
                seen[item.get("id")] = now
        save_seen(seen)
        logger.info(f"已记录 {len(initial_listings)} 条初始招募，开始监控新招募..."
                    + ("（重复提醒开着：这些旧招募若还挂着，间隔到期后也会提醒）" if REPEAT_NOTIFY else ""))

    # 轮询主循环
    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            listings = fetch_listings(DUTIES)
            consecutive_errors = 0

            new_found = []  # (item, is_repeat) 列表

            for item in listings:
                lid = item.get("id")
                dc = item.get("datacenter", "")
                desc = item.get("description", "") or ""

                # 只关心中文 DC + 智能米检测；提醒过且未到间隔的跳过
                if dc in CHINESE_DCS and is_rice_party(desc):
                    now = time.time()
                    if should_notify(lid, seen, now):
                        new_found.append((item, lid in seen))
                        seen[lid] = now

            if new_found:
                repeat_count = 0
                for item, is_repeat in new_found:
                    if is_repeat:
                        repeat_count += 1
                    # 获取详情（含 slots）
                    detail = None
                    if FETCH_DETAIL_SLOTS:
                        detail = fetch_detail(item.get("id"))

                    console_msg = format_listing_console(item, detail)
                    notify_msg = format_listing_detail(item, detail)
                    bark_title = item.get("duty", "")
                    bark_body = format_bark_body(item)

                    logger.info(console_msg)
                    if is_repeat:
                        logger.info("（重复提醒：这条招募已提醒过，间隔到期后再次提醒）")
                    logger.info(f"--- 通知内容 ---\n{notify_msg}\n---")

                    notify(f"发现老板队！({item.get('duty', '')})", notify_msg,
                           bark_title=bark_title, bark_body=bark_body)

                save_seen(seen)
                logger.info(f"本轮提醒 {len(new_found)} 条老板队招募" +
                            (f"（其中重复提醒 {repeat_count} 条）" if repeat_count else ""))

            # 统计当前所有绝欧招募
            total_cn = sum(1 for l in listings if l.get("datacenter", "") in CHINESE_DCS)
            rice_cn = sum(1 for l in listings
                         if l.get("datacenter", "") in CHINESE_DCS
                         and is_rice_party(l.get("description", "") or ""))
            logger.info(f"当前绝欧招募: {len(listings)} 条 (中文DC {total_cn} 条, 其中老板队 {rice_cn} 条)")

        except KeyboardInterrupt:
            logger.info("用户中断，退出监控")
            save_seen(seen)
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"监控异常: {e}")
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"连续失败 {max_consecutive_errors} 次，等待 5 分钟后重试...")
                time.sleep(300)
                consecutive_errors = 0
                continue

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
