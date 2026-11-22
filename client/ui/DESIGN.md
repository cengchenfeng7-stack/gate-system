# task16 设计：U1 使用界面骨架（阶段五·使用界面线 实现第一段）

简报：task16.md。上游定稿：work10/DESIGN.md §2（信息架构）/§12（分段计划
U1 行）；交接输入：work14/DESIGN.md §5（「给 U1 的输入」）。基线版本
0.18.0（client/VERSION；CHANGELOG 顶部未接线清单已核对）。

## 0. 定位与范围

- 本段是 **GUI 实现段**：交付 `usage_window.py`（按落在 `client/ui/` 下的
  口径写：包内相对 import、类名 `UsageWindow`）+ 离线测试。`app.py` 本体
  **一行不动**——集成改动全部写成 §3 清单，由审查方照单执行。
- 范围 = work10 §12 的 U1 行：①连接状态区 + ②六板块节点选择区 + 菜单
  骨架 + 现窗降后台 + 托盘迁移 + 两窗共用 Store/Orchestrator。
  ③规则与网络区（U2）、④容灾区（U6）、⑤测速与速度展示（U4）、⑥特殊
  网站测试（U5）本段不做、不摆空壳占位。
- 铁律：`orchestrator.py` 一行不改；永不自动切换节点（②区排序只是展示
  顺序，`history.sort_key` docstring 是边界）；假数据 only；GUI 测试一律
  `QT_QPA_PLATFORM=offscreen`。

## 1. 窗口结构与归属

```
gate-client（同一进程、同一 Store、同一 Orchestrator）
├── UsageWindow（主窗，启动默认显示，标题「gate-client · 使用界面」）
│   ├── ① 连接状态区（恒顶置：常驻状态面板 + 当前节点行 + 主按钮行）
│   ├── ② 节点选择区（六板块 + 未分类，七页签）
│   └── 菜单骨架：打开后台 / 设置（如实提示未接线）/ 诊断导出（如实提示未接线）
└── MainWindow（现有窗降为后台窗，启动不显示，标题「gate-client · 后台（来源管理）」）
    └── 四来源页 + 导入/登记/更新/验真管理/搜集/删除/拉黑/改判（全部留后台）
```

- **谁是主窗**：UsageWindow（work10 §2.1 定稿「托盘归属主窗」的「主窗」）。
- **谁是后台窗**：现有 MainWindow，定位/标题口径见 §3 改动点⑤。
- **托盘归谁**：主窗。UsageWindow 已实现同款 `_setup_tray`/`_update_tray`/
  `closeEvent`/`_quit`（app.py:530-580 口径迁移，托盘菜单加「打开后台」）；
  后台窗去托盘（§3 改动点②），其 closeEvent 随之变真关窗（不再隐藏进
  托盘），会话常驻由主窗托盘承担。
- **主窗关窗行为**：最小化到托盘、会话保持（沿用现行 UX）。work14 §5.2
  曾建议「前台关窗=真退出或另定」——那是待主人裁定的遗留项，裁定后只需
  改 `UsageWindow.closeEvent` 一处（§6 未解决项）。
- 状态变化一律走告警条/消息行，不弹模态框（0.7.0 既定偏好）。①区分三条
  通道：连接状态常驻（任何消息不写进状态面板——work14 §5.1-1）、过程
  消息行、红色告警条；`_show_alert`/`_show_info` 函数名与调用面与现窗
  一致（app.py:1261-1270），只换内部落点。

## 2. 复用点清单（逐项指到类/函数；行号=client-dev 0.18.0）

| # | 本窗落点 | 复用来源 | 口径 |
|---|----------|----------|------|
| 1 | 事件桥与工作线程 | `_Bridge`/`_Worker`（app.py:157-176） | **直接 import**，不另造线程模型 |
| 2 | 托盘圆点图标 | `_dot_icon`（app.py:178-187） | 直接 import |
| 3 | 深色主题与主按钮样式 | `DARK_QSS`（app.py:60-135，primaryBtn 71-76） | 集成后 `app.setStyleSheet` 全局生效；独立冒烟 main() 自挂同一份 |
| 4 | 场景/网络标签 | `SCENE_LABELS`/`NETWORK_LABELS`（app.py:55-56） | 直接 import，不另定一套（work14 §5.2） |
| 5 | ①区当前节点名 | `verify.exit_display_name`（core/verify.py:299-309） | 实测名口径「国家中文 · 出口IP · 运营商」 |
| 6 | ①区延迟展示 | `history.format_latency_ms`/`stats_for`（core/history.py:89-116） | 养鱼池中位延迟；吞吐行归 U4 |
| 7 | ①区接管方式 | `mode_combo` 约束（app.py:411-419）、`_on_mode_changed`（970-982）、CONNECTED 禁用（1278-1283） | 逐行同款：只在未连接时可切；TUN 未就绪提示=引擎 `_check_tun_privilege`（engines/singbox_engine.py:157-176）报错经 error 事件上告警条，tooltip 同款照搬 |
| 8 | ①区主按钮管道 | `connect_selected`（app.py:613-637）/`disconnect`（639-643） | 逐行同款：隔离警告不拦（622-626）→ 健康预检（629）→ 模式写引擎（633-635）→ 工作线程 `orch.connect`（637）；primaryBtn 交互同款 |
| 9 | 连接前预检 | `_health_precheck`/`_sysproxy_conflict_text`/`_takeover_mode`/`_startup_health_check`（app.py:960-1041） | 逐行同款（work14 §5.2：前台原样继承健康预检文案三件套） |
| 10 | 行内测速 | `_test_node`/`_probe_and_record` + 临时端口 10818/10819（app.py:654-724） | 逐行同款（简报「测速走现有 _test_node 同一管道」）；OpenVPN 未连接 skip 文案照搬 |
| 11 | 动作状态机 | `_run_task`（app.py:605-611）、`_update_action_states`（1272-1297） | 同款；本窗无批量/自动探测/拉黑/删除分支 |
| 12 | ②区列口径与行渲染 | `SourcePage.COLUMNS`（app.py:192）、`populate`（284-325） | 八列：名称/协议/出口/类型/干净/延迟/稳定性/最近（简报口径；前台行不含「场景」列，场景徽标只在①区）；●活动前缀/📌/☠ 标记同款 |
| 13 | ②区排序 | `history.sorted_nodes`/`sort_key`（core/history.py:119-138） | 展示顺序唯一来源；置顶优先→最近成功→成功率→延迟；铁律边界在 sort_key docstring |
| 14 | ②区分组 | `classify_proto.board_of`/`dims_of`/`BOARDS`/`BOARD_LABELS`/`UNCLASSIFIED`（core/classify_proto.py:89-105、146-257） | 唯一字典；判不出维度落「未分类」，不许硬塞；整库只读消费，改判入口归后台 |
| 15 | orchestrator 构造 | `_build_orchestrator`（ui/cli.py:264-289） | 独立冒烟模式复用；集成模式 orchestrator 在主窗建、注入后台窗 |
| 16 | Store 线程口径 | `Store`（core/store.py:67-79） | check_same_thread=False + 内部 RLock；两窗共用同一实例 |
| 17 | 会话事件枚举 | `SessionEvent.kind` 全集（core/orchestrator.py:35-41） | 七种事件每个都有呈现分支（work14 §5.1-2），connect_started→「连接中」 |
| 18 | 状态呈现三分通道 | work14 §5.1-1 + proto_status_demo 模式 | 常驻状态面板/消息行/红色告警条，从第一版就分通道 |
| 19 | 缺 PySide6 降级 | `INSTALL_HINT` 文案（app.py:151-153） | 同文案打印安装指引退出 2，不假装能跑 |

## 3. 集成改动点清单（审查方照单改 app.py；usage_window.py 落 client/ui/ 即插即用）

**集成顺序**：usage_window.py 落 `client/ui/` → 改动点①③④⑤（main() 装配
+ 扇出 + 标题）→ 改动点②（托盘迁移）→ GUI 冒烟（`QT_QPA_PLATFORM=offscreen
python -m client.ui.app`）：主窗出现、②区七板块有数、菜单「打开后台」
可用、托盘归主窗、连接/断开/测速行为与现窗一致。

**① 启动默认显示主窗**——main()（app.py:1430-1446）改装配顺序：
```python
from .usage_window import UsageWindow   # 顶部 import 区补这行
...
store = Store(args.db)
usage = UsageWindow(store)              # orchestrator/engines 在主窗内建
backend = MainWindow(store, orchestrator=usage.orchestrator,
                     engines=usage.engines)
usage.set_backend_window(backend)       # 菜单「打开后台」跳转用
usage.show()                            # 主窗启动显示；后台窗不 show
backend.schedule_startup_probe()        # 自动探测留后台（②区只读消费样本）
```

**② 托盘归属迁移**——MainWindow 内三处：
- 删 app.py:460 的 `self._setup_tray()` 调用一行；
- closeEvent（571-580）改为直接 `super().closeEvent(event)`（后台窗关窗=
  真关，不再隐藏进托盘——会话常驻已由主窗托盘承担）；
- `_update_tray` 与两处 `self.tray.showMessage`（1111-1113、1190-1192）
  因 `self.tray is None` 自然变 no-op，不必删（托盘气泡的统一接法见 §6）。

**③ 事件通道统一扇出**——main() 两窗建好后接管（app.py:373 的窗口级接线
被此覆盖，MainWindow.__init__ 一行不用改）：
```python
def _fanout(ev):
    usage.bridge.session_event.emit(ev)
    backend.bridge.session_event.emit(ev)
usage.orchestrator.on_event = _fanout
```
usage_window 自带链式并入 `_install_event_listener`（独立冒烟模式的兜底，
见 §4）；集成走本统一扇出，两条路不冲突、不叠加。

**④ 菜单「打开后台」跳转**——主窗已实现（show/raise/activateWindow）；
main() 需做的只有改动点①的 `set_backend_window` 注入。反向跳转不做
（后台窗不塞使用界面功能，work10 §2.1）。

**⑤ MainWindow 定位/标题口径**——app.py:375 `setWindowTitle` 改
「gate-client · 后台（来源管理）」；启动不显示（改动点①）；尺寸与其余
不动。

**⑥ 明确不改清单**：orchestrator.py 一行不改（铁律）；SourcePage/来源
页/验真/搜集逻辑不动；自动探测与健康三件套留后台窗 poll_once；Store 构造
不新增（两窗同一实例）；不改 client-dev 任何导入结构。

## 4. 两窗共享 Store/Orchestrator 的接法与线程口径

- **Store**：main() 建一次、同一实例传两窗。Store 已是
  `check_same_thread=False` + 内部 RLock（store.py:70-79），跨线程读写
  在锁内串行——两窗的工作线程与主线程共用它是既定设计；
  **不许各开一个连接**（简报红线）。
- **Orchestrator**：同一实例。`on_event` 单槽（orchestrator.py 一行不改），
  集成模式由 main() 统一扇出（改动点③）；独立冒烟模式用
  `_install_event_listener` 链式并入（新监听把既有监听链在后面，谁先挂
  谁先收，不覆盖别人）。事件由工作线程 emit、Qt 信号队列化回各窗主线程
  （既有 `_Bridge` 模式，app.py:157-161）。
- **并发互斥**：两窗的 `_busy` 是各自窗口的按钮门闩，不共享；跨窗互斥靠
  orchestrator 自己的铁律守卫（已有活动连接时 `connect` 抛
  OrchestratorError，orchestrator.py:91-93），失败事件扇出后两窗各自亮
  告警条。不需要新锁。
- **poll**：两窗各有 5s QTimer（后台窗还挂健康子周期）。
  `orchestrator.poll()` 有状态守卫（orchestrator.py:151-154），双调无害。
  主窗 poll_once 额外做两件对账：①区状态对账（orchestrator 状态为准，
  漏事件自愈）；②区库变更对账（`count_nodes()` 变了才重排板块——后台
  入库/删除后主窗 5 秒内即见，同一 SQLite）。
- **engines**：同一 dict 注入两窗；`takeover_mode` 写入仍只在主线程连接前
  发生（app.py:633-635 同款），CONNECTED 期间控件禁用。

## 5. 边界与不做清单

- ③④⑤⑥ 不做不占位（U2/U6/U4/U5）。
- 前台行内动作**恰为** {连接、置顶、测速}（右键菜单 + 双击=连接）；
  加上断开与接管方式切换，就是 work10 §2.1 允许的前台操作全集。
  导入/登记/更新来源、验真管理、搜集、删除/拉黑、分类改判：零入口
  （work10 §2.3）。
- 排序是展示顺序；代码与文案无「优选」语义；work10 §5.5 四禁词
  （connect_best / connect_fastest / 按延迟排序备用组 / 定时延迟对比换
  节点）grep 干净——测试断言。
- 健康探测/自动探测/批量测速不做在前台（后台机制，前台只读消费其样本）。
- 设置页与诊断导出只留菜单入口，点击如实提示「尚未接线」
  （0.16.0/0.11.0 未接线清单），不假装可用。

## 6. 未解决项

1. 主窗关窗=进托盘沿用现行 UX；work14 §5.2「真退出或另定」待主人裁定
   （改 `UsageWindow.closeEvent` 一处）。
2. 托盘迁移后，后台窗健康探测/掉线的托盘气泡（app.py:1111-1113、
   1190-1192）暂缺托盘可挂——告警条仍在两窗各自亮；气泡统一改经主窗
   托盘发，留后续段一处小改。
3. 后台窗改 pin 后主窗②区不即时重排（主窗只在会话事件/库计数变化时
   重排）；如需即时可把 refresh 挂进扇出，本段不做。
4. 「未分类」补标/改判入口归后台（task11 呈现段），主窗只展示。
5. 真机托盘气泡/字体/深色主题观感未验（本机 .venv-gui 或真机跑 GUI
   冒烟即可复验）。
