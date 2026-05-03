"""
World Objects - 具体实体实现

实现所有可交互物体：
  - Stall         : 商人摊位
  - FarmPlot      : 农田
  - OreVein       : 矿脉
  - BarCounter    : 酒馆吧台
  - WorldObjectManager : 实体管理器
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from .base import (
    WorldObject,
    ObjectType,
    ObjectState,
    Affordance,
    InteractionResult,
)


# === 具体实体类 ===

class Stall(WorldObject):
    """
    商人摊位。

    状态流转：
      AVAILABLE → OCCUPIED (NPC 开始摆摊)
      OCCUPIED → AVAILABLE (NPC 离开或交易完成)

    行为：
      - trade: NPC 在摊位上进行交易，获得金币
    """

    def __init__(self, zone_id: str, name: str = "摊位", **kwargs):
        super().__init__(
            object_type=ObjectType.STALL,
            zone_id=zone_id,
            name=name,
            **kwargs
        )
        self.trades_completed = 0

    def get_affordances(self) -> list[Affordance]:
        if self.state == ObjectState.AVAILABLE:
            return [
                Affordance(action="trade", description="摆摊交易", energy_cost=10, duration_ticks=3),
            ]
        elif self.state == ObjectState.OCCUPIED:
            return [
                Affordance(action="continue_trade", description="继续交易", energy_cost=5, duration_ticks=2),
            ]
        return []

    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        if self.state == ObjectState.BROKEN:
            return False, "摊位损坏了"
        if self.state == ObjectState.OCCUPIED and self.current_user != npc_id:
            return False, f"摊位被 {self.current_user} 占用"
        if action not in ["trade", "continue_trade"]:
            return False, f"摊位不能执行动作: {action}"
        return True, "可以交易"

    def interact(self, npc_id: str, action: str) -> InteractionResult:
        if self.state == ObjectState.AVAILABLE:
            self.occupy(npc_id)

        self.trades_completed += 1
        loot = [f"金币+{random.randint(1, 5)}"]
        description = f"完成了一笔交易，获得 {'、'.join(loot)}"

        return InteractionResult(
            success=True,
            description=description,
            loot=loot,
            state_change=None,
            next_available_at=None,
        )


class FarmPlot(WorldObject):
    """
    农田。

    状态流转：
      AVAILABLE (fallow) → OCCUPIED (planted)
      OCCUPIED (planted) → 等待几个 tick → growing
      growing → harvestable
      harvestable → AVAILABLE (收获后回到 fallow)

    行为：
      - plant : 播种（需 fallow 状态）
      - water : 浇水（需 planted 状态）
      - harvest : 收获（需 harvestable 状态）
    """

    FALLOW = "fallow"
    PLANTED = "planted"
    GROWING = "growing"
    HARVESTABLE = "harvestable"

    def __init__(self, zone_id: str, name: str = "农田", **kwargs):
        super().__init__(
            object_type=ObjectType.FARM_PLOT,
            zone_id=zone_id,
            name=name,
            **kwargs
        )
        self.growth_state = self.FALLOW
        self.planted_at: datetime | None = None
        self.growth_ticks = 0

    def get_affordances(self) -> list[Affordance]:
        if self.growth_state == self.FALLOW:
            return [Affordance(action="plant", description="干农活播种种地", energy_cost=15, duration_ticks=1)]
        elif self.growth_state == self.PLANTED:
            return [Affordance(action="water", description="浇水", energy_cost=5, duration_ticks=1)]
        elif self.growth_state == self.GROWING:
            return []
        elif self.growth_state == self.HARVESTABLE:
            return [Affordance(action="harvest", description="收获", energy_cost=10, duration_ticks=1)]
        return []

    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        if self.state == ObjectState.BROKEN:
            return False, "农田损坏"
        if self.state == ObjectState.OCCUPIED and self.current_user != npc_id:
            return False, f"农田被 {self.current_user} 占用"
        if self.growth_state == self.FALLOW and action != "plant":
            return False, "需要先播种"
        if self.growth_state == self.PLANTED and action != "water":
            return False, "需要先浇水"
        if self.growth_state == self.GROWING:
            return False, "作物还在生长中"
        if self.growth_state == self.HARVESTABLE and action != "harvest":
            return False, "可以收获了"
        return True, "可以操作"

    def interact(self, npc_id: str, action: str) -> InteractionResult:
        # 通用 "farm" 动作 → 根据生长状态自动选择具体动作
        if action == "farm":
            if self.growth_state == self.FALLOW:
                action = "plant"
            elif self.growth_state == self.PLANTED:
                action = "water"
            elif self.growth_state == self.HARVESTABLE:
                action = "harvest"

        if action == "plant":
            self.occupy(npc_id)
            self.growth_state = self.PLANTED
            self.planted_at = datetime.now()
            self.growth_ticks = 0
            return InteractionResult(
                success=True,
                description="播下了种子",
                loot=[],
                state_change=ObjectState.OCCUPIED,
                next_available_at=None,
            )
        elif action == "water":
            self.growth_state = self.GROWING
            self.growth_ticks = 0
            return InteractionResult(
                success=True,
                description="浇了水，作物开始生长",
                loot=[],
                state_change=None,
                next_available_at=None,
            )
        elif action == "harvest":
            self.growth_state = self.FALLOW
            self.release()
            self.planted_at = None
            self.growth_ticks = 0
            loot = ["农作物"]
            return InteractionResult(
                success=True,
                description="收获了农作物",
                loot=loot,
                state_change=ObjectState.AVAILABLE,
                next_available_at=None,
            )
        return InteractionResult(success=False, description="未知动作", loot=[])

    def tick(self):
        """每个游戏 tick 更新生长状态"""
        if self.growth_state == self.GROWING:
            self.growth_ticks += 1
            if self.growth_ticks >= 3:
                self.growth_state = self.HARVESTABLE
                self.release()


class OreVein(WorldObject):
    """
    矿脉。

    状态流转：
      AVAILABLE → OCCUPIED (开采中)
      OCCUPIED → AVAILABLE (矿石耗尽前可重复)
      DEPLETED (资源耗尽，无法使用)

    行为：
      - mine : 挖掘矿石
    """

    def __init__(self, zone_id: str, name: str = "矿脉", richness: float = 1.0, **kwargs):
        super().__init__(
            object_type=ObjectType.ORE_VEIN,
            zone_id=zone_id,
            name=name,
            **kwargs
        )
        self.richness = richness  # 0.0~1.0，矿石丰富程度
        self.total_mined = 0
        self.max_mines = int(20 * richness)  # 资源上限

    def get_affordances(self) -> list[Affordance]:
        if self.state == ObjectState.AVAILABLE:
            return [Affordance(action="mine", description="挖掘矿石", energy_cost=20, duration_ticks=2)]
        elif self.state == ObjectState.OCCUPIED:
            return [Affordance(action="continue_mine", description="继续挖掘", energy_cost=15, duration_ticks=2)]
        return []

    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        if self.state == ObjectState.DEPLETED:
            return False, "矿脉已枯竭"
        if self.state == ObjectState.OCCUPIED and self.current_user != npc_id:
            return False, f"矿脉被 {self.current_user} 占用"
        if self.total_mined >= self.max_mines:
            self.state = ObjectState.DEPLETED
            return False, "矿脉已枯竭"
        return True, "可以挖掘"

    def interact(self, npc_id: str, action: str) -> InteractionResult:
        if self.state == ObjectState.AVAILABLE:
            self.occupy(npc_id)

        ores = ["铁矿石", "铜矿石", "煤矿", "石材"]
        loot = [random.choice(ores)]
        self.total_mined += 1

        if self.total_mined >= self.max_mines:
            self.state = ObjectState.DEPLETED
            self.release()
            return InteractionResult(
                success=True,
                description=f"挖掘了 {loot[0]}，矿脉已枯竭",
                loot=loot,
                state_change=ObjectState.DEPLETED,
                next_available_at=None,
            )

        return InteractionResult(
            success=True,
            description=f"挖掘了 {loot[0]}",
            loot=loot,
            state_change=None,
            next_available_at=None,
        )


class BarCounter(WorldObject):
    """
    酒馆吧台。

    行为：
      - drink : 喝酒（恢复体力）
      - talk  : 闲聊（社交机会）
    """

    def __init__(self, zone_id: str, name: str = "吧台", **kwargs):
        super().__init__(
            object_type=ObjectType.BAR_COUNTER,
            zone_id=zone_id,
            name=name,
            **kwargs
        )

    def get_affordances(self) -> list[Affordance]:
        if self.state == ObjectState.AVAILABLE:
            return [
                Affordance(action="drink", description="喝酒休息", energy_cost=-15, duration_ticks=2),
                Affordance(action="talk", description="闲聊社交", energy_cost=5, duration_ticks=2),
            ]
        elif self.state == ObjectState.OCCUPIED:
            return [
                Affordance(action="talk", description="闲聊社交", energy_cost=5, duration_ticks=2),
            ]
        return []

    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        if self.state == ObjectState.LOCKED:
            return False, "酒馆已关门"
        return True, "可以使用吧台"

    def interact(self, npc_id: str, action: str) -> InteractionResult:
        if action == "drink":
            self.occupy(npc_id)
            return InteractionResult(
                success=True,
                description="喝了一杯，恢复了一些体力",
                loot=["体力+15"],
                state_change=ObjectState.OCCUPIED,
                next_available_at=datetime.now() + timedelta(minutes=30),
            )
        elif action == "talk":
            topics = ["最近的新闻", "天气", "镇上的八卦", "明天的计划"]
            topic = random.choice(topics)
            return InteractionResult(
                success=True,
                description=f"聊了聊{topic}",
                loot=[],
                state_change=None,
                next_available_at=None,
            )
        return InteractionResult(success=False, description="未知动作", loot=[])



# === 占位实体（实现缺失的 ObjectType）===

class LibraryDesk(WorldObject):
    def __init__(self, zone_id: str, name: str = "书桌", **kwargs):
        super().__init__(object_type=ObjectType.LIBRARY_DESK, zone_id=zone_id, name=name, **kwargs)
    def get_affordances(self) -> list[Affordance]:
        return [Affordance(action="research", description="研究阅读", energy_cost=10, duration_ticks=3)]
    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        return True, "可以使用"
    def interact(self, npc_id: str, action: str) -> InteractionResult:
        return InteractionResult(success=True, description="阅读了相关书籍", loot=[])

class TempleAltar(WorldObject):
    def __init__(self, zone_id: str, name: str = "祭坛", **kwargs):
        super().__init__(object_type=ObjectType.TEMPLE_ALTAR, zone_id=zone_id, name=name, **kwargs)
    def get_affordances(self) -> list[Affordance]:
        return [Affordance(action="pray", description="祈祷治疗休息", energy_cost=-10, duration_ticks=2)]
    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        return True, "可以使用"
    def interact(self, npc_id: str, action: str) -> InteractionResult:
        return InteractionResult(success=True, description="祈祷后感到心神安宁", loot=["体力+10"])

class BarracksEquipment(WorldObject):
    def __init__(self, zone_id: str, name: str = "训练设施", **kwargs):
        super().__init__(object_type=ObjectType.BARRACKS_EQUIPMENT, zone_id=zone_id, name=name, **kwargs)
    def get_affordances(self) -> list[Affordance]:
        return [Affordance(action="train", description="训练巡逻守卫", energy_cost=15, duration_ticks=3)]
    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        return True, "可以使用"
    def interact(self, npc_id: str, action: str) -> InteractionResult:
        return InteractionResult(success=True, description="训练完毕", loot=[])

class ForestHuntingGround(WorldObject):
    def __init__(self, zone_id: str, name: str = "狩猎场", **kwargs):
        super().__init__(object_type=ObjectType.FOREST_HUNTING_GROUND, zone_id=zone_id, name=name, **kwargs)
    def get_affordances(self) -> list[Affordance]:
        return [Affordance(action="hunt", description="狩猎", energy_cost=20, duration_ticks=3)]
    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        return True, "可以使用"
    def interact(self, npc_id: str, action: str) -> InteractionResult:
        return InteractionResult(success=True, description=f"狩猎获得 {random.choice(['兽皮','野兔','野鸡肉','蘑菇'])}", loot=[])


# === 通用 Object（统一世界物件与配方工具） ===

class Object(WorldObject):
    """
    物体节点（通用，统一世界物件与配方工具）。

    所有 entity_type="object" 的图节点统一由此类创建。
    无独立状态机，用 max_concurrency 限制同时使用人数。
    """

    def __init__(self, zone_id: str, name: str, object_type: ObjectType,
                 max_concurrency: int = 1, **kwargs):
        super().__init__(
            object_id=kwargs.pop('object_id', None),
            object_type=object_type,
            zone_id=zone_id,
            name=name,
            **kwargs
        )
        self._max_concurrency = max_concurrency
        self._current_users: set[str] = set()

    def get_affordances(self) -> list[Affordance]:
        if len(self._current_users) < self._max_concurrency:
            return [Affordance(action="use", description=f"使用{self.name}", energy_cost=5, duration_ticks=1)]
        return []

    def can_interact(self, npc_id: str, action: str) -> tuple[bool, str]:
        if len(self._current_users) >= self._max_concurrency and npc_id not in self._current_users:
            return False, f"{self.name}当前已被占满"
        return True, ""

    def interact(self, npc_id: str, action: str) -> InteractionResult:
        self._current_users.add(npc_id)
        return InteractionResult(
            success=True,
            description=f"使用{self.name}",
            loot=[],
            state_change=None,
            next_available_at=None,
        )

    def release_user(self, npc_id: str):
        self._current_users.discard(npc_id)


# === 实体管理器 ===

class WorldObjectManager:
    """
    世界实体管理器。

    负责：
    - 创建和存储世界中的所有实体
    - 按 zone_id 查询可用实体
    - 按类型查询可用实体
    - 全局 tick（更新实体状态，如农田生长）
    """

    def __init__(self):
        self._objects: dict[str, WorldObject] = {}

    # === CRUD ===

    def add(self, obj: WorldObject) -> WorldObject:
        self._objects[obj.id] = obj
        return obj

    def get(self, object_id: str) -> WorldObject | None:
        return self._objects.get(object_id)

    def remove(self, object_id: str):
        if object_id in self._objects:
            del self._objects[object_id]

    def all(self) -> list[WorldObject]:
        return list(self._objects.values())

    # === 查询 ===

    def find_by_zone(self, zone_id: str) -> list[WorldObject]:
        return [o for o in self._objects.values() if o.zone_id == zone_id]

    def find_by_type(self, object_type: ObjectType) -> list[WorldObject]:
        return [o for o in self._objects.values() if o.object_type == object_type]

    def find_available(
        self,
        zone_id: str | None = None,
        object_type: ObjectType | None = None,
    ) -> list[WorldObject]:
        """
        查找可用的实体。
        
        Args:
            zone_id: 限制在特定 zone
            object_type: 限制特定类型
        """
        results = self._objects.values()
        if zone_id:
            results = [o for o in results if o.zone_id == zone_id]
        if object_type:
            results = [o for o in results if o.object_type == object_type]
        return [o for o in results if o.is_available()]

    def find_nearest_available(
        self,
        from_zone_id: str,
        object_type: ObjectType,
        zone_connections: dict[str, list[str]],
    ) -> WorldObject | None:
        """
        从指定 zone 出发，找最近的可用实体。
        
        使用 BFS 搜索相邻 zone。
        """
        from collections import deque
        
        # 首先检查当前 zone 是否有可用实体
        available = self.find_available(zone_id=from_zone_id, object_type=object_type)
        if available:
            return random.choice(available)

        visited = {from_zone_id}
        queue = deque([(from_zone_id, 0)])

        while queue:
            current_zone, distance = queue.popleft()
            for neighbor in zone_connections.get(current_zone, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                available = self.find_available(zone_id=neighbor, object_type=object_type)
                if available:
                    return random.choice(available)
                queue.append((neighbor, distance + 1))
        return None

    # === 生命周期 ===

    def tick(self):
        """全局 tick，更新所有实体的状态"""
        for obj in self._objects.values():
            # 释放被占用的物体（下次 tick 即可用）
            if obj.state == ObjectState.OCCUPIED:
                obj.release()
            # 自定义 tick 逻辑（如农田生长）
            if hasattr(obj, "tick"):
                obj.tick()

    # === 初始化预设世界 ===

    def _db_save(self, obj: WorldObject):
        """Save single object to DB"""
        from agent_world.db import get_session
        with get_session() as conn:
            cursor = conn.cursor()
            data = obj.to_db_dict()
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO world_objects
                    (id, name, object_type, zone_id, position_x, position_y, state,
                     current_user, capacity, current_goods, growth_stage, resources_left, uses_left, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    data["id"], data["name"], data["object_type"], data["zone_id"],
                    data["position_x"], data["position_y"], data["state"],
                    data["current_user"], data["capacity"], data["current_goods"],
                    data["growth_stage"], data["resources_left"], data["uses_left"], data["metadata"]
                ))
                print(f"[WorldObjectManager] Saved {obj.name} to DB, state={obj.state.value}")
            except Exception as e:
                print(f"[WorldObjectManager] DB save error for {obj.name}: {e}")

    def _db_load(self):
        """Load all objects from DB"""
        from agent_world.db import get_session
        self._objects = {}
        try:
            with get_session() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM world_objects")
                rows = cursor.fetchall()
                print(f"[WorldObjectManager] Found {len(rows)} objects in DB")
                for row in rows:
                    data = {
                        "id": row[0],
                        "name": row[1],
                        "object_type": row[2],
                        "zone_id": row[3],
                        "position_x": row[4],
                        "position_y": row[5],
                        "state": row[6],
                        "current_user": row[7],
                        "capacity": row[8],
                        "current_goods": row[9],
                        "growth_stage": row[10],
                        "resources_left": row[11],
                        "uses_left": row[12],
                        "metadata": row[13],
                    }
                    obj = WorldObject.from_db_dict(data)
                    self._objects[obj.id] = obj
                print(f"[WorldObjectManager] Loaded {len(self._objects)} objects from DB")
        except Exception as e:
            print(f"[WorldObjectManager] DB load error: {e}, starting fresh")
            self._objects = {}

    def init_default_world(self, zones: list[dict]):
        """
        根据 world zones 和 domain.json 对象定义创建默认实体。

        zones: list of {id, zone_type, ...} (来自 node_config.json)
        对象定义从 domain.json 的 "zones" 段读取。
        """
        from ..config.config_loader import get_domain_zones

        # 构建 zone_id → 对象定义 索引
        domain_zone_objects: dict[str, list[dict]] = {}
        for dz in get_domain_zones():
            domain_zone_objects[dz["id"]] = dz.get("objects", [])

        # 对象类型 → 构造类映射
        _OBJ_CLASSES: dict[str, type] = {
            "STALL": Stall,
            "FARM_PLOT": FarmPlot,
            "ORE_VEIN": OreVein,
            "BAR_COUNTER": BarCounter,
            "LIBRARY_DESK": LibraryDesk,
            "TEMPLE_ALTAR": TempleAltar,
            "BARRACKS_EQUIPMENT": BarracksEquipment,
            "FOREST_HUNTING_GROUND": ForestHuntingGround,
            "ANVIL": Object,
            "OVEN": Object,
            "MILL": Object,
            "BREWING_VAT": Object,
            "MORTAR": Object,
            "SEWING_TABLE": Object,
            "WORKBENCH": Object,
        }

        # 对象类型 → ObjectType 映射
        _OBJ_TYPES: dict[str, ObjectType] = {
            "STALL": ObjectType.STALL,
            "FARM_PLOT": ObjectType.FARM_PLOT,
            "ORE_VEIN": ObjectType.ORE_VEIN,
            "BAR_COUNTER": ObjectType.BAR_COUNTER,
            "LIBRARY_DESK": ObjectType.LIBRARY_DESK,
            "TEMPLE_ALTAR": ObjectType.TEMPLE_ALTAR,
            "BARRACKS_EQUIPMENT": ObjectType.BARRACKS_EQUIPMENT,
            "FOREST_HUNTING_GROUND": ObjectType.FOREST_HUNTING_GROUND,
            "ANVIL": ObjectType.ANVIL,
            "OVEN": ObjectType.OVEN,
            "MILL": ObjectType.MILL,
            "BREWING_VAT": ObjectType.BREWING_VAT,
            "MORTAR": ObjectType.MORTAR,
            "SEWING_TABLE": ObjectType.SEWING_TABLE,
            "WORKBENCH": ObjectType.WORKBENCH,
        }

        for zone in zones:
            zone_id = zone.get("id", "")
            obj_defs = domain_zone_objects.get(zone_id, [])
            for obj_def in obj_defs:
                obj_type_str = obj_def["type"]
                count = obj_def.get("count", 1)
                name_prefix = obj_def.get("name", obj_type_str)

                obj_cls = _OBJ_CLASSES.get(obj_type_str)
                obj_type = _OBJ_TYPES.get(obj_type_str)
                if obj_cls is None or obj_type is None:
                    continue

                for i in range(count):
                    if count > 1:
                        name = f"{zone_id}_{name_prefix}_{i+1}"
                    else:
                        name = f"{zone_id}_{name_prefix}"

                    if obj_cls == OreVein:
                        obj = obj_cls(zone_id=zone_id, name=name, richness=0.8)
                    elif obj_cls == Object:
                        obj = obj_cls(zone_id=zone_id, name=name, object_type=obj_type)
                    else:
                        obj = obj_cls(zone_id=zone_id, name=name)
                    self.add(obj)

    def to_dict(self) -> list[dict]:
        return [o.to_dict() for o in self._objects.values()]
