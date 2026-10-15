# 🚀 VPN Gate 四场景稳连节点系统

每 30 分钟通过 GitHub Actions 自动抓取 VPN Gate 节点，经 Cloudflare Worker 真实探测（SSTP over TLS + PPP 拨号 + 出口 IP 风控）+ 二次防闪断复测，按「在线时长、负载、抖动、延迟、IP 干净度」综合评级，分四条场景通道发布到 GitHub Pages。

## 目录结构

```
gate-system/
├── frontend/                    # 能跑版源码（GitHub Actions 每 30 分钟跑）
│   ├── vpngate.py               # 主程序（检测流水线）
│   ├── vpn_dial.py              # B 端：OpenVPN 系统级拨号器
│   ├── gen_client_config.py     # A 端：mihomo 四场景配置生成器
│   ├── web/index.html           # 可视化面板
│   ├── ban.txt / pin.txt        # 黑名单/置顶模板
│   ├── requirements.txt
│   └── README.md                # 前端说明
├── workers/
│   ├── checker/                 # 检测后端（SSTP+PPP+出口情报）
│   │   ├── CheckSocks5_hardened.js
│   │   └── wrangler.toml
│   └── vless/                   # VLESS 代理后端
│       ├── worker.js
│       └── wrangler.toml
├── docs/                        # 文档（新窗口/新助手看这里）
│   ├── 00-新窗口提示词.md       # 助手接手入口
│   ├── 01-架构与数据流.md       # 架构图
│   ├── 02-部署步骤.md           # 从零部署
│   ├── 03-配置说明.md           # 环境变量/Secret/阈值
│   └── 04-血泪坑清单.md         # 踩过的坑
├── .gitignore
└── README.md                    # 本文件
```

## 快速开始

### 前端（GitHub Actions）

1. 把 `frontend/` 推到仓库
2. GitHub Secrets 配置 `WORKER_CHECK_URL`（见 `docs/03-配置说明.md`）
3. Actions 自动启用（每 30 分钟一轮）

### 后端（Cloudflare Workers）

1. 创建两个 worker：`checker`（用 `workers/checker/`）和 `vless`（用 `workers/vless/`）
2. 绑定 KV 命名空间、机密（见 `docs/02-部署步骤.md`）
3. 验证 `/check` 和 `/sub` 接口

### 客户端

- **A 端（mihomo/Clash）**：`python gen_client_config.py` → 导入生成的 `client.yaml`
- **B 端（OpenVPN）**：`python vpn_dial.py --scene support` → 系统级 VPN

## 文档索引

| 文档 | 内容 |
|---|---|
| [00-新窗口提示词](docs/00-新窗口提示词.md) | 助手接手入口，复制粘贴即可 |
| [01-架构与数据流](docs/01-架构与数据流.md) | 系统架构、数据流、契约 |
| [02-部署步骤](docs/02-部署步骤.md) | 从零部署到 CF Workers |
| [03-配置说明](docs/03-配置说明.md) | 环境变量、Secret、阈值 |
| [04-血泪坑清单](docs/04-血泪坑清单.md) | 踩过的坑，别再踩 |

## 安全说明

- 源码零私有信息：不配置 Worker 地址直接退出；日志与 data.json 中 Worker 域名均脱敏
- 公开产物只含公共信息：nodes*.txt / subscription*.txt 仅含 VPN Gate 公共中继地址
- 不打爆 Worker：检测会话零重试、并发≤16、复测只测前排
- 失败不断线：本轮无成功节点时不发布、不清线
