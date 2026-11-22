# task29 设计：界面优化与调整第二轮（阶段五·商业级打磨 第三段）

工作目录 `~/workspace/zcode-tasks/work29/`。client-dev 只读参考：
`~/workspace/vpn-project/client-dev`（gate-client 本体）。

**版本锚点**：开工实测 `client/VERSION` = **0.30.0**（2026-11-22，task26
裁决落地已集成）。本段对着 0.30.0 实际代码做，锚点行号全部实树实测，
`test_ui_polish2.py` 逐项断言防编造。本段为设计 + 原型段（接 task14
体例）：不改 client-dev 一字节，改造落点以 diff 草案形式写进 §7，集成
由后续段照单执行。

**复审基线结论（先说总账）**：work14（v0.18.0）的 24 项改造设计
**一项都没有集成**——后台窗 `client/ui/app.py` 的呈现层自 0.17.0 起未动
（对比 work14/DESIGN.md 引用的行号与 0.30.0 实树逐项吻合，见 §1）；
U1–U6（task16–21，0.19.0–0.28.0）落的是 work14 §5.1 的**教训清单**
（状态分通道、connect_started 分支、行内右键、空态卡），不是完整改造
设计，且教训在新窗有再犯实例（N9/N11/N12/N13/N14）。所以本段 = 两件事：
后台窗收口（work14 遗留）+ 前台窗补课（新审计），一并进第二轮改造设计。

---

## 0. 审计口径（沿用 task14 写死）

1. **审计对象**：前台使用窗 `client/ui/usage_window.py`（UsageWindow，
   ①–⑥区）+ 后台窗 `client/ui/app.py`（MainWindow，三来源页）+ 设置页
   `client/ui/settings_dialog.py` + ③区 `client/ui/usage_rules_page.py`
   + ⑥区 `client/ui/special_sites_page.py`。CLI 不在表内。
2. **严重度三档**：挡路 / 难受 / 难看（task14 同款定义）。
3. **证据纪律**：每项给文件:行号（0.30.0 实测）或 grep 反证；缺失类以
   grep 全文为证。
4. **范围**：只动界面呈现与交互。数据口径与铁律（分类/验真/评级/健康
   判定、设置字段含义与默认值、生效时机、手动固定节点、永不按延迟自动
   切换）一律不碰。
5. **待裁定项纪律**：G15 及一切属个人偏好的项只列选项、不裁决、不预设
   结论；原型可演示形态供裁决用。

---

## 1. 复审：work14 推迟项对 0.30.0 实树逐项核对

复审对象 = task14 设计中明确「集成时定 / 待主人裁定 / 推迟到 U1 之后」
的九项。判定三态：**已落实 / 仍遗留 / 已过时**（过时 = 前提变了，按新
前提重新立项）。

| # | work14 项 | 判定 | 0.30.0 实树证据 |
|---|-----------|------|-----------------|
| R1 | G13 底部动作排 13 控件挤一行 | **仍遗留（按后台定位重新定）** | app.py:479–542 仍是一条 QHBoxLayout，且从 13 控件涨到 **14**（0.24.0 新增「设置」按钮 539–541）：连接 480 / 断开 484 / 模式 490 / 测速验出口 499 / 置顶 502 / 拉黑 505 / 删除 508 / 一键删废 511 / 自动探测勾选 517 / 间隔 521 / 测本页 528 / 一键全测 531 / 刷新 534 / 设置 539。默认窗宽仍 1080（app.py:455）。**前提已变**：该窗已降级为后台窗（app.py:454 标题「gate-client · 后台（来源管理）」，启动不显示 app.py:1676）——work14 按前台窗定的「两行分组 + 窗宽 1180」应按后台定位一次性重定（§7 R1）。 |
| R2 | G3/G19 序保持机制（populate 签名与刷新时机两边一起定） | **仍遗留（两窗都在犯）** | 后台：populate（app.py:321–363）整表重建，refresh_pages（745–756）每次都按 `history.sorted_nodes` 重排（754），任务收尾必全刷（1435）、会话事件必全刷（1378）；批量测速进行中只发 progress 消息（925/940 → 1448 status_label），表格列值纹丝不动（G19）、收尾整批跳变重排（G3）。前台：BoardPage.populate（usage_window.py:232–276）同款整表重建（keep_id 只保选中 233/272–273，不保序不保滚动位），refresh_boards 在每个会话事件（1411）与本窗任务收尾（1491）全量重排；②区批量测速中表格同样不动（917–941 只发 progress），收尾（1491）整批跳变。 |
| R3 | 托盘/关窗行为是否给前台窗另定口径 | **已有默认形态；是否另定仍待主人裁定（列选项）** | 0.30.0 已定默认：前台窗关窗=最小化到托盘 + 气泡「已最小化到托盘，连接保持不变。」（usage_window.py:1542–1553）；后台窗关窗=真关（app.py:739–742）。代码注释明示「work14 §5.2 的『前台关窗=真退出或另定』待主人裁定，裁定后只改本方法」（usage_window.py:1544–1545；work16/DESIGN.md §6-1 同记录）。本段只列选项不裁决：A. 维持现状（关窗=进托盘，会话常驻，托盘退出才真退）；B. 前台窗加「退出」确认口径（连着时关窗弹一次确认是否断开退出）；C. 关窗=真退出、托盘常驻改为可选设置（改设置字段=动数据口径，本段不立项，仅列作选项的代价说明）。原型不演示本项。 |
| R4 | G15 破坏性确认模态 3 处与两处输入模态 | **仍遗留且扩大（只列选项、不裁决）** | 0.30.0 模态存量：①后台窗 `QMessageBox.question` 3 处——删节点 app.py:1043、删上一轮批次 1101、删本页废 1117；输入模态 2 处——`QInputDialog.getMultiLineText`（粘贴链接）1520、`QInputDialog.getText`（订阅地址）1539；启动档 2 处——critical 1626、warning 1635（settings 损坏）。②设置页 `QMessageBox.question` 3 处——改数据目录 settings_dialog.py:300、改端口 328、恢复默认 345；warning 2 处——端口被占 316、系统代理冲突 323。③前台窗——诊断结果 information（usage_window.py:692）、启动档 critical 1615 / warning 1623。较 0.17.0 净增 8 处（设置页 5 + 前台窗 3）。work14 口径不变：0.7.0「不弹模态」本义是结果通知不走模态，破坏性操作前确认是明留口径；是否改行内确认条**待主人裁定**。原型三演示行内确认条形态（§6 组三）。 |
| R5 | G17 告警条红/琥珀分档 | **部分落实；关闭按钮与分档仍遗留** | 前台窗告警条已可换行（usage_window.py:424 `setWordWrap(True)` ✓）；后台窗告警条仍未换行（app.py:461–465 无 setWordWrap，长文案截断如「启动被挡：端口 7897…」三句话）；两窗均无关闭按钮（grep `alert_bar` 两窗全文无 close/dismiss 挂接）；两窗均单一红色 `#b00020`（app.py:463、usage_window.py:423），无琥珀档。分档建议随 §7 R5（红=会话事故，琥珀=需处置警告，灰=过程消息已由消息行承担）。 |
| R6 | G8 排序档位集合 | **仍遗留（两窗都没做）** | grep `setSortingEnabled` 两窗 0 处；无排序下拉。后台表顺序完全由 `history.sorted_nodes`（app.py:754）决定；前台板块页由 refresh_boards（usage_window.py:790）决定。排序口径本体不动，只加展示档（§7 R6）。 |
| R7 | G6 「最近」列日期口径 | **仍遗留（前台是新增同款）** | 后台 app.py:352–353 与前台 usage_window.py:264–265 都是 `time.strftime("%H:%M", …)`，隔夜分不出新旧。work14 推荐恒定「M/D HH:MM」实现最简，本设计沿用该推荐（§7 快赢 3）。 |
| R8 | G4 「一键删废节点」动态文案一个还是拆两个 | **仍遗留（待集成段按 §7 R8 落）** | 后台按钮文案恒「一键删废节点」（app.py:511），待清批次语义藏在 purge_waste_selected_page（1097–1124）的 `_batch_waste` 分支里，按钮上不可见。 |
| R9 | G23 活动节点行高亮底色 + 标记列 | **仍遗留（两窗）** | 两窗仍把标记拼进名称列：后台 app.py:328–331（`● ` + 📌🚫☠ + 名字），前台 usage_window.py:239–242（`● ` + 📌☠ + 名字）；活动节点行无高亮底色（QSS 无 selection 外状态色）。 |

**work14 其余项速览**（非推迟项，但第二轮设计需要全账）：

| 项 | 0.30.0 现状 |
|----|-------------|
| G1 状态行混用 | 后台窗原样（status_label 仍当消息看板，`_show_info` app.py:1447–1448）；前台窗已按教训落实分通道 ✓（session_panel 429–430 + message_line 447–450）。 |
| G2 connect_started | 前台窗有分支 ✓（usage_window.py:1375–1379）；**后台窗仍无**（grep `connect_started` app.py 0 处）——后台窗自身连按钮仍可用（后台是完整窗），连接中后台 status_label 停旧文案，难受级遗留。 |
| G5 测速失败亮红条 | 两窗原样：app.py:1415、usage_window.py:1469（本段 N9 立项）。 |
| G7 无搜索框 | 后台原样；前台 BoardPage 同款（usage_window.py 无 QLineEdit 实例化，grep 0；import 表 77–82 无 QLineEdit）（本段 N6 立项）。 |
| G9 置顶/拉黑单选 | 后台原样（app.py:1008–1018 仍 `selected_node()` 单选）；前台置顶单选是边界使然（前台无批量处置），不算新账。 |
| G10 无右键菜单 | 后台原样（grep `contextMenu` app.py 仅托盘 713）；前台已落实 ✓（usage_window.py:220–221 + 300–308）。 |
| G11 空页无引导 | 后台原样（populate 只有计数 362）；前台已落实 ✓（empty_hint 201–204 + _empty_text 224–229）。 |
| G12 过滤无结果不可区分 | 后台原样（refresh_pages 无 hidden 计数）；前台无场景过滤（板块即过滤），不适用。 |
| G14 过滤/导入混排 | 后台原样（SourcePage bar 237–258）。 |
| G16 裸毫秒 | 后台原样（app.py:1297 `f"，延迟 {latency_ms:.0f}ms"`）；全库唯一残留。 |
| G18 告警同槽覆盖 | 两窗原样：后台连接场景隔离告警（784–788）可被预检占用告警（1170–1174）覆盖；前台同款（usage_window.py:835–839 → 1264–1267）。 |
| G20 引擎缺失不预检 | 两窗原样（_startup_health_check app.py:1189–1212、usage_window.py:1282–1305 均不查二进制）。 |
| G21 模式英文枚举 | **两窗原样且前台新增**：app.py:1149、usage_window.py:1246 均 `f"接管方式：{mode}。…"`（mode="proxy"/"tun"）（本段 N14 立项）。 |
| G22 空值口径五样 | 后台原样；前台新增同款（本段 N11 立项）。 |
| G24 确认框英文按钮 | 原样且扩大：6 处 `question` 全部未显式给中文按钮（app.py:1043/1101/1117、settings_dialog.py:300/328/345）；启动档 warning 2 处（app.py:1635 / usage_window.py:1623）虽显式给了 `QMessageBox.Yes \| QMessageBox.No` 参数但按钮标签仍是英文 Yes/No；critical 2 处（app.py:1626 / usage_window.py:1615）为单 OK 按钮。 |

---

## 2. 新审计：使用界面 U1–U6 六区（对 0.30.0 实树）

编号 N1–N16（N=新审计）。①–⑥区指 usage_window 的分区（work10 §2.1/§2.2
口径）；后台窗、设置页的问题在 §1/§2.7。

### 2.1 挡路（3 项）

| # | 落点 | 问题 | 证据 |
|---|------|------|------|
| N1 | app.py:1232–1250/1266–1299 + app.py:1660–1676 + usage_window.py:1308–1327 | **连接健康三态在前台窗全盲：判死/可疑/复查告警都写在一个从不显示的窗里。** 健康探测（每 15s）只由后台窗 poll_once 调度（app.py:1316–1331 → `_maybe_health_probe` 1232–1250），结果落 `_finish_health_probe`（1266–1299）——写的是**后台窗自己的** `_show_alert/_show_info`（1439–1448）；而后台窗在 main() 构造后从不 show（app.py:1661–1663 构造、1676 只有 `usage.show()`）。于是：①HEALTH_DEAD「节点疑似已死：连续 5 次探测失败…」红条亮在看不见的窗里（app.py:1280–1286；托盘气泡 `self.tray is not None` 恒假——托盘已迁主窗、后台 self.tray=None，app.py:546）；②suspect「连接可疑…仍保持连接、不自动切换」写进看不见的状态行（1292–1295）；③系统代理 30s 复查（1252–1264 → 1301–1314）与 TUN 接管检查（1333–1343）同样只写后台窗。前台窗 ①区以 orchestrator.state 对账（usage_window.py:1317–1324）——引擎进程活着就永远是「已连接」绿点，出口已死毫无感知；唯一例外是容灾开关开时替换告警经 `_failover_alert` 上前台（usage_window.py:762–770）。**日常只看前台窗的用户，会话健康真相缺失。** 复现：容灾关，连一个出口已死的节点 → 后台探测判死 → 前台窗毫无变化（绿点「已连接」）。注：这是跨窗呈现接线缺口，不是探测逻辑问题；探测线程结构不动（不改即不违反「orchestrator/线程口径一行不改」）。 | 复现步骤如上；grep 证实 app.py `_finish_health_probe` 全部输出走 self.*（后台窗），无任何到前台窗的转发调用。 |
| N2 | usage_window.py:646 vs 325–542 | **菜单「诊断」两项首次点击即 AttributeError，诊断静默不跑。** `_launch_diag` 读 `self._diag_active`（646），但 `UsageWindow.__init__`（325–542）从未初始化该属性——grep 全文仅 645/646/649/1417 四处，无赋初值处。首次点「一键诊断」或「导出诊断包」→ 槽内 AttributeError；PySide6 槽异常只打 stderr，界面零反馈，诊断看起来「点了没反应」。直到某次诊断任务收尾（1417）才会补上该属性——但收尾永远到不了，因为第一次就炸了。 | grep `grep -n "_diag_active" ui/usage_window.py` → 645/646/649/1417，无 `self._diag_active = False` 初始化行。 |
| N3 | usage_window.py:421–531 + 398 | **前台窗纵向 10 段常驻堆叠，②区主表被 ③⑤⑥ 压扁——信息层级在空间分配上倒挂。** root 自上而下 10 块：告警条 426、状态面板 430、节点行 436、速度行 444、消息行 450、按钮行 475、页签表 487、③区 497、⑤区 513、⑥区 531；默认窗 1080×640（398）。③区常驻约 9 行（标题 105 + 五字段 109–123 + 规则集行 130–138 + 三层提示 141–144 + 状态行 148–150）；⑥区常驻更高（标题 + 3 行说明 148–151 + 清单表 154–164 + 编辑行 167–182 + 测试行 185–199 + 状态行 202–204）；⑤区 2 行。640 高下②区表格（板块页签 + 主力操作面）常被压到约三百像素，四七个板块页签再分。work10 §2.2 信息层级「连接状态 > 节点选择 > 功能开关 > 高级工具」——功能开关区（③）与高级工具区（⑥）把节点选择区挤扁，倒挂。 | 复现：1080×640 开前台窗（独立冒烟 `python -m client.ui.usage_window --db <空库>`），②区表格可视高度目测 <½ 窗高；③区+⑤区+⑥区合计常驻 ≈ 一半窗高。 |

### 2.2 难受（7 项）

| # | 落点 | 问题 | 证据 |
|---|------|------|------|
| N4 | usage_window.py:1350–1352 vs 1369–1371 | **同一个延迟数字在①区两行重复。** node_detail 行尾「延迟：{养鱼池中位}」（1350–1352）与 speed_line「…延迟：{同一中位}」（1369–1371）每刷新一次都各报一遍同一个数（数据源同为 `history.stats_for(...).median_latency_ms`）。work10 §2.2 ① 的口径里延迟只要一处。 | 复现：连接成功 → ①区两行同现「延迟：45 ms」。 |
| N5 | special_sites_page.py:191–195 + usage_window.py:1118–1121 | **⑥区「测本板块特殊站」作用域是②区当前页签，但文案与 tooltip 都不点名。** 按钮在⑥区（special_sites_page.py:191），实际测 `tabs.currentWidget()` 的板块（usage_window.py:1118–1121）；从⑥区看「本板块」指哪个不可知。②区同职能先例无歧义：每页自己的「测本板块」（usage_window.py:193–199，作用域=所在页）。空/无选中时消息「本板块没有可测的节点。」（1122）同样不点名。 | 复现：切到「免费机房」页签 → 去⑥区点「测本板块特殊站」→ 实际测的是免费机房，但按钮上、过程消息里都看不出。 |
| N6 | usage_window.py:161–308（BoardPage） | **②区无搜索、无排序档、双击=连接零提示**（G7/G8/G10 的前台新实例）。板块页没有搜索框（usage_window 全文无 QLineEdit，import 表 77–82 亦无）；没有排序档（grep `setSortingEnabled` 0 处）；双击行=连接（216–217）无 tooltip。节点多的板块找节点只能肉眼扫。 | grep 反证如上。 |
| N7 | usage_window.py:773–796 + 1325–1327 + app.py:1435 | **后台测速落库后前台②区不刷新（跨窗对账缺口）。** 前台 refresh_boards 只在四处触发：构造 540、会话事件 1411、本窗任务收尾 1491、库计数变化 1325–1327。后台批量测速只写 history/节点（不改 count_nodes），前台延迟/最近/稳定性列保持旧值，直到下次会话事件或增删节点。work16/DESIGN.md §6-3 已记「后台改 pin 后主窗不即时重排」未解决项——测速样本是同族缺口，且后台批量一轮动几十行。 | 复现：开两窗 → 后台「一键全测」跑完 → 前台②区延迟列全程旧值。 |
| N8 | usage_window.py:917–941 + 1452–1461 | **批量摘要一闪而过，过程流水刷掉一切**（G1 教训在消息通道内部的变体）。②区批量每节点两发 progress（921/939–941）写 message_line；收尾摘要（1456–1461，含 3 行 note）也写同一行（1461），下一轮任何消息立即覆盖；⑥区批量同款（1207/1218–1220 → 1485–1490）。过程有流水、结果无处安放。 | 复现：跑板块批量 → 收尾摘要出现 → 点一下「刷新」或任何消息到达 → 摘要没了，只记得「好像测完了」。 |
| N9 | usage_window.py:1469 + 1472–1473 | **单节点测速失败 / 特殊站测试失败亮会话级红条**（G5 前台新实例）。verify 失败走 `_show_alert`（1469）、special_failed 走 `_show_alert`（1472–1473），与掉线（1396）、假连（1392）、端口占用（1264–1267）同一根红条同一红色；后台同款（app.py:1415）。高频日常结果稀释真事故视觉权重。 | 复现：选中死节点右键「测速」→ 红条「测速失败：…」，与掉线红条无区别。 |
| N10 | usage_window.py:254–261 + special_sites_page.py:231–249 | **空态只做了「0 节点」维度，没做「0 数据」维度。** 板块空态卡 ✓（224–229）；但满库 0 样本时满屏「无样本/未测」（254–261，G22 口径）没有任何「先测一轮」引导——新用户的下一步动作只能猜；⑥区清单空时表格 0 行只有表头（231–249），没有空态卡（PANEL_NOTE 说明在表格上方，语义上不指向「先添加」这一步）。 | 复现：空库导入 3 个节点不测 → 三板块满屏「未测/无样本/—」无引导；⑥区清单空 → 一张空表。 |

### 2.3 难看（5 项）

| # | 落点 | 问题 | 证据 |
|---|------|------|------|
| N11 | usage_window.py:245/252/256/261 + special_probe.py:356–359 | **空值口径前台窗五样**（G22 前台新实例）：出口「未验」（245）、干净「未知」（252）、稳定性「无样本」（256）、最近「未测」（261）、②区「特殊站」列清单空时整列空串（special_probe.py:356–359 返回 ""）。同一件事五种说法。 | 表格首行未测节点肉眼可见。 |
| N12 | usage_window.py:239–242 | **标记符号拼名称列、活动节点行无高亮**（G23 前台新实例）：`● `（活动）+ 📌（置顶）+ ☠（隔离）前缀拼接在名字里；无标记列、无行高亮底色。 | 同 G23 复现。 |
| N13 | usage_window.py:264–265 | **「最近」列只有 HH:MM**（G6 前台新实例，R7 同款）。 | 同 G6 复现。 |
| N14 | usage_window.py:1246 + app.py:1149 | **接管方式消息显示英文枚举**（G21 前台新实例 + 后台原样）：「接管方式：proxy。」而下拉框明明是「系统代理 / TUN 接管全机」。 | 复现：切一下接管方式 → 消息行/状态行现英文单词。 |
| N15 | usage_rules_page.py:59–61 + 141–144 | **③区只读提示一行塞三层信息，窄栏换行 3–4 行。** READONLY_NOTE（59–61）把「值是现读的」「编辑入口在设置页」「生效口径=下次连接生效、不做热重载」挤进一句；③区只占窗宽中一栏（N3 折叠后更窄）时观感差。**按 task26 已裁决形态审计，不重议裁决本身**——只调排版不调口径。 | 复现：③区在 1080 宽下 note 折 2 行；折叠面板演示态下 3–4 行。 |

### 2.4 缺席登记（1 项）

| # | 落点 | 问题 | 证据 |
|---|------|------|------|
| N16 | usage_window.py:694–770 + work21/DONE.md §6-① | **④容灾区整区未落位**（对照 work10 §2.2 ④：总开关+红字标注+备用组列表+禁用说明）。task21 只交付了控制器接线（_setup_failover 695–733、_failover_feed 735–753、_failover_alert 762–770）与控件原型（work21/proto/failover_zone_proto.py）；备用组增删/排序/总开关在界面上无任何入口（backup_group.json 只能手改）。总开关默认关 → 当前零动作零记录，无害；但一旦手改 JSON 开了开关：替换告警能上前台 ✓，而开关状态/组状态在界面无呈现，用户无从关闭它（只能再手改 JSON）。**本段不裁决落位时机**（work21/DONE.md §6-① 归「④区集成段」）；只交收起态设计输入：④区落位时按 §7 N3 的折叠分区形态（收起=一行「④ 容灾 · 总开关：关 · 组内 N 个」，展开=work21 原型控件面）。 | grep `backup_group` usage_window.py 仅控制器装配 44/700/708–709，无任何 UI 控件。 |

### 2.5 ③区按 task26 已裁决形态核对（不重议，全对）

- 只读摘要五行现读 ✓（usage_rules_page.py:159–176 reload 现读 settings.json，无编辑控件，`_displayed_usage` 只是展示对照不是编辑副本）；
- 「在设置页修改」常驻提示 ✓（141–144 + READONLY_NOTE 59–61）；
- 存储损坏红字 + 值行显「—」✓（164–173，不用默认值冒充生效值）;
- 规则集「立即更新」行原样保留 ✓（130–138，写规则集缓存不写 settings.json，task26 ① 范围外）；
- ③区对 settings.json 零写路径 ✓（全文无 save 调用）。
- 唯一呈现小项 = N15（提示行排版），不动口径。

### 2.6 ⑤区数字口径核对（大体合规）

- 监测状态行三态文案现读设置 ✓（speedtest.py:324–340 monitor_status_text；usage_window.py:990–995 损坏如实显示「设置读不出」，不用默认值盖掉）；
- 速度行三态「实测/自报/—」口径 ✓（speedtest.py:293–318 format_speed_display：实测带时间戳、自报分得清、无值「—」）;
- 问题仅 N4（延迟两行重复）。⑤区「定时监测」行与后台自动探测共用 settings.json 同一份 ✓（无分叉）。

### 2.7 ⑥区徽标文案核对（合规）

- 全过=「登录页可达（N/N 全部 L2 通过）。真实登录与使用仍需人工确认…」✓（special_probe.py:385–387）——**L2 只标「登录页可达」，全库无「可用」表述**（special_probe test_lock 锁死）；
- 行徽标「<站名> 不通 / <站名> 存疑 / <站名> 403 / <站名> 未测」✓（special_probe.py:330–343 + 346–371）；
- ⑥区常驻分级说明（L0/L1/L2/L3，L3 永远人工）✓（special_sites_page.py:56–62）；
- 小项：清单空时②区「特殊站」列空串（N11 一并处理）。

---

## 3. 铁律专项：疑似自动切换（零违例）

任务书要求全文排查「疑似自动切换」，零违例为通过标准。实测通过，证据：

1. **orchestrator.connect 调用点全库 3 处，全部用户点名路径**：
   - 后台窗 app.py:802（connect_selected ← 连接按钮 482 / 表格双击 270）；
   - 前台窗 usage_window.py:849（connect_node ← 连接按钮 456 / 右键菜单 294 / 双击 216–217）；
   - CLI cli.py:407（cmd_connect ← 显式 `connect <节点>` 参数）。
   除此之外唯一一处是容灾通道 failover.py:282 `self.ops.connect(chosen_id)`（见 2）。
2. **容灾替换满足三约束**（用户 2026-11-22 裁决口径，task21 已验收，0.30.0 复核）：
   - **只换红色节点**：触发只吃两路红色信号——HEALTH_DEAD 与引擎 dropped（failover.py:51–52 常量 + 模块 docstring「触发只吃两路红色信号」；GUI 喂入点仅 app.py:1290–1291 与 usage_window.py:1403 两处）；suspect 不喂（app.py:1292–1295 suspect 分支无 feed；喂未知原因 failover.handle 当场 ValueError）；
   - **只换组内**：候选来自 `group_node_ids()` 注入（usage_window.py:723），组外节点不可达；
   - **活节点不互切**：模块无延迟输入（FailoverOps 接口无延迟字段）、无 sort、无定时器（failover.py:44–47 自检锁 + §5.5 四禁词）；一轮至多一次尝试、失败不重试（failover.py:289–293 注释）；总开关默认关、关时零动作零记录（failover.py:31–33）。
3. **呈现文案零违例**（逐条 grep 复核）：掉线「已停在原地（不会自动切换）」（orchestrator.py:160）；健康判死「连接保持原样、绝不自动换节点——请手动断开，再自己选下一个节点」（app.py:1281–1282）；可疑「仍保持连接、不自动切换」（app.py:1293–1294）；开窗恢复上次节点只选中不自动连（app.py:562–577）；排序「只决定展示顺序；连接永远由用户点选（铁律）」（history.py:125）；前台置顶「只影响展示排序…不连接、不切换任何节点」（usage_window.py:857–859）；容灾告警如实点名「已按容灾替换：A → B（原因：…）」（failover.py:299–303）；预检「客户端不会改你的系统代理，也不会切换节点」（app.py:1184–1185、usage_window.py:1278–1279）。
4. **全库「自动」语义复核**：grep `自动` 五个 UI 文件 + orchestrator/failover/history——「自动探测」（测速）、「自动归位」（分类展示）、「自动重试」（否定句）、「自动切换」（全部否定句）；无任何肯定句的自动换节点表述。
5. **相邻呈现风险（不是自动切换，是误点风险）**：G3/R2 重排跳行。已立项（序保持），不升挡路。

**结论：零违例，通过。** N1 的改法（健康告警呈现转发到前台窗）只搬呈现、不搬触发，不触碰第 1/2 条的任何调用点。

---

## 4. 改造设计（与审计表逐项对应）

总原则三条：
1. **通道纪律**（work14 §5.1-1 的第二轮补课）：连接状态、健康、过程消息、
   批量结果四类信息各归各槽，互不覆盖（N8/N9；R5）。
2. **空间纪律**（N3）：前台窗按 work10 §2.2 信息层级分配纵向空间——
   ①区恒顶、②区吃满余宽、功能开关与高级工具区默认收起、④区落位即折叠。
3. **同管道两窗一起定**（R2）：序保持/搜索/排序/空值口径这些两窗共用的
   机制，一次定签名、两窗同改，不造两套。

| 审计项 | 改法 | 量级 |
|--------|------|------|
| N1 健康三态前台全盲 | **呈现转发，不动线程**：main()（app.py）装配两窗后，给后台窗注入一个呈现转发出口 `backend._frontend_feed = usage._frontend_feed`（形态照抄 work21 `_failover_feed` 注入先例 app.py:1669）。后台窗 `_finish_health_probe` 的 dead/suspect/正常 三分支与 `_finish_sysproxy_check` 冲突/解除、`_tun_takeover_check_once` 未接管/恢复，在原有 self.* 输出之外调 `self._frontend_feed(text, level)`（level ∈ red/amber/info）；前台窗 `_frontend_feed` 按 level 写告警条（红）/琥珀条/消息行。托盘气泡统一走主窗托盘（work16 §6-2 欠账一并收：后台窗三处 tray.showMessage 挂到转发出口）。探测线程、判级函数（connection_health_detail）、触发口径零改动。diff 草案 §7-D1。 | 中 |
| N2 诊断首点异常 | usage_window.py `__init__` 补一行 `self._diag_active = False`（旗标其他三处读写不动）。 | 极小（快赢 1） |
| N3 纵向堆叠压扁②区 | ③⑤⑥（含将来④区）统一改**可折叠分区面板**：每区一个标题行（QToolButton 箭头 + 标题 + 一行状态摘要）+ 可收起的正文容器；默认 ③⑤⑥ 全收起、②区吃满余宽；标题行即状态摘要（③「规则与网络 · 分流：全局 · 拦截：关」；⑤「测速与监测 · 定时：开/10 分钟」；⑥「特殊网站测试 · 清单 3 站」），收起也不丢一眼信息。展开态正文不变。④区落位时同形态（N16 收起态答案）。原型组二演示。 | 中 |
| N4 延迟两行重复 | 延迟只留节点行（node_detail），speed_line 只留吞吐三态「速度：…」；`_apply_speed_row`（1359–1371）删延迟段。 | 小（快赢 9） |
| N5 ⑥区批量不点名 | 「测本板块特殊站」文案与 tooltip 动态带板块名：UsageWindow 在 `tabs.currentChanged` 时调 `special_panel.set_board_label(classify_proto.BOARD_LABELS.get(...))`，按钮变「测「免费住宅」板块特殊站」；无页签时维持原文案。作用域机制本身不动（仍是②区当前页签）。 | 小 |
| N6 ②区无搜索/排序/提示 | 板块页过滤行加：搜索框（QLineEdit，「按名称/出口搜索」，输入即滤——纯前端过滤不动 store）；排序下拉（默认「推荐序」=现行 sorted_nodes，另给「延迟↑」「最近成功↓」两档——只改传给 populate 的排序包装，不动 history.sorted_nodes 本体）；表头 tooltip「双击行＝连接」。序保持随 R2 机制走。 | 中 |
| N7 跨窗刷新缺口 | main() 给后台窗注入 `backend._refresh_notify = usage._light_refresh`；后台窗批量/自动探测收尾（app.py:1435 refresh_pages 前后）调它。前台 `_light_refresh` 只做**行内值回填不重排**（复用 R2 序保持管道：按节点 id 定位行，更新延迟/稳定性/最近/干净单元格），不整表重建。diff 草案 §7-D2。 | 中 |
| N8 批量摘要一闪 | 板块页页脚加「最近一轮」常驻槽（QLabel，随页走）：批量收尾摘要写入所在板块页的槽（而非只写 message_line）；过程流水照旧走消息行。⑥区已有 status 槽 ✓ 不动。 | 小 |
| N9 测速失败亮红条 | verify 失败（usage_window.py:1469、app.py:1415）与 special_failed（1472–1473）降级进消息行（琥珀文案前缀「⚠」可选）；红条只留会话事故（掉线/假连/错误/端口占用/TUN/健康判死/容灾）。 | 小（快赢 2） |
| N10 空态缺数据维度引导 | 0 样本满屏时板块页页脚槽给一行「这一页还没有测速数据：右键行「测速」或点「测本板块」先测一轮」；⑥区清单空时状态行显「清单为空：在下方添加站点后才能测」（set_status 空态语义，不新增控件）。 | 小 |
| N11 空值五样 | 统一「—」+ 列头 tooltip：未验/未知/无样本/未测 → 「—」（各列语义进 tooltip：出口 —=未验真；干净 —=无情报；稳定性 —=样本不足；最近 —=未测）；特殊站列清单空 → 「—」+ tooltip「未启用特殊站清单」。两窗同改。 | 小（快赢 3） |
| N12 标记拼名称 | 名称列只留名字；标记改独立「标记」列（📌 置顶 / ☠ 隔离 / ●+底色 活动，带 tooltip），活动节点行整行加高亮底色（QSS 状态色，非 selection 色）。前台后台同款（后台另有 🚫 拉黑）。 | 中 |
| N13 最近列无日期 | 「M/D HH:MM」恒定（work14 推荐口径，实现最简、跨夜跨年都不歧义）。 | 极小（快赢 4） |
| N14 英文枚举 | `_on_mode_changed` 用 `self.mode_combo.currentText()`（两窗各一行）。 | 极小（快赢 5） |
| N15 ③区提示排版 | READONLY_NOTE 拆两行常驻：「在设置页修改（菜单「设置」）；值现读，无编辑控件。」「生效口径：下次连接生效——连接时现读设置生成配置，不做热重载。」口径零改动。 | 极小 |
| N16 ④区缺席 | 不裁决落位时机（归④区集成段）；本段交付收起态设计输入（随 N3 折叠形态）+ work21 原型控件面沿用。 | — |
| R1 后台动作排 | 按后台定位重排（供给侧低频动作收编）：第一行=会话组（连接/断开/模式）+ 页内测速组（测速验出口/测本页/一键全测）；第二行=处置组（置顶/拉黑/删除/一键删废）+ 维护组（自动探测勾选+间隔/刷新）靠右；「设置」并入菜单栏（后台窗加菜单「设置」入口，或保留按钮于第二行行尾——集成段二选一，推荐进菜单：设置是全局入口，前台窗菜单已有，后台窗不该有第二个摆位）。默认窗宽 1080 维持（后台窗不常驻前台，不追 1180）。 | 中 |
| R2 序保持 | populate 统一加 `keep_order=False` 参数（默认 False=旧行为，兼容现有测试；True=按传入顺序回填且更新行内值不重排）：批量进行中/跨窗轻刷新（N7）走 keep_order=True；任务收尾重排只在「无悬停无拖选」时发生（保守替代：只在点「刷新」/切页签时重排——集成段二选一，推荐前者）。两窗 populate 同签名。diff 草案 §7-D3。 | 中 |
| R3 托盘/关窗 | 维持现状为默认（关窗=进托盘+气泡）；是否另定待主人裁定（选项见 §1 R3 行），裁定后只改 usage_window.py:1542–1553 一处。本段零改动。 | — |
| R4 确认模态 | **只列选项不裁决**：A. 维持模态（0.7.0 明留口径，零改动）；B. 破坏性确认改行内确认条（告警条位次出一条带「确认删除/取消」按钮的条，无打断；原型组三演示形态）；C. 折中：删除类（高频）行内化，设置页危险项（低频强后果）维持模态。**本段不预设结论**；无论选哪，确认语义（一次确认、默认按钮=否）不变。 | — |
| R5 告警条分档 | 两窗告警条统一三档：红（会话事故）、琥珀（需处置警告：系统代理冲突/引擎缺失/规则集失败）、灰→消息行（过程）；加「×」关闭按钮（点击只隐藏本条）；后台窗补 `setWordWrap(True)`。琥珀色样 `#b26a00` 底 / 深底琥珀字两版随原型给。 | 小 |
| R6 排序档位 | 下拉三档：推荐序（默认，现行口径）/ 延迟↑ / 最近成功↓；只包 `sorted_nodes` 的 key，本体不动；不启用 QTableWidget 原生点击排序（与 R2 冲突）。 | 中 |
| R7 最近列 | 同 N13。 | 极小 |
| R8 删废按钮 | **推荐拆两个**（后台定位下语义本就该分开）：「删上一轮废节点（N）」只在有待清批次时出现（N=0 隐藏）；「删本页废节点」常驻（点击时若本页无废节点给消息行提示，维持现口径）。比动态文案一个按钮少一次状态歧义，与「待处理积压必须带数量」（work14 §5.1-3）一致。若集成段裁决要一个按钮，动态文案「删上一轮废节点（N）/删本页废节点」同效。 | 小 |
| R9 标记列/高亮 | 同 N12（两窗一起）。 | 中 |

### 4.1 明确不改的项（与理由）

| 不改 | 理由 |
|------|------|
| orchestrator 结构与事件协议、线程通道 | 铁律执行者与既定接法（work16 §3），一行不改；N1 只加呈现转发回调，N7 只加收尾通知，都不进 orchestrator。 |
| 来源页组织维度 / 来源即归属 | 主人既定口径；R1 只重排页内控件，页签与按钮归属不动。 |
| `history.sorted_nodes` 排序口径本体 | 「能用的排前面」是用户需求；R6/N6 只加展示档，默认档不变。 |
| 验真 / 评级 / 健康分级判定 | verify_exit / verdict_muse / connection_health_detail 判定逻辑不动；N1 只转发其产出文本。 |
| 五项规则字段的含义、默认值、生效时机 | task26 裁决形态整体不动；N15 只拆排版。 |
| ⑥区分级判定与徽标文案本体 | special_probe 判定/文案口径不动（L2=登录页可达红线）；空值展示「—」只动空串形态。 |
| 容灾判定与触发口径 | failover.py 不动；④区落位时机归后续段（N16 只交设计输入）。 |
| 批量测速/自动探测的管道与端口纪律 | _run_batch/_run_board_batch/_run_auto_probe、10818/10819/10828/10829 口径不动；N7/R2 只改结果的呈现刷新。 |
| 订阅地址不回显 | 红线照旧；R4 任何形态都不新增回显。 |
| 破坏性操作「一次确认」语义 | R4 无论模态还是行内条，确认次数与默认按钮=否的语义不变。 |
| 深色主题 DARK_QSS 总体 | 只局部加状态色/琥珀档/高亮底色，不推翻。 |
| 数据口径红线全项 | 分类/验真/评级/健康判定、设置字段含义与默认值、生效时机——零改动。 |

### 4.2 快赢清单（可立即做的小改，按优先序）

1. **N2** `_diag_active` 初始化（1 行）——bug 修复最高优先。
2. **N9** 测速/特殊站失败降级消息行（2 窗各 1–2 行）。
3. **N13/R7** 「最近」列 M/D HH:MM（2 窗各 1 行）。
4. **N11** 空值统一「—」+ 列头 tooltip（2 窗 populate 各 5 行）。
5. **N14** 接管方式 currentText()（2 窗各 1 行）。
6. **G16** 后台健康消息裸毫秒 → `history.format_latency_ms`（app.py:1297 一行）。
7. **G24** 6 处 question 显式中文按钮（「删除 / 取消」等显式 buttons+setText；app.py:1043/1101/1117、settings_dialog.py:300/328/345），启动档 2 处 warning（app.py:1635 / usage_window.py:1623）一并给中文按钮。
8. **N4** 速度行去重复延迟（1 行）。
9. **R5 后台半项** 后台窗告警条 `setWordWrap(True)` + 两窗「×」关闭（<10 行）。
10. **N5** ⑥区批量按钮带板块名（~5 行）。
11. **G4/R8** 删废按钮动态文案或拆两个（~10 行）。
12. **N15** ③区提示拆两行（1 行改 2 行）。

---

## 5. diff 草案（落点清单，不打入 client-dev）

集成段照单执行；以下为关键草案，快赢类以「行级落点」给足，结构类给
hunk 形态。行号 = 0.30.0 实树。

### D1 健康三态呈现转发（N1 + work16 §6-2 托盘气泡）

```diff
--- a/client/ui/usage_window.py
+++ b/client/ui/usage_window.py
@@ class UsageWindow
+        def _frontend_feed(self, text: str, level: str = "info"):
+            """后台窗健康/复查/TUN 呈现转发出口（task29 N1）。level:
+            red=告警条, amber=琥珀条, info=消息行。只呈现，不触发任何
+            连接动作（铁律）。"""
+            if level == "red":
+                self._show_alert(text)
+            elif level == "amber":
+                self._show_warn(text)          # 琥珀档（R5 落地后的槽）
+            else:
+                self._show_info(text)
+
         def _failover_alert(self, payload):
```

```diff
--- a/client/ui/app.py
+++ b/client/ui/app.py
@@ MainWindow.__init__（394 行附近，_failover_feed 注入位次）
+            self._frontend_feed = None      # main() 注入（task29 N1）
@@ _finish_health_probe HEALTH_DEAD 分支（1283 行 _show_alert(text) 后）
+                if self._frontend_feed is not None:
+                    self._frontend_feed(text, "red")
+                elif self.tray is not None:   # 独立冒烟形态兜底
+                    self.tray.showMessage(...)
@@ suspect / 正常 分支：同款转发（amber / info）
@@ _finish_sysproxy_check（冲突 red/amber、解除 info）、
   _tun_takeover_check_once（未接管 amber、恢复 info）：同款转发
@@ 三处 tray.showMessage → 经转发出口统一走主窗托盘
@@ main()（1669 行 backend._failover_feed = ... 之后）
+        backend._frontend_feed = usage._frontend_feed
```

### D2 跨窗轻刷新（N7）

```diff
--- a/client/ui/usage_window.py
+++ b/client/ui/usage_window.py
@@ class UsageWindow
+        def _light_refresh(self):
+            """后台任务收尾的行内值回填（task29 N7）：按节点 id 定位行，
+            更新 干净/延迟/稳定性/最近/特殊站 单元格；不重排不重建表
+            （R2 序保持管道，keep_order=True 同源）。"""
+            active = self.orchestrator.active_node_id
+            for pg in self.boards.values():
+                pg.update_rows_inplace(self.store, active)
```

```diff
--- a/client/ui/app.py
@@ _on_task_finished 尾部（1435 refresh_pages 附近，batch/auto_probe 收尾时）
+            if getattr(self, "_refresh_notify", None) is not None:
+                self._refresh_notify()
```

### D3 populate 序保持签名（R2，两窗同改）

```diff
--- a/client/ui/usage_window.py   （BoardPage.populate，232 行）
-        def populate(self, nodes, active_node_id):
+        def populate(self, nodes, active_node_id, keep_order=False):
@@ keep_order=True 时：不 setRowCount(0)，按传入 nodes 顺序对已有行
+            回填行内值；新节点 append；消失节点移除。默认 False=旧行为
+            （整表重建），现有 test_usage_window 断言零漂移。
--- a/client/ui/app.py           （SourcePage.populate，321 行）
+        同款签名。批量进行中/轻刷新走 keep_order=True。
```

### D4 其余行级落点（快赢类，一行一落点）

- N2：`usage_window.py` `__init__`（346 行 `self._failover_pending = None` 后）加 `self._diag_active = False`。
- N4：删 `usage_window.py:1371` 延迟段，speed_line 只留「速度：…」。
- N13/R7：`usage_window.py:265` 与 `app.py:353` 的 `%H:%M` → `%m/%d %H:%M`。
- N14：`usage_window.py:1246`、`app.py:1149` 的 `{mode}` → `{self.mode_combo.currentText()}`。
- G16：`app.py:1297` `{latency_ms:.0f}ms` → `{history.format_latency_ms(latency_ms)}`。
- N9：`usage_window.py:1469/1472–1473`、`app.py:1415` `_show_alert` → `_show_info`。
- N11：`usage_window.py:245/252/256/261`、`app.py:333/340/344/349` 空值 → 「—」。
- N15：`usage_rules_page.py:59–61` READONLY_NOTE 拆两行常量。
- G24：`app.py:1043/1101/1117`、`settings_dialog.py:300/328/345`、`app.py:1635`、`usage_window.py:1623` 显式中文按钮。
- N5：`special_sites_page.py` 加 `set_board_label()`；`usage_window.py` `tabs.currentChanged` 接线。
- R5：`app.py:461–465` 补 wordWrap；两窗 alert 容器加「×」（小结构，随琥珀档一起落）。
- N10：`usage_window.py` BoardPage 加页脚槽（R8/N8 同一容器）；`special_sites_page.py` 清单空态 set_status 语义。
- R8：`app.py:511–513/1097–1124` 拆两按钮（或动态文案，集成段裁决）。

---

## 6. 原型（proto/，PySide6，假数据）

挑最痛三组（前台窗挡路/难受优先），**不接真实 Store/引擎、不 import
client 包、不用模态对话框**，节点数据全虚构（RFC 5737 保留 IP、
example 域、虚构名称）：

| 原型 | 对应审计项 | 演示内容 |
|------|-----------|----------|
| 组一：前台窗①②区改版 | N1 N4 N8 N9 N11 N12 N13 R5 | ①区四通道齐备且互不覆盖（状态面板/节点行/速度行/消息行）；健康判死红条 + suspect 琥珀条 + 可关闭（N1 呈现形态 + R5 分档）；②区板块表带标记列/活动行高亮/「—」空值/M-D HH:MM 最近列/页脚「最近一轮」批量摘要槽；延迟只在节点行出现一次。 |
| 组二：分区折叠与④区收起态 | N3 N15 N16 | ③⑤⑥ 折叠分区（收起=标题行带状态摘要，展开=现形态正文）；④区收起态演示（「④ 容灾 · 总开关：关 · 组内 0 个」标题行 + 展开占位说明，指向 work21 原型控件面）；②区吃满余宽的前后对比。 |
| 组三：行内确认条与可发现性 | R4(演示) N5 N6 | 行内确认条形态（「删除选中 2 个节点？[确认删除] [取消]」条内按钮，无模态打断——**仅供 G15 裁决演示，不是裁决**）；板块页搜索框 + 排序下拉（默认推荐序）+ 表头「双击行＝连接」tooltip；⑥区批量按钮动态带板块名。 |

运行（本 VM 复用 `work14/.venv-gui` 的 PySide6 6.12.0，系统 Python 无
PySide6）：

```bash
QT_QPA_PLATFORM=offscreen work14/.venv-gui/bin/python work29/proto/proto_app.py --shot work29/proto/shots
```

`--shot` 目录落 PNG 截图（主窗 + 组一/二/三演示页），逐张人眼核验，
所见写进 DONE.md。原型文件不 import client 包、不用模态对话框
（测试断言看守）。

---

## 7. 验收与测试

- `test_ui_polish2.py`（work29/ 内，离线）三组：
  1. **锚点组**（无 Qt 依赖）：§1 复审表 R1–R9 与 §2 新审计 N1–N16 逐项
     断言落点真实（文件+行号+特征串精确匹配，防编造）；缺失类以 grep
     反证固定（setSortingEnabled=0、app.py 无 connect_started、
     usage_window 无 QLineEdit 实例化、app.py 表格无 contextMenu、
     `_diag_active` 无初始化行等）；client-dev 不在位整组 skip 记原因。
  2. **设计互证组**：DESIGN.md 审计条目齐全性（R1–R9/N1–N16 逐 ID 在
     表内、改造设计逐 ID 对应、快赢清单在位、铁律专项在位、待裁定项
     不含结论性裁决词），防表格与测试漂移。
  3. **原型组**（PySide6 缺席则整组 skip 记原因）：offscreen 构造原型
     主窗、三演示页控件齐全、行为断言（状态与消息并存不互冲、告警
     换行+关闭、空值「—」、待清/摘要文案、折叠展开状态数）、`shoot()`
     截图通路、红线看守（proto 不 import client、无模态对话框调用）。
- `cd work29 && python3 -m unittest` 全绿：系统 Python（无 PySide6）下
  原型组整组 skip 记原因、其余全跑；`work14/.venv-gui`（PySide6 6.12.0）
  下全绿 0 skip。
- 锚点行号对 0.30.0 实树精确成立；后续集成段改动界面文件致锚点漂移时
  测试失败并指出漂移处——这是特性不是缺陷。
