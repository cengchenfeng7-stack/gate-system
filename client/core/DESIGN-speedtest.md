# task19 设计：U4 测速与速度展示（阶段五·使用界面线 第五段·实现段）

依据：work10/DESIGN.md §10（吞吐实测口径）、§2.2（①②⑤区口径）、§13-G
（载荷 URL 主人核定制）。本文件只写**接线决策与落点**，实现逻辑见
`impl/client/core/speedtest.py`（唯一新文件，纯标准库）。

**版本对账**：开工先读 client/VERSION——本次开工读到 **0.23.0**（简报
起草时为 0.17.0，已前进 6 版）。下文行号是 0.23.0 快照，仅供定位参考；
**锚点以引文文本为准**（client-dev 在前进，行号会漂，引文不会）。
全部接线块已在 0.23.0 临时拷贝上逐锚点机械套用并通过行为冒烟
（见 DONE.md「集成前验证」）。

## 0. 集成步骤（审查方照单执行）

1. 拷 `impl/client/core/speedtest.py` → `client/client/core/speedtest.py`（新文件）。
2. 按 §1（W1，5 块）改 `client/core/settings_proto.py`；按 §2（W2，14 块）
   改 `client/ui/usage_window.py`。均为锚点插入/替换，无整文件重写。
3. `cd work19 && python3 -m unittest` 应 31 用例全绿。
4. 复核红线：全仓 grep 无真实载荷 URL 字面量；实测/自报数据与展示两层
   分得清（§3）；临时端口纪律未被破坏（§4）。

## 1. W1：载荷地址设置项（task12 统一设置模型扩展位）

登记进 settings_proto.Settings（唯一模型，不另起设置文件）。字段名
`speedtest_payload_url`、默认空（=未配置）、校验只认 http(s) 形态。
校验器单一事实源在 core/speedtest.validate_payload_url——实测器与设置
层同用一份，不复制第二份规则（这是 settings_proto 头部「不 import
client 包」立场的唯一例外，docstring 一并改口）。

**W1-a 模块 docstring（第 6–8 行引文）**——字段数与例外声明：

```python
# 旧（0.23.0 settings_proto.py 第 6–8 行）：
DESIGN.md §3/§4 的定稿口径落成代码。只用标准库；不依赖 PySide6、
不 import client 包；不读文件、不碰网络——纯数据结构 + 校验 + 序列化
+ 合并（与 task10 usage_model.py 同一独立性口径）。
# 新：
DESIGN.md §3/§4 的定稿口径落成代码。只用标准库；不依赖 PySide6、不读
文件、不碰网络——纯数据结构 + 校验 + 序列化 + 合并（与 task10
usage_model.py 同一独立性口径）。唯一例外（U4/task19 扩展位）：相对
import core/speedtest 的载荷地址校验器——校验单一事实源，不复制第二份规则。
```

**W1-b import（第 41–42 行锚点）**：

```python
import ipaddress
from dataclasses import dataclass

from .speedtest import SpeedTestConfigError, validate_payload_url
```

**W1-c 字段（第 171 行 `auto_probe_interval_min: int = 10` 之后插入）**：

```python
    # —— U4 扩展位：测速载荷地址（task19 / work10 §10、§13-G） ——
    # 默认空 = 未配置（客户端不内置地址，主人在设置页核定填入）；
    # 非空只认 http(s) 形态，校验单一事实源 = core/speedtest
    # .validate_payload_url（实测器同用一份，两处口径不打架）。
    speedtest_payload_url: str = ""
```

**W1-d __post_init__ 校验（第 198–201 行 auto_probe 校验块之后插入）**：

```python
        # U4 扩展位（task19）：载荷地址空 = 未配置（合法默认）；非空
        # 只认 http(s) 形态（校验器与实测器同源，见模块 docstring）。
        # 校验失败换 SettingsError 抛——设置层的错归设置层错误族。
        if self.speedtest_payload_url:
            try:
                validate_payload_url(self.speedtest_payload_url)
            except SpeedTestConfigError as exc:
                raise SettingsError(f"speedtest_payload_url: {exc}") from exc
```

**W1-e to_dict（第 216 行 `"auto_probe_interval_min": …,` 之后插入一行）**：

```python
            "speedtest_payload_url": self.speedtest_payload_url,
```

（from_dict 用 `cls(**d)`：旧 settings.json 缺新键 → 补默认空槽，向后
兼容；merge 走 to_dict 展开自动带上新字段——已冒烟核验。）

## 2. W2：usage_window.py 接线清单（0.23.0 快照）

新 GUI 面：①区速度行、②区按板块批量测入口、⑤区监测状态行。

**W2-0 模块 docstring（第 25–27 行引文）——分区归属对账**：

```python
# 旧：
- orchestrator.py 一行不改（铁律）；永不自动切换节点；③④⑤⑥区归
  U2/U6/U4/U5，本段不做、不摆空壳占位；使用界面永不出现：导入/登记/
# 新：
- orchestrator.py 一行不改（铁律）；永不自动切换节点；③区已落
  （task17），①区速度行、②区按板块批量测、⑤区监测状态行已落
  （task19/U4）；④区归 U6，⑥区归 U5，未落区不摆空壳占位；使用界面
  永不出现：导入/登记/
```

**W2-1 stdlib import（第 34–36 行锚点）**：

```python
import argparse
import sys
import time
import urllib.request
```

**W2-2 core import（第 40–41 行 `from ..core import diag_collect` /
`from ..core import history` 之后插入）**：

```python
from ..core import speedtest
from ..core.settings_store import (
    SettingsStoreError, load_settings,
)
```

**W2-3 载荷 opener 构造（模块级，INSTALL_HINT 块之后插入）**——真实
网络胶水只活在 UI 层，core/speedtest 的 opener 注入参数由此构造
（core 不碰真实网络）；形态同 verify._opener（core/verify.py:357-362）：

```python
_PAYLOAD_TIMEOUT_S = 30   # 单次建连/响应超时（整次下载另有 budget_s 预算）


def _make_proxy_opener(proxy_url):
    """经给定代理入口的 urllib opener——core/speedtest 的 opener 注入
    参数由此构造（真实网络胶水只活在 UI 层，core 不碰真实网络）。
    形态同 verify._opener（core/verify.py:357-362）：代理进 ProxyHandler，
    空代理（{}）=不配代理并覆盖环境变量代理。连接类网络错误按
    OSError/TimeoutError 抛出（urllib 正是如此），speedtest 据此归类。
    返回 url→二进制流 函数（符合 speedtest 的 opener 合同）。"""
    def _open(url: str):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {"http": proxy_url, "https": proxy_url} if proxy_url else {}))
        return opener.open(url, timeout=_PAYLOAD_TIMEOUT_S)

    return _open
```

**W2-4 批量旗标（__init__ 第 276 行 `self._last_node_count = -1 …` 之后插入）**：

```python
            self._batch_active = False   # ②区按板块批量测进行中旗（task19）
            self._batch_stop = False     # 批量停止旗（后台 _run_batch 同名同义）
```

**W2-5 ①区速度行（第 322–326 行当前节点行块之后插入）**：

```python
            # ①区速度行（task19，work10 §10 展示口径）：延迟（既有探测值）
            # + 最近吞吐实测/自报三态（speedtest.format_speed_display）。
            # 只显示最近实测，不常驻跑吞吐（常驻下载会干扰连接本身）。
            self.speed_line = QLabel("")
            self.speed_line.setWordWrap(True)
            self.speed_line.setStyleSheet("color:#c9cbd3;padding:2px 12px;")
            root.addWidget(self.speed_line)
```

**W2-6 ⑤区监测状态行（第 373–374 行 rules_panel 块之后插入）**：

```python
            # ---------------- ⑤ 测速与监测区（task19：本段落监测状态行） ----
            # 状态行只读 task12 Settings 的 auto_probe 两字段
            # （speedtest.monitor_status_text 拼文案），不持有第二份
            # 开关/间隔；轮询时现读 settings.json 同一份存储（改即见）。
            zone5 = QFrame()
            z5 = QVBoxLayout(zone5)
            z5.setContentsMargins(10, 4, 10, 4)
            zone5_title = QLabel("测速与监测")
            zone5_title.setStyleSheet("font-weight:bold;padding:2px;")
            z5.addWidget(zone5_title)
            self.monitor_line = QLabel("")
            self.monitor_line.setWordWrap(True)
            self.monitor_line.setStyleSheet("color:#9a9ca6;padding:2px 12px;")
            z5.addWidget(self.monitor_line)
            root.addWidget(zone5)
```

**W2-7 板块页批量测按钮（BoardPage.__init__ 第 138–143 行 bar 块之后插入）**
——板块级入口落在②区每页 bar 上；不动 ROW_ACTIONS 行内三件套（连接/
置顶/测速）：

```python
            # 板块级批量测入口（task19，②区落点；不是 ROW_ACTIONS 行内
            # 三件套）。决策在 core/speedtest.board_batch_plan（板块分组
            # + 载荷门卫），本按钮只执行计划；批量中兼做「停止」。
            self.batch_btn = QPushButton("测本板块")
            self.batch_btn.setToolTip(
                "按板块批量测：延迟必测；设置里填了测速载荷地址才附带吞吐"
                "实测（5MB 档、可中止）。只测与回写，不连不切节点。")
            self.batch_btn.clicked.connect(
                lambda: self.window.batch_test_board(self))
            bar.addWidget(self.batch_btn)
```

**W2-8 方法组（第 571–577 行 `test_from_page` 整方法之后、第 580 行
「# 测速管道」注释之前插入）**——决策全在 core 纯函数，GUI 只执行；
批量管道 = 后台 toggle_batch_test/_run_batch 同款形态（_Worker +
"batch" tag + _batch_stop 停止旗 + progress 协议），**不是第二条管道**：

```python
        # ---------------- ②区按板块批量测 + 吞吐实测（task19/U4） ----------
        def batch_test_board(self, page: BoardPage):
            """②区板块级批量测入口：决策（板块分组/载荷门卫）全在 core 的
            board_batch_plan 纯函数，本方法只执行计划。批量管道 = 后台窗
            toggle_batch_test/_run_batch 同款形态（_Worker + "batch" tag +
            _batch_stop 停止旗 + progress 消息协议）——不是第二条管道。
            载荷空槽时给「未配置测速载荷」提示并只测延迟（不静默失败）。
            停止同款：再点一次置 _batch_stop，吞吐实测即时中止（partial）。"""
            if self._batch_active:
                self._batch_stop = True
                self._show_info("正在停止批量测速…")
                return
            if self._busy:
                return
            try:
                payload_url = load_settings().speedtest_payload_url
            except SettingsStoreError as exc:
                self._show_alert(f"设置文件读不了，批量测停在这里：{exc}")
                return
            plan = speedtest.board_batch_plan(
                self.store.list_nodes(include_banned=False), page.board_key,
                payload_url)
            if plan["notice"]:
                self._show_info(plan["notice"])
            if not plan["ids"]:
                return
            label = classify_proto.BOARD_LABELS.get(page.board_key, "未分类")
            self._batch_stop = False
            self._batch_active = True
            self._run_task("batch", lambda: self._run_board_batch(
                plan["ids"], label, plan["measure_speed"], payload_url))

        def _run_board_batch(self, ids, label, measure_speed, payload_url):
            """批量管道主体（后台 _run_batch 同形态，app.py:761-787）:
            逐节点先 _test_node（延迟/出口，fast 口径；临时引擎 10818/10819
            纪律同款），载荷已配置再附带吞吐实测（_throughput_for_node）。
            只展示与回写结果，不连不切任何节点（铁律）；前端不删节点，
            不带后台的废节点清单语义（work10 §2.3 边界清单）。"""
            ok_n = fail_n = skip_n = done = speed_n = 0
            fail_ids, speed_notes = [], []
            total = len(ids)
            for nid in ids:
                if self._batch_stop:
                    break
                node = self.store.get_node(nid)
                name = node.name if node else nid
                self.bridge.progress.emit(f"测速中 {done + 1}/{total}：{name}")
                kind, _text = self._test_node(nid, fast=True)
                done += 1
                if kind == "verify_ok":
                    ok_n += 1
                elif kind == "skip":
                    skip_n += 1
                else:
                    fail_n += 1
                    fail_ids.append(nid)
                if measure_speed and not self._batch_stop:
                    result = self._throughput_for_node(nid, payload_url)
                    if result is not None and result.measured:
                        speed_n += 1
                    elif result is not None:
                        speed_notes.append(
                            f"{name}：吞吐未实测（{result.category}："
                            f"{result.detail}）")
                self.bridge.progress.emit(
                    f"测速中 {done}/{total}：可连 {ok_n} · 不通 {fail_n}"
                    f" · 跳过 {skip_n} · 实测吞吐 {speed_n}")
            return ("batch_done", ok_n, fail_n, skip_n, done, total, label,
                    speed_n, speed_notes)

        def _throughput_for_node(self, node_id: str, payload_url: str,
                                 tier_mb=None):
            """单节点吞吐实测（_test_node 三分支同形态，端口纪律同款——
            未连接走手动组 10818/10819 临时引擎，测完即停，不碰当前会话、
            不碰 10828/10829 自动组）：
            - 正在连着的节点：经活动引擎 HTTP 入站（当前会话）测；
            - sing-box 系未连接：临时引擎测完即停；
            - OpenVPN（会话无 HTTP 入站，直连无法判定走不走隧道）与其余
              形态：如实返回 None 不硬测——宁缺毋假。
            结果经 speedtest.write_back 回写（只有 ok/partial 才写
            speed_mbps + speed_measured_at）并落库。只测与回写，不连不切
            任何节点（铁律）。"""
            node = self.store.get_node(node_id)
            if node is None:
                return None
            tier = tier_mb if tier_mb is not None else speedtest.TIERS_MB[0]
            orch = self.orchestrator
            proxy, temp_engine, handle = None, None, None
            if orch.state != Orchestrator.IDLE and orch.active_node_id == node_id:
                engine = self.engines.get(node.protocol)
                if not isinstance(engine, SingBoxEngine):
                    return None
                proxy = engine.http_proxy_url
            elif node.protocol in SINGBOX_PROTOCOLS:
                temp_engine = self._make_temp_engine()
                try:
                    handle = temp_engine.start(node)
                except EngineError:
                    return None   # 引擎起不来＝连不上口径，放弃本节点吞吐
                proxy = temp_engine.http_proxy_url
            else:
                return None
            try:
                opener = _make_proxy_opener(proxy)
                return speedtest.run_throughput_test(
                    payload_url, opener, tier,
                    abort_fn=lambda: self._batch_stop)
            finally:
                if temp_engine is not None:
                    temp_engine.stop(handle)

        def _refresh_zone5(self):
            """⑤区状态行数据供给：现读 task12 设置一份口径（settings.json
            同一份存储），文案由 speedtest.monitor_status_text 拼（只读
            auto_probe 两字段，不持有第二份状态）。文件损坏如实显示，
            不用默认值盖掉（settings_store §4.2 口径）。"""
            try:
                self.monitor_line.setText(
                    speedtest.monitor_status_text(load_settings()))
            except SettingsStoreError as exc:
                self.monitor_line.setText(f"设置读不出：{exc}")
```

**W2-9 轮询刷新⑤区（poll_once 尾部，`if self.store.count_nodes() !=
self._last_node_count:` 两行块之后插入一行）**：

```python
            self._refresh_zone5()   # ⑤区状态行现读设置（task19，改即见）
```

**W2-10 ①区速度行数据供给（`_apply_node_detail` 的 setText 块 +
`_clear_node_detail` 原两行，替换为下块）**：

```python
            self.node_detail.setText(
                f"当前节点：{name}　[板块：{board_label}]　[场景：{scenes}]　"
                f"延迟：{history.format_latency_ms(stats.median_latency_ms)}")
            self._apply_speed_row(node.id)

        def _clear_node_detail(self):
            self.node_detail.setText("")
            self._apply_speed_row(None)

        def _apply_speed_row(self, node_id):
            """①区速度行数据供给（task19）：当前节点延迟（history 中位，
            既有探测值）+ 最近吞吐实测/自报三态
            （speedtest.format_speed_display）。只读最近实测，不触发任何
            测速（不常驻跑吞吐）；无节点清空。"""
            node = self.store.get_node(node_id) if node_id else None
            if node is None:
                self.speed_line.setText("")
                return
            stats = history.stats_for(self.store, node.id)
            self.speed_line.setText(
                f"速度：{speedtest.format_speed_display(node)}　"
                f"延迟：{history.format_latency_ms(stats.median_latency_ms)}")
```

**W2-11 批量收尾与结果分支（`_on_task_finished` 内两处）**：

(a) `self._busy = False` 行之后、`ok, payload = result` 之前插入
（诊断分支的 `return` 不受影响）：

```python
            if tag == "batch":
                self._batch_active = False   # 批量收尾（旗标复位，后台同款）
```

(b) `elif tag == "verify" and isinstance(payload, tuple):` 分支**之前**
插入批量结果分支，并把分支后的收尾两行换成三行：

```python
            elif tag == "batch" and isinstance(payload, tuple):
                (_kind, ok_n, fail_n, skip_n, done, total, label,
                 speed_n, speed_notes) = payload
                stopped = "（已手动停止）" if done < total else ""
                msg = (f"批量测速完成（{label}）{stopped}：可连 {ok_n} · "
                       f"不通 {fail_n} · 跳过 {skip_n}"
                       f"（共测 {done}/{total}）· 吞吐实测 {speed_n}")
                for note in speed_notes[:3]:
                    msg += f"\n· {note}"
                self._show_info(msg)
```

收尾（原 `self.refresh_boards()` + `self._update_action_states()` 两行
之间插一行——实测回写后速度行随之刷新）：

```python
            self.refresh_boards()
            self._apply_speed_row(self.orchestrator.active_node_id)
            self._update_action_states()
```

**W2-12 批量按钮状态（`_update_action_states` 尾部
`self.disconnect_btn.setEnabled(…)` 行之后追加）**：

```python
            # 板块级批量测按钮（task19）：批量进行中＝停止测速，其余随 _busy
            batch_text = "停止测速" if self._batch_active else "测本板块"
            batch_enabled = True if self._batch_active else not self._busy
            for pg in self.boards.values():
                pg.batch_btn.setText(batch_text)
                pg.batch_btn.setEnabled(batch_enabled)
```

## 3. 实测/自报区分口径的落点（两层）

- **数据层**：`NodeMetrics.speed_mbps`（core/model.py:85）+ `extra
  ["speed_measured_at"]`（epoch 秒，与 verify 样本 ts 同惯例）。分界 =
  时间戳在不在：在 → 当前 speed_mbps 即最近实测；不在 → feed 自报。
  只有 `speedtest.write_back` 能写这对值，且只在 ok/partial（中止但下
  到了数据）时写；四类失败与零字节中止不写、不碰自报值。
- **展示层**：`speedtest.format_speed_display(node)` 三态——
  「实测 X.X Mbps（时间）」＞「自报 X.X Mbps」＞「—」；节点行/①区
  速度行共用这一份口径函数。

## 4. 临时端口纪律复用说明

- 批量吞吐（未连接节点）与延迟测速同用 `_make_temp_engine` →
  **10818/10819 手动组**：批量测是用户点「测本板块」触发，属手动管道；
  自动组 10828/10829（auto_probe 专用）本段零接触。不新开端口段。
- 连接态吞吐走**当前会话 HTTP 入站**（活动引擎 http_proxy_url，
  7897），不经任何新监听。
- 逐节点串行：`_test_node` 的临时引擎在其 finally 里 stop 后才轮到
  本节点吞吐的临时引擎（同端口复用不冲突）。
- 吞吐只许用户触发或批量测时跑；①区速度行只读最近实测值——不常驻
  跑吞吐（常驻下载会干扰连接本身）；全段无第二套定时器。

## 5. 载荷空槽的收口流程（§13-G）

1. 主人在设置页（task12 集成段）把**核定过的**测速载荷地址填进
   `speedtest_payload_url`（settings.json，settings_store 一份存储）。
   收什么：http(s) 形态、稳定可经代理下载的纯载荷（5/25MB 档对应
   静态大文件），无重定向跳板、无登录态。
2. 默认空槽时的一切行为（已冒烟核验）：设置层收空槽为合法默认；
   ②区批量入口给「未配置测速载荷：…本批只测延迟」提示（不报错、
   不静默、不内置地址）；延迟测速不受影响。
3. 地址非法形态（非 http(s)）在设置层当场拒（SettingsError）；批量
   入口再遇非法值也只降级提示「本批只测延迟」，不炸不猜。
