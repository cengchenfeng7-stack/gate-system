# gate-client —— 自研代理客户端（M1 核心骨架）

按《客户端设计-v1-2026-11-18》实现的 M1：核心层 + 三类来源 + OpenVPN
引擎封装 + CLI。纯 Python 标准库，零第三方依赖，零遥测，所有数据只存本地。

## 四页结构（来源是第一组织维度，用户定 2026-11-19）

节点按**来源**分成四个独立页面，一个节点只归属它进来的那一页，
协议重叠也**不合并**（同一端点从两个来源进来就是两条独立记录，
ID 第一段就是来源名）：

| 页（Node.source） | 内容 | 入口 |
|---|---|---|
| `openvpn` | OpenVPN 节点页：OpenVPN 引擎的节点池（.ovpn 导入/拨号候选） | `intake --ovpn 文件` |
| `vpngate_feed` | VPN Gate feed 页：feed 原始收纳列表（底层虽是 OpenVPN，独立成页） | `intake --feed feed.csv` |
| `manual` | 手动导入页：用户手动贴入的分享链接（vless/ss/trojan/sstp） | `intake --link "链接"` |
| `pipeline` | 自家流水线产物页：data.json 评级节点（含 CF 类与志愿者节点，用 protocol/子标签区分） | `intake --pipeline-data data.json` / `--pipeline-nodes nodes.txt` |

四场景（客服 support / AI ai / 游戏 games / 下载 download）只是**页内**
徽标与过滤（`list --scene`），不做跨页面的统一默认视图。

归属规则备注：.ovpn 无论从哪个入口导入一律归 `openvpn` 页（它进的是
OpenVPN 引擎池）；分享链接归 `manual` 页。`Node.source` 枚举见
`core/model.SOURCES`，旧名 `feed` 已改齐为 `vpngate_feed`。

## 收纳循环（全设计的轴）

```
拿过来（sources）→ 扫描探测 → 验真出口（core/verify，本机测量）
→ 按四场景分类 → 收纳入本地 SQLite（core/store）→ 持续复验（core/history 养鱼池）
```

## 目录

```
client/
├─ core/
│ ├─ model.py 统一 Node 模型（dataclass，to_dict/from_dict）
│ ├─ store.py SQLite 存储（nodes + verify_samples，pin/ban）
│ ├─ verify.py 验出口：纯函数（分类/场景判定）+ 可注入 probe + 真实 probe
│ ├─ history.py 养鱼池：成功率/延迟中位数/波动率/活跃天数，只排序不替选
│ └─ orchestrator.py 会话状态机（铁律执行者）
├─ sources/
│ ├─ pipeline.py data.json（对齐 vpngate.build_outputs）+ nodes_*.txt
│ ├─ feed.py VPN Gate feed（CSV/镜像 JSON；解析函数复制自 vpngate/vpn_dial 并注明出处）
│ └─ manual.py 分享链接 +.ovpn 文本
├─ engines/
│ ├─ base.py Engine 协议接口
│ ├─ openvpn_engine.py 封装 vendored vpn_dial（supervise 后台线程，连上即返回）
│ ├─ _vpn_dial.py gate-system/frontend/vpn_dial.py 原样复制，仅加一行来源注释
│ └─ _vpngate_shim.py vendored 文件 import vpngate 的最小垫片（只补凭证常量等，见文件内说明）
├─ ui/
│ └─ cli.py argparse CLI（intake/list/pin/ban/verify/connect/disconnect/status）
└─ tests/ unittest 离线测试（假 probe/假引擎，绝不触网、绝不拨号）
```

## 铁律（orchestrator 写死）

1. 同一时刻只允许一个活动节点。
2. 连接必须显式给 node_id；**没有** connect_best/auto_switch 等任何自动选择/切换 API。
3. 连接后强制验出口，失败立刻断开并报 verify_failed（防假连）。
4. 掉线只上报 dropped，停在原地等用户选下一个，绝不自动重连/切换。

## 跑测试

```bash
cd client-dev
python -m unittest discover -s client/tests -t. # 74 个，全离线
```

## CLI 快速上手

```bash
cd client-dev
python -m client.ui.cli --db /tmp/c.db intake --pipeline-data data.json --feed feed.csv
python -m client.ui.cli --db /tmp/c.db list # 默认按四页分组
python -m client.ui.cli --db /tmp/c.db list --source pipeline --scene support
python -m client.ui.cli --db /tmp/c.db verify pipeline:sstp:vpn123456.opengw.net:443
python -m client.ui.cli --db /tmp/c.db pin <node_id>
```

默认 DB：`~/.local/share/gate-client/client.db`（可用 `--db` 或环境变量
`GATE_CLIENT_DB` 覆盖）。

## M1 范围与未做项

- **未做（M2）**：sing-box 引擎、订阅来源（sources/subscription.py）。
因此 vless/ss/trojan 节点目前只能收纳、验出口（经已连隧道/本地代理），
不能由本客户端直接拨号，connect 会明确报错而不是假装能连。
- **未做（M3）**：PySide6 图形界面与托盘。CLI 是单次进程形态：
`connect` 在本进程内完成「引擎启动 + 验出口」全流程后干净断开，
不留常驻会话、不留孤儿进程；常驻连接在 M3。
- **M1 出口门槛未在本包内完成**：住宅节点真机验证（排除 public-vpn、
逐个真连验出口）需要在用户本机网络执行，不属于离线代码交付。
- sstp 节点（pipeline 页主体）目前无引擎：VPN Gate 的 SSTP 拨号在
Windows 原生/旧流水线侧，M1 的 connect 对 sstp 会明确报“暂无引擎”。

## 与设计/原代码的已知差异

- **场景阈值以 vpngate.py 常量为准**：其 evaluate_stability 的 docstring
写的是旧阈值（在线≥12h/抖动≤150/延迟≤600），与文件顶部常量
（客服 ≥6h/≤800ms/≤3500ms 等，SSTP 现实放宽版）不一致。
core/verify.py 复制的是常量，并在文件头注明。
- **feed 解析函数是复制而非 import**：客户端要求独立可运行（不依赖
流水线目录、纯标准库），故 parse_csv/parse_mirror_json（源 vpngate.py）
与 parse_feed_text 等（源 vpn_dial.py）复制进 sources/feed.py 并逐段
注明出处；逻辑未改。改上游解析时需同步评估此处。
- **engines/_vpngate_shim.py**：vendored vpn_dial 在 import 时强依赖
vpngate 模块（缺失即 sys.exit）。引擎导入前先试真实 vpngate，
没有才装垫片（仅 SSTP 凭证常量、die、转调 feed 解析），不改变
vpn_dial 本身任何逻辑。
