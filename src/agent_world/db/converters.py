"""
db/converters.py — DB 拓扑格式 ↔ 业务模型之间的转换器。

NodeDB 保持纯 CRUD，不知道 NPC/Zone 是什么。
转换逻辑全部放在这里：把 nodes 表 raw dict 转成业务模型，反之亦然。
"""

import json
import logging

logger = logging.getLogger(__name__)


# ─── 辅助函数 ───


def _persona_tags_to_list(p_tags) -> list[str]:
    """将 PersonaTags 对象转为字符串列表"""
    result = []
    try:
        if hasattr(p_tags, 'model_dump'):
            d = p_tags.model_dump()
        elif hasattr(p_tags, '__dict__'):
            d = p_tags.__dict__
        else:
            return []
        for k, v in d.items():
            if isinstance(v, list):
                for item in v:
                    if item:
                        result.append(f"{k}:{item}")
            elif v:
                result.append(f"{k}={v}")
    except Exception:
        pass
    return result


def _safe_dict(obj) -> dict:
    """安全地将对象转为 dict"""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    return {}


# ─── NPC 转换 ───


def node_to_npc(node: dict):
    """
    从 nodes 表的节点 dict 重建 NPC 模型。

    转换逻辑：
      data.attributes._extra 中存了 NPC 模型的扩展字段
      （level, status, inventory, position_zone_id, physical, relationships）。
    """
    from ..models.npc import NPC, NPCStatus

    try:
        data = node["data"] if isinstance(node["data"], dict) else json.loads(node["data"])
        attrs = data.get("attributes", {})
        extra = attrs.get("_extra", {})

        npc = NPC(name=node["name"], role=data.get("role", ""))
        npc.vitality = attrs.get("vitality", 100)
        npc.satiety = attrs.get("satiety", 50)
        npc.mood = attrs.get("mood", 50)
        npc.level = extra.get("level", 1)
        npc.status = NPCStatus(extra.get("status", "active"))
        npc.inventory = list(extra.get("inventory", []))
        npc.position.zone_id = extra.get("position_zone_id", "")
        npc.attributes = dict(attrs)

        _physical_raw = extra.get("physical", {})
        if isinstance(_physical_raw, dict):
            from ..models.npc import PhysicalAttributes
            try:
                npc.physical = PhysicalAttributes(**_physical_raw)
            except Exception:
                npc.physical = PhysicalAttributes()

        _rel_raw = extra.get("relationships", {})
        if isinstance(_rel_raw, dict):
            npc.relationships = dict(_rel_raw)

        return npc
    except Exception as e:
        logger.warning(f"[node_to_npc] 失败 {node.get('id','?')}: {e}")
        return None


def npc_to_node_dict(npc) -> dict:
    """
    将 NPC 业务模型转为 nodes 表所需的 data dict。

    用于：
      - db.py 的 _seed_from_config()
      - api/npc.py 的 create/update
    """
    p_tags = _persona_tags_to_list(getattr(npc, "persona_tags", None))
    attributes = dict(getattr(npc, "attributes", None) or {})
    attributes["vitality"] = getattr(npc, "vitality", 100)
    attributes["satiety"] = getattr(npc, "satiety", 50)
    attributes["mood"] = getattr(npc, "mood", 50)
    attributes["_extra"] = {
        "level": getattr(npc, "level", 1),
        "status": getattr(npc.status, "value", "active") if hasattr(npc, "status") else "active",
        "inventory": list(getattr(npc, "inventory", [])),
        "position_zone_id": getattr(getattr(npc, "position", None), "zone_id", ""),
        "physical": _safe_dict(getattr(npc, "physical", {})),
        "relationships": _safe_dict(getattr(npc, "relationships", {})),
    }
    return {
        "role": npc.role,
        "desc": "",
        "traits": p_tags,
        "attributes": attributes,
        "connected_entity_ids": [],
        "conserved": False,
        "space": "physical",
        "recent_info": attributes.get("_recent_info", ""),
    }
