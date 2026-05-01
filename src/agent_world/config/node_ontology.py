"""
Node Ontology — 节点类型查询包装。

所有查询委托给 config_loader，不暴露类型名常量。
引擎代码不应直接引用 "npc"、"zone" 等字符串。
"""

from __future__ import annotations
from typing import Any

from .config_loader import (
    get_type_def,
    get_type_prefix,
    is_terminal,
    is_same_type_blocked,
    has_recent_info,
    has_role,
    prefix_to_type_id,
    all_type_ids,
    init_config,
)

# ─── 抽象查询（引擎只用这些） ───

def get_ontology(type_id: int) -> dict[str, Any]:
    """获取类型的完整本体定义"""
    return get_type_def(type_id)


def prefix_to_type_id_wrap(eid: str) -> int:
    """Delegated prefix lookup"""
    return prefix_to_type_id(eid)


# 构建 NODE_ONTOLOGY 表（仅用于旧式遍历，不包含类型名信息）
NODE_ONTOLOGY: dict[int, dict[str, Any]] = {
    tid: get_type_def(tid)
    for tid in all_type_ids()
}
