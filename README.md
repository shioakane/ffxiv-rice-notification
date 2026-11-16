# FF14 绝欧米茄老板队监控 (Rice Monitor v2)

监控 xivpf.littlenightmare.top 上的绝欧米茄招募，用正则规则智能检测"米"关键词（老板队），发现新招募时通过 **MessageBox 弹窗 + 系统提示音** 通知。

## 文件说明

| 文件 | 用途 |
|------|------|
| `config.py` | 配置文件（API地址、轮询间隔、通知开关等） |
| `rice_monitor.py` | 主监控脚本（持续运行，含智能米检测+详情API+多级通知） |
| `test_api.py` | 单次测试脚本（查一次API，打印所有招募+slots详情） |
| `test_notification.py` | 通知方式测试（测 MessageBox / plyer / 提示音） |
| `test_notify_simulated.py` | 模拟通知测试（用假数据发一条完整通知，验证弹窗效果） |
| `start_monitor.bat` | 双击启动监控 |
| `test_once.bat` | 双击单次查询 |
| `requirements.txt` | Python 依赖 |
| `seen_listings.json` | 已通知过的招募 ID（自动生成） |
| `monitor.log` | 运行日志（自动生成） |

## 安装

```bash
cd D:\GAME\rice
pip install -r requirements.txt
```

## 使用

### 1. 测试通知是否正常
```bash
python test_notify_simulated.py
```
你会看到 MessageBox 弹窗 + 听到提示音。如果没弹出，说明有问题。

### 2. 单次查询当前招募
```bash
python test_api.py
```

### 3. 启动持续监控
```bash
python rice_monitor.py
```
或双击 `start_monitor.bat`

## 手机通知（Bark 推送）

架构：**电脑当 server 轮询，手机只做接收**，不依赖云端。

```
电脑 (rice_monitor.py, 45 秒轮询 xivpf)
   │ 发现新老板队
   ▼
Bark 服务器 (api.day.app)
   │ Apple 推送
   ▼
iPhone (Bark App，锁屏就能看到)
```

### 1. 装 Bark，拿 key

1. iPhone 打开 App Store，搜 **Bark** 安装；
2. 打开 Bark，首页会显示一串 key（形如 22 位字符），点复制；
3. 把 key 填到 `config.py` 的 `BARK_KEY`（三选一）：
   - 直接填 `BARK_KEY = "你的 key"`；
   - 或新建 `bark.key` 文件，只写一行 key；
   - 或设环境变量 `BARK_KEY`。

> key 只用来给你自己的手机发推送，不要发给别人。别人拿到 key 就能往你手机上推消息。

### 2. 测推送通不通

```bash
python test_bark.py
```
或双击 `test_bark.bat`。手机上收到测试通知就说明链路通了。

### 3. 启动监控

```bash
python rice_monitor.py
```
或双击 `start_monitor.bat`。发现老板队时按开关通知：PC 通知是本机弹窗 + 提示音，手机通知是一条 Bark 推送。

### 通知开关（`config.py` 里的 `NOTIFY_PC` / `NOTIFY_BARK`，多选）

- `NOTIFY_PC = True` - PC 通知：本机 MessageBox 弹窗 + 提示音 + plyer toast
- `NOTIFY_BARK = True` - 手机通知：通过 Bark 推到 iPhone
- 两个都开 = 电脑弹窗 + 手机推送同时来；只想用手机就把 `NOTIFY_PC` 设 `False`，反之亦然

### 重复提醒（`config.py` 里的 `REPEAT_NOTIFY` / `REPEAT_NOTIFY_MINUTES`）

- `REPEAT_NOTIFY = True` - 同一条老板队招募提醒过之后，如果过一段时间还挂着，就再提醒一次；设 `False` 则只提醒一次（旧行为）
- `REPEAT_NOTIFY_MINUTES = 10` - 间隔（分钟），默认 10 分钟；改成 30 就是半小时提醒一次，设 0 就是每轮（45 秒）都提醒
- 每次提醒的时间记在 `seen_listings.json` 里（新格式是 `{招募ID: 上次提醒时间}`）；旧版的纯 ID 列表文件升级时会自动转换，不会一启动就刷屏

### 手机通知格式

标题是副本名字，正文三行：

```
欧米茄绝境验证战
50w/h 老板躺 过本结 7p1
陆行鸟-张三@拉诺西亚 7/8
接收 11-14 16:42｜剩余约 25 分钟
```

第 1 行描述、第 2 行"大区-队长@服务器 人数"、第 3 行接收时间和剩余时间。PC 端弹窗仍是原来的完整版（含阵容详情）。

推送失败会在 `monitor.log` 里记一条 `Bark 推送失败`，并自动重试 3 次；重试都失败也不会影响本机弹窗和监控继续跑。

### Bark 相关配置（`config.py`）

- `BARK_KEY` - 你的 Bark key
- `BARK_GROUP` - Bark 里的分组名，默认 `rice`
- `BARK_SOUND` - 推送提示音，默认 `alarm`，留空用默认音
- `BARK_RETRY` - 推送失败重试次数，默认 3

## 通知方式

采用**三级通知**保证一定能看到：

1. **MessageBox 弹窗**（核心）- 用 `ctypes.windll.user32.MessageBoxW`，系统级弹窗，**一定能显示**，带感叹号图标，置顶显示
2. **系统提示音** - 连续三声 `MessageBeep`
3. **plyer toast**（辅助）- 尝试发 Windows 通知中心 toast（如果通知中心开着可以看到）

> **为什么不用 plyer/win10toast？** Windows 通知中心的 toast 通知依赖系统的通知设置，如果用户关了通知权限或专注助手开着，toast 就不会弹出。MessageBox 是系统级 API 调用，不受这些限制。

## 米检测逻辑

采用**正则规则匹配**，不依赖大模型：

| 检测层 | 模式 | 说明 |
|--------|------|------|
| 强关键词 | `时薪` `全黑` `老板躺` `过立结` `搞转` 等 | 出现任一个 → 直接判定为老板队 |
| 价格模式 | `\d{1,3}[wW万萬]` | 匹配 `50w` `20W` `100万` 等 |
| 人数拆分 | `[1-7][pP][1-7]` | 匹配 `7p1` `6P2` 等 |
| 过本关键词 | `过本` `渡劫` `通关` `清CD` | |
| 老板关键词 | `老板` `老闆` `打工` | |

**组合规则**：
- 强关键词 → 直接是
- 人数拆分 + (价格 or 过本) → 是
- 价格 + 过本 → 是
- 价格 + 老板/打工 → 是

**关键改进**：旧版简单匹配 `"00"` 会把 `2100-2400` 误判为老板队，新版正则不会。

## 详情 API

发现老板队后，自动调用 `GET /api/listing/{id}` 获取 slots 信息，通知中会展示：
- 已有职业（如 `DRK(T)`, `WHM(奶)`, `DNC(DPS)`）
- 空位及可接受职业（如 `空位(DPS/BLM SMN RDM PCT…)`）

## 配置

编辑 `config.py`：
- `DUTIES` - 监控副本 ID（908=绝欧, 1006=伊甸, 788=龙诗）
- `POLL_INTERVAL` - 轮询间隔秒数（建议 30~60）
- `FETCH_DETAIL_SLOTS` - 是否获取详情 API 的 slots 信息
- `PLAY_SOUND` - 是否播放提示音
- `USER_AGENT` - 联系方式（xivpf 要求标注）

## 停止

`Ctrl+C` 停止，已通知列表自动保存。
