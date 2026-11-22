#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
出口性质归类关键词表（单一事实源，task25 其一「逐词对齐」落地）
================================================================

两张表只在这一处定义，两处消费：
- client/core/verify.py   classify_network（四场景判定的
  residential/datacenter 前置判定）；
- client/core/classify_proto.py classify_exit（task11 六板块的
  exit_kind 三态判定，经 DATACENTER_KEYWORDS / RESIDENTIAL_KEYWORDS
  兼容别名引用本表）。

表内容 = verify.py 校准表的逐字快照（client 0.27.0 实树，2026-11-22），
逐词清单与对齐裁决见 work25/ALIGN-DIFF.md：classify 侧原表多出的
13 个词（机房 2 + 住宅 11）全部销账——其中 HURRICANE ELECTRIC /
BT OPENREACH / KT CORP 三词是本表既有词的子串冗余（删去零行为
变化），其余 10 词若保留会改动 verify 侧现行判定（task25 硬约束
禁区），一律去掉，方向偏保守（落 unknown 而非错判，人工改判
exit_kind_override 兜底）。

维护纪律（沿用 classify_proto 原注记 + verify 冻结口径）：
- 改词 = 同时改 verify 四场景与 classify 板块两处判定，须主人裁决；
  改完跑 client.tests.test_verify、client.tests.test_classify 与
  test_classify_align（冻结基线断言在本组，动表必红，逼着显式
  重校准）三组回归。
- 只增不改不删须谨慎；短关键词（如两位缩写）先评估误伤再加；
  新条目须公开常识级或经交叉核实（主人信息处理规则 2026-11-19）。
- 本表是归类不是排名：不含任何优选/排序/切换语义（永不自动切换
  节点铁律在词表层的落点）。

出处注记（承 verify.py 原注释）：关键词表逻辑复制自
vpngate.classify_network，词表同步复制——本模块即那份同步复制的
唯一落点，verify 与 classify 两处都不再各留一份。
"""

# 托管商/云厂商关键词（命中 → datacenter）。机房先查（两处消费方同序）。
DATA_CENTER_ORG_KEYWORDS = (
    "GOOGLE", "AMAZON", "AWS", "MICROSOFT", "AZURE", "OVH", "HETZNER", "DIGITALOCEAN",
    "AKAMAI", "CLOUDFLARE", "FASTLY", "RACKSPACE", "EQUINIX", "LINODE", "VULTR",
    "HURRICANE", "TENCENT", "ALIBABA", "ALIYUN", "LEASEWEB", "LEASWEB", "CHOOPA",
    "CONTABO", "M247", "DATACAMP", "COGENT", "ORACLE",
)

# 住宅 ISP 关键词（命中 → residential）。机房未命中才查本表。
RESIDENTIAL_ORG_KEYWORDS = (
    "NTT EAST", "NTT WEST", "NTT COMMUNICATIONS", "NTT BROADBAND", "KDDI", "DOCOMO",
    "SOFTBANK", "AU COMMUNICATIONS", "J:COM", "JCOM", "OCN", "BIGLOBE",
    "IIJ", "SEIKO", "CLEVER-NET", "AT&T", "COMCAST", "XFINITY", "VERIZON",
    "CHARTER", "SPECTRUM", "COX", "CENTURYLINK", "FRONTIER",
    "TELUS", "ROGERS", "BELL CANADA", "VODAFONE", "ORANGE", "DEUTSCHE TELEKOM",
    "BREEZE", "TIM S.P.A", "LIBERO", "FASTWEB", "FREE FRANCE", "BT OPEN",
    "KT", "SK BROADBAND", "LG UPLUS", "CHUNGHWA", "HINET", "SINGTEL", "STARHUB",
)
