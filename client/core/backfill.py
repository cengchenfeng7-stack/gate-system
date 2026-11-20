"""M2.5 回填：把已存的实测出口画像补应用到节点上（不重新连接）。

用途：全量验出口扫描（sweep）跑完后，库里节点已有 exit_profile，
但改名/隔离是在 apply_outcome 时发生的；扫描期间若客户端还是旧版，
画像存了、名字没改。本模块对所有带真实出口的节点重放
_apply_exit_naming + _apply_quarantine，幂等（orig_name 只存第一次，
重复跑结果不变）。
"""
from .store import Store
from .verify import _apply_exit_naming, _apply_quarantine


def backfill_exit_names(store: Store) -> dict:
    renamed = 0
    quarantined = 0
    cleared = 0
    scanned = 0
    for node in store.list_nodes(include_banned=True):
        profile = node.exit_profile
        if profile is None or not profile.ip:
            continue
        scanned += 1
        before = (node.name, dict(node.extra))
        _apply_exit_naming(node, profile)
        _apply_quarantine(node, profile)
        if node.name != before[0]:
            renamed += 1
        after_q = node.extra.get("quarantined")
        if after_q and not before[1].get("quarantined"):
            quarantined += 1
        if before[1].get("quarantined") and not after_q:
            cleared += 1
        if (node.name, node.extra) != before:
            store.upsert_node(node)
    return {"scanned": scanned, "renamed": renamed,
            "quarantined": quarantined, "cleared": cleared}


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "accept.db"
    with Store(path) as s:
        print(backfill_exit_names(s))
