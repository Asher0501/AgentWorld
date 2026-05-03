# NPC Data Model

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class NPCRole(str, Enum):
    """NPC 职业类型"""
    MERCHANT = "merchant"       # 商人
    GUARD = "guard"             # 守卫
    FARMER = "farmer"           # 农民
    SCHOLAR = "scholar"         # 学者
    ARTISAN = "artisan"         # 工匠
    MINER = "miner"             # 矿工
    HEALER = "healer"           # 治疗师
    BARBARIAN = "barbarian"     # 野蛮人
    SAGE = "sage"               # 贤者
    WANDERER = "wanderer"      # 流浪者


class NPCStatus(str, Enum):
    """NPC 当前状态"""
    IDLE = "idle"              # 空闲
    WORKING = "working"        #工作中
    TRAVELING = "traveling"    # 移动中
    SOCIALIZING = "socializing" # 社交中
    RESTING = "resting"        # 休息中


class Position(BaseModel):
    """NPC 位置"""
    zone_id: str = "village_square"
    x: float = 0.0
    y: float = 0.0


class PhysicalAttributes(BaseModel):
    """NPC 身体属性"""
    energy_capacity: float = Field(default=100.0, ge=0.0, description="最大活力上限")
    health: float = Field(default=100.0, ge=0.0, description="生命值")
    recovery_speed: float = Field(default=1.0, ge=0.0, description="恢复速度倍率")
    age: int = Field(default=30, ge=0, le=300, description="年龄")


class PersonaTags(BaseModel):
    """NPC 人格标签"""
    work_ethic: str = "普通"           # 勤奋/懒散/忠诚/仁慈...
    social_class: str = "平民"         # 平民/商人/学者/贵族...
    reputation: str = "普通"            # 好/坏/普通/神秘...
    interests: list[str] = Field(default_factory=list)  # 兴趣爱好
    personality: list[str] = Field(default_factory=list)  # 性格特点
    special_traits: list[str] = Field(default_factory=list)  # 特殊 traits


class NPC(BaseModel):
    """NPC 数据模型"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, description="NPC 名称，不可为空")
    role: NPCRole

    # 等级与属性
    level: int = Field(ge=1, le=100, default=1)
    attributes: dict = Field(default_factory=lambda: {
        "strength": 10,
        "intelligence": 10,
        "charisma": 10,
        "endurance": 10,
        "wisdom": 10
    })

    # 背包与位置
    inventory: list[str] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)

    # 关系
    relationships: dict = Field(default_factory=dict)  # {npc_id: affinity (-100 to 100)}

    # 身体属性与人格
    physical: PhysicalAttributes = Field(default_factory=PhysicalAttributes)
    persona_tags: PersonaTags = Field(default_factory=PersonaTags)

    # 内部驱动属性
    status: NPCStatus = NPCStatus.IDLE
    vitality: float = Field(default=100.0, ge=0.0, description="当前活力/能量 0-energy_capacity")
    satiety: float = Field(default=50.0, ge=0.0, le=100.0, description="饱腹度，0=饿 100=饱")
    mood: float = Field(default=50.0, ge=0.0, le=100.0, description="心情，0=极差 100=极好")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def drain_energy(self, amount: float):
        """消耗活力（受年龄影响，年纪大恢复慢）"""
        age_factor = 1.0 if self.physical.age < 50 else 0.8
        self.vitality = max(0.0, self.vitality - amount * age_factor)
        if self.vitality < 20:
            self.status = NPCStatus.RESTING

    def restore_energy(self, amount: float):
        """恢复活力（受恢复速度影响）"""
        recovered = amount * self.physical.recovery_speed
        self.vitality = min(self.physical.energy_capacity, self.vitality + recovered)

    def update_relationship(self, npc_id: str, delta: int):
        """更新与某 NPC 的关系值"""
        if npc_id not in self.relationships:
            self.relationships[npc_id] = 0
        self.relationships[npc_id] = max(-100, min(100, self.relationships[npc_id] + delta))
        self.updated_at = datetime.now()