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
