# task15 DESIGN：长稳与异常覆盖（阶段四·产品化 第八段）

- 基线：client-dev `client/VERSION` = **0.17.0**（2026-11-22），全套离线测试 752 个。
  本文所有「现状」均按 0.17.0 的文件实测落点给出（文件 + 函数 + 行号），
  行号以 0.17.0 快照为准，后续版本漂移以函数名为准。
- 范围：本段**只补测试与脚本，不改产品行为本体**。测出的行为缺口逐条
  如实记录（§2 缺口列），留给后续段修；原型与测试不替产品打补丁。
- 依据：既有离线测试（752 个）以正常路径与参数边界为主，异常路径零星；
  真机事故有据可查：0.7.1 引擎字段翻译缺口 → 17 个节点引擎启动失败；
  0.5.1 GUI 启动期可选依赖缺失打不开窗；0.9.0 集成记录：测试冷端口
  18089/18090 与本机 ZCode 中继撞车；0.17.0 本机同步记录：打包自检拦下
  8 个 `.bak-20261121-*` 备份快照。

## 1. 期望行为总口径（所有异常类共用，测试反向断言的根据）

1. **异常一律显式**：报错/亮告警，给人话原因；不静默吞、不假装成功。
   吞错必须满足两个条件之一才算豁免：代码注释声明理由且失败不影响用户
   数据与会话状态（如清扫兜底），或失败被降级为「未知字段」而非「成功」。
2. **会话状态机干净**：引擎死亡（启动失败/中途退出/stop 后残留）后，
   会话状态必须回到未连接（IDLE），不留半开隧道、不留脏句柄。
3. **不丢已有数据**：任何异常路径不得覆盖/清空用户既有数据（节点库、
   样本、pin/ban 标记）；打不开就显式报错拒开，绝不静默新建空库顶替。
4. **铁律（反向断言）**：**绝不因异常自动切换节点或自动重连**——异常
   处置的终点永远是「回 IDLE 等用户动作」。测试要反向断言：异常发生后
   不存在任何自发的新连接尝试；编排替身不存在 connect_best / connect_next /
   auto_switch / auto_reconnect 之类方法。
5. **健康分级不修故障**：探测/巡检只分级呈现（ok/suspect/dead），只提示，
   不自动断开、不自动重连、不改系统代理、不修路由（0.9.0/0.10.0 既有口径）。

## 2. 异常分类表

三张表逐项给：注入点 / 现状（0.17.0 实测落点）/ 期望 / 缺口。
缺口编号 GAP-*；每条缺口标注**修责段位**（按模块归口，不改产品行为本体）。
状态列：**达标**（现状=期望，无缺口）/ **GAP**（现状≠期望）/ **豁免**
（现状吞错但满足 §1-1 的豁免条件，如实记录理由）。

### 2.1 引擎异常（对照 engines/singbox_engine.py、core/orchestrator.py、core/health.py）

| # | 注入点 | 现状（0.17.0 实测） | 期望 | 缺口 |
|---|--------|---------------------|------|------|
| E1 | sing-box 进程**启动失败**：二进制缺失 / 配置生成失败 / 端口被占 / Popen OSError / 进程早退 / 就绪超时 | 全部显式 EngineError，不假装成功：二进制缺失 → `start()` 抛 `_missing_binary_error`（singbox_engine.py:831-833，人话含安装指引）；缺字段 → `build_config` EngineError 点名缺哪个字段（:181-193 等）；端口被占 → 启动前 `check_ports_free` 兜底直报人话（:843-847，health.py:46-65 `check_ports_free`、:172-182 `describe_occupied`）；Popen 失败 → 清临时目录再抛（:863-867）；进程早退/就绪超时 → `_fail_start`（:877-891, :922-931）读日志尾巴（:933-941）带尾巴抛错、停进程、删含凭证的临时目录。orchestrator 层 `connect()` 捕 EngineError → 落样本 connect=False → 发 `error` 事件，状态保持 IDLE（orchestrator.py:107-109） | 与现状一致 | **达标** |
| E2 | 引擎**运行中途退出**（proc 死、handle 还在） | `status(handle)` 按 poll 诚实报 "stopped"（singbox_engine.py:1140-1144）；orchestrator `poll()` 只在 CONNECTED 时查（orchestrator.py:145-160）：非 running → teardown、回 IDLE、清 active_node_id、落样本 drop=False、发 `dropped` 事件，文案「节点掉线，已停在原地（不会自动切换）」（:160）。检测节拍由接线层驱动（GUI 复用 5s 定时器；CLI 是单次进程形态，连接验证完即断开，ui/cli.py:330-332，无常驻会话可漏） | 与现状一致；铁律反向断言：dropped 后再 poll → None，无任何自动重连/切换 | **达标** |
| E3 | **stop 后进程残留**（terminate/kill 失败或超时） | 链路有三处吞错叠加成缺口：① `_terminate` 的 terminate→wait(5)→kill→wait(5) 中**第二次超时被外层 except 吞掉**（singbox_engine.py:1127-1138，:1133-1136 raise 的 TimeoutExpired 落进 :1137 的 `except (OSError, subprocess.TimeoutExpired, ValueError): pass`）；② `stop()` 先 `_untrack` 摘句柄再 terminate（:943-946, :992-994）——失败后句柄已不在 `_live_handles`，atexit 兜底 `_stop_all_live` 只停 tracked（:996-1002），进程没人认领；③ 孤儿清扫 `_reap_one` 见 engine.json 里 owner_pid（本客户端进程）还活着就直接放过（:1096-1125，:1107-1109）——「owner 活着 = 不是孤儿」，可残留进程占着端口直到客户端进程退出后的下一次启动才被清。orchestrator `_teardown_engine` 对 stop 再兜一层 `except Exception: pass`（orchestrator.py:72-77），残留对用户完全不可见 | stop 后显式校验进程真死；校验不过 → 显式报错/告警（进程残留 + pid + 端口），会话照常回 IDLE，等用户处理；不清数据、绝不自动重连 | **GAP-E1**——修责：引擎段（改 singbox_engine.py 行为本体：stop/terminate 失败外显 + 编排层 teardown 后核验 status）；orchestrator 的核验调用属编排段 |
| E4 | **verifier/probe 抛错**（probe callable raise 而非返回失败）——编排层异常路径 | `orchestrator.connect()` 只捕引擎的 EngineError（orchestrator.py:107）；`verify_exit` 对 probe 无 try（verify.py:230 `r = probe(node)`）；`verifier(node)`（orchestrator.py:114）抛错时：engine 已启动、`self._engine/_handle` 已挂上（:110），但 state 仍是 IDLE、异常原样冒出 connect()——**半开隧道**（引擎进程活着、无 disconnect、`poll()` 因 state≠CONNECTED 永远不收）+ 用户拿到原始异常栈 | verifier 调用包住：任何异常 → teardown 引擎 → 回 IDLE → 落样本 connect=False → 发 `error` 事件带原因；绝不让隧道半开 | **GAP-E2**——修责：编排段（orchestrator.py connect 的 verifier 调用包 try/except + teardown）；测试用抛错 probe 反向断言替身无半开隧道 |
| E5 | 引擎 `on_event` 回调抛错（start 成功后的 connected 事件回调） | 回调异常被吞（singbox_engine.py:892-896），但有注释声明理由（「回调异常不能弄丢已就绪的隧道」）——吞的是通知回调，不影响隧道与数据 | 豁免可接受（满足 §1-1）；宜再记一行日志 | **豁免**（GAP-E3 轻微，低优先级——引擎段顺手补日志即可） |

引擎侧既有兜底（不动、如实记录）：孤儿清扫三层兜底齐备——①实例登记 + atexit 必停（singbox_engine.py:123, :996-1002）；②每次 start 前清扫（:835-839，失败不拦启动）；③engine.json 登记所有者（:975-990）+ `reap_stale_engines` 按所有者死活/收养时长判孤儿（:1069-1094）。`_write_engine_meta` 写失败吞（:989-990）——清扫只是兜底不是主流程，**豁免**（有注释）。meta 写失败会导致③失效，属观察项（长跑段留意，不另立缺口）。

### 2.2 DB 损坏（对照 core/store.py）

| # | 注入点 | 现状（0.17.0 实测） | 期望 | 缺口 |
|---|--------|---------------------|------|------|
| D1 | 库文件被**截断为 0 字节** | `Store.__init__` 直接 `sqlite3.connect(path)` + `executescript(_SCHEMA)`（store.py:68-80；_SCHEMA 全部 `CREATE TABLE IF NOT EXISTS`，:23-52）。SQLite 把 0 字节文件当合法新库 → **静默新建空库**，节点/样本/pin/ban 全部「消失」且无任何报错告警；CLI/GUI 构造点无校验（ui/cli.py:449、ui/app.py:1442） | 打开时校验：文件存在且非 0 字节时，先验 SQLite 文件头（magic `SQLite format 3\0`）或跑 `PRAGMA quick_check`；不像有效库 → 显式报错拒开、指路备份，**绝不静默新建空库覆盖** | **GAP-D1**——修责：存储段（store.py 打开期校验）。测试在临时目录造真截断文件，断言现状=静默空库（事实断言）+ DESIGN 如实记录（缺口登记断言） |
| D2 | 库文件**头部被覆写**（前 N 字节换垃圾） | sqlite 打开后第一条语句抛 `sqlite3.DatabaseError: file is not a database`，从 `Store()` 构造器原样冒出；三个构造点都无捕获，构造形态均为 `store = Store(resolve_db_path(explicit_db=args.db, env_db=env_db, settings=settings))`（task24 §8-3 四档接线后形态，测试锚 test_faults.py::test_wiring_store_construction_uncaught 按此形态重锚）：CLI（ui/cli.py）、GUI 后台窗（ui/app.py）、使用界面主窗独立冒烟 main（ui/usage_window.py）→ 用户拿到原始异常栈（GUI 形态直接崩溃退出，参 0.5.1 同类教训） | 构造点/存储层把 DatabaseError 翻成人话：「节点库文件损坏（疑似被覆写/不是本客户端的库），已拒开，原文件未动；请检查备份」，退出码非 0；绝不删库、绝不新建顶替 | **GAP-D2**——修责：接线段（ui/cli.py、ui/app.py 构造点捕获）+ 存储段（GAP-D1 的打开期校验顺带拦住大半）。测试造真覆写文件断言 DatabaseError 现状 + DESIGN 登记 |
| D3 | **schema 版本不符**（新代码开旧库/旧代码开新库） | **无任何 schema 版本机制**：store.py 无 `PRAGMA user_version`、无迁移表、无迁移函数（全文实测）；`CREATE TABLE IF NOT EXISTS` 不会给既有表补列（:23-52）——schema 一旦演进，旧库不炸在打开期、而炸在日后的某条 INSERT/SELECT（sqlite3.OperationalError 原始栈） | 打开期读 `PRAGMA user_version` 与代码内 SCHEMA_VERSION 比对：偏低 → 显式迁移（可回滚备份先行）；偏高 → 显式报错拒开（降级打开只会坏数据）；绝不静默 | **GAP-D3**——修责：存储段（迁移机制 + user_version）。现状无版本号的文件锚点：store.py 无 user_version 字样 |
| D4 | **写入中途断电**（半条记录） | SQLite 默认回滚日志（journal）保证单事务原子，commit 前断电会回滚，不会留半条——这一层现状达标；但 `Store` 未启用 WAL、无 `PRAGMA integrity_check/quick_check` 自检路径（store.py 全文实测）：页级损坏（非头部）会在日后某次查询炸出原始 DatabaseError，用户无从知道库坏了多久、坏到哪 | 打开期（或每 N 次启动）跑 `quick_check`，坏 → 显式报错 + 指路备份；写入保持事务原子（现状已达标部分不动） | **GAP-D4**——修责：存储段（启动期 quick_check + 人话报错）。测试断言：未 commit 即断连 → 数据回滚不丢（达标部分）；页级损坏 → `integrity_check` 能报出（修复路径依赖的事实） |

### 2.3 网络断绝（对照 core/verify.py、core/health.py）

| # | 注入点 | 现状（0.17.0 实测） | 期望 | 缺口 |
|---|--------|---------------------|------|------|
| N1 | 连接时**探测超时 / DNS 解析失败 / 出口不可达**（验出口环节） | 真实 probe `_fetch` 把一切异常（含超时、URLError/DNS 失败、连接拒绝）吞成 `(0, "")`（verify.py:365-373）；三个存活探针全灭 → `alive=False, error="存活探针全灭：链路不通"`（:392-394）；`verify_exit` → `ok=False`（:232-233）；orchestrator → teardown、回 IDLE、落样本、发 `verify_failed`（orchestrator.py:113-122）——分级与处置达标；**缺口在呈现**：超时/DNS/不可达三类原因折叠成同一句话，用户无法区分是网络问题还是节点问题 | 处置维持现状（显式失败、断开回 IDLE、不自动重连）；呈现补**原因分级**：ProbeResult 加 error_kind（timeout/dns/unreachable/…），`_fetch` 收集异常类型而非全吞；健康分级可继续按连败次数，呈现按因 | **GAP-N1**——修责：健康段（verify.py `_fetch`/ProbeResult + 呈现接线）。现状锚点：verify.py:365-373 的宽 except |
| N2 | 连接后**巡检期节点死亡**（15s 探测、ok/suspect/dead 分级） | 三态分级纯函数齐备：`connection_health_detail`（health.py:361-388），连败 ≥3 → suspect、≥5 → dead（常量 :340-341）；坏样本当场抛 ValueError（:344-358，不静默）；接线：GUI 复用 5s 定时器、3 tick≈15s 探测，样本 deque(maxlen=10)（ui/app.py:343-346, :1061-1078）；suspect/dead 的呈现进状态栏/告警条（0.9.0 口径）。**只分级不动作**——不自动断开/重连/切换（§1-5） | 与现状一致；铁律反向断言：dead 后不触发任何自动行为 | **达标**（已知边界如实记录：样本只在内存 deque，不落库、离线导出无历史——0.11.0 已声明，属观察项非本段缺口） |
| N3 | 巡检/验真中 **ip-api 出口情报查询失败**（DNS 坏、被墙、超时） | 吞成 `info={}`，画像字段落 None（verify.py:396-403）——验真主结论（alive+exit_ip）不受影响，未知字段显式为「未知」（unknown 不猜，符合口径） | 豁免可接受：降级为未知 ≠ 假装成功；宜在 ProbeResult 加 note 字段 | **豁免**（有 except pass 但语义正确；健康段顺手补 note，不强制） |
| N4 | **muse 探测链路死**（主链路活、muse.ai 不可达） | `_fetch` 异常 → `muse_code=0`（:405-406）；`verdict_muse(0)` → (False, **「链路死」**, 「节点不通…」)（verify.py:105）——alive=True 的语境下该标签误导（节点明明活着，只是 muse 探测没完成）；且 muse_code=0 不进 dirty（:241-243 只认 400/403），呈现层拿到的 `muse_ok=False` 与「链路死」标签并存 | muse 探测未完成（code=0）单列口径：「muse 未能探测」≠「链路死」≠「被拒/被拦」；结论不进 dirty 维持现状 | **GAP-N2**（轻微）——修责：健康段（verdict_muse 调用侧对 0 的标签） |
| N5 | 系统代理冲突判读**判不了**（gsettings 缺失/格式读不出） | `proxy_conflict` 对 None/不可用/mode 解析不出一律 `conflict=False` + 人话说明「判不了」（health.py:276-323）——宁可判不了不硬判，符合 §1-1 | 与现状一致 | **达标** |

### 2.4 缺口登记（测试反向断言的登记表，faults.py `GAPS` 同源）

| 编号 | 一句话 | 修责段位 | 严重度 |
|------|--------|----------|--------|
| GAP-E1 | stop/terminate 失败被三处吞 → 残留进程无人认领（句柄已摘 + 清扫认 owner 活） | 引擎段（singbox_engine.py stop 口径）+ 编排段（teardown 后核验） | 中 |
| GAP-E2 | verifier/probe 抛错未捕 → 半开隧道 + 原始异常冒出 | 编排段（orchestrator.connect 包 try/except） | 高 |
| GAP-E3 | 引擎事件回调异常吞（有注释豁免，宜补日志） | 引擎段 | 低 |
| GAP-D1 | 0 字节截断库 → 静默新建空库覆盖，数据无声丢失 | 存储段（store.py 打开期校验） | 高 |
| GAP-D2 | 覆写文件头 → DatabaseError 原样冒出构造点（CLI/GUI 无捕） | 接线段（ui/cli.py:449、ui/app.py:1442）+ 存储段 | 高 |
| GAP-D3 | 无 schema 版本/迁移机制，schema 演进炸在日后查询期 | 存储段 | 中 |
| GAP-D4 | 无完整性自检/修复路径（页级损坏日后炸原始错） | 存储段（启动期 quick_check） | 中 |
| GAP-N1 | 探测失败原因不分级（超时/DNS/不可达折叠一句话） | 健康段（verify._fetch/ProbeResult） | 中 |
| GAP-N2 | muse_code=0 标签误用「链路死」（alive=True 语境误导） | 健康段（verdict_muse 调用侧） | 低 |

## 3. 长跑稳定性脚本设计（soak.py）

### 3.1 目标

回答「跑久了会不会漏」：孤儿引擎进程、文件句柄、内存、DB 体积四样在
长循环下有没有无界增长。本段交付**设计 + 假引擎干跑原型**；真引擎长跑
留本机集成段（§3.5），脚本入口与判读口径本段写死、不真跑。

### 3.2 循环动作集（每轮 = round，--rounds N 参数化）

1. **连接/断开循环**（每轮）：`connect → verify（假 probe）→ 健康采样 1 次 → disconnect`。
   对应真机常驻行为：GUI 15s 巡检（ui/app.py:346, :1061-1078）——干跑里
   把「一轮」抽象成一次巡检节拍。
2. **批量测速**（每 `--speed-test-every` 轮，默认 10）：对节点池全部假节点
   各探一次（模拟 GUI 批量验真节拍）。
3. **自动探测节奏模拟**（每 `--auto-probe-every` 轮，默认 10）：全库探测一轮，
   对应 GUI 自动探测（默认 10 分钟档，ui/app.py:466, :494-497, :516）——
   干跑用「每 N 轮」缩放时间，不真等 10 分钟。
4. 轮间 `--interval` 秒睡眠（默认 0.05，真跑拉长）。

### 3.3 监控指标（每轮采样一次，Linux 只读 /proc，纯标准库）

| 指标 | 采集法 | 对照的真机口径 |
|------|--------|----------------|
| 子进程数 | 扫 `/proc/*/stat` 取 ppid == 本进程 pid 的条目数 | 孤儿引擎（singbox_engine.py:969-973 真机事故：残留一下午占 10818） |
| fd 数 | `len(os.listdir('/proc/self/fd'))` | 日志/临时文件句柄泄漏（引擎 stdout 已落文件不落管道，singbox_engine.py:858-868） |
| 内存 RSS | `/proc/self/status` 的 `VmRSS`（kB） | 常驻进程内存 |
| DB 文件大小 | `os.path.getsize(--db)` | verify_samples 只进不出的体积（store.py:40-51） |

### 3.4 判据（--rounds 与资源增长上限，报告逐项 PASS/FAIL，任一 FAIL 退出码 1）

| 判据 | 上限 | 说明 |
|------|------|------|
| 轮数跑满 | `rounds_run >= --rounds` | 干跑必须满轮结束，不许中途静默缩水 |
| 子进程不增长 | 全程 max(子进程数) == 基线（假世界=0） | 假引擎不起真进程；>0 即脚本自身或世界泄漏 |
| fd 不增长 | `last_fd - first_fd <= 2`（容 2 个临时波动） | 假世界 fd 应零增长 |
| DB 增长有界 | `db_last - db_first <= max(65536, 1024 * rounds)` 字节（`--db-max-growth-bytes` 可覆盖） | 每轮 ~2 条样本（几百字节），1KB/轮是宽上限；超限=样本无界堆积预警 |
| RSS | 报告不判，只报 first/last/max/growth | 真跑再定阈值（干跑的 RSS 是解释器自身，无判读意义） |

### 3.5 报告形态与真跑手册

- stdout：人话小结（轮数、动作计数、四指标 first/last/max/增长、判据逐项 PASS/FAIL）。
- `--report PATH`：另写 JSON（版本口径、参数、每动作计数、指标摘要、
  判据明细、PASS/FAIL），供集成段归档比对。
- 退出码：0=全过，1=判据失败，2=参数错，3=真引擎模式本段拒绝执行。
- **真引擎模式手册**（`--mode real`，本段只写入口与判读口径，执行退出码 3）：
  1. 前置：本机真 sing-box（`GATE_CLIENT_SINGBOX_BIN` 或 ~/.local/bin/sing-box）、
     用户点头的一次真连接、冷门测试端口（§4）、磁盘余量。
  2. 动作集同 §3.2 但用真引擎 + 真 probe；每轮后 `poll()` 反向断言不自动重连。
  3. 判读口径：子进程数 ≤ 活动会话数（连接/断开循环里 ≤1，断开态=0，
     连续 2 轮 >0 判孤儿泄漏）；fd 对数期后持平（增速 ≈0 判稳，持续线性
     增长判泄漏）；RSS 取 2 小时基线再定上限（先观察不硬判）；DB 同 §3.4
     有界判据；sing-box 日志文件大小一并记录（不判，真机事故 0.9.0 的
     教训是占端口，日志轮转另行评估）。
  4. 报告同 §3.5；真跑时长建议 ≥2 小时（覆盖 ≥12 个 10 分钟自动探测节拍）。

## 4. 测试端口口径（0.9.0 撞车教训）

- 禁用段（CHANGELOG 0.9.0 集成记录 + 0.14.0 保留段）：**7897–7898**
  （引擎默认端口）、**10818–10829**（0.14.0 设置保留段）、**18089–18095**
  （0.9.0 撞车段，18089 曾被本机 ZCode 中继占用）。
- 本段选用冷门段：**29311–29329**（faults.py 常量 TEST_SOCKS_PORT=29311、
  TEST_HTTP_PORT=29312；soak.py 同段取值；测试用 `pick_free_port()` 从该段
  起步向上找空闲，跳过禁用段）。
- 测试中的端口占用用例是真实 bind（127.0.0.1，测完即关），不碰外网。

## 5. 交付物映射（审查按图索骥）

| 交付物 | 覆盖 |
|--------|------|
| faults.py | 假引擎四行为（normal/start_fail/mid_death/stop_residue）、假 probe（normal/timeout/parse_fail/raise）、会话编排替身（同 orchestrator 形状 + §1 期望口径）、DB 损坏工具（truncate/header/middle + open_behavior 现状探测）、GAPS 登记表、FILE_ANCHORS 客户端锚点 |
| test_faults.py | §2 表逐行断言：替身断期望行为；sqlite3 真损坏文件断现状事实；client-dev 现状走 FILE_ANCHORS 文件锚点；GAPS ↔ DESIGN.md 登记互验；铁律反向断言（§1-4）；测试端口 §4 |
| soak.py | §3 全部：假引擎干跑满轮 + 四指标 + §3.4 判据 + JSON 报告；`--mode real` 只写手册退出码 3 |

## 6. 未解决项 / 移交

见 DONE.md「未解决项」：9 条 GAP 的修责段位（§2.4）、长跑真跑待本机
条件、GAP-D1/D2 的备份指引话术待主会话定稿。
