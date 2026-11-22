## 0.26.0 - 2026-11-22
- 阶段五第六段：使用界面 U5 特殊网站测试（task20 落地）——⑥区特殊站清单管理 + 分级探测 + ②区「特殊站」列与行内「测特殊站」动作。新模块 client/core/special_sites.py（special_sites.json 加载/保存/校验：条目 {name, domain, category, enabled}，裸域名逐项校验、enabled 严格 bool、包裹层 {"version": 1, "entries": [...]}、原子写、缺文件为空清单不编造默认站点、损坏/重复域名如实报错；路径与 site_blocklist.json 同族口径）、client/core/special_probe.py（verdict_site：verdict_muse 推广，400/403/429 记风控命中且级别停 L2；probe_site L0→L2 流水线：探测动作全注入、任一级失败即停、不重试、只打根路径 https://<域名>/、不跟随重定向；L2 形态判定 default_shape_judge 可注入；write_special_tests 只写节点 extra["special_tests"] = {domain: {level, code, ts}}，不进 verify_samples、不碰 history/store；呈现纯函数 row/badge/summary，全过只说「登录页可达」并附「真实登录与使用仍需人工确认」）、client/ui/special_sites_page.py（⑥区面板：清单表增删/启用勾选/测当前节点/测本板块/状态行 + make_site_actions 真实网络胶水：经代理复用 core/verify._opener/_fetch 同 UA 策略、单次不重试、不跟随重定向；无代理直连做本地 getaddrinfo + TCP 443）。接线：usage_window.py 14 块（⑥区构造与接线方法、②区「特殊站」列与行内第四件动作、任务收尾旗、按钮状态；批量复用批量测速管道形态，临时引擎端口纪律 10818/10819，活动引擎入站或临时引擎测完即停；OpenVPN 隧道未连接时如实跳过）；test_usage_window.py 2 块（行内动作集合三件扩四件，断言随行）。orchestrator 一行不改；只测与回写 extra，永不自动切换节点；special_tests 不在 diag 白名单，导出自动整键剔除。版本针 faults.py 随集成由 0.25.0 改 0.26.0（适配 4 既定口径）。
- 审查注记：work20 离线测试重跑 93 全绿（skip 12：GUI offscreen 组，VM 无 PySide6），与 DONE.md 声明一致；审查方在独立审查复本（0.25.0 实树 + 本段）上复跑全套 1026 全绿（skip 108），.venv-gui offscreen 全套 1008 项、1 失败 + 4 错误，与 0.25.0 原树失败清单逐项相同（全是本机 .venv-gui 缺 PyYAML 的 clash/yaml 环境项，与本段零相关）。改动范围核实：恰 3 新文件 + usage_window.py + test_usage_window.py；core 两模块零网络构件（无 socket/urllib/requests 导入、无 history/store 导入）、零路径级 URL 字面量；「自动切换」字面只出现在「永不自动切换」表述中。集成后 VM 全套见下条。
- 未解决项（DONE.md 转录，待主人裁决）：① L3「永远人工」的界面措辞（「要登录会话/图形验证才能确认，客户端判不了，永远人工」）请主人定稿；② L2 形态特征假阳性/假阴性边界（9 个公开挑战页指纹 + 200 字符阈值是保守起点，极简登录页会误记未过、无标记长 HTML 拦截页可能误给通过，标记表走 make_shape_judge 注入换参数即可）；③ 真实清单由主人维护，代码与测试只有 example.com 类假数据；④ GUI offscreen 组本机未跑（无 PySide6），面板控件行为由审查方 GUI venv 复验。
- 测试：新增 93 个 U5 特殊站测试（work20/ 内，着陆形态）；全套 1026 个，VM 全绿（跳过 108）。本机同步：8 文件同步 + md5 双边 8/8 一致；本机全套 1026 全绿（skip 109）、GUI 冒烟通过；已写 INTEGRATED。

## 0.25.0 - 2026-11-22
- 阶段四补欠账段：设置页落页（task24 落地，task12 欠账）——统一设置模型集成落地 + 设置页模态对话框。新文件 client/core/settings_proto.py（落地形态：消镜像 §8-2——规则五项常量/校验唯一事实源 = usage_model；SettingsStore 读写 settings.json §4.1/§4.2；QSettings 迁移 §4.3/§8-6 只搬 auto_probe 两键；路径四档 §8-3 `--db` > GATE_CLIENT_DB > settings.data_dir > 内置默认）、client/ui/settings_dialog.py（§5/§6 分组 + §5.3 生效时机逐项标注 + §5.4 危险项确认 + §8-4 保留段 10818/10819/10828/10829 呈现层校验 + 间隔四档 5/10/20/30）。接线：cli/app/usage_window 三文件 38 hunk（设置只读加载、构造点传设置端口、供给器 settings_file 落点修正、端口检查随设置走、两窗同一份 settings/settings_store、设置页 open_settings、--db 四档）。五项规则字段的唯一编辑入口仍是设置页（③区现状见下条报备）；QSettings auto_probe 两键停用不再读写；data_dir 变更不搬家。orchestrator 一行不改；设置只管配置本身，永不自动切换节点。
- 审查注记：work24 离线测试重跑 81 全绿（skip 36：GUI 组，VM 无 PySide6），与 DONE.md 声明一致；着陆测试在变基树上复跑 81 全绿（系统 python skip 36；.venv-gui offscreen 81 全绿零 skip）。审查方在 client-copy（0.24.0 实树 + 本段）上独立复跑全套 1026 全绿（skip 108）；.venv-gui offscreen 全套与 0.24.0 原树逐项相同（1008 项、1 失败 + 4 错误，全是本机 .venv-gui 缺 PyYAML 的 clash/yaml 环境项，与本段零相关）。审查变基（DONE 基线 0.23.0、本轮实树已是 0.24.0）：wiring.diff 对 0.24.0 干跑 cli/app 全过、usage_window 9/10 过；审查修正两处集成问题（均已在 CHANGELOG 留痕，不属交付物 bug）：① usage_window hunk #1（import 段）误删 `diag`/`diag_collect` 导入——_run_doctor/_export_diag 仍在用，实机点「一键诊断」必报 NameError，审查方手工合并且恢复两导入（task19 的 speedtest/settings_store/urllib.request 导入一并保留）；② settings_proto.py 整文件替换会抹掉 task19 的 W1 扩展位（speedtest_payload_url），审查方在 task24 版上重贴 W1 五处（字段/校验/to_dict/校验器 import/docstring 注记），奇偶校验仍绿。test_ui_audit 50 锚点 + 2 hits 期望行经变基复核全绿（重算值随 0.23.0+本段，与 0.24.0 树一致）。交付物禁区复扫干净（假数据 only：RFC 2606 域名、RFC 5737 保留 IP、保留端口段、/tmp 路径，零真实节点/订阅/密钥）。
- 报备主会话（DONE §6 转录，待裁决，本段不擅动）：① 「唯一编辑入口」与 task17 ③区并存——设置页与 ③区都是编辑面、一份存储、都立即落盘，③区是否降级为只读摘要待裁决；② 两套持久化 API 并存（settings_proto.SettingsStore 与 task17 settings_store.py load/save/reset，读写同一文件同格式），归属收敛待裁决；③ diag 导出包内端口探测仍按模块默认端口（task22 交付物，不在本段清单）；④ DESIGN §2.2-D2 现状列同步归后续轮。测试：新增 81 个设置页落页验收测试（work24/ 内，着陆形态）；全套 1026 个，VM 全绿（跳过 108）。本机同步：11 文件同步 + md5 双边 11/11 一致；本机全套 1026 全绿（skip 109）、GUI 冒烟通过；已写 INTEGRATED。


## 0.24.0 - 2026-11-22
- 阶段五第五段：使用界面 U4 测速与速度展示（task19 落地）——吞吐实测器 + ①区速度行/②区按板块批量测/⑤区监测状态行。新模块 client/core/speedtest.py（纯标准库：分档 5/25MB 吞吐实测，opener/时钟注入，三态 ok/partial/fail，四类失败逐类可辨，失败不写时间戳不碰自报值；write_back 只在 ok/partial 写 speed_mbps + extra["speed_measured_at"]；format_speed_display 实测/自报/无值三态；board_batch_plan 按板块分组 + 载荷门卫；monitor_status_text 只读 task12 Settings auto_probe 两字段；validate_payload_url 载荷校验单一事实源）。接线：W1 settings_proto 扩展位 speedtest_payload_url（默认空槽、http(s) 校验、SettingsError 归族、to_dict/from_dict/merge 自动带）；W2 usage_window 14 块（①区速度行、②区每页「测本板块」批量入口 + 后台同款批量管道、⑤区监测状态行现读设置、载荷 opener 只活 UI 层、_throughput_for_node 三分支、批量收尾复位 _batch_active）。载荷 URL 仍空槽待主人核定（§13-G，默认空、不内置地址）；真机吞吐实测未跑（本段离线口径）；25MB 档暂无 GUI 入口。orchestrator 一行不改；批量只测与回写，永不自动切换节点；未连接节点吞吐走 10818/10819 手动组临时引擎，10828/10829 自动组零接触，不新开端口段。
- 审查注记：work19 离线测试重跑 31 全绿，与 DONE.md 声明一致；19 块接线锚点在 0.23.0 实树上逐块唯一命中（审查方 apply_wiring.py 机械套用，见 work19/apply_wiring.py）；审查方在 client-copy 上独立复跑全套 1026 全绿（skip 108），.venv-gui offscreen 下 test_usage_window/test_ui_audit/test_speedtest/test_batch_entry 120 全绿。审查发现并修正两处集成问题（均已在 CHANGELOG 留痕，不属交付物 bug）：① test_settings 奇偶校验的 Settings 专属字段集须随 W1 扩展位加 "speedtest_payload_url"（简报明定的统一模型扩展位，校验集即锁已知扩展）；② 集成版 test_speedtest 的 _settings_proto_ref 改包导入（W1-b 的相对 import 需包上下文，spec 裸加载跑不起来）。交付物禁区复扫干净（假数据 only：RFC 5737 文档段、example 系、虚构域名，零真实节点/订阅/密钥；全仓无真实载荷 URL 字面量）。
- 测试：新增 31 个 U4 测速测试（client/tests/test_speedtest.py + test_batch_entry.py，集成适配见各文件末尾清单）；全套 1026 个，VM 全绿（跳过 108）。本机同步：10 文件同步 + md5 双边 10/10 一致；本机全套 1026 全绿（skip 109）、GUI 冒烟通过；已写 INTEGRATED。


## 0.23.0 - 2026-11-22

- 修复（work17 REVIEW_FAIL 裁决·方案 A，merge 层拦截占位 rule_set）：`client/core/usage_merge.py` 的 `merge_usage_config` 增加可选第三参 `notes=None`（list 收集滤除说明，None 行为不变，引擎集成点现行两参调用零改动兼容）；合并五步之前经新增 `_live_fragment` 滤除——带 `prototype_placeholder` 键的占位 rule_set 条目（0.14.0 占位口径、真条目来源待 U3 主人核定）不进 live 配置，引用其 tag 的规则连带滤除，滤除说明经 notes 通道在案（不静默）。效果：U3 之前开广告/恶意拦截或切 rule 模式＝对应项无操作（不断连、不发无效配置——修复前占位条目原文照抄进 live 配置，真 sing-box 严格 JSON 解码整段拒绝，真机开广告拦截即断连，本机 `test_merged_tun_with_usage_rules_passes_check` 实测 FATAL）；U3 核定真来源、片段条目删占位键后不再被滤，拦截自然生效。0.14.0「占位」口径在片段层原样保留（usage_model.py 零改动）。test_usage_wiring.py 断言同步：片段层占位断言保留（原 line 386 用例如实改写为真·片段层断言）、新增 live 配置「占位被拦」断言（广告/恶意/rule 三形态 + notes 通道 + 片段不可变 + 两参调用兼容）、契约 1/2 与 tag 重复/resolve 锚点防线四处断言随滤除语义改写、check 组用例一字未动（原用例复跑即转绿）。版本号不动（仍 0.23.0，本笔随 0.23.0 段），faults.py 版本针不动。
- 阶段四补欠账段：诊断接线（task22 落地，task7 欠账）——材料收集器 + CLI 一键诊断/导出诊断包 + 前台诊断菜单两项接线。新模块 client/core/diag_collect.py（组装 work7/DESIGN §2 导出包全部材料：00-version / 01-environment / 02-checks / 03-health-now / 10-nodes-summary / 11-verify-stats / 12-sources / 20-engine-log-tail / 30-events-recent；run_checks 的 env 四个 health 注入键逐项由 core/health 现成函数原样注入，无第二套阈值；secrets 累积只进内存绝不写文件；引擎日志尾部经 SingBoxEngine.active_log_path() 只读取；近 7 天/近 24h 验真聚合对真实库开只读连接 mode=ro 跑统计 SQL；收集 → diag.sweep_files → diag.build_bundle 三层脱敏防线原样继承）。CLI doctor / export-diag（cli.py 7 处：_collect_check_env 与 cmd_status 共享 env 组装口；doctor 全绿退出 0 / 有 fail 1，env 缺键=「判不了」=ok；export-diag 默认 ~/gate-client-diag-<UTC时间戳>.zip，输出只有包路径与文件数一行，不回显任何节点信息，失败走 [错误] 行=退出码 2）。GUI 诊断菜单（usage_window.py 6 处：前台主窗「诊断导出」单条 stub 换成「诊断」菜单内「一键诊断」「导出诊断包」两项；后台线程通道 _launch_diag（app.py _launch_health 同口径，不占 _busy，_diag_active 轮次守卫）；诊断结果回主线程弹结果框，导出完成只提示包路径与文件数；app.py 一行未改）。orchestrator.py 等零改动；诊断全程只读，不连不测不改节点状态，永不自动切换节点。
- 审查注记：work22 离线测试重跑 33 全绿（skip 4：GUI 动作组整组，VM 无 PySide6；.venv-gui offscreen 下 33/33 全跑全绿），与 DONE.md 声明一致；wiring.diff 对 0.22.0 干跑零冲突（cli.py 1 处 fuzz 2、其余带偏移通过，落点逐项核对与 DONE.md §2/§4 一致）；审查方在 client-copy 上独立复跑全套 994 全绿（skip 108），并在 .venv-gui offscreen 下实跑 test_usage_window 33 全绿；test_diag_wiring.py 为 work22 目录内 staging harness（按自身路径找 wiring.diff 与 client-dev，不落 client/tests/，33 个验收用例留 work22/ 备查）；版本针 faults.py 由 0.22.0 改 0.23.0（适配 4 既定口径）；交付物禁区复扫干净（假数据 only：RFC 5737 文档段、example 系、虚构凭证，零真实节点/订阅/密钥）。
- 未接线（照 work7/DONE.md §5 继承，本段不扩范围）：OpenVPN 拨号日志不可导（20 号文件只覆盖 sing-box 会话日志）；健康巡检样本只在内存（离线导出无历史样本）；编码变体不识别（靠结构剔除兜底）；mask_host 默认保留首段（strict=True 全掩开关已在 diag.py，本段未改默认值）；GUI 真机托盘/字体/深色主题观感未验。
- 测试：新增 33 个诊断接线验收测试（work22/ 内）；全套 994 个，VM 全绿（跳过 108：92 个既有 GUI/offscreen + 12 个③区 offscreen 组 + 4 个 sing-box check 组，本机无 PySide6/sing-box 二进制）。本机同步：7 文件同步 + md5 双边 7/7 一致；本机全套 994 项、1 个失败（skip 109）——唯一失败是 task17 已知缺陷 `test_merged_tun_with_usage_rules_passes_check`（占位 rule_set 进 live 配置被真 sing-box 拒，见 work17/REVIEW_FAIL.md，与 task22 无关，修复轮已派）；GUI 冒烟通过。按流程暂不写 INTEGRATED 标记，见 work22/REVIEW_FAIL.md，待 task17 修复落地、本机全套转绿后补标记。

## 0.22.0 - 2026-11-22
- 阶段五第二段：使用界面 U2 分流与 DNS（task17 落地）——前台③规则与网络区 + 引擎接线 + 设置/名单文件层。新模块 client/core/usage_merge.py（merge_usage_config：§4.5 五条顺序契约，纯 dict 变换、深拷贝不改入参、契约破坏显式报错；常量全部 import 自 usage_model 单一事实源）、client/core/site_blocklist.py（名单文件层 load/save/校验，条目校验与双处置冲突沿用 usage_model.parse_site_blocklist 0.14.0 口径，包裹层 {"version":1,"entries":[…]}，原子写，路径经 settings_store 推导）、client/core/settings_store.py（task12 §4 持久化集成段落地一环：settings.json 读写，缺失→全默认、损坏→如实报错不盖掉、version 异常→拒读、原子写、reset 改名留档）、client/ui/usage_rules_page.py（③区面板：分流模式三选一/广告/恶意/IPv6/DNS 预设+自定义 IP 恰五控件，每个控件只写自己一个字段——现读存储 → settings_proto.merge 单字段 → 原子写回；校验失败红字+控件回退；常驻「下次连接生效」明示）。singbox_engine.py 接线 4 处（import／__init__ 加 usage_provider=None／build_config 尾部调用 merge／新增 _usage_fragment，供给器缺失或 usage=None 即不合并）；cli._build_orchestrator 与 app.engine_factory 各挂供给器闭包（连接时现读设置与名单）；usage_window.py 挂载③区面板（root.addWidget 后插 rules_panel）。orchestrator 一行不改；永不自动切换节点；拦截规则集真条目来源归 U3（本段开关只接 usage_model 既有占位口径，一个真实规则集 URL 都没编）。
- 审查注记：work17 离线测试重跑 66 个全绿（skip 16：③区 offscreen 12 + sing-box check 4，与 DONE.md 声明一致）；审查方在 client-copy（0.21.0 实树）上独立复跑全套 994 个全绿（skip 108），并在 .venv-gui offscreen 下实跑 test_usage_wiring 66 个全绿（skip 4：仅 sing-box check 组）；审查发现并修正三处集成问题（均已在 CHANGELOG 留痕，不属交付物 bug）：① DESIGN §5「把解析出的目录传给 load_settings(覆盖目录)」字面照做会炸——实测 load_settings/load_blocklist 的 path 参是文件路径，传目录必报 SettingsStoreError/SiteBlocklistError（Is a directory），--db 覆盖路径下连接会显式失败；供给器改传 settings_path(data_dir)/blocklist_path(data_dir) 推导出的文件路径，意图（覆盖目录跟 db 走）不变，--db 覆盖路径已用真引擎实测（direct 分流 + reject 名单前置 + final=direct-out + outbounds[0] 恒 node-out；损坏设置显式 EngineError，不静默按默认值连）；② test_ui_audit 锚点随 app.py/singbox_engine.py 改动漂移（57 处 assert_anchor + QLineEdit/ContextMenu 两处清单），按 difflib 映射逐条重算、特征串一字未动；③ TestDeliverables 原按 work17/ 目录读文件（沙盘在 work17 内跑才全绿），集成适配改按 _DELIV 映射读集成树落位，DESIGN.md 落 client/core/DESIGN-usage.md（client/core/DESIGN.md 已是 task15 的）。交付物禁区复扫干净（假数据 only：RFC 5737 保留 IP、RFC 2606 域名、伪造 uuid/password、假 WireGuard 本地地址夹具，零真实节点/订阅/密钥）。
- 测试：新增 66 个 U2 接线测试；全套 994 个，VM 全绿（跳过 108：92 个既有 GUI/offscreen + 12 个③区 offscreen 组 + 4 个 sing-box check 组，本机无 PySide6/sing-box 二进制）。本机同步：14 文件同步 + md5 双边 14/14 一致；本机全套 994 项、1 个失败（见 work17/REVIEW_FAIL.md：占位 rule_set 进 live 配置被真 sing-box 拒，`test_merged_tun_with_usage_rules_passes_check`）、skip 109；**未写 INTEGRATED 标记，等修复轮**（审查方自己的 CHANGELOG 笔误——0.22.0 条目原文写了假地址字面量、触发打包自检——已改写，VM test_packaging 29 全绿、本机重跑打包组全绿）。

## 0.21.0 - 2026-11-22
- 阶段四补欠账段：来源合并落页（task23 落地，task8 欠账）——OpenVPN 页与 VPNGate feed 页合并为「VPNGate 来源页」，来源页由四页变三页（`vpngate` / `manual` / `pipeline`）。新模块 client/sources/landing.py（启动迁移：登记表迁移 + 节点库迁移，样本重映射与节点改写同一事务、异常整体 rollback，迁移只调 merge_proto 既有函数、合并口径零重写；检测口径含条目 page 残留旧页键的扩展，误触发无害）；cli.main 与 app.main 各自在 Store 构造后立即调 run_startup_migration（GUI 与 CLI 无单一共同启动函数，故两处落点）；source_registry 镜像类型废除（ENTRY_TYPES 去 feed_mirror、owned_nodes 的 feed 分支改只认登记戳、_LOCAL_EXTRA_KEYS 增 merged_superseded 使迁移快照不被 feed 刷新洗掉）、feed/manual 解析器 source 默认值改 vpngate、model SOURCES 三键、diag 文案三处改页名、app _import_actions 两分支合一并删 register_feed_mirror、cli --type choices 去 feed_mirror。merge_proto.py / classify_proto.py / orchestrator.py / store.py / history.py / verify.py / collector.py / usage_window.py / 引擎层零改动。
- 审查注记：work23 离线测试重跑 21 个全绿（skip 3：VM 无 PySide6，GUI 组如实 skip，与 DONE.md 声明一致）；wiring.diff（8 文件）+ literal-ids.diff + ui-audit-reanchor.diff 对 0.20.0 实树干跑零冲突、逐项核对在简报范围内；审查方独立复跑 client-copy 全套 928 个全绿（skip 92）与集成树全套 928 个全绿（skip 92）；此前失败的 GUI 验收 test_migrated_nodes_landed_on_merged_page 在本机 .venv-gui（QT_QPA_PLATFORM=offscreen，有 PySide6）下实跑转绿——修复轮按主会话裁决方案 A 只改测试（断言前勾上 show_banned_chk 与 show_quarantined_chk 再 refresh，另补默认视图仍过滤的小断言），不动产品、不动过滤口径、不改固件（合并节点带 banned+quarantined 是 work8 合并口径既定语义）。范围偏差一处（DONE.md §三已声明、审查通过）：tests-rewrite/test_gui_sources.py 的 test_feed_register_and_update_via_button 同类断言由「页内恰一条」改三页时代正确期望（hosts == {"f1.opengw.net", "h1"}），根因同类（四页时代测试期望没跟上三页时代），不改产品。禁区复扫干净（假数据 only：RFC 2606 域名、RFC 5737 保留 IP、伪造 .ovpn/假 b64，零真实节点/订阅/密钥；vless 夹具链接为既有测试文件原有内容）。
- 测试：新增 21 个合并落页验收测试；全套 928 个，VM 全绿（跳过 92：66 个既有 GUI + 23 个 usage_window offscreen 组 + 3 个本段 GUI）。本机同步：已补齐（2026-11-22 15:0x）：23 文件同步 + md5 双边一致、本机全套 928 全绿（skip 97）、GUI 冒烟通过；已写 INTEGRATED。集成跟进：版本针 faults.py 的 client/VERSION 针随集成由 0.20.0 改 0.21.0（REVIEW_FAIL 适配 4 既定口径），本机首跑曾因此报 2 个失败、改针后 VM/本机全绿。

## 0.20.0 - 2026-11-22
- 阶段四第八段：长稳与异常覆盖（task15 落地）——异常注入测试成体系 + 长跑原型。新模块 client/core/faults.py（纯标准库，不 import client 包）：假引擎四行为、假 probe 四行为、会话编排替身 SessionTwin（与 orchestrator.py 同形状）、健康分级同形替身、端口预检同形替身、DB 损坏工具、GAPS 登记表（9 条缺口：GAP-E1/E2/E3、GAP-D1/D2/D3/D4、GAP-N1/N2，修责段位与严重度已标）、FILE_ANCHORS 文件锚点（正向 60+ 子串 + 负向锚）；client/core/soak.py 长跑原型（假引擎干跑 + 每轮采样 /proc 子进程数/fd 数/VmRSS/DB 大小 + DESIGN §3.4 判据逐项 PASS/FAIL，`--mode real` 只写真跑手册退出码 3）。新测试 client/tests/test_faults.py 51 个（三层断言：替身断期望行为、DB 损坏断现状事实、client-dev 现状走文件锚点；GAPS ↔ DESIGN 互验；铁律反向断言：替身无 auto_* 方法、异常路径后无自发连接）。
- 跟进修复（REVIEW_FAIL 裁决·方案 A，task15 假数据撞打包白名单）：`packaging/build_deb.py` 的 `_IP_WHITELIST` 由逐个枚举改为「RFC 5737 三段文档地址按类放行（192.0.2.0/24、198.51.100.0/24、203.0.113.0/24，`_DOC_NETS`）+ 非文档段既有豁免逐条保留（`_IP_EXPLICIT`：公共 DNS 预设、回环/未指定、TUN 默认网段、Chrome UA 版本号误配形态）」，`in` 运算协议不变（`_IpWhitelist.__contains__` → `ip_allowed()`），deb_selfcheck 第 5 项与 test_packaging 独立复扫原样可用；防线本意不变（白名单之外任何 IPv4 一律判失败）。新测试 client/tests/test_whitelist.py 13 个（文档段全段放行含撞墙两地址与边界、既有豁免保留、真实 IP 必拦、两层扫描模拟双向验证）。task15 假数据默认值逐字未动（方案 B 未采用）。
- 审查注记：work15 离线测试重跑 64 个全绿（51 本体 + 13 守卫侧），与 DONE.md 声明一致——复跑时曾因 VERSION 针一时不匹配（0.19.0 实读 vs 0.20.0 针）报 2 个失败，系集成版本号未定所致，集成后 VERSION=0.20.0 即与针一致，不属交付物问题；build_deb.py 修复副本与集成版 diff 仅两处改动（`import ipaddress` + 白名单区块，docstring banner 不套回）；交付物禁区复扫干净（假数据 only：RFC 5737 文档 IP、保留端口段、/tmp 路径，零真实节点/订阅/密钥）。集成适配 5 项（REVIEW_FAIL 适配 1–5：落位路径、test_faults import 改包口径、soak 双跑法导入、faults 根目录改由本文件位置推导、DESIGN 原样落位、SOAK_VERSION_TAG 保留 0.17.0 原值；另补 test_whitelist 的 packaging sys.path 插入，与 test_packaging.py 同口径），其余用例与原件逐行一致，适配清单见各测试文件末尾。
- 未接线（待后续段，DESIGN §2 / DONE.md §5）：GAP-E1/E2/E3、GAP-D1/D2/D3/D4、GAP-N1/N2 共 9 条缺口只记录不修（修责映射见 DONE.md §4）；soak 真跑（本机集成段，真 sing-box + 真连接 + ≥2 小时）；GAP-D2 的 GUI 侧崩溃形态待本机 .venv-gui 验证；GAP-D1 修复时的备份话术待主会话定稿。
- 测试：新增 64 个长稳与异常覆盖测试（51 + 13）；全套 907 个，VM 全绿（跳过 89：66 个既有 GUI + 23 个 usage_window offscreen 组，本机无 PySide6）。本机同步：已补齐（2026-11-22 14:2x）：8 文件同步 + 整树 md5 双边 93/93 一致、本机全套 907 全绿、GUI 冒烟通过；已写 INTEGRATED。

## 0.19.0 - 2026-11-22
- 阶段五第一段：专用使用界面 U1 骨架（task16 落地）——前台主窗实现 + 现窗降后台 + 托盘迁移 + 两窗共用同一 Store/Orchestrator。新模块 client/ui/usage_window.py（类 `UsageWindow`，按落 client/ui/ 口径写：包内相对 import）：①连接状态区（恒顶置：常驻状态面板四态 未连接/连接中/已连接/已断开 + 红色告警条复用现有样式 + 当前节点行（verify.exit_display_name 实测名 + classify_proto 板块徽标 + SCENE_LABELS 场景徽标 + history.format_latency_ms 延迟）+ 主按钮行（连接/断开 primaryBtn 交互与 orchestrator 管道 + 接管方式 mode_combo 同约束：只在未连接时可切、CONNECTED 禁用、TUN 未就绪提示照搬 _check_tun_privilege 文案经 error 事件上告警条））与 ②节点选择区（七页签：classify_proto.BOARDS 六板块 + 「未分类」兜底不许硬塞；板块内排序复用 history.sorted_nodes；每行八列与后台 SourcePage.COLUMNS 口径一致；行内动作恰为 {连接、置顶、测速}——右键菜单 + 双击=连接，测速走 _test_node 同一管道（临时引擎 10818/10819 口径不变））+ 菜单骨架（打开后台 / 设置 / 诊断导出，后两项点击如实提示「尚未接线」，不假装可用）。③④⑤⑥区（U2/U6/U4/U5）零占位。七个 SessionEvent kind 每个都有呈现分支；状态变化全走告警条/消息行，不弹模态框（0.7.0 既定偏好）；托盘从后台窗迁入主窗（work10 §2.1「托盘归属主窗」）。
- 口径（DESIGN.md §2/§4，集成段必读）：app.py 集成改动 6 点（client/ui/DESIGN.md §3 清单照单执行）：① main() 装配两窗、主窗默认显示（orchestrator 在主窗建、注入后台窗）→ ③事件通道 main() 统一扇出（MainWindow.__init__ 一行不用改）→ ④菜单「打开后台」经 set_backend_window 注入 → ⑤MainWindow 标题改「gate-client · 后台（来源管理）」、启动不显示 → ②托盘迁移（_setup_tray 调用改 self.tray = None + closeEvent 改真关窗；其余 self.tray 引用全有 is None 守卫，自然 no-op）→ ⑥明确不改清单（orchestrator/来源页/健康三件套/自动探测全部留后台原样）。偏离 DESIGN.md §3 一处：UsageWindow 模块级 from .app import …，app.py 顶层 import 它会循环导入，故 main() 内懒加载（行为等价，见 app.py main() 注释）。两窗各有 5s QTimer，orchestrator.poll() 有状态守卫、双调无害；主窗 poll_once 额外做①区状态对账与②区库变更对账（count_nodes() 变了才重排）。
- 审查注记：work16 离线测试重跑 33 个全绿（系统 python：锚点组 10 个实跑 + offscreen 组 23 个 skip，VM 无 PySide6；work14/.venv-gui offscreen 真跑 33 个全绿，与 DONE.md 声明一致）；改动只在简报范围内（新 usage_window.py + client/ui/DESIGN.md + 新测试 + app.py 上述 6 点 + 版本/日志/待推清单），orchestrator.py 一行不改；禁词 grep 干净（测试内嵌断言）；app.py 其余行为文件未动。集成时测试做 4 处适配（CLIENT_DEV 兜底路径改 client-dev 口径、删 __path__ 内存挂载、锚点组改读 usage_mod.__file__、DESIGN.md 断言改读 client/ui/DESIGN.md），其余用例与原件逐行一致，适配清单见测试文件末尾。GUI 冒烟：offscreen 下 main() 装配实跑（两窗构造、标题/可见性、扇出双收事件，见本轮审查记录）。
- 未接线（待后续集成段，DESIGN §6 / DONE.md 未解决项）：主窗关窗=进托盘沿用现行 UX，work14 §5.2「前台关窗=真退出或另定」待主人裁定（改 UsageWindow.closeEvent 一处）；托盘迁移后后台窗健康探测/掉线的托盘气泡暂缺托盘可挂（告警条仍在两窗各自亮；气泡统一改经主窗托盘发，留后续段一处小改）；后台窗改 pin 后主窗②区不即时重排（主窗只在会话事件/库计数变化时重排）；「未分类」补标/改判入口归后台，主窗只展示；真机托盘气泡/字体/缩放/深色主题观感未验（本机 .venv-gui 或真机跑 GUI 冒烟复验）。
- 测试：新增 33 个使用界面测试；全套 843 个，VM 全绿（跳过 89：66 个既有 + 23 个 usage_window offscreen 组，本机无 PySide6）。集成适配另含 test_ui_audit.py 锚点行号同步：task16 改 app.py 致行号整体偏移（_setup_tray 调用处 +2 行、closeEvent 改写 -6 行，净 -4 行；460–571 行区间 +2 行），30 处 assert_anchor 行号与 ContextMenu 单点断言 545→547 按新行号逐项核对改写，断言特征串一字未动；proto/DESIGN.md 的审计表行号仍指 0.18.0 快照，不改。本机同步：已补齐（2026-11-22 13:4x）：整树 md5 双边 88/88 一致、本机全套 843 全绿、GUI 冒烟通过；INTEGRATED_VM 已换成 INTEGRATED。

## 0.18.0 - 2026-11-22
- 阶段四第七段：界面优化第一轮（task14 落地）——审计 + 原型 + 离线测试集成。新原型 proto/（PySide6，7 个文件，不 import client 包、不接真实 Store/引擎，假数据 only：RFC 5737 保留 IP、example 域、虚构名称）：状态呈现改版（状态面板与消息行分离，连接状态不再被任何消息覆盖）、来源页改版（十列口径：标记独立成列、类型中文、空值统一「—」、最近带日期、搜索框/排序档/计数贴过滤条件）、空态与待清演示（空态卡两形态 + 「删上一轮废节点（n）」动态文案 + 行内确认条替模态）。DESIGN.md（proto/ 下）审计表 24 项（挡路 2 / 难受 18 / 难看 4，每项带文件:行号落点 + 证据）+ 改造设计逐项对应 + 明确不改清单 8 项 + 铁律核查（疑似自动切换零违例）+ 「给 U1 的输入」专节（应避开的毛病 6 条、应复用资产 8 项、接线注意 4 条）。
- 口径（DESIGN §2/§5，集成段必读）：改造只动呈现与交互，不动业务语义（验真/评级/健康分级判定一律不碰）；`_show_alert`/`_show_info` 函数名与调用面保持不变；G3/G19 的序保持机制改 `populate` 签名时默认参数必须保持旧行为；G22 统一空值「—」会波及 test_gui_app 对「未验/未测/无样本」文案的断言，集成时同步改断言；自动探测 QSettings 键（auto_probe/enabled、auto_probe/interval_min）跨窗共用，U1 前台窗直接读同一键；快赢排序（可先做）：G16 改调 `history.format_latency_ms` 一行、G21 改 `currentText()` 一行、G24 显式 setText 按钮三处、G17 加 `setWordWrap(True)`+关闭钮、G9 置顶/拉黑换 `selected_node_ids()`。
- 审查注记：work14 离线测试重跑 56 个全绿（skip 19：VM 无 PySide6，原型组 skip 记原因，与 DONE.md 声明一致）；锚点逐项命中 0.17.0 树（G1–G24 正锚点 + 5 条缺失反证 + 铁律正面证据）；改动只在简报范围内（新 proto/ + 新测试 + 版本/日志/待推清单），未改任何既有 client-dev 行为文件；proto/shots/ 4 张 offscreen 自检截图不落树、只留 work14/；work14/.venv-gui（261MB）只为自检而建、非交付物。集成时测试做 4 处适配（DEV_ROOT/PROTO_DIR 路径改 client-dev 口径、sys.path 插 DEV_ROOT、DESIGN 读取改 proto/DESIGN.md、CLIENT_DEV 默认改 DEV_ROOT 使本机锚点也能实跑），其余用例与原件逐行一致，适配清单见测试文件末尾。
- 未接线（待后续集成段，DESIGN §2/§5 / DONE.md 未解决项）：G13 动作排重排建议等 U1 落地后按「后台窗」定位一次性做；G3/G19 序保持机制待 U1 前台板块视图与后台页共用 populate 管道时两边一起定；托盘/关窗行为是否给前台窗另定口径；G15 破坏性确认保留模态还是改行内确认条、G17 告警条分档配色、G8 排序档位集合、G6「最近」列日期口径、G4 一键删废节点按钮形态、G23 活动节点行高亮底色均属个人偏好待主人裁定；真机字体/缩放/托盘气泡/深色主题下 QSS 实际观感未验（本机 .venv-gui 装好后跑 `--shot` 即可复验）。
- 测试：新增 56 个界面审计测试；全套 808 个，VM 全绿（跳过 66：47 个既有 GUI/打包测试 + 19 个原型测试，本机无 PySide6）。本机同步：11 文件同步 + md5 双边 11/11 一致；本机系统 Python 全套跑完 + 1 个错误——test_packaging 的 setUpModule 在本机构建时六项自检第 5 项报非法文件形态：本机 ~/gate-client/client/ 下 8 个 `.bak-20261121-*` 备份快照被打进包（与 0.17.0 同一已知问题，见 work13/REVIEW_FAIL.md，用户已裁决硬化打包排除表、GLM 修复进行中）；其余 781 个测试全绿；本机 .venv-gui 下 `import client.ui.app` 冒烟通过。按流程暂不写 INTEGRATED 标记。追记：task13 硬化已集成（见 0.17.0 段），本机重跑全套 810 个，809 绿 + 1 个错误——test_packaging 的 GUI 回退用例在本机超时：本机 ~/.venv-gui 有 PySide6，启动器候选链命中后跑起真 GUI 常驻，用例的跳过条件未覆盖候选链 （与 .bak 问题无关，见 work13/REVIEW_FAIL.md 追记 2，等裁决）；本机 .venv-gui 下 `import client.ui.app` 冒烟通过。追记：本机全套已转绿（task13 追记 2 方案 A 修复落地），2026-11-22 13:4x 整树 md5 双边 88/88 一致、本机全套 843 全绿、GUI 冒烟通过，本标记已换成 INTEGRATED。

## 0.17.0 - 2026-11-22
- 阶段四第六段：安装包（task13 落地）——deb 设计 + 打包脚本原型 + 离线测试集成。新模块 packaging/build_deb.py（标准库 only，不 import client 包、不联网）：版本一律读 client/VERSION 拼 deb 版本（不手写）；staging 按 DESIGN 安装布局（/usr/bin/gate-client 与 gate-client-gui 双包装脚本、/usr/share/gate-client 程序本体、/usr/share/applications/gate-client.desktop）；打包期清洗 3 处原型期作者 home 路径（清洗点漂移显式报错）；SOURCE_DATE_EPOCH 固定 + mtime 归一（幂等，两次构建字节级一致）；deb_selfcheck() 六项自检（包名/版本/入口/无绝对路径/无订阅内容/脚本不碰用户数据）。DESIGN.md（packaging/ 下）逐项出处 + 复现手册 + 未解决项；测试 client/tests/test_packaging.py 27 个。
- 口径（DESIGN §3–§6，集成段必读）：安装包只管装，不管自动更新/节点优选/自动切换（铁律未触碰）；用户数据（client.db、settings.json）与 sing-box 二进制不进包；维护脚本 preinst/prerm 刻意 no-op、postinst 只做包内预编译 + 桌面库刷新、postrm 只清包内 __pycache__ 残留；purge 默认不删用户数据（包不拥有用户数据目录，dpkg 语义 + 脚本禁区双重理由）；PySide6 的 Ubuntu 包名 noble 查无 → 不编包名，Suggests python3-venv + 待本机核实；GUI 解释器候选链 GATE_CLIENT_GUI_PYTHON → ~/.venv-gui → ~/gate-client/.venv-gui → python3（无 PySide6 时 GUI 显式打印安装指引退出 2，不假装能跑）。
- 审查注记：work13 离线测试重跑 27 个全绿（skip 1：GUI offscreen 冒烟，VM 无 PySide6，原因如实记录），与 DONE.md 声明一致（ZCode 对话里一条旧消息误写 22 用例，以文件实测 27 为准）；.deb 原型 gate-client_0.16.0-1_all.deb（132,820 B，sha256 a4482a2df31c49ba…）dpkg-deb -f/-c/-e 实测与 DONE.md 一致；staging 树与 client-dev client/ 树 diff 仅 2 个清洗文件不同（3 处清洗点）；zipapp 旧路线产物已清（无 .pyz/build.py 残留），zipapp/PyInstaller 只在 DESIGN §7/§8 各留一小节存档；改动只在简报范围内（新 packaging/ 目录 + 新测试 + 版本/日志/待推清单），未改任何既有 client-dev 行为文件。集成时测试做 4 处适配（路径改 client-dev 口径、版本号去写死、安装模拟落 client-dev 根内、自审路径改 Path(__file__)），其余用例与原件逐行一致，适配清单见测试文件末尾。
- 未解决项（DONE.md / DESIGN §11，待本机与后续段）：图标缺失（.desktop Icon= 空缺，红线不许现编）；PySide6 Ubuntu 依赖包名待本机核实；maintainer scripts 待隔离环境 dpkg -i 实装验证（升级/卸载/purge 三种路径）；手工安装迁移步骤待本机实测；GUI offscreen 冒烟 VM 未真跑（本机 .venv-gui 下全套运行时自动生效）；deb 真实安装语义（dpkg -i）未验证；python3 下限 3.7 为语法实测，运行实测仅 3.12.3。
- 测试：新增 27 个打包测试；全套 752 个，VM 全绿（跳过 47：46 个 GUI 测试 + 1 个打包 GUI 冒烟，本机无 PySide6）。本机同步：集成时本机在线，5 文件同步 + md5 双边 5/5 一致；本机系统 Python 全套 725 个跑完 + 1 个错误——test_packaging 的 setUpModule 在本机构建时六项自检第 5 项报非法文件形态：本机 ~/gate-client/client/ 下 8 个 .bak-20261121-* 备份快照（VM 树没有）被打进包，形态防线拦下；其余五项自检全过、其余 725 个测试全绿；.venv-gui 下 `import client.ui.app` 冒烟通过。按流程不写 INTEGRATED 标记，问题与候选修复（清本机备份 / 硬化打包排除表）见 work13/REVIEW_FAIL.md，等裁决后补做。
- 打包器硬化（2026-11-22 REVIEW_FAIL 跟进，用户裁决方案 2，不删本机备份，版本号不动仍 0.17.0）：staging 排除表新增编辑器备份/临时文件形态（`*.bak*`、`*~`、`*.orig`、`*.rej`、`*.swp`/`.*.swp`、`*.swx`/`.*.swx`、`*.tmp*`、`#*#`、`.DS_Store`），只作用于 staging 副本、源树原件一律不删不移；文件形态自检保持原样，真正非法的数据文件仍被拦下（新增回归用例注入 `nodes_export.json` 验证防线仍有牙）；新增夹具回归测试 2 个（12 个 decoy 注入临时夹具树：staging 零混入、真文件零误伤、deb 照常打出且六项自检全过）；三处写死 0.16.0 的版本断言改动态读 client/VERSION。work13 离线测试重跑 29 个全绿（skip 1：GUI offscreen 冒烟）；集成后 VM 全套 810 个全绿（跳过 66）。本机 8 个 `.bak-20261121-*` 备份保留不动，打包行为对树内备份文件从「炸包」变「预期内排除」。追记 2 修复（方案 A）已落位：`test_extracted_gui_fallback_without_qt` 子进程 HOME 指空临时目录并 unset `GATE_CLIENT_GUI_PYTHON`，只改测试、不动产品，断言不变（退出 2 +「缺少 PySide6」）；work13 离线测试重跑 29 个全绿（被改用例实跑通过、非 skip），VM 全套 843 个全绿（跳过 89）。

## 0.16.0 - 2026-11-22
- 阶段四第五段：设置页（task12 落地）——设计 + 统一设置模型原型 + 离线测试集成。新模块 client/core/settings_proto.py（纯标准库，只 import ipaddress/dataclasses，不 import client 包、不依赖 PySide6、不联网）：`Settings` frozen dataclass（10 字段，默认值=现行行为：SOCKS 7898 / HTTP 7897、数据目录 ~/.local/share/gate-client、自动探测开/10 分钟、UsageSettings 五项镜像；`__post_init__` 校验，非法抛 `SettingsError`）；`to_dict`/`from_dict`（缺字段补默认、未知键抛错不静默丢）；`merge(base, override)`（只覆盖显式字段）；`usage_dict()`（五项子集，供集成段构造 `UsageSettings`）。
- 口径（DESIGN §3–§6，集成段必读）：与 UsageSettings 是包含关系——五项字段定义逐字拷贝自 usage_model.py（0.15.0 快照），常量/默认值/校验/错误类型族全部等价，集成期改为 import 消镜像（DESIGN §8-2），最终代码树里字段定义只有 usage_model 一处；评级阈值不进设置页（改=改评级结果，DESIGN §3.3-1，只读展示与否待主人裁决）；持久化定 ~/.local/share/gate-client/settings.json（与 client.db 同目录、不另起目录，version 包裹层=1，文件名待主会话确认）；设置页里 UsageSettings 五项只出现一次（唯一编辑入口），使用界面③区复用同一份值；端口校验须把保留段 10818/10819/10828/10829 列为拒绝（原型未硬编码，集成段落地）；QSettings 迁移只导 auto_probe/enabled 与 auto_probe/interval_min 两键，last_node_id 不迁移。
- 审查注记：work12 离线测试重跑 46 个全绿，与 DONE.md 声明一致，口径一致组 6 个实跑未跳过；DESIGN 锚点抽查属实（singbox_engine.py:54-55 端口、cli.py:46 DEFAULT_DB、usage_model.py:167-171 五项、app.py:438-445 自动探测默认开/10 分钟、525 schedule_startup_probe、780 _maybe_auto_probe 首行开关 gate）；两份代码交付物禁词 grep 为空（仅 DNS 预设公共解析 IP 8.8.8.8/1.1.1.1/9.9.9.9，系 usage_model 同款口径）；改动只在简报范围内（新模块 + 新测试 + 版本/日志/待推清单），未改任何既有 client-dev 行为文件。DONE.md 行号小偏差（schedule_startup_probe 误标 494、_maybe_auto_probe 误标 781，实为 525/780）不影响结论。
- 未接线（待后续集成段，DESIGN §8 / DONE.md 未解决项）：引擎构造点改读设置（cli.py:271、app.py:363 等接线点已列）、端口检查兜底点、路径优先级 --db > GATE_CLIENT_DB > settings.data_dir > 内置默认、生效时机（自动探测立即/端口与规则五项下次连接/data_dir 下次启动）、TUN mtu/stack 与 diag 导出位置是否开设置项（均未接线）、auto_probe 间隔 UI 四档 5/10/20/30 是否放开。
- 测试：新增 46 个设置模型测试；全套 725 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

## 0.15.0 - 2026-11-22
- 阶段四第四段：界面板块分类（task11 落地）——设计 + 分类判定原型 + 离线测试集成。新模块 client/core/classify_proto.py（纯标准库，不 import client 包、不依赖 PySide6、不联网）：`classify_exit(isp_name, org)` 出口性质三态判定（只认验真 ISP 字符串，无数据或未命中一律 unknown，绝不猜）；`dims_of` 三维推导（来源性质/出口性质/接入形态，含 LEGACY_SOURCE_KEYS 兼容与 extra.nature / exit_kind_override 改判）；`board_of` 六板块组合（P1 pipeline > P2 openvpn > P3 性质×出口，缺维落 unclassified）；`classify_library` 整库只读分类（计数 + 未分类清单带缺维原因）。板块是归类不是排名，不含优选/排序/切换语义。
- 口径（DESIGN §2–§9，集成段必读）：exit_kind 一律重算 classify_exit，不直接取 ExitProfile.network_type（后者含 verify.classify_network 的 host 启发段，按名字猜是铁律禁区；两口径差异在迁移时以新口径为准并出差异清单）；extra 三键（nature / exit_kind_override / collected_from）必须在一切既有管道中存活，丢一处就静默丢改判与标注；板块键/标签唯一字典是 BOARDS / BOARD_LABELS，前台后台不许另定一套；verify.classify_network 与四场景判定维持现状（是否统一留 U2 待主人+集成段裁决）。
- 审查注记：归类表与 verify.py 关键词表同源校准但非逐字相同——机房表 27/27 词中 proto 用 "HURRICANE ELECTRIC" 替 "HURRICANE"、漏 "LEASWEB" 拼写变体；住宅表 48 词中漏 "AU COMMUNICATIONS / BREEZE / BT OPEN / CLEVER-NET / KT / SEIKO" 6 词、另补中国主流 ISP 与 "NTT / TELEKOM" 等宽口径。漏词方向一律偏保守（落 unknown 而非错判），符合「未知不许猜」；逐词对齐留待后续集成段。
- 未接线（待后续集成段，DESIGN §10 / DONE.md 未解决项）：D1 pipeline 与 openvpn 板块优先级（现 P1>P2，主人一句话可翻）；U2 场景判定 residential 是否切 classify_exit 口径；U3 登记条目 nature 必填还是默认 free；U4 复查反证时是否自动清除人工改判；U5 classify_exit 是否并入 ip-api hosting 布尔位；前台板块视图与后台来源页徽标/改判入口（work10 U1 依赖由本设计解除，落点见 DESIGN §9）与登记表 nature 字段扩展均未接线。
- 测试：新增 46 个分类模型测试；全套 679 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机夜间离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

## 0.14.0 - 2026-11-22
- 阶段五第一段：专用使用界面总体设计（task10 落地）——设计 + 规则配置模型原型 + 离线测试集成。新模块 client/core/usage_model.py（纯标准库，只 import ipaddress/re/dataclasses，不依赖 PySide6、不 import client 包、不联网）：`UsageSettings`（frozen dataclass：分流模式 global/rule/direct、广告拦截、恶意拦截、IPv6、DNS 选择；非法条目 UsageSettingsError 报错不静默吞，含「IPv6 关时拒 v6 DNS」交叉校验）；`build_route_config` 产出 sing-box 路由/DNS 片段 + 合并顺序提示（insert_before=resolve，先拦截后解析）+ dns_patch（只写回 remote-dns 的 server 与 resolve 的 strategy 两个字段，detour/final 防泄漏口径不动）+ 显式 uncovered 清单（9 项，绝不假装生成）；规则集条目一律带 `prototype_placeholder` 占位键。
- 口径（DESIGN §4–§6，集成段必读）：合并顺序契约——usage_model 片段插在既有 resolve 规则之前；TUN 场景 tun_config 的 sniff/hijack-dns 保持最前置；direct 模式才改 route.final 并补 direct-out 出站，global/rule 模式单节点单出站语义不动；网站黑名单 reject 条目全模式生效、direct 条目仅 rule 模式生成（其他模式显式跳过并写 notes），同域名双处置在 parse 层报错；容灾/自动替换口径已定（备用组预先定多节点、只换红色节点、活节点永不按延迟切换、总开关默认关），全库禁词 grep 为空。
- 盘点结论（DESIGN §2，17 项全指到文件/函数）：已有 2（节点黑名单=banned 全链路、订阅导入四格式）、部分有 9、没有 6（容灾、广告拦截、自动替换、应用净化、恶意拦截、分流模式）；增量全部可复用既有 core 层，orchestrator 铁律结构一行不改。分段计划 U1–U7（DESIGN §12）：U1 骨架与六板块（依赖 task11）→ U2 分流/DNS/IPv6/黑名单消费（usage_model 集成 + 引擎 merge 函数）→ U3 拦截规则集 → U4 测速与速度展示 → U5 特殊网站测试 → U6 容灾/自动替换 → U7 应用净化（有条件）。
- 未接线（待后续集成段，DESIGN §13 / DONE.md 未解决项）：规则集真条目来源 URL 本设计一个没编（待主人核定，换真条目时删占位键即视为主人核定完成）；A/B/C 三个主人点头项（脏是否自动拉黑 / 白名单变脏处置 / banned 恢复是否自动放回）；D（sing-box Linux process_name 路由是否成立）、E（rule_set 远端条目字段形状）、F（TUN+IPv6）、G（吞吐测速载荷 URL）、H（名单复查跨进程常驻调度）、I（task8/task9 集成后才闭环）；task8 合并、task9 搜集在 0.13.0 均为「已落地未接线」，本设计的「三来源页」口径以 task8 集成为前提。
- 测试：新增 37 个规则配置模型测试；全套 633 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机夜间离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

## 0.13.0 - 2026-11-22
- 阶段四第三段：免费节点自动搜集（task9 落地）——设计 + 原型 + 离线测试集成。新模块 client/sources/collector.py（纯标准库，fetch/parse/时间全注入，不 import client 包、不联网）：健康账本 load_ledger / save_ledger / record_result / pick_probation（R0–R8：初始权重 1.0、成功 +0.2 封顶 1.0、失败 −0.25、连续 3 败停用、活而空每 3 轮 −0.2、停用冷却 7 天后每轮至多 1 个缓刑复测、手动停用永不自动复用）；extract_candidates（markdown 链接 / html href / 纯文本 URL 三形态，只收 http(s)，去重保序）；run_collection（整轮编排，任何单点失败吞进报告、整轮永不中断、零产出轮如实报告）。
- 口径（DESIGN §0 写死）：搜集止步候选池，候选未经 `verify_exit` 验真且 `pollution_of` 未污染，绝不进正式库；身份去重按登记制 `make_node_id`（先出现者胜），候选地址级按归一化 URL 跨种子去重、首次发现者计功；入库记录 extra 四溯源键（collect_seed_id / collect_seed_category / collect_round / collected_from）；倾向「节点入库、地址只在健康账本留痕、不自动转登记表」，保住「登记过=主人认过」的信任边界。
- merge_fresh 的坑（DESIGN §5，集成段必读）：对现有库命中身份只借 `merge_fresh` 的字段合并口径（保 pin/ban/出口画像/实测指标/orig_name），**不打** `registry_entry` 戳——打了以后 `owned_nodes` 会把没人登记的地址的节点误判归属。
- 未接线（待后续集成段，DESIGN §7.3 / DONE.md 未解决项）：种子清单（各类别具体地址由主会话定，本设计不编造任何真实网址）、转登记门槛数字、CLI/GUI 接线（建议 `sources collect`）、collect_ledger 最终路径、候选池验真的批量节奏与上限、自动搜集开关与定时器（本段只做手动一键，定时只做设计预留）。
- 测试：新增 17 个搜集模块测试；全套 596 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机夜间离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

## 0.12.0 - 2026-11-22
- 阶段四第二段：OpenVPN 与 VPNGate feed 来源合并（task8 落地）——设计 + 迁移原型 + 离线测试集成。新模块 client/sources/merge_proto.py（纯标准库，只用 copy/re，不 import client 包、不联网）：合并后 canonical 来源键 `vpngate`（旧 `openvpn` 与 `vpngate_feed` 两键退役，协议/接入形态降为节点字段）；`merge_records` 按 DESIGN §3 逐字段合并同身份记录（验真出口取新者＋dirty 并集、实测延迟 None≠0、feed 报告指标 feed 侧为准、钉选/封禁布尔或、配置 feed b64 为主＋非主方整份进快照）；`migrate_library` 整库迁移（改键、重算 id、按身份去重合并）；`sample_id_map` / `remap_samples` 样本表 node_id 重映射；`migrate_registry_json` sources.json 登记表迁移（feed_mirror 类型废除、两入口合一）。
- 无损机制：两份完整旧记录 deep copy 进 `extra["merged_superseded"]`（键＝旧记录 id）——无损由结构保证，不依赖规则正确；迁移幂等（连跑两遍 JSON 深等价，`merge_records(r, r) == r`）。测试证明：44 个用例、假数据 only（RFC 2606 域名、RFC 5737 保留 IP、伪造配置），逐项覆盖验收口径（来源只剩合并键、节点数＝身份并集一台不丢、验真出口/样本/钉选/封禁/OpenVPN 配置合并后都在、样本重映射不丢行、登记表迁移与幂等）。
- 未接线（待后续集成段，DESIGN §8 清单）：GUI/CLI/解析器落页改动与既有测试重写（`test_same_endpoint_feed_vs_ovpn_are_two_records` 行为反转等）；`_LOCAL_EXTRA_KEYS` 增补 `"merged_superseded"`（不加则首次 feed 刷新洗掉快照）；`owned_nodes` feed 分支改戳归属（不加则手导 .ovpn 被卷进消失判定）；启动时一次性迁移触发（检测 sources.json 含 feed_mirror 或库内存在旧两键）。本段只落原型模块与测试，不改 GUI/CLI/登记行为。已知边界（DONE.md §5）：自定义镜像地址采纳后失去「官方失败回退」（集成期可给条目附加备用地址字段，属新 schema 决策）；IPv6 host 的 id 段歧义是上游既有边界；逐字节相同重复记录的防御路径下旧键旧 id 字符串不进快照（内容零丢失）。
- 测试：新增 44 个合并迁移模块测试；全套 579 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机夜间离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

# gate-client 更新日志

版本号规矩：主版本.次版本.修订。里程碑（M1/M2/M3…）升次版本，缺陷修复与小改进升修订。每次发布在本文件顶部加一段，写明日期与改动要点；代码入库以本文件与 VERSION 为准。

## 0.11.0 - 2026-11-22
- 阶段四第一段：日志导出与一键诊断（task7 落地）——设计 + 脱敏原型 + 离线测试集成。新模块 client/core/diag.py（纯标准库，与 health.py 同独立性口径）：redact_text / redact_node_fields / run_checks（9 项纯判读）/ build_bundle（含 MANIFEST）+ mask_host / mask_url / mask_node_id / sweep_files 掩码基元；SingBoxEngine 加只读访问器 active_log_path()（无活动会话返回 None），供导出取引擎日志尾部。
- 脱敏口径（DESIGN §3，代码与文档一致）：凭证类 extra 键（password/uuid/username/token/secret/auth）与 config_ref / extra[\"clash\"] / extra.query 整项剔除、extra 未知键默认剔除；host 与出口 IP 掩码留首尾段、订阅地址只留掩码后域名；三层兜底（结构剔除 → 文本清扫 → 写包前 build_bundle(secrets=…) 再扫一遍）。测试证明：含假密码/uuid/订阅地址的材料走完整管线写 zip，解开后无任何假 secret 原文（含大小写变体、userinfo、token），另有阳性对照证明掩码形态确实在包里。
- 未接线（待后续集成段）：材料收集器 diag_collect.py、CLI doctor / export-diag、GUI 菜单动作尚未实现——本段只落原型模块、访问器与测试，不改 GUI/CLI 行为。已知边界（DONE.md §5）：OpenVPN 拨号日志不可导（orchestrator 未传 log 事件）；健康巡检样本只在内存，离线导出无历史样本；编码变体（b64/URL-encode）不识别，靠结构剔除兜底；mask_host 默认留首段，私有服务器建议 strict=True 全掩。
- 测试：新增 64 个 diag 模块测试 + 4 个 active_log_path 访问器测试；全套 535 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。本机同步待补：集成时本机夜间离线，同步 + md5 双边核对 + 本机测试顺延（INTEGRATED_VM）。

## 0.10.0 - 2026-11-22
- 阶段三第二段：TUN 模式（task6 落地）——设计 + 配置生成原型 + 离线测试集成。引擎新增接管方式属性 `takeover_mode`（默认 proxy，现行系统代理）；tun 模式时 build_config 尾部并入 tun inbound（gate-tun / 172.19.0.1/30 / mtu 1500 / stack system / dns_mode hijack / auto_route+strict_route）与 DNS 防泄漏规则（route 前置 sniff + hijack-dns，dns 规则按 tun-in 指到现行 remote-dns 并压 ipv4_only，auto_detect_interface=true）；现行 DNS 段、route.final 与单节点单出站语义零改动；orchestrator 一行不改。
- 权限模型（明确结论）：建 TUN + 改路由都要 CAP_NET_ADMIN；v1 推荐 pkexec + root wrapper（wrapper 监听 stdin-EOF 联动停机，解用户态 stop() 对 root 进程 SIGTERM 会 EPERM 的坑），v2 目标独立特权 helper + fd 递回，setcap 不推荐（能力扩散 + 升级后 xattr 静默丢失）。「普通用户点一下就能开」做不到的部分（授权弹窗免不掉、无 polkit 环境、缺 tun 模块、headless）已如实列清，不假装能做。
- 激活未就绪、显式拒绝：提权启动器（pkexec + root wrapper）尚未实现，`start()` 遇 tun 模式直接报「TUN 模式尚未就绪」，不静默回退到系统代理模式（审查方裁决：取 DESIGN §2.4 主方案，询问式回退备选未采用；CLI 打印本错误并提示手动改用系统代理模式）。GUI 模式控件只在未连接时可切、连接中禁用；CLI 增加 `connect --mode tun` 与 status 接管方式一行。
- 健康衔接：三件套原样适用；检测②系统代理冲突在 TUN 模式换装文案（回环流量不经 tun，仍是真冲突）；新增检测④接管生效性：TUN 连接后每 30s 只读查 /proc/net/route，默认路由未接管到 gate-tun 只告警、不修路由、不重连。
- 验证：本机 sing-box 二进制对真实引擎生成的 TUN 合并配置跑 `sing-box check -c`（见集成记录）；真实流量连接与 11 条待真机核实项（DESIGN §9：dns_mode 实测、strategy legacy 化、stack 废弃时点、strict_route 共存性、172.19 段与 docker 冲突抽查、mtu/sniff 真机调参、wrapper 三类退出路径、file_descriptor、auto_redirect）仍待真机。
- 测试：新增 49 个 tun_config 模块测试 + 7 个接管生效性测试 + 9 个引擎/CLI 接线测试 + 7 个 GUI 测试；全套 467 个，VM 全绿（跳过 46 个 GUI 测试，本机无 PySide6）。
- 版本号说明：本段为阶段三第二段，按「新段升次版本」取 0.10.0；原路线里 0.10.0 曾预留给阶段四（产品化），阶段四顺延至 0.11.0，待用户确认。

## 0.9.0 - 2026-11-22
- 阶段三第一段：连接健康三件套（task5 落地）——①引擎端口占用预检：连接前（GUI/CLI）与引擎启动兜底（SingBoxEngine.start()，孤儿清扫后）双关，被占直接报人话错误、不发起注定失败的启动；②系统代理冲突判读：GNOME gsettings 只读，manual 指向别家只亮告警条、不拦截连接；③连接后每 15s 出口探测分级 ok/suspect/dead（末尾连续失败 3/5 次），suspect 进状态栏、dead 进告警条+托盘，系统代理每 30s 复查、冲突解除自动撤条。只提示、永不自动切换节点/重连（铁律）；orchestrator 一行不改。
- 接线：新模块 client/core/health.py（纯标准库）；GUI 连接预检 + 开窗体检 + 巡检子周期（复用 5s 定时器，不新增定时器；探测走后台线程、不占 _busy）；CLI connect 预检（被占退出码 2）+ status 两行体检。
- 测试：新增 47 个健康模块测试 + 12 个接线测试（引擎守卫、CLI 预检/体检、GUI 预检/探测落地）；全套 395 个，VM 全绿（跳过 39 个 GUI 测试，本机无 PySide6）；本机系统 Python 全套 395 全绿（跳过 38）、.venv-gui 下 GUI 测试 43 个全绿、界面 import 冒烟通过。
- 集成时修掉两处测试端口与本机 ZCode 中继的冲突：测试冷端口 18089/18090 → 18093/18094（18089 正被 ZCode 中继占用），test_cli_verify_proxy 自定义端口 18089 → 18095；守卫本身按设计工作，是它先报出了这次占用。
- 已知边界：gsettings 真机格式未核对（沙盒无 GNOME，解析已按 GVariant 多形态防御并测试）；15s/30s 探测节奏未在真机隧道上实测；dead 之后是否需要更强持续提醒待用户反馈；健康样本不落库（kind="health" 不污染养鱼池统计）。

## 0.8.0 - 2026-11-22
- 阶段二收尾段：引擎侧消费补齐（task4 落地）——Clash 直导入的 vmess（按 extra[\"clash\"] 字段造出站，tcp/ws/grpc/h2 传输、tls/reality）、hysteria2、tuic、wireguard 与带插件的 ss 终于能被引擎拉起，不再是导得进、连不上；ss 插件只认 obfs-local 与 v2ray-plugin，其余插件名显式报错、不静默丢弃。
- wireguard 按 sing-box 1.13+ 口径走 endpoints（wireguard 出站已移除，配置里写出站会加载失败），tag 仍为 node-out，route.final 指向它；SINGBOX_PROTOCOLS 扩到七协议，命令行与界面接线随之覆盖新协议。
- 验证：本机真有 sing-box 二进制，`sing-box check -c` 对全协议生成配置校验通过；但真实流量连接尚未验收，wireguard endpoint 与 route.final 的实际行为仍待真机连通验证。
- 已知边界：hy2 端口跳跃 ports、tuic reduce-rtt、v2ray-plugin 部分 SIP003 参数未映射（口径未核实、宁缺勿猜）；hy2/tuic 链接导入解析仍缺（manual.py 不解析 hy2://、tuic://）；wireguard reserved 透传不做格式预检；hy2 up/down 解析不出正整数时静默不写键、无显式提示。
- 测试：新增 45 个（引擎补齐测试，含 sing-box check 1 个在本机真跑通过）；另更新 2 个旧测试口径（四协议元组→七协议、接线键集合→八协议）；全套 336 个，VM 与本机双侧全绿（本机首跑曾有 1 次未复现偶发失败，重跑两次均全绿）。

## 0.7.1 - 2026-11-21
- 修复 vless 字段翻译缺口（全库排查实测出的 17 个引擎启动失败）：flow 的 Xray 变体 xtls-rprx-vision-udp443 翻译为 sing-box 的 xtls-rprx-vision（其余 flow 原样透传、未知值让内核显式报错）；utls 指纹只写 sing-box 认的集合，mihomo 的 unsafe（本义不伪造指纹）与未知值改为不写 utls 块，缺省仍是 chrome。
- 测试：新增 4 个（flow 变体/透传、unsafe 与未知指纹、缺省 chrome）；全套 291 个。

## 0.7.0 - 2026-11-21
- 删除大改（用户亲点）：节点表支持多选（Ctrl/Shift），「删除」一次删选中的全部，连接中的自动跳过，只弹一次确认。
- 新增「一键删废节点」按钮：删当前页里最近验真失败（不通）和已污染的节点，删前先报数确认；拉黑的不算废节点，不碰。
- 测本页 / 一键全测调整：跑完会收集本轮测出的不通与污染名单并记下，状态栏提示，点「一键删废节点」即删这批，不用回头逐个找（不弹模态框打断）。
- 测试：新增 GUI 删除/清理测试 4 个；全套 287 个。

## 0.6.0 - 2026-11-21
- 来源登记制（task3 落地）：可续期的节点来源终于有地方记了——订阅地址、VPN Gate feed、feed→OpenVPN 镜像都能登记，一键更新按节点身份合并：已验出的出口/延迟/样本全保住，新节点进来、源里消失的只打标记不删。登记表存库旁边的 sources.json，GUI 与命令行共用同一份。
- 界面：每页多了「更新已登记来源」；feed 页「登记并更新 feed」；OpenVPN 页「登记 feed 镜像」；订阅地址导入后自动登记，下次不用再贴地址。
- 命令行：`sources list/add/update/update-all/remove/enable/disable`（列表只显示域名、不回显订阅地址全址）。
- 集成时修掉一处原型与接线的不一致：UpdateReport.summary 是方法不是属性，接线处改为 summary() 调用（CLI 测试抓到）。
- 测试：新增来源登记模块测试 28 个、CLI 来源命令测试 7 个、GUI 来源测试 5 个；全套 283 个。

## 0.5.1 - 2026-11-21
- 紧急修复：v0.4.0 起图形界面打不开（点图标无反应）。根因：Clash 直导入给 subscription.py 加了顶层 PyYAML 依赖，而界面专用的 Python 环境（.venv-gui）没装 PyYAML，程序在导入期直接崩溃、窗口根本起不来；本机系统 Python 有该库，故同步前的系统测试没照出此问题（全新环境验收漏项）。
- 修复：PyYAML 与 clash_import 改为懒加载（只在真导入 Clash YAML 时才加载）——界面启动不再依赖任何可选格式库，缺库最多让该格式导入降级，绝不再拖垮整个程序；同时已给 .venv-gui 补装 PyYAML 6.0.4，Clash 导入在界面内亦可用。
- 验证：界面专用环境导入通过、GUI 测试 22 个全过；全套 243 个测试在本机复跑通过（期间有 1 次未复现的偶发失败，重跑两次均全绿）。

## 0.5.0 - 2026-11-21
- 阶段二第二段：订阅格式统一识别导入（新模块 sources/format_import.py，ZCode 原型、云曦审查集成）。订阅导入（文件与地址）现走统一入口：b64 整段、明文链接列表、txt 文件、Clash YAML 四格式自动识别；修掉旧启发式的三个实测失败模式——b64 与明文混排不再整块丢失（两组分开解、合并去重）、注释头不再挡住 base64 识别、`$` 开头的行不再被整行跳过。识别不了的内容现在明确报错（「无法识别导入内容」），不再静默返回 0 节点。
- 已知边界：纯字母数字短行可能被标成 mixed（不影响导入正确性）；多段 b64 被乱码行隔开时不逐段恢复；流程式（JSON 风格）YAML 不认作 Clash；vmess:// 链接的 base64 JSON 载荷仍不解析（直导入 Clash YAML 可绕开）。
- 离线测试增至 243 个（新增 format_import 31 + CLI 混合导入 1；另更新 1 个旧测试口径：垃圾文件现为显式报错）。

## 0.4.0 - 2026-11-21
- 阶段二第一段：Clash YAML 直导入（新模块 sources/clash_import.py，ZCode 原型、云曦审查集成）。订阅文件导入自动识别 Clash 配置（顶层含 proxies 列表），七协议（ss/vmess/vless/trojan/hysteria2/tuic/wireguard）逐字段直入 Node，不再绕分享链接：老转换器丢的 ss plugin/plugin-opts、trojan grpc serviceName 与 fp/alpn、vless/vmess 的 alpn/allowInsecure、ws early-data 全部保住；每条原条目另存 extra["clash"] 零丢弃保真。hy2/tuic/wireguard 从「直接跳过」变为可入库（引擎出站未实现，启动时明确报错）。proxy-groups 一律不导入（铁律：无自动切换）。
- 已知边界：直入的 vmess 节点引擎暂不能启动（引擎 vmess 出站只认链接 JSON，待阶段二引擎侧改 extra 取参）；ss plugin 引擎未实现，字段已保真待引擎补。
- 离线测试增至 211 个（新增 clash_import 19 + 订阅路由 3）。

## 0.3.2 - 2026-11-21
- 延迟显示人性化（用户亲点）：1 秒以内显示毫秒数，超过 1 秒换算为「5.7 秒 / 23 秒」，不再出现 5745ms、23147ms 这种裸数字；验出口成功消息同口径。
- 新增删除功能（用户亲点）：工具栏「删除」按钮，删前弹确认、正在连接的节点拦截；底层真删节点及其全部测速样本（此前只有拉黑，库只进不出）。
- 离线测试增至 189 个。

## 0.3.1 - 2026-11-21
- 引擎生命周期护栏：进程异常退出后遗留的 sing-box 子进程会被下次启动时自动回收（按 owner 存活判定，含僵尸进程处理），不再出现残留引擎占端口。
- GUI 记住上次连接的节点：连接成功后记录，下次打开自动选中该节点所在页（只选中，不自动连接）。
- 默认端口改为 7897（HTTP）/ 7898（SOCKS），接替原 Clash 位置，既有软件无需改配置。
- 离线测试增至 180 个。

## 0.3.0 - 2026-11-20（M3）
- Linux 图形界面（PySide6）：四来源分页、场景过滤、大连接按钮、红色告警条、系统托盘、5 秒状态轮询。
- 实时延迟/连通性监控（自动探测，数据落 verify_samples，列表显示出口/延迟/稳定性/最近结果）。
- 批量测速：「测本页」与「一键全测」。

## 0.2.5 - 2026-11-20（M2.5）
- 按真实出口重命名节点（国家 · 出口IP · 运营商），原名保存在 extra。
- 脏出口隔离（quarantine）；restore-names 命令；backfill 回填。

## 0.2.0 - 2026-11-19（M2）
- 订阅导入；sing-box 引擎接入（官方版 + lx 分叉支持 xhttp）。
- 真机验收通过：253 个唯一节点入库，115 个验出真实出口、0 污染。

## 0.1.0 - 2026-11-19（M1）
- 核心状态机 + SQLite 存储 + CLI；OpenVPN 引擎；验真出口流水线。
