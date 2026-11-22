# task17 设计：U2 分流与 DNS（阶段五·使用界面线 实现第二段）

依据：work10/DESIGN.md §4.1–§4.5（规则模型与引擎合并顺序契约）、§2.2（③区
定稿）、§2.3（前后台边界）、§11（设置模型关系）；task12/DESIGN.md §4（持久化
口径）、§6（与 task10 接口）。开工先读 client/VERSION=0.20.0 与
CHANGELOG.md 顶部，核对「未接线事项」后落笔；本段为**实现段**，交付可
被审查方直接集成的代码。红线：不改 client-dev 任何文件，本段所有改动
以清单形式交付（§5–§6），orchestrator 一行不改，永不自动切换节点。

---

## 0. 开工核对结论（如实，含所缺一环）

- client-dev 0.20.0：`usage_model.py`（UsageSettings + build_route_config，
  0.14.0 集成）与 `settings_proto.py`（Settings + usage_dict()，0.16.0
  集成）都在；引擎 `build_config` 未消费 usage 片段；③区不存在
  （usage_window.py ①②区 + 菜单骨架，0.20.0）；网站黑名单无文件形态。
- **所缺一环（简报预判命中）**：Settings 落盘接线缺失——task12/DESIGN §4
  标注「原型不实现 IO，集成段落地」，全库没有 settings.json 读写代码
  （仅 test_packaging.py 夹具提及文件名）。**本段接法**：按 task12 §4.1–§4.3
  定稿口径在 core 层补上存储层（work17 `settings_store.py`，落点 §3），
  形态（文件/目录/包裹层/原子写/损坏处置/version）逐条照 §4，未引入
  新口径；这不是另造平行存储——模型只有 settings_proto.Settings 一套，
  存储只有 settings.json 一处。若 task12 设置页集成段后续按 §4 另有
  实现，以彼为准对齐，差异报 DONE.md 等裁决。
- QSettings 迁移（auto_probe 两键首启导入，task12 §4.3）依赖 Qt，归设置页
  集成段，本段不做（core 层保持 Qt-free）；本段五项设置的编辑入口即③区，
  设置页集成后两处写同一存储（§7）。

---

## 1. 合并接线图（UsageSettings 从设置存储到 build_config 的流向）

```
~/.local/share/gate-client/settings.json          site_blocklist.json
  {"version":1,"settings":{…10 字段…}}              {"version":1,"entries":[…]}
        │                                                  │
        │ core/settings_store.load_settings()               │ core/site_blocklist.load_blocklist()
        ▼                                                  ▼
  settings_proto.Settings（唯一模型，work10 §11）          规范化条目元组
        │ .usage_dict()（五项子集）                        │
        ▼                                                  │
  usage_model.UsageSettings(**usage_dict)                  │
        │                                                  │
        └──────────────┬───────────────────────────────────┘
                       ▼
       usage_model.build_route_config(usage, blocklist)
                       │  路由/DNS 片段（insert_before=resolve、
                       │  dns_patch、outbound_patch、final_override、
                       │  rule_set 占位条目、notes/uncovered）
                       ▼
       core/usage_merge.merge_usage_config(config, fragment)   ← 新模块（本段）
                       │  §4.5 五条顺序契约（§2）
                       ▼
  engines/singbox_engine.build_config 尾部调用（接线清单 §5）→ 完整配置 → start()

连接时点（生效口径）：以上链条在「点连接 → engine.start() → build_config」
时现读现算——改动下次连接生效，不做热重载（work10 §2.2）。读设置与读
名单集中在引擎构造点注入的供给器闭包（§5 改动点 2/4），构造时不读、
连接时才读，天然满足「下次连接生效」。
```

---

## 2. 引擎 merge 的落点与调用接法

**落点定稿：`client/core/usage_merge.py`（core 层，非 engines 层）。**
依据：片段生产者 usage_model 在 core；同族「纯函数合并进完整 config」的
先例 `tun_config.build_tun_config` 也在 core，且引擎正是从 core 导入并在
build_config 尾部调用它（singbox_engine.py:250-257）。本段照同一形状接线，
保持「core 出纯函数、engines 出进程管理」的既有分层。

**调用位置：`build_config` 尾部、TUN 合并块之后、`return config` 之前。**
这一位置同时满足契约 1（TUN 的 sniff/hijack-dns 已被 tun_config 前置到
rules[0]/[1]，usage 规则插在 resolve 前、天然在它们之后——tun_config.py
277-278 行现状一行不动）与「并入 build_config 尾部」的简报口径。
五条契约逐条落点（usage_merge.py 函数体编号注释同源）：

| §4.5 | 契约 | merge 实现 |
|------|------|-----------|
| 1 | TUN sniff/hijack-dns 保持最前置 | merge 不插队到 tun 前置规则之前（锚点是 resolve， tun 前置在 resolve 前）；tun_config 零改动 |
| 2 | reject/route 规则插在 resolve 之前 | 锚定首个 `action=="resolve"` 规则前插入；找不到显式报错（不猜尾插） |
| 3 | dns_patch 只动两字段 | remote-dns 条目只写 `server`；resolve 规则只写/删 `strategy`（None=移除）；detour/final/server_port/type/tag 一概不碰 |
| 4 | mode≠global 才追加 direct-out | global 直接跳过 outbound_patch；且复核片段：global 带补丁=契约破坏即报错 |
| 5 | route.final 仅 direct 改写 | final_override 仅 direct 模式非 None；其余模式片段带 override=契约破坏即报错 |

**默认值零操作（回归红线）**：默认 UsageSettings + 空名单的片段=无规则、
无 rule_set、dns_patch 写回值（8.8.8.8 / ipv4_only）与引擎现行写死值
逐字相同、无补丁——merge 对空规则不插入、空 rule_set 不建键，结果与
现行 build_config 输出**深等价**。test_usage_wiring.py 按 vless/ss/
vmess/trojan/hysteria2/tuic/wireguard 七协议 + proxy/tun 两接管方式逐个
实证（`merged == config` 字典深比较）。

---

## 3. 新模块落点清单（全部按 client/ 包内落点写，包内相对 import）

| 文件 | 集成落位 | 职责 |
|------|---------|------|
| work17/usage_merge.py | client/core/usage_merge.py | §4.5 合并（纯 dict 变换，不读文件不碰网） |
| work17/site_blocklist.py | client/core/site_blocklist.py | 名单文件层 load/save/校验（§4） |
| work17/settings_store.py | client/core/settings_store.py | Settings 落盘（task12 §4 口径，§0 所缺一环） |
| work17/usage_rules_page.py | client/ui/usage_rules_page.py | ③区面板（§6），挂 UsageWindow |
| work17/test_usage_wiring.py | client/tests/test_usage_wiring.py | 离线测试（集成时做路径适配，文末注明） |

---

## 4. 名单文件形态定稿（site_blocklist.json）

- **字段/结构**：`{"version": 1, "entries": [...]}`；entries 条目=裸域名
  str（默认处置 reject）或 `{"domain":…, "action":"reject"|"direct"}` dict
  （usage_model §4.2 既定两形态）；save 统一规范化为 dict 键序
  `{"domain","action"}`，save(load(p)) 字节级一致（测试实证）。
- **落点**：数据目录、与 settings.json 同目录（`blocklist_path()` 经
  settings_store.settings_path().parent 推导——路径从设置存储取，不写死；
  --db/GATE_CLIENT_DB 覆盖目录跟随口径与 task12 §4.1 相同）。
- **读写口径**：每次全量读、全量原子写（tmp + os.replace）；条目校验/
  同域名双处置冲突检测**一律沿用 usage_model.parse_site_blocklist**
  （0.14.0 口径：reject 全模式生效、direct 仅 rule 模式生成、双处置在
  parse 层报错），本模块不另造第二套校验。
- **损坏/缺失处置（简报定稿：默认空名单 + 如实报错，不静默吞）**：
  | 情形 | 处置 |
  |------|------|
  | 文件不存在（首次运行） | 空名单 `()`（零提示零动作，平滑起步） |
  | JSON 损坏 / 顶层非 dict / 包裹层未知键 / entries 非列表 | `SiteBlocklistError`（ValueError 族）如实报错，**不当空名单吞** |
  | version 高于/低于 1 | 拒读报错（防静默降级/猜结构） |
  | 条目非法（scheme/斜杠/通配符/IDN/单标签…） | `usage_model.UsageSettingsError` 原样上抛 |
  | 同域名双处置并存 | parse 层报错（0.14.0 既定） |
- **消费接线**：连接时供给器读名单 → `build_route_config(usage, blocklist)`
  → 片段进 merge。名单编辑 UI 归后台（work10 §2.3 边界，本段不做）；③区
  与后台共用本文件层读写口径。

---

## 5. 引擎集成改动点清单（singbox_engine.py，审查方照单接线）

> 本段不动 client-dev；以下 4 处改动 + 2 处构造点接线全部给出原文位置与
> 目标代码。改动 1–4 之外**其余一行不动**（进程管理/TUN/出站翻译全保持）。

**改动 1｜模块头 import 区**（`from ..core import tun_config as _tun` 之后）：

```python
from ..core import usage_merge as _usage_merge
from ..core import usage_model as _usage_model
```

**改动 2｜`SingBoxEngine.__init__`**：签名尾部加关键字参数
`usage_provider=None`；`atexit.register` 行之前加一行：

```python
        # U2（task17）：使用设置供给器——连接时经它现读设置存储与网站
        # 黑名单（「下次连接生效」口径的机械保证）。None = 不合并
        # （现行行为，零变化，默认值红线）。
        self.usage_provider = usage_provider
```

**改动 3｜`build_config` 尾部**：现 254-258 行 TUN 块之后、`return config`
之前插入（TUN 块保持原样——契约 1 的「现状不动」由调用顺序保证）：

```python
        fragment = self._usage_fragment()
        if fragment is not None:
            try:
                config = _usage_merge.merge_usage_config(config, fragment)
            except ValueError as exc:
                raise EngineError(f"使用设置合并失败: {exc}") from exc
        return config
```

**改动 4｜新增方法**（`build_config` 之后，与 `_need_endpoint` 等静态
辅助同区）：

```python
    def _usage_fragment(self):
        """U2：经供给器现读使用设置与网站黑名单，产出路由片段。
        供给器缺失/返回 usage=None → 不合并（现行行为）。供给器异常
        显式报错不静默降级——设置非法时连接必须失败，而不是按用户没
        设过的参数连（与 tun 合并失败同款 EngineError 口径）。"""
        if self.usage_provider is None:
            return None
        try:
            usage, blocklist = self.usage_provider()
        except ValueError as exc:
            raise EngineError(f"使用设置/黑名单读取失败: {exc}") from exc
        if usage is None:
            return None
        return _usage_model.build_route_config(usage, blocklist)
```

**构造点接线 A｜cli.py `_build_orchestrator`（271 行 `singbox = SingBoxEngine()` 处）**：

```python
        def _usage_provider():
            # 每次连接现读（下次连接生效）；设置/名单损坏如实抛 ValueError，
            # 经引擎包成 EngineError 显式失败，不静默按默认值连。
            usage = UsageSettings(**settings_store.load_settings().usage_dict())
            blocklist = site_blocklist.load_blocklist()
            return usage, blocklist

        singbox = SingBoxEngine(usage_provider=_usage_provider)
```

import 区相应加：`from ..core import settings_store, site_blocklist` 与
`from ..core.usage_model import UsageSettings`。`--db`/GATE_CLIENT_DB 覆盖
目录时把解析出的目录传给 `load_settings(覆盖目录)`/`load_blocklist(覆盖目录)`
（优先级链 task12 §8-3，覆盖目录 = `os.path.dirname(os.path.abspath(db路径))`）。

**构造点接线 B｜ui/app.py engine_factory（363 行同款）**：闭包内同款
`_usage_provider`（store 已在场，覆盖目录同上推导）。

**回归红线核对**：接线后默认设置 + 空名单 → 片段=默认片段 → merge 零操作
→ `build_config` 输出与 0.20.0 现行**深等价**（test_usage_wiring.py 用真
引擎逐协议实证，集成后原样复跑）。settings.json 不存在时
`load_settings()` 返回全默认——即现行行为，零迁移负担。

**不做的事**：engine 不 import settings_store/site_blocklist（供给器注入，
core 依赖不倒灌引擎）；orchestrator.py 一行不改（orchestrator 只调
`engine.start`，start 内部调 build_config，供给器对它透明）；永不生成
urltest/selector；global/rule 模式 outbounds[0] 恒为 node-out。

---

## 6. ③区控件与生效口径（usage_window.py 挂载清单）

**挂载改动（usage_window.py，2 处）**：
1. 模块头 try 块内（`from .app import …` 之后）加
   `from .usage_rules_page import UsageRulesPanel`；
2. `__init__` 中 `root.addWidget(self.tabs)`（359 行）之后、`# 托盘归主窗`
   注释之前插入：

```python
            # ---------------- ③ 规则与网络区（U2/task17） ----------------
            # 唯一编辑值 = task12 Settings 存储（usage_dict() 一侧）；生效
            # 口径=下次连接生效（连接时供给器现读），面板常驻明示。
            self.rules_panel = UsageRulesPanel()
            root.addWidget(self.rules_panel)
```

**控件面**（work10 §2.2 ③区定稿五项，一个不多）：分流模式
QComboBox（SPLIT_MODE_LABELS 三项文案即 usage_model 口径）＋广告拦截/
恶意拦截/IPv6 三个 QCheckBox（默认全关=现行行为）＋ DNS QComboBox
（预设 google/cloudflare/quad9，标签带 IP）+「自定义 IP…」档（QLineEdit，
IP 字面量、域名拒收）。**每个控件只写自己那一个字段**：现读存储 →
`settings_proto.merge(base, {单字段})` → 原子写回——merge 的显式字段
语义保证③区不碰设置页管的其余九项（端口/数据目录/自动探测），设置页
集成后与③区写同一存储互不为副本（§7）。校验失败（如 IPv6 关时给 v6
DNS）→ 状态行红字给模型层原文 + 控件回退存储值，不静默纠正不静默保存。

**生效口径**：面板常驻文案「以上改动下次连接生效（连接时现读设置生成
配置；不做热重载）」；连接中修改同样只是保存，绝无半生效状态。状态
反馈走面板状态行（红字/回执），不弹模态框（0.7.0 偏好）。菜单「设置」
仍如实提示设置页未接线（task12 集成段交付）；名单编辑入口归后台，
③区无编辑 UI（work10 §2.3）。

**存储异常形态**：settings.json 损坏 → 面板红字（含文件路径）+ 控件
禁用 + 不做任何保存（不用默认值盖掉、不覆盖原始证据，task12 §4.2）；
重置入口（reset_settings 原语已在 settings_store）归设置页集成段接线。

---

## 7. 与 task12 设置存储的接线关系（work10 §11 唯一模型）

- **模型**：UsageSettings 五项字段定义只有 usage_model.py 一处（口径单一
  事实源）；Settings 镜像由 test_settings.py 口径一致组锁定，本段零改动
  两模型——③区经 `Settings.usage_dict()` 读写（task12 §6-2 消费路径原样：
  `Settings` → `UsageSettings(**usage_dict())` → `build_route_config` →
  引擎尾部合并）。
- **存储**：settings.json 唯一存储（§0 补齐的一环）；③区与设置页（未来
  集成段）都是「读同一文件 → merge 单字段 → 原子写回」，**不存在两个
  可编辑副本**——任何时刻值只有一份、写入都过 Settings 校验。
- **生效时机**：五项=下次连接生效（work10 §2.2 / task12 §5.3 同一口径）；
  其余设置项（端口下次连接、数据目录下次启动、自动探测立即）不在③区、
  不在本段范围。
- **边界重申**：网站黑名单不进设置页（task12 §6-3），名单文件层（§4）
  是连接层材料；评级阈值等不进设置项的项本段未触碰。

---

## 8. 测试口径（test_usage_wiring.py，离线）

- 锚点/静态组（无 Qt 依赖）：交付物在位、DESIGN 必备节在位、四禁词
  （work10 §5.5：connect_best / connect_fastest / 按延迟排序备用组 /
  定时延迟对比换节点）对四个交付模块扫描为零。
- 合并组：七协议 × proxy/tun 默认片段深等价（零行为变化红线）；§4.5
  五条契约逐条断言；dns_patch 字段隔离（深拷贝对照：只许两字段变）；
  锚点缺失/目标缺失/契约破坏/入参不可变等报错路径。
- 名单组：往返无损（字节级）、str/dict 两形态、双处置冲突、非法条目、
  缺失→空名单、损坏→报错、version 拒读、路径从设置存储推导。
- 存储组：缺失→默认、往返、未知键报错、损坏如实报错、version 拒读、
  reset 留档、merge 单字段保九项（③区接线机制）。
- ③区 offscreen 组（PySide6 缺席整组 skip 并记 DONE，不伪造通过）：
  offscreen 构造、控件→落盘往返、单字段写入不碰其余九项、交叉校验
  红字+回退、损坏文件形态、常驻生效文案。
- sing-box check 组（二进制不在场 skip 并记 DONE）：合并后配置跑
  `sing-box check -c`（默认片段 / direct 模式 / global+reject 名单三种；
  rule 模式含占位 rule_set 条目、其路径系 U3 真条目核定前的占位——不在
  本组范围，见 DONE 未解决项）。

---

## 9. 修复注记：占位 rule_set 不进 live 配置（方案 A，2026-11-22 裁决）

依据 work17/REVIEW_FAIL.md（本机 sing-box check 实测 FATAL：占位条目带
`prototype_placeholder` 未知字段，真内核严格 JSON 解码拒绝整个配置）与
主会话方案 A 裁决。**本节取代 §2/§8 中与占位 rule_set 相关的旧表述**：

- `merge_usage_config` 增加可选第三参 `notes=None`（list 时收集滤除
  说明，None=行为不变）——引擎集成点现行两参调用 `merge_usage_config(
  config, fragment)` **原样兼容，集成树零改动即可吃进修复**。
- 合并五步之前先经 `_live_fragment` 滤除：带 `prototype_placeholder`
  键的 rule_set 条目不进 live 配置，引用其 tag 的规则连带滤除，滤除
  说明进 notes（不静默）。
- 效果：U3 之前开广告/恶意拦截或切 rule 模式 = 对应项无操作（不断连、
  不发无效配置）；U3 核定真来源、片段条目删占位键后不再被滤，拦截
  自然生效。0.14.0「占位」口径在片段层原样保留（usage_model 不改）。
- 测试：片段层占位断言保留（原 line 386 用例改为**真·片段层**断言——
  修复前该用例实际断言的是合并后 live 配置含占位键，与方案 A 直接
  冲突，已如实改写并在 DONE.md 记明）；新增 live 配置「占位被拦」
  断言（三触发形态 + notes 通道 + 片段不可变 + 两参调用兼容）；
  契约 1/2、tag 重复防线、resolve 锚点防线四处断言随滤除语义同步
  改写；check 组用例一字未动（原用例复跑即转绿）。
