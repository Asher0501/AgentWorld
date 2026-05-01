# World Data Model

"""
世界数据模型。

ZoneType 枚举和 DEFAULT_ZONES 由 node_config.json 驱动。
如需新增区域类型，编辑 config/node_config.json 的 entities.zones 列表即可。
无需修改此文件。
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ZoneType(str, Enum):
    """区域类型（自动从 config 扩展）"""
    VILLAGE_SQUARE = "village_square"
    MARKET = "market"
    TAVERN = "tavern"
    FARM = "farm"
    MINE = "mine"
    FOREST = "forest"
    LIBRARY = "library"
    TEMPLE = "temple"
    BARRACKS = "barracks"
    OUTSKIRTS = "outskirts"

    # 额外类型可以通过 zone_type 字段传入（Pydantic 允许任意字符串）


class Zone(BaseModel):
    """世界中的一个区域"""
    id: str
    name: str
    zone_type: ZoneType
    description: str = ""
    
    # 地理信息
    bounds: dict = Field(default_factory=lambda: {
        "min_x": 0, "max_x": 100,
        "min_y": 0, "max_y": 100
    })
    
    # 区域内 NPC 上限
    capacity: int = 20
    
    # 连接的区域
    connected_zones: list[str] = Field(default_factory=list)


class WorldTime(BaseModel):
    """世界时间系统"""
    year: int = 1
    month: int = 1
    day: int = 1
    hour: int = 8  # 游戏内时间（24小时制）
    minute: int = 0
    
    def tick(self, minutes: int = 1):
        """推进时间"""
        self.minute += minutes
        while self.minute >= 60:
            self.minute -= 60
            self.hour += 1
        while self.hour >= 24:
            self.hour -= 24
            self.day += 1
        while self.day >= 31:
            self.day -= 30
            self.month += 1
        while self.month >= 13:
            self.month -= 12
            self.year += 1
    
    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "month": self.month,
            "day": self.day,
            "hour": self.hour,
            "minute": self.minute
        }

    def get_time_of_day(self) -> str:
        """返回 'dawn' | 'day' | 'dusk' | 'night' | 'midnight'"""
        h = self.hour
        if 5 <= h < 8:    return "dawn"
        if 8 <= h < 17:   return "day"
        if 17 <= h < 20:  return "dusk"
        if 20 <= h < 24:  return "night"
        return "midnight"

    def is_night(self) -> bool:
        return self.hour >= 20 or self.hour < 5

    def get_season(self) -> str:
        """返回 'spring' | 'summer' | 'autumn' | 'winter'"""
        season_map = {1: "spring", 2: "spring", 3: "spring",
                      4: "summer", 5: "summer", 6: "summer",
                      7: "autumn", 8: "autumn", 9: "autumn",
                      10: "winter", 11: "winter", 12: "winter"}
        return season_map.get(self.month, "spring")

    def to_display_str(self) -> str:
        """如 '春·第 3 天 14:30'"""
        seasons = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}
        season_name = seasons.get(self.get_season(), "春")
        return f"{season_name}·第 {self.day} 天 {self.hour:02d}:{self.minute:02d}"


class World(BaseModel):
    """整个游戏世界"""
    id: str = "main_world"
    name: str = "Agent World"
    description: str = "一个 AI Agent 与 NPC 共存的世界"
    
    # 世界分区
    zones: list[Zone] = Field(default_factory=list)
    
    # 世界时间
    world_time: WorldTime = Field(default_factory=WorldTime)
    
    # 统计
    active_npcs: int = 0
    total_events: int = 0
    
    # 创建时间
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def is_night(self) -> bool:
        return self.world_time.is_night()

    def get_time_str(self) -> str:
        return self.world_time.to_display_str()


# ─── 从 config 生成 DEFAULT_ZONES ───

def _build_default_zones() -> list[Zone]:
    """从 node_config.json 构建默认区域列表"""
    from ..config.config_loader import get_zones, get_zone_connections, build_zone_model_full
    
    zones = []
    for zdef in get_zones():
        zid = zdef["id"]
        full = build_zone_model_full(zid)
        if full is None:
            continue
        zones.append(Zone(
            id=zid,
            name=zdef.get("name", zid),
            zone_type=zdef.get("zone_type", zid),
            description=zdef.get("description", ""),
            bounds=full["bounds"],
            capacity=full["capacity"],
            connected_zones=get_zone_connections(zid),
        ))
    return zones


DEFAULT_ZONES: list[Zone] = _build_default_zones()
