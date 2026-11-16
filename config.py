# ========== 绝欧米茄老板队监控配置 v2 ==========

# API 地址
API_URL = "https://xivpf.littlenightmare.top/api/listings"
DETAIL_API_URL = "https://xivpf.littlenightmare.top/api/listing/{id}"
PER_PAGE = 100

# 监控的副本 ID（参考 Java 代码注释：908=绝欧, 1006=伊甸, 788=龙诗, 1094=妖星）
DUTIES = [908]

# 轮询间隔（秒），建议 30~60
POLL_INTERVAL = 45

# User-Agent（xivpf 要求带联系方式）
USER_AGENT = "CC(contact: your-email@example.com)"  # xivpf 要求带联系方式，改成你自己的

# 中文数据中心名称（简体+繁体，xivpf API 实际返回可能用繁体）
CHINESE_DCS = {"陆行鸟", "猫小胖", "莫古力", "豆豆柴"}

# 是否调用详情 API 获取 slots 职业信息（True=获取并显示阵容）
FETCH_DETAIL_SLOTS = True

# 已通知过的招募 ID 持久化文件
SEEN_FILE = "seen_listings.json"

# 日志文件
LOG_FILE = "monitor.log"

# 通知声音（True = 播放系统提示音，连续三声）
PLAY_SOUND = True

# ========== Bark 推送（手机通知） ==========
# 电脑当 server 轮询，发现老板队后通过 Bark 把消息推到 iPhone 上，手机只做接收。
# key 的获取：iPhone 安装 Bark App，打开就能看到一串 key。
# key 的填写（三选一，优先级：环境变量 BARK_KEY > bark.key 文件 > 下面这行）：
#   1. 填下面 BARK_KEY；2. 新建 bark.key 文件只写一行 key；3. 设环境变量 BARK_KEY
BARK_KEY = ""  # 填你自己的 Bark key；或新建 bark.key 文件只写一行 key；或设环境变量 BARK_KEY
BARK_GROUP = "rice"        # Bark 里的分组名（手机上可按组查看/静音）
BARK_SOUND = "birdsong"    # 推送提示音；留空 "" 则用 Bark 默认音
BARK_RETRY = 3             # 推送失败自动重试次数

# ========== 通知开关（多选，True = 开） ==========
NOTIFY_BARK = True         # 手机通知：通过 Bark 推到 iPhone
NOTIFY_PC = False          # PC 通知：本机 MessageBox 弹窗 + 提示音 + plyer toast（默认关，只用手机）
# 两个都开 = 电脑弹窗 + 手机推送同时来；只想用手机就把 NOTIFY_PC 设 False

# ========== 重复提醒（同一条老板队招募过一段时间还挂着，再提醒一次） ==========
REPEAT_NOTIFY = True          # 开关：True = 同一条招募隔一段时间再提醒一次，False = 只提醒一次（旧行为）
REPEAT_NOTIFY_MINUTES = 12    # 间隔（分钟）：上次提醒后过这么久、招募还挂着，就再提醒一次；设 0 = 每轮都提醒
