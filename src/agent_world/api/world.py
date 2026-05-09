# World REST API

from datetime import datetime
from fastapi import APIRouter, HTTPException

from ..db import NodeDB, get_session
from ..db.schemas import WorldResponse, SuccessResponse
from ..models.world import WorldTime

router = APIRouter(prefix="/world", tags=["World"])


@router.get("", response_model=WorldResponse)
def get_world():
    """获取世界信息"""
    with get_session() as conn:
        ndb = NodeDB(conn)
        wt_dict = ndb.get_world_time()
        world_time = WorldTime(**wt_dict)
        return WorldResponse(
            id="main_world",
            name="Agent World",
            description="一个 AI Agent 与 NPC 共存的世界",
            world_time=world_time.to_dict() if hasattr(world_time, 'to_dict') else wt_dict,
        )


@router.post("/tick", response_model=WorldResponse)
def tick_world(minutes: int = 1):
    """推进世界时间"""
    with get_session() as conn:
        ndb = NodeDB(conn)
        wt_dict = ndb.get_world_time()
        world_time = WorldTime(**wt_dict)
        world_time.tick(minutes)
        ndb.save_world_time({
            "year": world_time.year,
            "month": world_time.month,
            "day": world_time.day,
            "hour": world_time.hour,
            "minute": world_time.minute,
        })
    return WorldResponse(
        id="main_world",
        name="Agent World",
        description="一个 AI Agent 与 NPC 共存的世界",
        world_time=world_time.to_dict() if hasattr(world_time, 'to_dict') else wt_dict,
    )


@router.post("/refresh")
async def refresh_world():
    """
    手动触发一次世界刷新（World Update）。
    
    执行内容：
    - 推进世界时间
    - LLM/规则评估世界状态
    - 生成世界事件（天气、经济、社交）
    - 广播事件
    """
    from ..services.world_updater import get_world_updater
    updater = get_world_updater()
    result = updater.refresh()
    return {"success": True, **result}


@router.get("/events")
def get_world_events(limit: int = 10):
    """获取最近的世界事件"""
    from ..services.world_updater import get_world_updater
    updater = get_world_updater()
    return {
        "success": True,
        "events": [e.to_dict() for e in updater._event_history[-limit:]],
        "current_weather": updater._current_weather,
    }
