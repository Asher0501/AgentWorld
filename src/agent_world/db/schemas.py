# Database Schemas - Pydantic schemas for DB layer validation

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from ..models.npc import NPC, NPCStatus
from ..models.world import World, Zone, WorldTime


# --- NPC Schemas ---

class NPCCreate(BaseModel):
    """创建 NPC 的请求 schema"""
    name: str = Field(..., min_length=1, max_length=50)
    role: str


class NPCUpdate(BaseModel):
    """更新 NPC 的请求 schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    level: Optional[int] = Field(None, ge=1, le=100)
    status: Optional[NPCStatus] = None
    inventory: Optional[list[str]] = None


class NPCResponse(BaseModel):
    """NPC 响应 schema"""
    id: str
    name: str
    role: str
    level: int
    status: NPCStatus
    position: dict
    attributes: dict
    inventory: list[str]
    relationships: dict
    memory: list[dict]
    created_at: datetime
    updated_at: datetime


# --- World Schemas ---

class ZoneResponse(BaseModel):
    """区域响应 schema"""
    id: str
    name: str
    zone_type: str
    description: str
    bounds: dict
    capacity: int
    connected_zones: list[str]


class WorldTimeResponse(BaseModel):
    """世界时间响应 schema"""
    year: int
    month: int
    day: int
    hour: int
    minute: int


class WorldResponse(BaseModel):
    """世界响应 schema"""
    id: str
    name: str
    description: str
    zones: list[ZoneResponse]
    world_time: WorldTimeResponse
    active_npcs: int
    total_events: int
    created_at: datetime


# --- DB Response Wrappers ---

class SuccessResponse(BaseModel):
    """通用成功响应"""
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """通用错误响应"""
    success: bool = False
    error: str
    code: Optional[str] = None


class ListResponse(BaseModel):
    """列表响应"""
    success: bool = True
    data: list
    count: int
