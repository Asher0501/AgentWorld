"""
NPC 默认数据 — 从 node_config.json 读取，不再维护本地工厂函数。
"""

from .npc import NPC, Position, PhysicalAttributes, PersonaTags


def _expand_inventory(inv: dict[str, int]) -> list[str]:
    """{物品名: 数量} → [物品名] * 数量"""
    result = []
    for name, qty in inv.items():
        result.extend([name] * qty)
    return result


def _make_npc_from_dict(d: dict) -> NPC:
    """从 config dict 构造 NPC 对象"""
    physical_kw = d.get("physical", {})
    persona_kw = d.get("persona", {})
    inv = _expand_inventory(d.get("inventory", {}))

    return NPC(
        name=d["name"],
        role=d["role"],
        position=Position(zone_id=d.get("zone", "village_square")),
        vitality=d.get("vitality", 100.0),
        satiety=d.get("satiety", 50.0),
        mood=d.get("mood", 50.0),
        inventory=inv,
        attributes={
            "strength": 10, "intelligence": 10,
            "charisma": 10, "endurance": 10, "wisdom": 10,
            "_recent_info": d.get("recent_info", "[]"),
        },
        physical=PhysicalAttributes(
            energy_capacity=physical_kw.get("energy_capacity", 100.0),
            health=physical_kw.get("health", 100.0),
            recovery_speed=physical_kw.get("recovery_speed", 1.0),
            age=physical_kw.get("age", 30),
        ),
        persona_tags=PersonaTags(
            work_ethic=persona_kw.get("work_ethic", "普通"),
            social_class=persona_kw.get("social_class", "平民"),
            reputation=persona_kw.get("reputation", "普通"),
            interests=persona_kw.get("interests", []),
            personality=persona_kw.get("personality", []),
            special_traits=persona_kw.get("special_traits", []),
        ),
    )


def create_diverse_npcs(small: bool = False) -> list[NPC]:
    """从 node_config.json 读取 NPC 定义并实例化"""
    from ..config.config_loader import get_npc_defs

    defs = get_npc_defs(small=small)
    return [_make_npc_from_dict(d) for d in defs]
