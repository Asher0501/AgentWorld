# Database Layer - SQLite Storage

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

from ..models.npc import NPC, NPCStatus, Position
from ..models.npc_defaults import create_diverse_npcs
from .converters import npc_to_node_dict

# ─── 节点类型常量 ───

class NodeType:
    """nodes 表的 type 字段枚举"""
    NPC = "npc"
    ZONE = "zone"
    ITEM = "item"
    RECIPE = "recipe"
    OBJECT = "object"


DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "agent_world.db"


def get_db_path() -> Path:
    """获取数据库路径"""
    db_path = Path(__file__).parent.parent.parent.parent / "data" / "agent_world.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path








@contextmanager
def get_session() -> Generator:
    """获取数据库会话（上下文管理器）"""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()



class NodeDB:
    """
    节点数据库操作 — 唯一的实体持久层。
    所有实体（NPC/zone/item/recipe/object）和系统数据都通过 nodes 表管理。
    """

    SYSTEM_WORLD_ID = "_system_world"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_nodes(self, type_filter: str | None = None) -> list[dict]:
        """获取所有节点，可选的按 type 筛选"""
        if type_filter:
            cursor = self.conn.execute(
                "SELECT id, type, name, data FROM nodes WHERE type = ? ORDER BY id",
                (type_filter,)
            )
        else:
            cursor = self.conn.execute("SELECT id, type, name, data FROM nodes ORDER BY id")
        result = []
        for row in cursor.fetchall():
            node = {"id": row[0], "type": row[1], "name": row[2], "data": json.loads(row[3])}
            result.append(node)
        return result

    def get_node(self, node_id: str) -> dict | None:
        """获取单个节点"""
        cursor = self.conn.execute("SELECT id, type, name, data FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {"id": row[0], "type": row[1], "name": row[2], "data": json.loads(row[3])}

    def upsert_node(self, node_id: str, node_type: str, name: str, data: dict):
        """插入或替换节点"""
        self.conn.execute(
            "INSERT OR REPLACE INTO nodes (id, type, name, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM nodes WHERE id = ?), ?), ?)",
            (node_id, node_type, name, json.dumps(data, ensure_ascii=False),
             node_id, NodeDB._now(), NodeDB._now())
        )
        self.conn.commit()

    def upsert_many(self, nodes: list[dict]):
        """批量插入或替换节点"""
        now = NodeDB._now()
        for n in nodes:
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO nodes (id, type, name, data, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM nodes WHERE id = ?), ?), ?)",
                    (n["id"], n["type"], n["name"], json.dumps(n["data"], ensure_ascii=False),
                     n["id"], now, now)
                )
            except Exception as e:
                logger.warning(f"[NodeDB] upsert_many 失败 {n.get('id','?')}: {e}")
        self.conn.commit()

    def delete_node(self, node_id: str):
        """删除节点"""
        self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self.conn.commit()

    # ═══════════════════════════════════════════
    # 世界时间
    # ═══════════════════════════════════════════

    def get_world_time(self) -> dict:
        """从系统节点读取世界时间"""
        nd = self.get_node(self.SYSTEM_WORLD_ID)
        if nd:
            d = nd["data"] if isinstance(nd["data"], dict) else {}
            return d.get("world_time", {"year": 1, "month": 1, "day": 1, "hour": 8, "minute": 0})
        return {"year": 1, "month": 1, "day": 1, "hour": 8, "minute": 0}

    def save_world_time(self, world_time: dict):
        """保存世界时间到系统节点"""
        nd = self.get_node(self.SYSTEM_WORLD_ID) or {"data": {}}
        d = nd["data"] if isinstance(nd["data"], dict) else {}
        d["world_time"] = world_time
        self.upsert_node(self.SYSTEM_WORLD_ID, "system", "system", d)

    # ═══════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════

    def count(self, type_filter: str | None = None) -> int:
        if type_filter:
            cursor = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE type = ?", (type_filter,))
        else:
            cursor = self.conn.execute("SELECT COUNT(*) FROM nodes")
        return cursor.fetchone()[0]

    def delete_nodes_by_type(self, node_type: str):
        """按类型删除节点"""
        self.conn.execute("DELETE FROM nodes WHERE type = ?", (node_type,))
        self.conn.commit()

    # ═══════════════════════════════════════════
    # 播种（首次运行时从 config 填充）
    # ═══════════════════════════════════════════

    def load_or_seed(self) -> list[dict]:
        """
        从 DB 加载所有实体节点。如果为空，从 config 播种。
        这是统一的实体加载入口。
        """
        nodes = self.get_nodes()
        entity_nodes = [n for n in nodes if n["type"] != "system"]
        if not entity_nodes:
            self._seed_from_config()
            nodes = self.get_nodes()
        return nodes

    def _seed_from_config(self):
        """从 config 播种所有实体（NPC/zone/item/recipe）+ 世界时间"""
        from ..services.graph_adapter import _make_eid, entity_to_node_dict

        # 1. NPC（从 create_diverse_npcs 播种到 nodes）
        for npc in create_diverse_npcs():
            eid = _make_eid("npc", npc.name)
            data = npc_to_node_dict(npc)
            self.upsert_node(eid, NodeType.NPC, npc.name, data)

        # 2. Zone
        from ..config.config_loader import get_zones, build_zone_model_full
        zone_name_map = {zdef["id"]: zdef["name"] for zdef in get_zones()}
        for zdef in get_zones():
            full = build_zone_model_full(zdef["id"])
            if not full:
                continue
            eid = f"zone_{full['name']}"
            raw_conns = full.get("connected_zones", []) or full.get("connects_to", [])
            exact_conns = [f"zone_{zone_name_map.get(cn, cn)}" for cn in raw_conns]
            data = {
                "role": "", "desc": full.get("description", ""), "traits": [],
                "attributes": {
                    "capacity": full.get("capacity", 100),
                    "is_safe": full.get("is_safe", True),
                    "zone_type": full.get("zone_type", ""),
                    "_config_id": zdef["id"],
                },
                "connected_entity_ids": exact_conns,
                "conserved": False, "space": "physical", "recent_info": "",
            }
            self.upsert_node(eid, NodeType.ZONE, full["name"], data)

        # 3. Item
        for item_def in self._get_config_items():
            name = item_def["name"]
            eid = _make_eid("item", name)
            data = {
                "role": "", "desc": "这是一个物品，可以持有、使用、交易。", "traits": [],
                "attributes": {},
                "connected_entity_ids": [],
                "conserved": True, "space": "physical", "recent_info": "",
            }
            self.upsert_node(eid, NodeType.ITEM, name, data)

        # 4. Recipe
        for recipe_def in self._get_config_recipes():
            name = recipe_def["name"]
            eid = f"recipe_{name}"
            data = {
                "role": "recipe", "desc": recipe_def.get("description", ""), "traits": [],
                "attributes": {
                    "consumes": recipe_def.get("inputs", {}),
                    "produces": recipe_def.get("outputs", {}),
                    "need_zone": recipe_def.get("zone", ""),
                    "need_tool": recipe_def.get("tool", ""),
                    "tool_interface": recipe_def.get("tool_interface", ""),
                    "vitality_cost": recipe_def.get("vitality_cost", 0),
                },
                "connected_entity_ids": [],
                "conserved": False, "space": "abstract", "recent_info": "",
            }
            self.upsert_node(eid, NodeType.RECIPE, name, data)

        # 5. 世界时间
        self.upsert_node(self.SYSTEM_WORLD_ID, "system", "system", {
            "world_time": {"year": 1, "month": 1, "day": 1, "hour": 8, "minute": 0},
        })

        logger.info(f"[NodeDB] 从 config 播种完成")

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    @staticmethod
    def _get_config_items() -> list[dict]:
        try:
            from ..config.config_loader import get_items
            return get_items()
        except Exception:
            return []

    @staticmethod
    def _get_config_recipes() -> list[dict]:
        try:
            from ..config.config_loader import _load_domain
            return _load_domain().get("recipes", [])
        except Exception:
            return []



def init_db():
    """初始化数据库"""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    
    # 创建 nodes 表（万物之源）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    
    print(f"数据库已初始化: {db_path}")