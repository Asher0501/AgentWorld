"""
Config Loader — 从 node_config.json 驱动所有节点类型和实体定义。

职责：
  1. 读取 node_config.json
  2. 按声明顺序自动分配全局 type_id（从 1 开始）
  3. 提供抽象查询：开关状态、角色标签、前缀→ID
  4. 提供实体视图：区域/物品/物件列表（含 bounds / connections）
  5. 提供世界配置：区域连接、默认时间

接口设计原则：
  - 不暴露节点类型名（"npc"、"zone" 等），只暴露 type_id 数字
  - 用角色标签（roles）替代类型名比较
  - 所有语义字符串只在 node_config.json 中出现
"""

from __future__ import annotations
import json, os
from typing import Any

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "node_config.json")

# ─── 加载时填充 ───
_TYPE_DEFS: dict[int, dict[str, Any]] = {}         # type_id → 完整定义
_TYPE_PREFIX_TO_ID: dict[str, int] = {}            # "npc_" → 1
_ENTITIES: dict[str, list[dict]] = {}              # "zones" / "items" / "objects"
_ENTITY_INDEX: dict[str, dict[str, dict]] = {}     # type → {id → entity}
_ZONE_CONNECTIONS: dict[str, list[str]] = {}       # zone_id → [connected zone_ids]
_RAW_CONFIG: dict[str, Any] = {}                    # 完整原始配置
_WORLD_CONFIG: dict[str, Any] = {}                 # world 顶级配置
_LABEL_MAP: dict[str, str] = {}                    # 实体名称 → 拓扑标签


def _load() -> None:
    """加载并解析 node_config.json（幂等）"""
    if _TYPE_DEFS:
        return

    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # ── 0. 原始配置 + 世界顶级配置 ──
    _RAW_CONFIG.clear()
    _RAW_CONFIG.update(config)
    _WORLD_CONFIG.clear()
    _WORLD_CONFIG.update(config.get("world", {}))

    # ── 1. 分配 type_id ──
    for idx, tdef in enumerate(config.get("node_types", []), start=1):
        tid = idx
        tdef["_type_id"] = tid
        _TYPE_DEFS[tid] = tdef

        prefix = tdef.get("prefix", f"{tdef['id']}_")
        _TYPE_PREFIX_TO_ID[prefix] = tid

    # ── 2. 区域连接 ──
    # ── 1.5. 标签映射 ──
    _LABEL_MAP.clear()
    for entry in config.get("label_mappings", {}).get("labels", []):
        name = entry.get("name", "")
        label = entry.get("label", "")
        if name and label:
            _LABEL_MAP[name] = label

    _ZONE_CONNECTIONS.clear()
    for conn in _WORLD_CONFIG.get("zone_connections", []):
        from_zone = conn["from"]
        to_zones = conn.get("to", [])
        _ZONE_CONNECTIONS[from_zone] = to_zones
        for toz in to_zones:
            if toz not in _ZONE_CONNECTIONS:
                _ZONE_CONNECTIONS[toz] = []
            if from_zone not in _ZONE_CONNECTIONS[toz]:
                _ZONE_CONNECTIONS[toz].append(from_zone)

    # ── 3. 加载实体 ──
    entities = config.get("entities", {})
    _ENTITIES.clear()
    _ENTITY_INDEX.clear()
    for category, items in entities.items():
        _ENTITIES[category] = items
        if not isinstance(items, list):
            # npc_sets 是 dict {small:[], default:[]}，跳过索引
            continue
        idx_map: dict[str, dict] = {}
        for item in items:
            eid = item.get("id", "")
            if eid:
                idx_map[eid] = item
        _ENTITY_INDEX[category] = idx_map


# ═══════════════════════════════════════════
# 抽象查询接口（不暴露类型名）
# ═══════════════════════════════════════════

def all_type_ids() -> list[int]:
    """获取所有已注册的 type_id"""
    _load()
    return sorted(_TYPE_DEFS.keys())


def get_type_def(type_id: int) -> dict[str, Any]:
    """获取类型完整定义（含 switches / prompt / roles / properties）"""
    _load()
    return _TYPE_DEFS.get(type_id, {})


def get_type_prefix(type_id: int) -> str:
    """获取实体 ID 前缀，如 → 'npc_'"""
    _load()
    tdef = _TYPE_DEFS.get(type_id, {})
    return tdef.get("prefix", "unknown_")


# ─── 前缀 → type_id ───

def prefix_to_type_id(eid: str) -> int:
    """从实体 ID 前缀推断 type_id，如 'npc_老陈' → 1"""
    _load()
    for pfx, tid in _TYPE_PREFIX_TO_ID.items():
        if eid.startswith(pfx):
            return tid
    return 0


# ─── 开关查询 ───

def is_terminal(type_id: int) -> bool:
    """BFS 是否在此类型停下？"""
    _load()
    switches = _TYPE_DEFS.get(type_id, {}).get("switches", {})
    return bool(switches.get("terminal", False))


def is_same_type_blocked(type_id: int) -> bool:
    """同类型边阻断 BFS？"""
    _load()
    switches = _TYPE_DEFS.get(type_id, {}).get("switches", {})
    return bool(switches.get("same_type_block", False))


def has_recent_info(type_id: int) -> bool:
    """此类型支持 recent_info 投影？"""
    _load()
    switches = _TYPE_DEFS.get(type_id, {}).get("switches", {})
    return bool(switches.get("has_recent_info", False))


# ─── 标签映射查询 ───

def get_all_label_mappings() -> dict[str, str]:
    """获取完整标签映射：实体名称 → 拓扑标签（如 {'王老板': 'A', '蔬菜': 'C', ...}）"""
    _load()
    return dict(_LABEL_MAP)


def get_label_for_name(name: str) -> str | None:
    """按实体名称查拓扑标签，未配置则返回 None"""
    _load()
    return _LABEL_MAP.get(name)


# ─── 角色标签查询 ───

def has_role(type_id: int, role: str) -> bool:
    """
    此类型是否持有指定角色标签？

    role 参数是语义字符串（如 "actor"、"region"、"fixture"），
    但只在调用时传入，不在接口签名中固化。
    所有 role 标签定义在 node_config.json 的 node_types[].roles[] 中。
    """
    _load()
    roles = _TYPE_DEFS.get(type_id, {}).get("roles", [])
    return role in roles


# ─── 实体查询 ───

def get_zones() -> list[dict]:
    """获取所有区域定义（含 bounds / capacity）"""
    _load()
    return _ENTITIES.get("zones", [])


def get_zone(zone_id: str) -> dict | None:
    """按 ID 获取单个区域定义"""
    _load()
    return _ENTITY_INDEX.get("zones", {}).get(zone_id)


def get_items() -> list[dict]:
    """获取所有物品定义"""
    _load()
    return _ENTITIES.get("items", [])


def get_objects() -> list[dict]:
    """获取所有物件定义"""
    _load()
    return _ENTITIES.get("objects", [])


def get_entity(category: str, eid: str) -> dict | None:
    """按类别和 ID 查实体"""
    _load()
    return _ENTITY_INDEX.get(category, {}).get(eid)


# ─── 区域拓扑 ───

def get_zone_connections(zone_id: str) -> list[str]:
    """获取某区域连接的所有区域 ID 列表"""
    _load()
    return _ZONE_CONNECTIONS.get(zone_id, [])


def get_all_zone_connections() -> dict[str, list[str]]:
    """获取全部区域连接表（双向）"""
    _load()
    return dict(_ZONE_CONNECTIONS)


def get_zone_bounds(zone_id: str) -> dict:
    """获取区域边界"""
    _load()
    z = _ENTITY_INDEX.get("zones", {}).get(zone_id, {})
    return z.get("bounds", {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100})


def get_zone_capacity(zone_id: str) -> int:
    """获取区域容量"""
    _load()
    z = _ENTITY_INDEX.get("zones", {}).get(zone_id, {})
    return z.get("capacity", 20)


# ─── 世界配置 ───

def get_world_default_time() -> dict:
    """获取世界默认时间"""
    _load()
    return _WORLD_CONFIG.get("default_time", {"hour": 8, "minute": 0, "day": 1, "month": 1, "year": 1})


def get_world_config(key: str, default=None):
    """获取 world 顶级配置中的任意键值"""
    _load()
    return _WORLD_CONFIG.get(key, default)


def get_verification_config(key: str, default=None):
    """获取 verification 校验配置（注册表/重试次数等）"""
    _load()
    v = _RAW_CONFIG.get("verification", {})
    return v.get(key, default)


def is_verification_check_enabled(check_name: str) -> bool:
    """查询特定校验项是否开启"""
    _load()
    checks = _RAW_CONFIG.get("verification", {}).get("checks", {})
    check = checks.get(check_name, {})
    return check.get("enabled", False)


# ─── Zone 对象构建 ───

def build_zone_models() -> list[dict]:
    """构建可直接传给 init_entity_manager 的 zone 字典列表（兼容旧格式）"""
    _load()
    return [
        {"id": z["id"], "zone_type": z.get("zone_type", z["id"])}
        for z in _ENTITIES.get("zones", [])
    ]


def build_zone_model_full(zone_id: str) -> dict | None:
    """构建完整 Zone 模型字典（含 bounds / connections / capacity 等）"""
    _load()
    z = _ENTITY_INDEX.get("zones", {}).get(zone_id)
    if not z:
        return None
    return {
        "id": z["id"],
        "name": z["name"],
        "zone_type": z.get("zone_type", z["id"]),
        "description": z.get("description", ""),
        "bounds": z.get("bounds", {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100}),
        "capacity": z.get("capacity", 20),
        "connected_zones": _ZONE_CONNECTIONS.get(zone_id, []),
    }


# ─── NPC 配置读取（从 JSON 返回原始 dict，不依赖 NPC 模型）───

def get_npc_defs(small: bool = False) -> list[dict]:
    """从 node_config.json 返回 NPC 定义列表（原始 dict，非 NPC 对象）"""
    _load()
    key = "small" if small else "default"
    names = _ENTITIES.get("npc_sets", {}).get(key, [])
    all_npcs = {n["name"]: n for n in _ENTITIES.get("npcs", [])}
    result = []
    for n in names:
        d = all_npcs.get(n)
        if d:
            result.append(d)
        else:
            print(f"[Config] WARNING: NPC '{n}' 在 config 中未定义")
    return result


def get_all_npc_defs() -> list[dict]:
    """返回全部 NPC 定义"""
    _load()
    return _ENTITIES.get("npcs", [])


def get_npc_def(name: str) -> dict | None:
    """按名称查询 NPC 定义"""
    _load()
    all_npcs = {n["name"]: n for n in _ENTITIES.get("npcs", [])}
    return all_npcs.get(name)


# ─── 提示词辅助（显示类型名，仅用于 LLM 提示——引擎自身不用）───

def _type_name(type_id: int) -> str:
    """仅供内部调试/提示词使用。引擎代码不应依赖此函数。"""
    _load()
    return _TYPE_DEFS.get(type_id, {}).get("id", "unknown")


# ─── domain.json 加载（域特定内容）───

_DOMAIN_PATH = os.path.join(os.path.dirname(__file__), "domain.json")
_DOMAIN_CACHE: dict[str, Any] | None = None


def _load_domain() -> dict:
    """加载 domain.json（幂等）"""
    global _DOMAIN_CACHE
    if _DOMAIN_CACHE is not None:
        return _DOMAIN_CACHE
    with open(_DOMAIN_PATH, "r", encoding="utf-8") as f:
        _DOMAIN_CACHE = json.load(f)
    return _DOMAIN_CACHE


def get_domain_zones() -> list[dict]:
    """返回 domain.json 中的区域对象定义列表"""
    return _load_domain().get("zones", [])


def get_domain_recipes() -> list[dict]:
    """返回 domain.json 中的配方定义列表"""
    return _load_domain().get("recipes", [])


def get_domain_npc_zones() -> dict[str, str]:
    """返回 domain.json 中 NPC 初始区域映射 {npc_name: zone_id}"""
    return _load_domain().get("npc_initial_zones", {})


def get_domain_adapter() -> dict:
    """返回 domain.json 中的 adapter 段（所有 slot 文本）"""
    return _load_domain().get("adapter", {})


# ─── 初始化（显示加载状态）───

def init_config() -> None:
    """显式初始化配置（纯加载，无副作用）"""
    _load()
    zone_count = len(_ENTITIES.get("zones", []))
    conn_count = len(_ZONE_CONNECTIONS)
    print(f"[Config] 已加载 {len(_TYPE_DEFS)} 个节点类型, "
          f"{zone_count} 个区域, "
          f"{len(_ENTITIES.get('items', []))} 个物品, "
          f"{len(_ENTITIES.get('objects', []))} 个物件, "
          f"{conn_count} 个区域连接")


# 模块加载时自动初始化
_load()
