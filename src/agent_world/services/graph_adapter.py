"""
Graph Adapter：将世界配置 → 纯拓扑节点

不再创建任何接口（EntityInterface）。Node 只携带：
- self description（type/role/attrs/traits/desc）
- space（physical / abstract）
- connected_nodes（拓扑连接，指向其他实体的 type:name 引用）
"""

from __future__ import annotations
import logging
import uuid
from typing import Any

from ..entities.base_entity import Entity

logger = logging.getLogger(__name__)

# ─── 类型:名称 → entity ID 解析 ───

def resolve_ref(ref: str) -> str:
    """
    将配置中的 'type:name' 引用解析为 entity_id。

    示例：
      resolve_ref('npc:杰洛特')   → 'npc_97845b74'
      resolve_ref('item:草药')     → 'item_e65c06d5'
      resolve_ref('zone:白果园')   → 'zone_白果园'
      resolve_ref('recipe:研磨魔药') → 'recipe_研磨魔药'
    """
    if ":" not in ref:
        return ref  # 已是完整 entity_id
    type_, name = ref.split(":", 1)
    if type_ == "zone":
        return _make_zone_eid(name)
    elif type_ == "recipe":
        return f"recipe_{name}"
    else:
        return _make_eid(type_, name)


# ─── Entity 创建 ───

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

    # 读回 primary_goal（迁移自 node_config.json）
    raw_pg = getattr(config, "attributes", {}).get("primary_goal", "")
    if raw_pg:
        ent.attributes["primary_goal"] = raw_pg

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
    eid = _make_zone_eid(config.name)
    ent = Entity(entity_id=eid, name=config.name)
    ent.role = getattr(config, "role", "")
    ent.desc = getattr(config, "desc", "")
    ent.attributes["capacity"] = float(getattr(config, "capacity", 100))
    ent.attributes["is_safe"] = float(getattr(config, "is_safe", True))
    # 显式存储 zone_type，供 prompt 渲染时展示区域功能类型
    zt = getattr(config, "zone_type", "")
    if zt:
        ent.attributes["zone_type"] = zt
    return ent


def recipe_to_entity(config: dict) -> Entity:
    """从 recipe 配置创建抽象空间实体。"""
    eid = f"recipe_{config['name']}"
    ent = Entity(entity_id=eid, name=config['name'])
    ent.space = "abstract"
    ent.role = "recipe"
    ent.desc = config.get("description", "")
    ent.attributes["consumes"] = config.get("inputs", {})
    ent.attributes["produces"] = config.get("outputs", {})
    ent.attributes["need_zone"] = config.get("zone", "")
    ent.attributes["need_tool"] = config.get("tool", "")
    ent.attributes["tool_interface"] = config.get("tool_interface", "")
    ent.attributes["vitality_cost"] = config.get("vitality_cost", 0)
    return ent


# ─── 世界图构建（主入口）───

def build_world_graph(npcs: list, objects: list, zones: list,
                      items: list[dict] | None = None,
                      recipes: list[dict] | None = None,
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

    # 2. 创建物品
    if items:
        for cfg in items:
            ent = item_to_entity(cfg["name"])
            if ent:
                entities[ent.entity_id] = ent

    # 3. 创建对象
    if mgr:
        for obj in mgr.all():
            oid = f"obj_{obj.id[:8]}" if hasattr(obj, 'id') else obj.entity_id
            if oid not in entities:
                name = getattr(obj, 'name', oid)
                ent = Entity(entity_id=oid, name=name)
                ent.role = getattr(obj, 'object_type', "")
                ent.attributes["state"] = "intact"
                entities[oid] = ent

    # 4. 创建区域
    for cfg in zones:
        ent = zone_to_entity(cfg)
        if ent:
            entities[ent.entity_id] = ent

    # 5. 创建配方（抽象空间节点）
    if recipes:
        for cfg in recipes:
            ent = recipe_to_entity(cfg)
            if ent:
                entities[ent.entity_id] = ent

    logger.info(f"[Adapter] 构建完毕: {len(entities)} 个实体")
    return entities


# ─── 图连接 ───

def init_graph_edges_from_adapter(ge, npcs: list, zones: list):
    """
    在 GraphEngine 上创建所有初始边（向后兼容版）。

    本函数保持旧签名不变，处理以下连接：
      1. NPC → 初始区域（从 zone 字段）
      2. NPC → 初始库存（从 inventory 字段）
      3. 区域 → 相邻区域（从 Zone 模型的 connected_zones）

    参数：
        ge: GraphEngine 实例（已注册所有实体）
        npcs: NPC 配置/模型列表
        zones: Zone 模型对象列表
    """
    zone_lookup = _build_zone_lookup(zones)

    # NPC → 区域 + NPC → 物品（初始库存）
    for cfg in npcs:
        npc_eid = _make_eid("npc", cfg.name)

        # NPC → 区域
        zone_key = _get_zone_for(cfg)
        if zone_key:
            zone_eid = _resolve_zone_eid(ge, zone_key, zone_lookup)
            if zone_eid:
                ge.connect(npc_eid, zone_eid, qty=1)

        # NPC → 物品（初始库存）
        raw_inv = _normalize_inventory(cfg)
        for item_name, qty in raw_inv.items():
            item_eid = _make_eid("item", item_name)
            if not ge.get_entity(item_eid):
                ge.register_entity(item_to_entity(item_name, qty))
            ge.connect(npc_eid, item_eid)
            ge.set_edge_quantity(npc_eid, item_eid, qty)

    # 区域双向连接
    for cfg in zones:
        zone_eid = _make_zone_eid(cfg.name)
        connects = getattr(cfg, "connects_to", []) or getattr(cfg, "connected_zones", [])
        for neighbor_name in connects:
            neighbor_eid = _resolve_zone_eid(ge, neighbor_name, zone_lookup)
            if zone_eid and neighbor_eid:
                if not ge.get_edge(zone_eid, neighbor_eid):
                    ge.connect(zone_eid, neighbor_eid)
                    ge.connect(neighbor_eid, zone_eid)

    logger.info(f"[Adapter] 初始边创建完毕 ({len(npcs)} NPC, {len(zones)} 区域)")


def process_config_edges(
    ge,
    npc_config_dicts: list[dict] | None = None,
    item_config_dicts: list[dict] | None = None,
    zone_config_dicts: list[dict] | None = None,
    recipe_config_dicts: list[dict] | None = None,
):
    """
    从配置的 connected_nodes 补充图边。

    - NPC config: NPC→zone, NPC→recipe, NPC→item（出边配置全部已包含）
    - Zone config: zone→NPC, zone→item（补全反向连接，zone→item 设 qty=-1）
    - Recipe config: recipe→抽象空间内部（如有）

    连接方向规则：connected_nodes 中的每条记录是 src→tgt 单向。
    双向连接由两端各自配置实现。
    """
    # === NPC 出边 ===
    if npc_config_dicts:
        for cfg in npc_config_dicts:
            name = cfg.get("name", "")
            if not name:
                continue
            src_id = _make_eid("npc", name)
            if not ge.get_entity(src_id):
                logger.warning(f"[ConfigEdges] NPC 实体不存在: {name} ({src_id})")
                continue
            for tgt_ref in cfg.get("connected_nodes", []):
                tgt_id = resolve_ref(tgt_ref)
                if not ge.get_entity(tgt_id):
                    logger.warning(f"[ConfigEdges] 目标不存在: {tgt_ref} ({tgt_id})")
                    continue
                # 跳过已连接的（初始库存/区域已建边）
                # 用 connected_entity_ids 检查方向性，而不是 get_edge（它做双向查找）
                cache_src = ge.get_entity(src_id)
                if cache_src and tgt_id not in cache_src.connected_entity_ids:
                    ge.connect(src_id, tgt_id)

    # === Zone 出边 ===
    if zone_config_dicts:
        for cfg in zone_config_dicts:
            # domain.json 的 id 是英文 key（如 white_orchard），
            # entity ID 使用中文名称（如 zone_白果园），需用 name 解析
            src_id = resolve_ref(f"zone:{cfg.get('name', '')}")
            cache_ent = ge.get_entity(src_id)
            if not cache_ent:
                continue
            for tgt_ref in cfg.get("connected_nodes", []):
                tgt_id = resolve_ref(tgt_ref)
                if not ge.get_entity(tgt_id):
                    continue
                if tgt_id not in cache_ent.connected_entity_ids:
                    ge.connect(src_id, tgt_id)
                # zone→item 无限产出
                if tgt_ref.startswith("item:"):
                    ge.set_edge_quantity(src_id, tgt_id, -1)

    # === Recipe 出边 ===
    if recipe_config_dicts:
        for cfg in recipe_config_dicts:
            src_id = resolve_ref(f"recipe:{cfg['name']}")
            cache_src = ge.get_entity(src_id)
            if not cache_src:
                continue
            for tgt_ref in cfg.get("connected_nodes", []):
                tgt_id = resolve_ref(tgt_ref)
                if not ge.get_entity(tgt_id):
                    continue
                if tgt_id not in cache_src.connected_entity_ids:
                    ge.connect(src_id, tgt_id)

    # === Item 出边 ===
    if item_config_dicts:
        for cfg in item_config_dicts:
            name = cfg.get("name", "")
            if not name:
                continue
            src_id = _make_eid("item", name)
            cache_src = ge.get_entity(src_id)
            if not cache_src:
                continue
            for tgt_ref in cfg.get("connected_nodes", []):
                tgt_id = resolve_ref(tgt_ref)
                if not ge.get_entity(tgt_id):
                    continue
                if tgt_id not in cache_src.connected_entity_ids:
                    ge.connect(src_id, tgt_id)

    # 注：NPC→item 数量不从 config 默认值设置，
    # 由之前的 init_graph_edges_from_adapter(ge, db_npcs, ...) 从 DB 持久化值设置。
    # 此处再覆盖会重置掉上 tick 的 LLM #4a delta 效果。

    logger.info(f"[ConfigEdges] 配置边补充完毕")


# ═══════════════════════════════════════════
# Entity ↔ DB Node Data 转换
# ═══════════════════════════════════════════

# type 映射表（供 entity_to_node_dict 使用）
_ID_TO_TYPE: dict[str, str] = {}


def _init_type_map():
    """初始化 entity_id → type_name 映射"""
    global _ID_TO_TYPE
    if _ID_TO_TYPE:
        return
    from ..config.config_loader import get_all_prefixes, get_type_def
    for pfx in get_all_prefixes():
        # 去掉尾部下划线
        role_str = pfx.rstrip("_")
        _ID_TO_TYPE[role_str] = role_str


def entity_to_node_dict(entity) -> dict:
    """
    将 Entity 序列化为 DB nodes 表的 data 列可存格式
    """
    return {
        "id": entity.entity_id,
        "type": entity.entity_type or "npc",
        "name": entity.name,
        "data": {
            "role": entity.role,
            "desc": entity.desc,
            "traits": list(entity.traits),
            "attributes": dict(entity.attributes),
            "connected_entity_ids": list(entity.connected_entity_ids),
            "conserved": entity.conserved,
            "space": entity.space,
            "recent_info": entity.recent_info,
        },
    }


def node_dict_to_entity(node: dict) -> Entity:
    """
    从 DB nodes 表的记录重建 Entity
    """
    data = node["data"] if isinstance(node["data"], dict) else json.loads(node["data"])
    ent = Entity(entity_id=node["id"], name=node["name"], entity_type=node["type"])
    ent.role = data.get("role", "")
    ent.desc = data.get("desc", "")
    ent.traits = list(data.get("traits", []))
    ent.attributes = dict(data.get("attributes", {}))
    for conn in data.get("connected_entity_ids", []):
        ent.connect_to(conn)
    ent.conserved = data.get("conserved", False)
    ent.space = data.get("space", "physical")
    ent.recent_info = data.get("recent_info", "")
    return ent


def build_graph_from_nodes(ge, node_data_list: list[dict]):
    """
    从 nodes 表读取的节点列表注册到图引擎。
    包含 entity 注册 + 边恢复。
    """
    # 1. 注册所有实体
    for nd in node_data_list:
        ent = node_dict_to_entity(nd)
        ge.register_entity(ent)

    # 2. 从 connected_entity_ids 重建边
    data_eids = {nd["id"] for nd in node_data_list}
    for nd in node_data_list:
        src_id = nd["id"]
        data = nd["data"] if isinstance(nd["data"], dict) else json.loads(nd["data"])
        for tgt_id in data.get("connected_entity_ids", []):
            if tgt_id in data_eids:  # 只连已知实体
                # 不覆盖 qty（重建时默认=1，之后由 process_config_edges 修正）
                if not ge.get_edge(src_id, tgt_id):
                    ge.connect(src_id, tgt_id, qty=1)

    total = len(node_data_list)
    logger.info(f"[Adapter] 从 nodes 构建: {total} 个实体")


def sync_graph_to_nodes(node_db, ge):
    """
    将图引擎所有实体状态同步回 DB nodes 表。
    tick 结束时调用。
    """
    nodes = []
    for ent in ge.all_entities():
        nd = entity_to_node_dict(ent)
        nodes.append(nd)
    if nodes:
        node_db.upsert_many(nodes)
    logger.info(f"[Adapter] 同步 {len(nodes)} 个实体回 nodes 表")


def sync_entity_to_db(node_db, entity):
    """
    将单个实体同步回 DB nodes 表。
    在 LLM 自动注册新实体时调用。
    """
    nd = entity_to_node_dict(entity)
    node_db.upsert_node(nd["id"], nd["type"], nd["name"], nd["data"])


# ─── 辅助 ───

def _make_eid(subject: str, name: str) -> str:
    """生成唯一实体 ID。支持两种用法：
    - role 模式: _make_eid("actor", name) → 从 config 查 actor 角色的前缀
    - legacy 模式: _make_eid("npc", name) → 直接用 "npc" 作为前缀
    """
    from ..config.config_loader import get_prefix_by_role
    pfx = get_prefix_by_role(subject)
    if pfx is None:
        pfx = f"{subject}_"  # legacy fallback
    safe_name = name.replace(" ", "_").replace("　", "_")
    import hashlib
    h = hashlib.md5(safe_name.encode()).hexdigest()[:8]
    return f"{pfx}{h}"


def _make_zone_eid(name: str, *, from_role: str = "region") -> str:
    """使用 config 前缀生成区域 EID。区域使用名称直接作为 ID（不 hash）。"""
    from ..config.config_loader import get_prefix_by_role
    pfx = get_prefix_by_role(from_role) or "zone_"
    return f"{pfx}{name}"


def _build_zone_lookup(zones: list) -> dict[str, str]:
    """
    构建 {可解析名称: entity_eid} 映射。

    zone_to_entity() 用 cfg.name（中文显示名）构造 eid 如 zone_狐狸与鹅酒馆，
    但 NPC 初始区域的 zone_id 可能是 config_key（如 fox_and_goose）或中文名。
    本映射将所有可解析名称指向同一个 entity eid。
    """
    lookup: dict[str, str] = {}
    for cfg in zones:
        name = cfg.get("name", "") if isinstance(cfg, dict) else getattr(cfg, "name", "")
        zid = cfg.get("id", "") if isinstance(cfg, dict) else getattr(cfg, "id", "")
        if not name:
            continue
        eid = _make_zone_eid(name)
        lookup[name] = eid
        if zid and zid != name:
            lookup[zid] = eid
    return lookup


def _resolve_zone_eid(ge, raw: str, zone_lookup: dict[str, str]) -> str | None:
    """多策略解析 zone entity ID"""
    if raw in zone_lookup:
        return zone_lookup[raw]
    if ge.get_entity(raw):
        return raw
    constructed = _make_zone_eid(raw)
    if ge.get_entity(constructed):
        return constructed
    match = ge.find_entity_by_name(raw)
    if match:
        return match.entity_id
    return None


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
