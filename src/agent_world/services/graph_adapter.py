"""
Graph Adapter：将世界配置 → 纯拓扑节点

不再创建任何接口（EntityInterface）。Node 只携带：
- self description（type/role/attrs/traits/desc）
"""

from __future__ import annotations
import logging
import uuid
from typing import Any

from ..entities.base_entity import Entity
from ..config.config_loader import has_role, get_domain_npc_zones

logger = logging.getLogger(__name__)

# ─── NPC → Entity ───

def npc_to_entity(config: Any) -> Entity:
    if not config or not hasattr(config, "name"):
        return None
    eid = _make_eid("npc", config.name)
    ent = Entity(entity_id=eid, name=config.name)
    ent.role = str(getattr(config, "role", "")) if not isinstance(getattr(config, "role", ""), str) else getattr(config, "role", "")

    # 从 DB 模型读取 traits（兼容 persona_tags / personality_tags / traits）
    raw_traits = (getattr(config, "persona_tags", []) or
                  getattr(config, "personality_tags", []) or
                  getattr(config, "traits", []))
    ent.traits = []
    for t in raw_traits:
        if isinstance(t, dict):
            ent.traits.append(t.get("tag", str(t)))
        else:
            ent.traits.append(str(t))

    ent.desc = getattr(config, "desc", "")

    # 属性（从 DB 值读取，有则用，无则默认）
    ent.attributes["vitality"] = float(getattr(config, "vitality", 100.0))
    ent.attributes["satiety"] = float(getattr(config, "satiety", 100.0))
    ent.attributes["mood"] = float(getattr(config, "mood", 50.0))
    ent.attributes["strength"] = float(getattr(config, "strength", 50.0))
    ent.attributes["consciousness"] = 100.0

    # 读回上 tick 的近况投影（由 _sync_back_to_nodes 写入 attributes._recent_info）
    raw_ri = getattr(config, "attributes", {}).get("_recent_info", "")
    if raw_ri:
        ent.recent_info = raw_ri

    return ent


def item_to_entity(name: str, initial_qty: int = 1) -> Entity:
    eid = _make_eid("item", name)
    ent = Entity(entity_id=eid, name=name)
    ent.desc = f"这是一个物品，可以持有、使用、交易。"
    ent.conserved = True
    return ent


def zone_to_entity(config: Any) -> Entity:
    if not config or not hasattr(config, "name"):
        return None
    eid = f"zone_{config.name}"
    ent = Entity(entity_id=eid, name=config.name)
    ent.role = getattr(config, "role", "")
    ent.desc = getattr(config, "desc", "")
    ent.attributes["capacity"] = float(getattr(config, "capacity", 100))
    ent.attributes["is_safe"] = float(getattr(config, "is_safe", True))
    return ent


# ─── 世界图构建（主入口）───

def build_world_graph(npcs: list, objects: list, zones: list,
                      mgr=None) -> dict[str, Entity]:
    """
    从世界配置构建实体图（纯节点）。

    返回 {entity_id: Entity} 实体字典。
    边的创建必须通过 GraphEngine，由调用方在注册实体后调用
    init_graph_edges_from_adapter() 完成。
    """
    entities: dict[str, Entity] = {}

    # 1. 创建 NPC
    for cfg in npcs:
        ent = npc_to_entity(cfg)
        if ent:
            entities[ent.entity_id] = ent

    # 2. 创建对象（如果有 WorldObjectManager）
    if mgr:
        for obj in mgr.all():
            oid = f"obj_{obj.id[:8]}" if hasattr(obj, 'id') else obj.entity_id
            if oid not in entities:
                name = getattr(obj, 'name', oid)
                ent = Entity(entity_id=oid, name=name)
                ent.role = getattr(obj, 'object_type', "")
                ent.attributes["state"] = "intact"
                entities[oid] = ent

    # 3. 创建区域
    for cfg in zones:
        ent = zone_to_entity(cfg)
        if ent:
            entities[ent.entity_id] = ent

    logger.info(f"[Adapter] 构建完毕: {len(entities)} 个实体")
    return entities


def _build_zone_lookup(zones: list) -> dict[str, str]:
    """
    构建 {可解析名称: entity_eid} 映射。

    zone_to_entity() 用 cfg.name（中文显示名）构造 eid 如 zone_狐狸与鹅酒馆，
    但 NPC 初始区域的 zone_id 可能是 config_key（如 fox_and_goose）或中文名。
    本映射将所有可解析名称指向同一个 entity eid。
    """
    lookup: dict[str, str] = {}
    for cfg in zones:
        # 兼容 dict 和 object 两种 zone 配置格式
        name = cfg.get("name", "") if isinstance(cfg, dict) else getattr(cfg, "name", "")
        zid = cfg.get("id", "") if isinstance(cfg, dict) else getattr(cfg, "id", "")
        if not name:
            continue
        eid = f"zone_{name}"
        lookup[name] = eid       # "狐狸与鹅酒馆" → "zone_狐狸与鹅酒馆"
        if zid and zid != name:
            lookup[zid] = eid    # "fox_and_goose" → "zone_狐狸与鹅酒馆"
    return lookup


def _resolve_zone_eid(ge, raw: str, zone_lookup: dict[str, str]) -> str | None:
    """多策略解析 zone entity ID

    策略顺序：
    1. zone_lookup[raw] — config ID / display name 查映射表
    2. ge.get_entity(raw) — 直接 eid 匹配（如 zone_狐狸与鹅酒馆）
    3. ge.get_entity(f'zone_{raw}') — 构造 zone_前缀
    4. ge.find_entity_by_name(raw) — 按 display name 查找
    """
    # 1. config key / display name lookup
    if raw in zone_lookup:
        return zone_lookup[raw]
    # 2. 已是完整 eid
    if ge.get_entity(raw):
        return raw
    # 3. 尝试 zone_ 前缀
    constructed = f"zone_{raw}"
    if ge.get_entity(constructed):
        return constructed
    # 4. 按 display name 查找
    match = ge.find_entity_by_name(raw)
    if match:
        return match.entity_id
    return None


def init_graph_edges_from_adapter(ge, npcs: list, zones: list):
    """
    在 GraphEngine 上创建所有初始边。

    参数：
        ge: GraphEngine 实例（已注册所有实体）
        npcs: NPC 配置列表
        zones: 区域配置列表（Zone 模型对象，含 .id 和 .name）
    """
    zone_lookup = _build_zone_lookup(zones)

    # 从 domain.json 读取权威 NPC 初始区域
    domain_zones = get_domain_npc_zones()

    for cfg in npcs:
        npc_eid = _make_eid("npc", cfg.name)

        # NPC → 区域（位置）
        # 权威来源: domain.json > DB position
        zone_key = domain_zones.get(cfg.name) or _get_zone_for(cfg)
        if zone_key:
            zone_eid = _resolve_zone_eid(ge, zone_key, zone_lookup)
            if zone_eid:
                ge.connect(npc_eid, zone_eid, -1)

        # NPC → 物品（初始库存）
        raw_inv = _normalize_inventory(cfg)
        for item_name, qty in raw_inv.items():
            item_eid = _make_eid("item", item_name)
            if not ge.get_entity(item_eid):
                ge.register_entity(item_to_entity(item_name, qty))
            ge.connect(npc_eid, item_eid, qty)

    # 区域双向连接
    for cfg in zones:
        zone_eid = f"zone_{cfg.name}"
        connects = getattr(cfg, "connects_to", []) or getattr(cfg, "connected_zones", [])
        for neighbor_name in connects:
            neighbor_eid = _resolve_zone_eid(ge, neighbor_name, zone_lookup)
            if zone_eid and neighbor_eid:
                if not ge.get_edge(zone_eid, neighbor_eid):
                    ge.connect(zone_eid, neighbor_eid, 0)
                    ge.connect(neighbor_eid, zone_eid, 0)

    logger.info(f"[Adapter] 初始边创建完毕 ({len(npcs)} NPC, {len(zones)} 区域)")


# ─── 辅助 ───

def _make_eid(prefix: str, name: str) -> str:
    """生成唯一实体 ID"""
    safe_name = name.replace(" ", "_").replace("　", "_")
    import hashlib
    h = hashlib.md5(safe_name.encode()).hexdigest()[:8]
    return f"{prefix}_{h}"


def _get_zone_for(cfg) -> str:
    """从各种可能的字段名中提取 zone id"""
    pos = getattr(cfg, "position", None)
    if pos and isinstance(pos, dict):
        return pos.get("zone_id", "")
    if pos and hasattr(pos, "zone_id"):
        return pos.zone_id
    return getattr(cfg, "zone", "")


def _normalize_inventory(cfg) -> dict[str, int]:
    """兼容 list[str] 和 dict[str,int] 两种库存格式"""
    raw = getattr(cfg, "inventory", []) or getattr(cfg, "items", [])
    if isinstance(raw, dict):
        return raw
    inv = {}
    for item in raw:
        name = item.name if hasattr(item, 'name') else str(item)
        inv[name] = inv.get(name, 0) + 1
    return inv
