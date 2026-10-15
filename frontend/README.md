# 🚀 VPN Gate SSTP 四场景稳连节点自动巡检系统（能跑版 v4）

每 30 分钟通过 GitHub Actions 自动抓取 VPN Gate 节点，经过 **Cloudflare Worker 真实探测（SSTP over TLS + PPP 拨号 + 出口 IP 风控查询）+ 二次防闪断复测 + 入口优选域名存活自检**，按「在线时长、负载人数、抖动、延迟、IP 干净度」综合评级，分成**四条场景通道**自动发布到 GitHub Pages。

---

## 一、四条场景通道（按需选用，别混用）

| 场景 | 文件 | 入选逻辑（全部基于实测，不臆造） | 典型用途 |
|---|---|---|---|
| ⭐ **客服稳连** | `nodes_support.txt` | 干净IP（无滥用/非Tor/非虚假）**且** 住宅 **且** 在线≥12h **且** 负载≤120 **且** 抖动≤150ms **且** 延迟≤600ms，仅收录欧美/亚洲客服常用国 | 找欧美/亚洲客服、工单、长连接办公 |
| 🎮 **游戏低延迟** | `nodes_game.txt` | 延迟≤400ms **且** 抖动≤100ms **且** 负载≤250 **且** 非脏IP，按 延迟+2×抖动 排序 | 外服游戏、实时语音 |
| ⬇️ **下载高速** | `nodes_speed.txt` | 账面带宽≥2Mbps **且** 实测可用 **且** 负载≤250，按带宽降序 | 大文件下载、更新游戏 |
| 🤖 **国外AI** | `nodes_ai.txt` | 干净IP **且** 在线≥6h **且** 抖动≤200ms **且** 延迟≤800ms，住宅优先 | ChatGPT / Claude / Gemini 等风控严格的海外AI |
| 🌍 **全量精选** | `nodes.txt` | 综合稳态分排序的全部入选节点 | 日常备用 |
| 📱 **通用订阅** | `subscription.txt` / `subscription_base64.txt` | 全部节点，remark 内含场景标签 | Hiddify / Clash / Sing-box / v2rayN |

> ⚠️ **脏IP 节点**（`is_abuser` 有滥用黑名单记录 / Tor / 虚假IP）永远不会进入 ⭐客服、🤖AI 和 🎮游戏 通道（游戏平台封禁滥用 IP 比客服更狠），面板中会标记 `⚠️脏IP`。

---

## 二、常用链接速查表（把 `<你的用户名>` 换成你的 GitHub 用户名）

| 用途 | 链接 |
|---|---|
| 📊 可视化监控面板 | `https://<你的用户名>.github.io/gate/` |
| ⭐ 客服稳连专线 | `https://<你的用户名>.github.io/gate/nodes_support.txt` |
| 🎮 游戏专线 | `https://<你的用户名>.github.io/gate/nodes_game.txt` |
| ⬇️ 下载专线 | `https://<你的用户名>.github.io/gate/nodes_speed.txt` |
| 🤖 国外AI专线 | `https://<你的用户名>.github.io/gate/nodes_ai.txt` |
| 🌍 全量 | `https://<你的用户名>.github.io/gate/nodes.txt` |
| 📱 通用订阅 | `https://<你的用户名>.github.io/gate/subscription.txt` |

EdgeTunnel / VLESS 客户端：把上述任一链接填入后台「自定义优选IP」框。
Hiddify / Clash / Sing-box：直接把 `subscription.txt` 当订阅链接导入。

---

## 三、防掉线操作铁律（最重要，先读这节）

平时遇到**玩着玩着掉线**、**和客服聊到一半重连**，按下面几条操作：

### 1. 对场景选对口通道
找客服/调 API → `nodes_support.txt`；打游戏 → `nodes_game.txt`；下载 → `nodes_speed.txt`；聊海外AI → `nodes_ai.txt`。面板上认准对应场景标签（⭐🎮⬇️🤖）。

### 2. 严禁开启“自动测速切换（URL-Test）”
**这是掉线重连的头号元凶。** 客服系统（工单/在线聊天）和海外 AI/API 会校验你的出口 IP。客户端若开启了自动选最快，每隔几分钟跳到另一个节点，**你的出口 IP 一变，对方立刻断开你的会话要求重连**。
**正确做法**：在客户端里**手动固定（Pin）一个 `稳连-01` 节点**，直到工作结束，中途不要让客户端自动跳。

### 3. 别用“刚开机”的临时节点
优先选面板里**持续在线 > 1 天**、负载 < 80 人的节点。刚开机的家庭电脑随时可能关机，聊到一半必断。

### 4. 入口域名坏了就换
`nodes.txt` 每行开头的 `入口:443#备注$sstp://...` 中，入口域名已做存活自检；若某地区集体连不上，换同文件里下一个入口的节点即可。

---

## 四、部署配置（GitHub Secrets 必配）

仓库 **Settings → Secrets and variables → Actions → New repository secret**：

| Secret | 必填 | 说明 |
|---|---|---|
| `WORKER_CHECK_URL` | ✅ **必填** | 你的检测 Worker 地址，如 `https://<你的Worker>.workers.dev/check?sstp=vpn:vpn@`。**若 Worker 启用了 TOKEN 鉴权，必须写成 `.../check?token=<TOKEN>&sstp=vpn:vpn@`** |
| `SSTP_USER` / `SSTP_PASS` | ⬜ 可选 | 默认 `vpn` / `vpn`，公开中继通用凭证 |

> 未配置 `WORKER_CHECK_URL` 时脚本**直接失败退出**（源码内不保留任何私有地址，防止仓库泄露 Worker 域名被白嫖）。

然后把本仓库推到 GitHub，**Actions 会自动启用**（每 30 分钟一轮）；首次可手动 **Actions → VPN Gate Node Check → Run workflow** 触发。
GitHub Pages 需在仓库 **Settings → Pages → Source: GitHub Actions** 开启。

---

## 五、阈值调优（环境变量，均可选）

| 变量 | 默认 | 作用 |
|---|---|---|
| `MAX_PER_COUNTRY` | 6 | 每国最多保留几个节点 |
| `CHECK_CONCURRENCY` | 16 | 检测并发（>16 容易打爆 Worker 免费版） |
| `CHECK_TIMEOUT` | 45 | 单节点检测超时（秒） |
| `MAX_CHECK_NODES` | 0 | 每轮最多检测多少节点（0=不限；跑太久可设 300~500） |
| `DOUBLE_CHECK_TOP` | 1 | 是否二次复测前排候选 |
| `SUPPORT_MIN_UPTIME_H` | 12 | ⭐客服：最少在线小时 |
| `SUPPORT_MAX_SESSIONS` | 120 | ⭐客服：最大负载人数 |
| `SUPPORT_MAX_JITTER` / `SUPPORT_MAX_LATENCY` | 150 / 600 | ⭐客服：抖动/延迟上限 |
| `GAME_MAX_LATENCY` / `GAME_MAX_JITTER` | 400 / 100 | 🎮游戏：延迟/抖动上限 |
| `GAME_MAX_SESSIONS` | 250 | 🎮游戏：最大负载 |
| `SPEED_MIN_MBPS` | 2 | ⬇️下载：最低账面带宽 |
| `AI_MIN_UPTIME_H` / `AI_MAX_JITTER` / `AI_MAX_LATENCY` | 6 / 200 / 800 | 🤖AI：在线/抖动/延迟门槛 |
| `EDGE_HOSTS` / `HOSTS_ENTRY` | 内置 9 个 | 覆盖默认优选入口域名列表（逗号分隔） |
| `SSTP_USER` / `SSTP_PASS` | vpn / vpn | 生成链接使用的凭证 |
| `BAN_FILE` / `PIN_FILE` | `ban.txt` / `pin.txt` | 手动黑名单 / 置顶名单文件路径 |
| `EXTRA_ENTRY_TOP` / `EXTRA_ENTRY_COUNT` | 3 / 2 | 每场景前 N 名节点附加几个备用入口行 |

## 六、手动干预：黑名单与置顶（ban.txt / pin.txt）

没有动态后台（零攻击面），但保留了人工介入的两个仓库级文件，改完推送即生效：

**`ban.txt` — 永久掐死节点**（每行一个，`#` 注释）：
```
vpn12345.opengw.net          # 掐掉该主机所有端口
vpn12345.opengw.net:1194     # 只掐这一个 host:port
```
命中者：不送检（省 Worker 配额）、不出现在任何产物。

**`pin.txt` — 手动置顶保送**（格式同上）：
- 排到每个场景文件最顶部（📌 标记）+ 自动附加 2 个备用入口行
- 免场景门槛：只要本轮检测成功，必进全部四条场景通道
- 安全底线：置顶**不能**把脏IP（有滥用黑名单记录）塞进 ⭐客服/🤖AI 通道
- 置顶的节点若本轮检测失败会被跳过并在 Actions 日志告警（死节点保送只会害你）

---

## 七、安全说明（私人部署向）

- **源码零私有信息**：不配置 Worker 地址直接退出；日志与 `data.json` 中 Worker 域名均为脱敏显示。
- **公开产物只含公共信息**：`nodes*.txt` / `subscription*.txt` 仅含 VPN Gate 公共中继地址与凭证，无 token、无你的域名。
- **不打爆 Worker**：检测会话**零重试**（429/5xx 直接记异常），二次复测只测每国前 8 名，总请求量有硬上限。
- **失败不断线**：本轮无任何成功节点时**不发布、不清线**，线上继续保留上一次的好数据。
- **依赖最小**：仅 `requests` + `urllib3`；无遥测、无第三方统计、无外部前端 CDN。
- **脏IP 拦截**：`is_abuser` / `is_tor` / `is_bogon` 来自检测 Worker 的出口风控查询（需搭配返回 `is_clean` 字段的加固版 checker）；脏 IP 自动排除在客服/AI 通道外。

---

## 八、本地运行（调试用）

```bash
pip install -r requirements.txt
CHECK_WORKER="https://你的worker域名/check?sstp=vpn:vpn@" python vpngate.py
# 产物生成在 web/ 目录，可直接双击 web/index.html 查看
```

## 九、系统级 VPN：OpenVPN 一键拨号器（vpn_dial.py）

不走 Cloudflare Worker、直连 VPN Gate 原生 OpenVPN 协议，适合**打游戏、整机下载**等要系统级 VPN 的场景。与流水线共用同一份 `data.json` 场景评级（置顶/黑名单/四场景自动生效）。

前置：安装 [OpenVPN Community](https://openvpn.net/community-downloads/)；Windows 需**以管理员身份运行**终端（TAP 网卡权限）。

```bash
# 先干跑一次（只选节点+生成 .ovpn，不拨号，自测用）
python vpn_dial.py --dry-run --scene support

# 正式拨号：⭐客服稳连场景，掉线自动换下一个候选
python vpn_dial.py --scene support

# 其他场景
python vpn_dial.py --scene game     # 🎮游戏低延迟
python vpn_dial.py --scene speed    # ⬇️下载高速
python vpn_dial.py --scene ai       # 🤖国外AI
python vpn_dial.py --scene all      # 全量按综合分
```

行为说明：按场景挑选候选（置顶优先、黑名单剔除）→ 生成 `.ovpn`+凭证文件（在 `ovpn_out/`）→ 启动 openvpn → **掉线/失败自动切下一个候选**（默认最多试 8 个，`--limit` 可调）。没有 `data.json` 时按 feed 账面分兜底。凭证用 `SSTP_USER/SSTP_PASS`（默认 vpn/vpn）。
