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


# ========== "米"检测逻辑（v3.2，2026-11-26 裸 Nh/N时直接放行 + 时间剔除扩展 + Nwk时薪，来源见 rice_regex/v3_2.py） ==========
REGEX_VERSION = "v3.2"
STRONG_V3 = [
    # v2.4: 时薪/小時薪/每小时/每小時 从强关键词移除（P1：拿着三百万时薪吐槽、1k2时薪跪下请安
    # 是裸词提及，非招募；时薪信号改由 HOURLY 的数字分支承担：N时薪 / 时薪N / 每小时N。
    # 三批 52807/72280/79370 的搞转时薪仍由 搞转/过立结/价格兜底，全五批验证无回归。）
    "全需", "全贪", "黑CD", "黑cd",
    "老板躺", "老闆躺", "躺黑", "后躺", "後躺",
    "过本结", "过立结", "过本結", "過本結", "搞转", "搞轉",
    "郭立姐", "郭丽姐", "郭立姊", "郭立杰",
    "搞扣", "搞赔", "搞賠",
    "立结",
    "低保", "底薪",
]
# 从强关键词降级：老板队/老闆隊/老板刷/全黑 -> 见 BOSS_WORDS(需组合)；赔钱/扣钱/扣工资 -> 见 DEDUCT_CTX(需组合)

# 价格 token: 数字+单位（万/W/k/K/千，含 kw 组合），或 数字/CD|饭|小时 形式，或 裸数字>=100（仅用于雇佣/狗组合）
# v2.2: 价格数字首位非零（0w 不是价格）；支持小数 k（2.5k=2500万、1.5kw）
# v2.5: 单位后加字母守卫——单位后不得紧跟字母（33WC 的 W 不是价格单位），
# 仅允 W 后接 CD（1800WCD）、K 后接 W（1kw）；其余字母紧跟时不认作价格
PRICE_UNIT = re.compile(r'[1-9]\d{0,3}(?:\.\d{1,2})?\s*[kK千]?[wW万萬](?:(?![A-Za-z])|(?=[cC][dD]))|[1-9]\d{0,3}(?:\.\d{1,2})?\s*[kK千](?![pP])(?:(?![A-Za-z])|(?=[wW万萬]))|[1-9]\d{0,3}(?:\.\d{1,2})?\s*VV')  # v2.4: VV 为 w 的双 v 变体（2000vv）
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
HOURLY_V3 = re.compile(
    r'(\d{1,4})\s*[wW万萬kK千]?\s*/\s*(?:[hH](?![1-4](?![0-9]))|小时|小時)'   # 1000/h, 800/h 6p
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*1\s*(?:小时|小時)'                    # 400W/1小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*1\s*[hH](?![1-4](?![0-9]))'           # 1000/1h
    r'|(\d{1,4})\s*[wW万萬K千]\s*.?.?(?:一|1)\s*(?:小时|小時)'             # 500w一小时/1200W黄金一小时
    r'|(\d{1,4})\s*(?:[kK千][wW万萬]?|[wW万萬K千])\s*打(?:一|1)(?:个)?(?:半\s*)?(?:小时|小時|[hH])'  # 1000w打一小时
    r'|(\d{1,4})\s*[wW万萬]\s*半(?:小时|小時)'                                       # 500w半小时 / 500w半小時
    r'|(\d{1,4})\s*[wW万萬kK千]\s*打\s*半\s*(?:小时|小時|[hH])'             # 500w打半小时
    r'|(\d{3,4})\s*打(?:一|1)(?:个)?(?:半\s*)?(?:小时|小時|[hH])'           # 1000打一小时（裸数字）
    r'|(\d{3,4})\s*(?:一|1)\s*(?:小时|小時)'                               # 1000一小时（裸数字）
    r'|(\d{2,4})(?!\d)\s*[wW万萬kK千]?\s*1\s*[hH](?![1-4](?![0-9]))'         # 1200w1h
    r'|(\d{1,4})\s*[kK千][wW万]?\s*[hH](?![1-4](?![0-9]))'                   # 1kw h
    r'|(\d{1,4})\s*[wW万萬]?\s*(?:时薪|時薪|小時薪)'                            # 1000w时薪 / 900時薪
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*打满\s*(?:一|1|两|二|2|三|3|半)?\s*(?:个)?\s*(?:小时|小時|[hH])'  # 3200w打满两小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*俩\s*(?:小时|小時|[hH])'                   # 600w俩小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*每\s*[hH](?![1-4](?![0-9]))'               # 400w每h
    r'|(\d{1,4})\s*[wW万萬kK千]\s*(?:时|時)(?!间|間|候)'                        # 1400w时（要求带 w）/ 1400w時
    r'|(\d{1,4})\s*[kK千][wW万萬]?\s*(?:时|時)(?!间|間|候)'                  # v2.4: 1kw时 / 1kw時
    r'|(\d{1,4})\s*[kK千][wW万萬]?\s*(?:一|1)?\s*(?:小时|小時)'             # v2.4: 1kw 1小时 / 1kw一小时
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*(?:时|時)(?!间|間|候)'                   # 1200/时 / 1200/時
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*\d{1,2}\s*(?:min|MIN|分钟|分鐘|分)'     # 500/30min
    r'|(?:时薪|小時薪|時薪)\s*[1-9]\d{0,3}(?!\d)'                            # v2.4: 时薪1000（数字在后；v2.4 时薪移出强关键词后由本分支兜底）
    r'|(?:每小时|每小時)\s*[1-9]\d{0,3}(?!\d)'                               # v2.4: 每小时1000
    r'|(\d{3,4})\s*半(?:小时|小時)'                                          # v2.4: 500半小时（裸数字+半小时）
    r'|(\d{1,4})\s*[wW万萬kK千]?\s*/\s*[1-9]\s*[hH](?![1-4](?![0-9]))'  # v2.8: N/Nh（700/2h；N/1h 已有分支）
    r'|(?:一千万|一千萬)\s*(?:一|1)?\s*(?:小时|小時|[hH](?![1-4](?![0-9])))'  # v2.8: 一千万一小时
    r'|(\d{2,4})\s*[wW万萬]\s*[hH](?![1-4](?![0-9]))'                   # v2.8: Nwh（1200wh；1000wh 郭丽姐先例）
    r'|(\d{1,4})\s*[wW][aA][nN]\s*/\s*[hH](?![1-4](?![0-9]))'  # v2.9: Nwan/h（800wan/h；全库仅 1 处）
)
# 人数拆分：要求前后不是数字/= /pP（排除 P2P3 阶段、7=1+P2 合并）；在未去空格原文上匹配
SPLIT_V3 = re.compile(r'(?<![\d=pP])[1-7][pP][1-7](?![\d])')
# 价格+次数：要求带单位，或首数字>=100；v2.2：价格支持小数，须 >0
# v2.5: 数字前不得紧贴字母/数字（去空格粘连：H2 20+ 不再拼成 H220+10；全五批验证无 TP 依赖字母前数字），
# 但 3+ 大写字母策略码（YMD/MGL/TUUF 等）后的数字不是职业码粘连（YMD300+1 是价+人数）
PRICE_PLUS_RUN_V3 = re.compile(r'(?:^|[^0-9A-Za-z:]|(?<=[A-Z]{3}))(\d{1,4}(?:\.\d{1,2})?)\s*([kK千]?[wW万萬]?)\s*(?:[+＋]|加)\s*(\d{1,2})(?:把|次|局)?')  # v2.6: 加 作分隔符（800加1；3加50 受 ≥100/单位门控保护）
CLEAR_V3 = ["过本", "過本", "渡劫", "通关", "通關", "包过", "包過", "复建"]
# v2.2: 全黑 从强关键词降级到此（组合词：需价格配合）
BOSS_WORDS = ["老板", "老闆", "打工", "躺", "老板队", "老闆隊", "老板刷", "全黑"]
HIRE_WORDS = ["陪练", "陪練", "傭兵", "佣兵", "代打", "代练", "代練", "代班"]
DEDUCT_STRONG = ["搞扣", "搞赔", "搞賠"]
DEDUCT_CTX = ["扣工资", "扣錢", "扣钱", "赔钱", "賠錢"]
STAGE_DEDUCT_V3 = re.compile(
    r'(?:[pP]?\s*)?([1-9])\s*[kK扣]\s*(?:[pP]?\s*)?([1-9])\s*[pP赔賠](?!\s*/\s*[cC][dD])'
    r'|(?:[pP]\s*)?([1-9])\s*扣\s*(?:[pP]\s*)?([1-9])\s*赔')
REQ_STRIP = [
    re.compile(r'过本(经验|记录|配置)'),
    re.compile(r'(内含|内置|内有|已有)[^，,。]{0,8}?(过本|通關|通关)'),
    re.compile(r'(要求|需要|需)[^，,。]{0,12}?(过本|通關|通关|渡劫)'),
    # v2.2: 进度目标表述不是付费过本（1000h 争取过本 … 7P1 是进度队；全三批仅此一例）
    re.compile(r'(争取|想|求|目标|為了|为了)过本'),
]
RANT_SENSITIVE = ["低保", "底薪", "时薪", "小時薪", "時薪", "每小时", "每小時"]
# v2.9: 低保/底薪招募语境（招募动词/进队/锁/时间/角色/价格/结算/狗/过本/拆分任一即有语境；
# 全库 131 处低保/底薪中仅 5648“低保就在手中”无语境）
LOWBAO_CTX_RE = re.compile(r'招|来|找|寻|进队|锁|点|D\d|H\d|MT|ST|狗|过本|渡劫|通关|立结|搞|低保队|7=|6=|5=|\+|W|万|时|结|赔|扣|CD|飯|饭')
RANT_RE = re.compile(r'骗[^，,。]{0,8}?(不给|不給)')
# v2.2: 嘲讽标记（还寄吧 500w+1 穷逼开什么队 是嘲讽帖，非招募）
RANT_MOCK = ["穷逼", "寄吧", "开什么队", "避雷", "挂人", "不会吧", "神人", "查清楚", "歉占"]  # v2.4: 避雷/挂人帖（76684）；v2.5: 不会吧不会吧讽刺模板（14822/15611）；v2.6: 神人/查清楚（没钱开100+1的神人嘲讽帖）、歉占（歉占问价帖，道歉语境）

def _norm(desc):
    return unicodedata.normalize('NFKC', desc or '')
def _flat(desc):
    return _norm(desc).upper().replace(' ', '').replace('　', '')
def _has_rant(raw):
    t = _flat(raw)
    if "不想活了" in t or "藏亲友" in t: return True
    if any(m in t for m in RANT_MOCK): return True
    if RANT_RE.search(t): return True
    # v2.6: “喜欢…就去…”引流嘲讽模板（200+1 喜欢摘棉花就去100+1，非招募；全库仅 3 处，均在第七批 FP 里）
    if re.search(r'喜欢[^，,。]{0,12}就去', t): return True
    # v2.5: 低保/底薪/时薪类 + 吗 问句（能获得低保吗、时薪多少钱吗）是提问不是招募
    if re.search(r'(低保|底薪|时薪|小時薪|時薪|每小时|每小時).{0,6}吗', t): return True
    return False

def _has_price_unit(text):
    return bool(PRICE_UNIT.search(text))
def _has_price_plus_run(text):
    m = PRICE_PLUS_RUN_V3.search(text)
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
        # v2.5: 其后紧跟 金 的数字是 gil（500金币=500 gil，约等于零），不是价格
        if win2[m.end():m.end()+1] == '金': continue
        return True
    return False
# v2.4: 雇佣窗口 16 -> 28（3238：招h1陪练…打半个小时，400w，价在 27 字外；
# 6896：9-11點 70萬，先剔时间段再取窗口）。窗口内先去掉电话/QQ 长串与时间段。
HIRE_WINDOW_V3 = 28
def _hire_with_price(raw):
    t = _flat(raw)
    for pat in TIME_STRIP: t = pat.sub('', t)
    t = _strip_time_ranges(t)
    for w in HIRE_WORDS:
        for m in re.finditer(re.escape(w), t):
            lo, hi = max(0, m.start()-HIRE_WINDOW_V3), m.end()+HIRE_WINDOW_V3
            win = t[lo:hi]
            if (PRICE_UNIT.search(win) or PRICE_PER_CD.search(win) or PRICE_PER_MEAL.search(win)
                    or _bare_num(win) or re.search(r'[1-9]\d*\s*/\s*(?:飯|CD|小时|小時)', win)
                    or re.search(r'\d{3,4}\s*[hH](?![A-Za-z0-9])', win)):
                return True
    return False
def _short_hire_with_price(raw):
    t = _flat(raw)
    if re.search(r'\d{3,4}\s*陪(?![练練])', t): return True
    # v2.6: Nh + 招/找/来 + 职业 + 陪简写（400h招d3陪；全库 5 处 Nh…陪，4 处已是 TP，仅 21414 新命中）
    return bool(re.search(r'\d{3,4}\s*[hH]\s*(?:招|找|来)\s*(?:非\w{0,3}[dD]\d|[dD]\d|H\d|MT|ST)?\s*陪(?![练練])', t))
def _hire_negotiable(raw):
    t = _flat(raw)
    # v2.4: 进队谈价也是议价（71562 价格进队谈；80105 价不对进队说）
    # v2.6: 带价来 = 价格可议（招D4陪练（带价来）；全库仅 1 处）
    if any(w in t for w in HIRE_WORDS) and bool(re.search(r'带价|价格(?:可议|可談|可谈|面议|详谈|进队(?:谈|说))|价不对进队(?:说|谈)|待遇(?:可谈|可談|可议|可議|面议|面談|详谈|詳談)', t)):
        return True
    # v2.8: 带价来 + 招/找/寻 + 职业（招今晚9到十一点d3…带价来；全库 带价 仅 2 处）
    if '带价' in t:
        return bool(re.search(r'(?:招募|招|找|寻)\s*(?:一|1)?\s*(?:个|位)?\s*(?:\S{0,12}?\s*)?(?:非\w{0,3}[dD]\d|[dD]\d|H\d|MT|ST)', t))
    return False
# v2.9: 招陪练 + 时薪机制词（可转时薪磨合；无数字价格，时薪本身就是付费机制；
# 全库 招/找/寻+陪练/佣兵+时薪 仅 20 处，17 处已有数字价格已是 TP，仅 3 处新命中，
# 均为第九批 FN；其余 8 处未命中 时薪 帖均为 rant/问价/吐槽，无该组合，不受影响）
def _hire_hourly_mechanism(raw):
    t = _flat(raw)
    return bool(re.search(r'(?:招|找|寻)\s*(?:一|1)?\s*(?:个|位)?\s*(?:\S{0,12}?\s*)?(?:陪练|陪練|佣兵|傭兵|代打|代班).{0,40}?(?:时薪|時薪|小時薪)', t))
def _dog_with_price(raw):
    # v2.2: 先去掉 QQ/电话等 7 位以上长串，避免 ±10 字窗口把 792061652 截断成 7920 当价格
    t = re.sub(r'\d{7,}', '', _flat(raw))
    # v2.6: 狗摇是机制词，其后数字不是价格（狗摇533；全库 15 处狗摇+数字，0 条 TP）
    t = re.sub(r'狗摇\s*\d+', '狗摇', t)
    # v2.7: 狗+数字 仅当数字是真价格时才算——数字后紧跟价格单位（w/W/万/萬/k/K/千），
    # 或数字位数 ≥2 且不全是 3 且不是 1211 且后不跟“金”；个位数一律不算（招人头数，如 +1）。
    # 实例“锁有 有狗 +1 33”：+1 是招 1 个人，33 是进度码（本体前 33%），不是价格（FP，
    # v2.3–v2.6 均误判）；保留“狗+200w”“狗+300”真价格命中。
    # 注意：须在保留空格的 _norm 文本上匹配——_flat 会把“+1 33”拼成“+133”误读。
    _tn = re.sub(r'狗摇\s*\d+', '狗摇', re.sub(r'\d{7,}', '', _norm(raw)))
    _m = re.search(r'狗\s*[+＋]\s*(\d+)', _tn)
    if _m:
        _num = _m.group(1)
        _after = _tn[_m.end():_m.end() + 1]
        if _after in ('w', 'W', '万', '萬', 'k', 'K', '千'):
            return True
        if len(_num) >= 2 and not re.fullmatch(r'3+', _num) and _num != '1211' and _after != '金':
            return True
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
# v3.2: HH:MM 到/至 HH:MM 时间段（8:30到10:30；连字符版已有 TIME_RANGE_HM）
TIME_RANGE_HM_TO = re.compile(r'\d{1,2}:\d{2}\s*(?:到|至|~|～)\s*\d{1,2}:\d{2}')
# v3.2: 晚/夜 + 1-2 位 - 2-3 位（晚8-111，打错字的晚8-11；后不跟价格单位/数字，
# 防 2000-3000w 这类价；探针证实 v3.1 里“晚8-111”会漏出 BARE_NUM=111）
TIME_RANGE_LATE = re.compile(r'(?:晚|夜)\s*\d{1,2}\s*[-~～]\s*\d{2,3}(?![\dwW万萬kK千])')
# v3.2: 单点钟点（21点/9点/9点半；只认 1-2 位，4 位数字+点如 2000点 不动；
# (?<!\d) 防长串内截断；bare fallback 里 1-2 位本就不算价，此处是显式兜底）
TIME_POINT = re.compile(r'(?<!\d)\d{1,2}\s*[点點](?:半)?(?!\d)')
# v2.5: N点(半)-N点(半) 时间段（9点半-11点半、9点-11点）；须两边都是合法钟点才剔（防 2000点-3000点 这类数字）
TIME_RANGE_HM2 = re.compile(r'(\d{1,2})\s*(?:点|點)\s*半?\s*[-~～]\s*(\d{1,2})\s*(?:点|點)\s*半?')
def _strip_time_ranges(t):
    t = TIME_RANGE_HM2.sub(lambda m: '' if 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 23 else m.group(0), t)
    t = TIME_RANGE_SIMPLE.sub('', t)
    t = TIME_RANGE_GLUED_CD.sub('', t)
    def _hhmm_repl(m):
        # 只有两边都是合法钟点才当时间段（2000-3000w 的 3000 不是钟点，不剔；1800-2000w 有 w 也不剔）
        if _looks_like_time(m.group(1)) and _looks_like_time(m.group(2)):
            return ''
        return m.group(0)
    t = TIME_RANGE_HHMM.sub(_hhmm_repl, t)
    t = TIME_RANGE_HM.sub('', t)
    t = TIME_RANGE_HM_TO.sub('', t)
    t = TIME_RANGE_LATE.sub('', t)
    t = TIME_POINT.sub('', t)
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
# v2.8: Nw一人 + 结账里程碑付款（100w一人 见到p1雷火线结账；全库 结账 21 处，
# 其余已有价格信号或无价）。窗口 28 字内须出现结账/结算。
PRICE_PER_PERSON_SETTLE_RE = re.compile(r'[1-9]\d{0,3}\s*[wW万萬kK千]?\s*(?:一|1)?\s*(?:个)?\s*人[^，,。]{0,28}?(?:结账|結賬|结算|結算)')
def _price_per_person_settle(raw):
    return bool(PRICE_PER_PERSON_SETTLE_RE.search(_flat(raw)))
# v2.8: 0w 玩笑价守卫——文本含边界内 0w/0万 且无任何真实价格信号时，扣赔信号不触发
# （0w加一，搞扣；全库真 0w 仅 3 处：3772“0w躺黑”为拟合批 TP，靠 躺黑 触发、不受影响；
# 15568“0w/cd…”回归例本就判 False；56590“50w+1稿赔300”有真实价格、不受影响）。
ZERO_PRICE_JOKE_RE = re.compile(r'(?<![\d.])0\s*[wW万萬]')
def _zero_price_joke(raw):
    t = _flat(raw)
    if not ZERO_PRICE_JOKE_RE.search(t):
        return False
    return not (_has_price_unit(t) or PRICE_PER_CD.search(_strip_time_ranges(t))
            or PRICE_PER_MEAL.search(t) or PRICE_PER_CLEAR.search(t)
            or HOURLY_V3.search(raw) or _has_price_plus_run(t) or _bare_num(t))
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
    # v2.5: 动词组加 招募（招募d4、招募个d1）
    # v2.6: 动词组加 寻（寻d3，800w；见头注安全核查）
    if re.search(price + r'\s*.{0,12}?(?:来|招募|招|找|寻)\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2):
        return True
    # 裸价 + 招/来 + 职业（3000招一H2）；裸数字读作时间时除外（1800招非黑d1）
    m = re.search(r'(\d{3,4})\s*(?:来|招募|招|寻)\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2)
    if m and not _looks_like_time(m.group(1)):
        return True
    # v2.2: 裸价 + 找 + 职业（1600找非镰d2）；找 不受时间排除（全三批仅此一例，招/来仍受排除）
    if re.search(r'\d{3,4}\s*找\s*(?:一|1)?\s*(?:个|位)?\s*' + job, t2):
        return True
    # 尾价——招/来/找 + 职业 … 价格在后（来个D4 1600W）
    # v2.5: 动词组加 招募；动词后允 ≤4 字修饰（招硬硬的MT、来8加D1、招新H1）；
    # 职业与价格之间允 [pP]\d 进度码（进度p5、P2近U）
    if re.search(r'(?:来|招募|招|找|寻)\s*(?:一|1)?\s*(?:个|位)?\s*(?:\S{0,4}?\s*)?' + job + r'(?:[^\d]|[pP]\d){0,20}?\d{1,4}(?:\.\d{1,2})?\s*(?:[kK千]?[wW万萬]|[kK千])', t2):
        return True
    # v2.2: 价 … 职业（300w，+1，ymd，武士d1；价在前、中间隔 +1/ymd/职业名，≤10 字无数字）
    if re.search(price + r'\s*[^\d]{0,10}?(?:D\d|H\d|MT|ST)', t2):
        return True
    # v2.4: 价 + 加一（1kw加一）；后跟汉字会把 一 扩成别的词（加一根），用前瞻挡住
    if re.search(price + r'\s*加\s*(?:一|1|壹)\s*(?:个|位|人)?(?![\u4e00-\u9fa5])', t2):
        return True
    # v2.9: 带单位价 + 等1/等一 + 职业（1500w，打到11点，等1非镰刀d1；全库仅 1 处新命中；
    # 价须带单位，防 p52 七等一h2 的裸数字 52 被读作价格；2200/cd等1h2 已是 TP）
    if re.search(price + r'\s*.{0,24}?等\s*(?:一|1)\s*(?:个|位)?\s*' + job, t2):
        return True
    return False

CHATTER = ["刚刚", "少给", "少給"]
# v3.0: 现金交易价格（用户指定格式：30r / 50糖 / 20t / N元 / ¥N）。
# r/t 要求 ≥2 位数字（防 "来1t" 招 1 个 T 这类招人写法被读作现金价）；
# 元/¥ 允许 1 位起（¥5、5元是常见写法）；RMB 全拼单独一支。
CASH_RE = re.compile(
    r'[1-9]\d{0,3}(?:\.\d{1,2})?\s*(?:RMB|rmb)'
    r'|[1-9]\d{1,3}(?:\.\d{1,2})?\s*[rR](?![A-Za-z0-9])'
    r'|[1-9]\d{1,3}\s*糖'
    r'|[1-9]\d{1,3}\s*[tT](?![A-Za-z0-9])'
    r'|[1-9]\d{0,3}(?:\.\d{1,2})?\s*元'
    r'|¥\s*[1-9]\d{0,3}(?:\.\d{1,2})?')

# v3.2: “1wk时薪”窄口径（1wk 是发帖人打错字；只对 wk + 小时语境放松字母守卫，
# 简单才留）。在 _price_hit 的结构化循环里用（t 已大写，故 WK）。
NWK_HOURLY_RE = re.compile(r'[1-9]\d{0,3}\s*WK\s*(?:时薪|時薪|小時薪|时|時)(?!间|間|候)')

def _hourly_bare_price(raw):
    """v3.2: 裸 Nh/N时直接算价格（用户 2026-11-26 晚裁决，取代原 v3.2 方案的
    28 字窗口语境规则）。1 级已经过滤掉大半问题，2 级只需要能定位到明确的报价
    数字就全部放行。Nh \\d{3,4}，N时 \\d{3,4}（用户 2026-11-26 20:00 裁决：时用
    \\d{3,4}，2 位数钟点如“21时”不得放行；27 条里的 N时 全是 3-4 位数，覆盖没问题）；
    含空格写法（900 h）亦可（_flat 去空格）。(?<!\\d) 防长串数字内截断（如
    QQ 号 12345h 不应读出 2345h）。原“裸 600h/1000h 无语境仍排除”守卫已作废。"""
    t = _flat(raw)
    m = re.search(r'(?<!\d)(\d{3,4})\s*[hH]|(?<!\d)(\d{3,4})\s*时(?!间|候)', t)
    return f'HOURLY_BARE={m.group()}' if m else None

def _hourly_nh_with_hire(raw):
    """v3.1: 裸 Nh + 雇佣词 = 时薪（批 7 已确立规则“裸Nh + 雇佣词 = 时薪，无雇佣词时
    才是游戏时长”）。stage 2 的 fallback 里裸 Nh 一律当游戏时长排除是错的
    （主助手抽查发现：20913“500h 招d1陪练”被误 veto）。
    (a) _short_hire_with_price 的雇佣语境（Nh + 招/找/来 + 职业 + 陪简写，或 Nh + 陪）；
    (b) Nh 在陪练/佣兵/代打/代班/代练前后 28 字内（复用 _hire_with_price 的窗口口径
    HIRE_WINDOW_V3 = 28）。无雇佣词的 Nh（如 1000h 过立结、800h打一小时）仍是游戏时长——该假设已被 27 条真老板队证伪（见 v3.2）；裸 Nh/N时现由 _hourly_bare_price 直接放行，本函数保留作兼容。"""
    t = _flat(raw)
    if not re.search(r'\d{3,4}\s*[hH]', t):
        return False
    if _short_hire_with_price(raw):
        return True
    for w in HIRE_WORDS:
        for m in re.finditer(re.escape(w), t):
            lo, hi = max(0, m.start() - HIRE_WINDOW_V3), m.end() + HIRE_WINDOW_V3
            if re.search(r'\d{3,4}\s*[hH](?![A-Za-z0-9])', t[lo:hi]):
                return True
    return False

def _price_hit(raw):
    """v3.0 stage 2：返回命中的数字价格字符串，无则返回 None。

    用户定版（2026-11-26 晚，最终设计）：二级过滤本质是找数字、然后排除与价格无
    关的数字方式。结算词（低保/底薪/立结/过立结/搞赔/搞转/黑CD 等）本身一律不算，
    但数字后跟结算词时数字本身算价格（如 1200搞转时）。
    顺序（用户指定）：先查结构化价格格式，只有裸数字才走“长数字排除 + 队形排除”
    的 fallback——5 位数非 0 结尾的排除只针对裸数字，后面紧跟 CD/价格单位的不排除
    （24001CD 里的 24001 不能当 QQ 号排除掉，因为后面有 CD）。
    结构化格式 = PRICE_UNIT / PRICE_PER_CD（时间段剔除后）/ PRICE_PER_MEAL /
    HOURLY 各分支 / CASH_RE（r/糖/t/元/¥）/ wan 认万 / 中文数字（千/百/万组合）。
    数字文本用 NFKC + 大写、保留空格的 _norm 文本（v2.7 教训：涉及数字边界的规则
    须在保留空格的文本上匹配，去空格粘连会误伤 D2 1000 / 300W ST / 70萬D3 / 800w p5）。
    v3.2 新增：裸 Nh/N时直接算价格（_hourly_bare_price，用户裁决；N时 \\d{3,4}，
    2 位数钟点“21时”不得放行）与 Nwk时薪窄口径（NWK_HOURLY_RE，只对 wk + 小时语境
    放松字母守卫）；时间剔除扩展（_strip_time_ranges 新增 TIME_RANGE_HM_TO /
    TIME_RANGE_LATE / TIME_POINT，单点 HH:MM 由 TIME_STRIP 整段去掉、不会留下 2100）。
    """
    raw = _norm(raw)
    t = raw.upper()          # NFKC + 大写，保留空格
    # v3.1: 纯净队 carve-out（用户定版，只查三个强关键词，没有别的格式）：
    # 文本含 纯净/纯绿/无科技 任一词时直接放行（即使无价格数字）。纯净老板队极少
    # 但通常是 2-3 倍报价。caveat：“非纯净”含“纯净”子串，九批中 4 行“非纯净”
    # 全部 stage 1 命中 0，未受影响；若未来出现 stage 1 命中的“非纯净”行会被误放。
    for _w in ('纯净', '纯绿', '无科技'):
        if _w in t:
            return f'PURE={_w}'
    t2 = _strip_time_ranges(t)
    # 1. 结构化价格格式（先查）
    for name, pat, txt in (
        ('PRICE_UNIT', PRICE_UNIT, t),
        ('PRICE_PER_CD', PRICE_PER_CD, t2),
        ('PRICE_PER_MEAL', PRICE_PER_MEAL, t),
        ('HOURLY', HOURLY_V3, raw),
        ('CASH', CASH_RE, t),
        ('HOURLY_WK', NWK_HOURLY_RE, t),
    ):
        m = pat.search(txt)
        if m:
            return f'{name}={m.group()}'
    # 2. 中文数字（只做招募里常见的 千/百/万 组合，不搞全量中文数字解析）
    hit = _chinese_number_hit(t)
    if hit:
        return hit
    # v3.2: 裸 Nh/N时直接算价格（用户裁决，见文件头）：新规则先行、覆盖下面
    # 的 _hourly_nh_with_hire（后者保留作兼容）。
    hit = _hourly_bare_price(raw)
    if hit:
        return hit
    # v3.1: 裸 Nh + 雇佣词 = 时薪（批 7 规则）：Nh 是陪练的时薪价格
    if _hourly_nh_with_hire(raw):
        return 'HOURLY_NH'
    # 3. 裸数字 fallback（时间段与长串已剔除的文本上找）
    t3 = t2
    for pat in TIME_STRIP:
        t3 = pat.sub('', t3)
    hit = _bare_number_hit(t3)
    if hit:
        return f'BARE_NUM={hit}'
    return None

# v3.0: 中文数字（只做招募里常见的 千/百/万 组合；长形式优先，避免“一千万”
# 被先匹配成“一千”后误判尾巴）。
_CN_NUM_RE = re.compile(
    r'[一二两三四五六七八九十]万[一二两三四五六七八九]千'
    r'|[一二两三四五六七八九十]千[一二两三四五六七八九]百'
    r'|[一二两三四五六七八九十]千[一二两三四五六七八九]'
    r'|[一二两三四五六七八九十][千百万]')

def _chinese_number_hit(t):
    """中文数字命中：任一中文数字（千/百/万组合）即算价格，除非后跟 小时/小時/h
    （一千小时 = 游戏时长，不是价格）。中文数字不会是 QQ 号/人数/进度码，无须
    其他排除口径。返回命中字符串或 None。"""
    for m in _CN_NUM_RE.finditer(t):
        tail = t[m.end():m.end() + 3].lstrip()
        if tail.startswith(('小时', '小時')) or tail[:1] in ('h', 'H'):
            continue  # 中文数字 + 小时 = 游戏时长
        return f'CN_NUM={m.group()}'
    return None

def _is_ascii_letter(ch):
    return ('A' <= ch <= 'Z') or ('a' <= ch <= 'z')

def _bare_number_hit(t):
    """裸数字 fallback：逐个找阿拉伯数字，排除与价格无关的数字方式后，剩下任一
    数字即算命中价格。排除口径复用已有规则（不凭空发明）：
    - ≥5 位且非 0 结尾的长数字按 QQ 号/联系方式排除（用户草图）；5 位以上 0 结
      尾的按草图原意暂放行（须过其余排除口径）；7 位以上长串已由 TIME_STRIP 剔除
      （已有规则）；1–2 位裸数字不算（人数/进度码/时间；带单位的由结构化正则兜底）。
    - 全 3、1211、后跟“金”不算（_bare_num 排除口径）；0 开头不算（PRICE_UNIT
      首位非零规则，0230 是时间不是价格）。
    - 后跟 h/H 不算（裸 Nh 游戏时长，含 "900 h" 带空格写法）；后跟 时/時（裸钟点，
      含 "900 时" 带空格写法）不算，时薪是价格词（后跟 时薪 放行）。
    - 前后紧贴 ASCII 字母/数字不算（粘连；3+ 大写字母策略码 YMD/MGL/TUUF 例外，
      v2.5 规则；N=M 队形紧贴 = 排除；NP1 如 7P1 已由 1–2 位长度排除）。
    - 前有 + 且 <100 不算（+N 人头数）；前有 + 且 ≥100 算（狗+300、1000+1 这
      类价+数）；后跟价格单位（w/W/万/萬/k/K/千，wan 认万）或 CD 算价格；其余
      字母紧贴（如 D2/H2/P5/职业码）与 _bare_num 同口径，不排除。
    返回命中的数字字符串或 None。"""
    for m in re.finditer(r'\d+(?:\.\d{1,2})?', t):
        num = m.group(0)
        digits = num.split('.')[0]
        n = len(digits)
        if n >= 5 and not digits.endswith('0'):
            continue  # QQ 号/联系方式（用户草图；只针对裸数字）
        if n <= 2:
            continue  # 人数/进度码/时间
        if re.fullmatch(r'3+', digits):
            continue  # 全 3（进度码）
        if digits == '1211':
            continue  # 1211（进度码）
        if digits.startswith('0'):
            continue  # 0 开头是时间（0230）不是价格
        # —— 后跟检查：看数字后第一个非空格字符（空格不是词界，“900 h”与“900h”
        # 同为游戏时长，“900 时”与“900时”同为钟点）——
        j = m.end()
        while j < len(t) and t[j] == ' ':
            j += 1
        after = t[j:j + 1] if j < len(t) else ''
        after_tail = t[j:]
        before = t[m.start() - 1:m.start()] if m.start() > 0 else ''
        if after == '金':
            continue
        if after in ('h', 'H'):
            continue  # 裸 Nh 游戏时长（含 "900 h" 带空格写法）
        if after in ('时', '時'):
            if after_tail.startswith(('时薪', '時薪', '小時薪')):
                return num  # 时薪是价格词
            continue  # 裸钟点（含 "900 时" 带空格写法）
        if after and _is_ascii_letter(after):
            if after_tail[:3].upper() == 'WAN':
                return num  # wan 认万（用户指定）
            if after in 'Ww万萬Kk千':
                nxt = after_tail[1:2]
                if not nxt or not _is_ascii_letter(nxt):
                    return num  # 价格单位后不是字母
                if after in 'Ww' and after_tail[1:3].upper() == 'CD':
                    return num  # W 后接 CD（如 2400WCD 的裸数字口径）
                continue  # 单位后紧贴字母（PRICE_UNIT 字母守卫，如 800WC）
            if after_tail[:2].upper() == 'CD':
                return num  # 后跟 CD（如 2400CD 的裸数字口径）
            # 其余字母紧贴（如 D2/H2/P5/职业码）：与 _bare_num 同口径，不排除，
            # 落到前贴检查（NP1 如 7P1 已由 1–2 位长度排除）
        # —— 前贴检查 ——
        if before == '+':
            if float(num) < 100:
                continue  # +N 人头数
            return num  # 狗+300、1000+1 这类价+数
        if before == '=':
            continue  # N=M 队形（如 5=3）
        if before and (_is_ascii_letter(before) or before.isdigit()):
            # 粘连；3+ 大写字母策略码（YMD/MGL/TUUF）例外（v2.5 规则）
            if m.start() >= 3 and re.fullmatch(r'[A-Z]{3}', t[m.start() - 3:m.start()]):
                return num
            continue
        return num
    return None

def _has_price_stage2(raw):
    return _price_hit(raw) is not None

def rice_stages(desc):
    """v3.1 两级判定：返回 (stage1, stage2, stage2_price_hit)。

    stage1 = v2.9 判定；stage2 = 是否命中数字价格；stage2_price_hit 为命中的价格字符串
    （无则为 None）。供 rice_monitor.py 的本地日志挂钩使用；is_rice_party 即 stage1 and stage2。
    """
    s1 = _is_rice_party_stage1(desc)
    hit = _price_hit(desc) if s1 else None
    return s1, (hit is not None), hit

def is_rice_party(desc):
    s1, s2, _ = rice_stages(desc)
    return s1 and s2

def _is_rice_party_stage1(desc):
    if not desc: return False
    if any(c in desc for c in CHATTER): return False
    raw = _norm(desc)
    text = _flat(raw)
    # v2.4: 反向要钱 / 讽刺语境不是老板队（v我50、赔老板；全 4945 条数据里各只出现 1 次，均在第四批 FP 里）
    if re.search(r'V\s*我\s*\d+', text): return False
    if re.search(r'(?<![\d扣搞])[赔賠]老板', text): return False  # v2.4: 赔前是数字/扣/搞时是扣赔机制（如 5扣6赔老板d1），不是反向付钱
    # v2.6: 反向给钱不是老板队（打工倒给老板300w时；全库仅 1 处）
    if '倒给' in text: return False
    # v2.8: 反向给钱不是老板队（+1打工仔给老板50w；全库 给老板 仅 2 处，另 1 处为批7 FP 25298）
    if re.search(r'(?:给|給)(?:老板|老闆)\s*[1-9]', text): return False
    # v2.9: 反向给钱不是老板队（+1 锁有 过本每人倒贴我100w；全库 倒贴 仅 1 处）
    if re.search(r'倒贴\s*(?:我|老板|老闆)', text): return False
    # v2.5: 问是不是老板队（确定是老板队吗）是提问/抱怨，不是招募
    if re.search(r'是老板队[吗嘛]', text): return False
    rant = _has_rant(raw)
    # v2.6: 金币是 gil（约等于零），不是价格；全文只有金币、没有真实价格信号时不是老板队
    # （5金币 躺黑 打工搞扣…；全库 7 处含金币，0 条 TP）
    if '金币' in text and not (_has_price_unit(text) or PRICE_PER_CD.search(_strip_time_ranges(text))
            or PRICE_PER_MEAL.search(text) or PRICE_PER_CLEAR.search(text)
            or HOURLY_V3.search(raw) or _has_price_plus_run(text)):
        return False
    strong_hits = [k for k in STRONG_V3 if k.upper() in text]
    # v2.9: 低保/底薪感慨守卫——低保/底薪是唯一强关键词且全文无招募语境时不触发
    # （岂不闻天无绝人之路，只要我想过，低保就在手中；其余 130 处均有语境）
    if strong_hits and all(k in ("低保", "底薪") for k in strong_hits) and not LOWBAO_CTX_RE.search(text):
        strong_hits = []
    if _zero_price_joke(raw):
        # v2.8: 0w 玩笑价时扣赔强关键词不触发（0w加一，搞扣；躺黑等非扣赔强关键词不受影响）
        strong_hits = [k for k in strong_hits if k not in DEDUCT_STRONG]
    if strong_hits:
        # v2.4: 问价语境下降权时薪类强关键词（想问一下，现在时薪多少钱）
        if _is_price_inquiry(text):
            # v2.4: 问价语境下降权扩展到 躺黑/老板躺（3006 躺黑现在多少钱；时薪类已移出强关键词）
            strong_hits = [k for k in strong_hits if k not in ("躺黑", "老板躺", "老闆躺")]
        if strong_hits and not (rant and all(k in RANT_SENSITIVE for k in strong_hits)):
            return True
    if HOURLY_V3.search(raw) and not rant: return True   # v2.2: 时薪在未去空格原文上匹配；v2.5: 吐槽语境下时薪不触发（不会吧不会吧 400W 时薪渡劫）
    if _hourly_bare_with_settlement(raw): return True   # v2.4: 裸 N时 + 结算锚点（300时 过本+100）
    # v2.4: 按 CD 价用剔除时间段后的文本（晚8-111cd 不再拼成 111CD）
    if PRICE_PER_CD.search(_strip_time_ranges(text)) or PRICE_PER_MEAL.search(text) or PRICE_PER_CLEAR.search(text): return True
    if PRICE_PER_RUN.search(text): return True   # v2.2 新增：按把计价
    if PRICE_UNTIL.search(text): return True     # v2.2 新增：价 + 打到 + 时间
    if _has_price_plus_run(text) and not rant: return True   # v2.2: 吐槽语境下价格+次数不触发
    if _hire_with_price(raw) or _short_hire_with_price(raw) or _hire_negotiable(raw) or _hire_hourly_mechanism(raw) or _dog_with_price(raw): return True
    if _price_per_person_settle(raw): return True  # v2.8: Nw一人 + 结账
    if _photo_entry_with_price(raw): return True
    if _price_recruit(raw): return True
    has_price = _has_price_unit(text)
    t2 = text
    for pat in REQ_STRIP: t2 = pat.sub('', t2)
    has_clear = any(k.upper() in t2 for k in CLEAR_V3)
    has_boss = any(k in text for k in BOSS_WORDS)
    has_split = bool(SPLIT_V3.search(raw))
    has_deduct = any(k.upper() in text for k in DEDUCT_STRONG) or bool(STAGE_DEDUCT_V3.search(text))
    has_deduct_ctx = any(k.upper() in text for k in DEDUCT_CTX)
    if _nonboss_settlement(raw): return True   # v2.4: 非老板+数字结算条款（非老板问题p5前33）
    if has_deduct and not _zero_price_joke(raw): return True  # v2.8: 0w 玩笑价 + 扣赔不触发
    if has_deduct_ctx and has_price: return True
    if has_split and (has_price or has_clear): return True
    if has_price and has_clear and not rant: return True   # v2.5: 吐槽语境下价格+过本不触发（不会吧不会吧 400W 时薪渡劫）
    if has_price and has_boss and not rant: return True   # v2.5: 同上，价格+老板亦然
    return False

def get_rice_reason(desc: str) -> List[str]:
    """返回匹配到的原因列表（v3.2 规则，1 级原因，用于日志展示，与 is_rice_party 的 stage 1 同口径）。"""
    if not desc:
        return []
    raw = _norm(desc)
    text = _flat(raw)
    # v2.3: 反向要钱/讽刺语境直接排除，不报原因
    if re.search(r'V\s*我\s*\d+', text): return []
    if re.search(r'(?<![\d扣搞])[赔賠]老板', text): return []
    # v2.5: 问是不是老板队直接排除，不报原因
    if re.search(r'是老板队[吗嘛]', text): return []
    # v2.6: 反向给钱直接排除，不报原因（打工倒给老板300w时）
    if '倒给' in text: return []
    # v2.8: 反向给钱直接排除，不报原因（+1打工仔给老板50w）
    if re.search(r'(?:给|給)(?:老板|老闆)\s*[1-9]', text): return []
    # v2.9: 反向给钱直接排除，不报原因（+1 锁有 过本每人倒贴我100w）
    if re.search(r'倒贴\s*(?:我|老板|老闆)', text): return []
    # v2.6: 只有金币、没有真实价格信号时直接排除，不报原因（5金币 躺黑…）
    if '金币' in text and not (_has_price_unit(text) or PRICE_PER_CD.search(_strip_time_ranges(text)) or PRICE_PER_MEAL.search(text) or PRICE_PER_CLEAR.search(text) or HOURLY_V3.search(raw) or _has_price_plus_run(text)): return []
    reasons = []
    strong_hits = [k for k in STRONG_V3 if k.upper() in text]
    # v2.9: 低保/底薪感慨守卫（与 is_rice_party 同口径）
    if strong_hits and all(k in ("低保", "底薪") for k in strong_hits) and not LOWBAO_CTX_RE.search(text):
        strong_hits = []
    if _zero_price_joke(raw):
        strong_hits = [k for k in strong_hits if k not in DEDUCT_STRONG]
    if strong_hits:
        if _is_price_inquiry(text):
            strong_hits = [k for k in strong_hits if k not in ("躺黑", "老板躺", "老闆躺")]
        if strong_hits and not (_has_rant(raw) and all(k in RANT_SENSITIVE for k in strong_hits)):
            reasons.append(f"强关键词={strong_hits}")
    m = HOURLY_V3.search(raw)  # v2.2: 时薪在未去空格原文上匹配；v2.5: 吐槽语境下不触发
    if m and not _has_rant(raw):
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
        reasons.append(f"价格+次数={PRICE_PLUS_RUN_V3.search(text).group()}")
    if _hire_with_price(raw):
        reasons.append("雇佣+价=陪练/佣兵/代打/代班+价格")
    if _short_hire_with_price(raw):
        reasons.append("陪简写+价=裸数字+陪")
    if _hire_negotiable(raw):
        reasons.append("雇佣+议价=价格可议")
    if _hire_hourly_mechanism(raw):
        reasons.append("雇佣+时薪机制=招陪练+可转时薪")
    if _dog_with_price(raw):
        reasons.append("狗+价")
    if _price_per_person_settle(raw):
        reasons.append("Nw一人+结账")
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
    clear_hits = [k for k in CLEAR_V3 if k.upper() in t2]
    if clear_hits:
        reasons.append(f"过本={clear_hits}")
    boss_hits = [k for k in BOSS_WORDS if k in text]
    if boss_hits:
        reasons.append(f"老板/打工={boss_hits}")
    sm = SPLIT_V3.search(raw)  # 拆分在未去空格原文上匹配
    if sm:
        reasons.append(f"人数拆分={sm.group()}")
    if (any(k.upper() in text for k in DEDUCT_STRONG) or STAGE_DEDUCT_V3.search(text)) and not _zero_price_joke(raw):
        reasons.append("扣赔机制")
    return reasons

# ========== 本地复核日志（v3.2） ==========
# 记录两类事件：(a) 1 级正则判定为老板队的条目；(b) 2 级打回的条目。
# 每条记录时间、id、文本、1 级原因、2 级判定。日志文件按天切分
# （安装目录下 logs/review-YYYY-MM-DD.log，JSON Lines），最多保留 7 天；
# 每次程序启动时删除 7 天前的日志文件。日志只写本地，不随 zip 发货、不上传 GitHub。
REVIEW_LOG_DIR = SCRIPT_DIR / "logs"
REVIEW_LOG_KEEP_DAYS = 7
# 进程内已记过 2 级打回日志的招募 id（避免每轮重复记；重启后会再记一次）
_logged_veto_ids = set()

def cleanup_old_review_logs():
    """启动时删除 7 天前的复核日志文件（按文件名日期）。"""
    try:
        if not REVIEW_LOG_DIR.is_dir():
            return
        today = datetime.now().date()
        for p in REVIEW_LOG_DIR.glob("review-*.log"):
            m = re.match(r"review-(\d{4})-(\d{2})-(\d{2})\.log$", p.name)
            if not m:
                continue
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            if (today - d).days > REVIEW_LOG_KEEP_DAYS:
                p.unlink(missing_ok=True)
    except Exception:
        pass

def log_review_event(lid, duty, desc, stage1_reasons, stage2_price_hit):
    """写一条复核日志（JSON Lines，按天切分）。
    stage1_reasons 为 1 级原因列表；stage2_price_hit 为命中的价格字符串
    （None 表示被 2 级打回）。"""
    try:
        REVIEW_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        rec = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "id": lid,
            "duty": duty,
            "description": desc,
            "stage1_reasons": stage1_reasons,
            "stage2": "pass" if stage2_price_hit else "veto",
            "stage2_price_hit": stage2_price_hit,
        }
        with open(REVIEW_LOG_DIR / f"review-{now.strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ========== 工具函数 ==========
def load_seen() -> Set[int]:
    """加载已通知过的招募 ID"""
    path = SCRIPT_DIR / SEEN_FILE
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"读取 seen 文件失败: {e}，重新开始")
            return set()
    return set()


def save_seen(seen: Set[int]):
    """保存已通知过的招募 ID（只保留最近 2000 个）"""
    path = SCRIPT_DIR / SEEN_FILE
    recent = sorted(seen)[-2000:]
    path.write_text(json.dumps(recent), encoding="utf-8")


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


def notify(title: str, message: str):
    """发送通知（多级降级保证一定能看到）"""
    logger.info(f"通知: {title}")
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


# ========== 主循环 ==========
def main():
    # 初始化
    seen = load_seen()
    logger.info("=" * 60)
    logger.info("FF14 绝欧米茄老板队监控 v2 启动")
    logger.info(f"  副本 ID: {DUTIES}")
    logger.info(f"  轮询间隔: {POLL_INTERVAL}s")
    logger.info(f"  正则版本: {REGEX_VERSION}")
    logger.info(f"  中文 DC: {CHINESE_DCS}")
    logger.info(f"  通知方式: MessageBox + 提示音 + plyer toast")
    logger.info(f"  详情 API: {'开启' if FETCH_DETAIL_SLOTS else '关闭'}")
    logger.info(f"  已记录 {len(seen)} 条历史招募 ID")
    logger.info("=" * 60)

    # 首次启动时，先把当前所有已有的招募加入 seen（不通知旧招募）
    if not seen:
        logger.info("首次启动：初始化已有招募列表（不通知旧招募）...")
        initial_listings = fetch_listings(DUTIES)
        for item in initial_listings:
            dc = item.get("datacenter", "")
            if dc in CHINESE_DCS:
                seen.add(item.get("id"))
        save_seen(seen)
        logger.info(f"已记录 {len(initial_listings)} 条初始招募，开始监控新招募...")

    # 轮询主循环
    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            listings = fetch_listings(DUTIES)
            consecutive_errors = 0

            new_found = []

            for item in listings:
                lid = item.get("id")
                if lid in seen:
                    continue

                dc = item.get("datacenter", "")
                desc = item.get("description", "") or ""

                # 只关心中文 DC + 智能米检测（v3.2 两级判定）
                if dc in CHINESE_DCS:
                    s1, s2, _price_hit = rice_stages(desc)
                    if s1 and s2:
                        new_found.append(item)
                        seen.add(lid)
                        # v3.0 本地复核日志：1 级命中（将通知）
                        log_review_event(lid, item.get("duty", ""), desc, get_rice_reason(desc), _price_hit)
                    elif s1 and lid not in _logged_veto_ids:
                        # v3.0 本地复核日志：2 级打回（进程内去重，只记一次）
                        log_review_event(lid, item.get("duty", ""), desc, get_rice_reason(desc), _price_hit)
                        _logged_veto_ids.add(lid)

            if new_found:
                for item in new_found:
                    # 获取详情（含 slots）
                    detail = None
                    if FETCH_DETAIL_SLOTS:
                        detail = fetch_detail(item.get("id"))

                    console_msg = format_listing_console(item, detail)
                    notify_msg = format_listing_detail(item, detail)

                    logger.info(console_msg)
                    logger.info(f"--- 通知内容 ---\n{notify_msg}\n---")

                    notify(f"发现老板队！({item.get('duty', '')})", notify_msg)

                save_seen(seen)
                logger.info(f"本轮发现 {len(new_found)} 条新老板队招募")

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
