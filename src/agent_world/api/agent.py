"""
Agent API - 外部 Agent 接入协议

Phase 4: 外部 AI Agent 可通过 API 加入世界

接口：
- POST /api/agent/register     : 注册外部 Agent
- GET  /api/agent/world       : 获取世界状态（Zones + Objects）
- GET  /api/agent/npcs        : 获取所有 NPC 列表
- GET  /api/agent/me          : 获取自身状态
- POST /api/agent/act         : 执行动作（移动/交互）
- GET  /api/agent/history     : 获取交互历史
"""

import uuid
import hashlib
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

router = APIRouter(prefix="/api/agent", tags=["agent"])

# === 数据模型 ===

class AgentRegisterRequest(BaseModel):
    name: str
    role: str = "explorer"  # explorer, trader, warrior, scholar...
    description: str = ""

class AgentRegisterResponse(BaseModel):
    agent_id: str
    api_key: str  # 简单 API Key（实际应用应使用 JWT）
    name: str

class WorldStateResponse(BaseModel):
    world_id: str
    world_time: dict
    zones: list[dict]
    objects: list[dict]

class ActRequest(BaseModel):
    action: str  # "move", "interact", "talk"
    target: str | None = None  # zone_id 或 object_id 或 npc_id
    extra: dict | None = None

class ActResponse(BaseModel):
    success: bool
    message: str
    state_change: dict | None = None
    reward: list[str] | None = None

class AgentStateResponse(BaseModel):
    agent_id: str
    name: str
    role: str
    position: dict
    inventory: list[str]
    memory: list[dict]


# === 简单 Agent 注册表（内存中，生产环境用数据库）===

_agents: dict[str, dict] = {}  # agent_id -> agent_info
_api_keys: dict[str, str] = {}  # api_key -> agent_id

def _gen_api_key(agent_id: str) -> str:
    """生成简单 API Key"""
    return hashlib.sha256(f"{agent_id}:{time.time()}".encode()).hexdigest()[:32]

def _verify_api_key(x_api_key: str) -> str | None:
    """验证 API Key，返回 agent_id"""
    return _api_keys.get(x_api_key)


# === 辅助端点（无需认证）===

@router.get("/agents")  
async def list_agents():
    """列出所有注册的外部 Agent（用于可视化）"""
    return {
        "success": True,
        "data": [
            {
                "agent_id": aid,
                "name": info["name"],
                "role": info["role"],
                "position": info["position"],
                "inventory": info["inventory"],
            }
            for aid, info in _agents.items()
        ],
        "count": len(_agents),
    }


# === API 端点 ===

@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest):
    """注册新的外部 Agent"""
    agent_id = str(uuid.uuid4())
    api_key = _gen_api_key(agent_id)
    
    agent_info = {
        "agent_id": agent_id,
        "name": req.name,
        "role": req.role,
        "description": req.description,
        "api_key": api_key,
        "position": {"zone_id": "village_square", "x": 50.0, "y": 50.0},
        "inventory": [],
        "memory": [],
        "created_at": time.time(),
    }
    
    _agents[agent_id] = agent_info
    _api_keys[api_key] = agent_id
    
    return AgentRegisterResponse(
        agent_id=agent_id,
        api_key=api_key,
        name=req.name,
    )


@router.get("/world", response_model=WorldStateResponse)
async def get_world(x_api_key: str = Header(...)):
    """获取世界状态"""
    from agent_world.db import get_session, WorldDB
    from agent_world.entities import WorldObjectManager
    
    agent_id = _verify_api_key(x_api_key)
    if not agent_id:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    with get_session() as conn:
        world_db = WorldDB(conn)
        world = world_db.get_world()
        if not world:
            raise HTTPException(status_code=404, detail="World not found")
        
        # 获取共享实体管理器
        from agent_world.entities import get_entity_manager, init_entity_manager
        zone_dicts = [z.model_dump() for z in world.zones]
        init_entity_manager(zone_dicts)
        obj_manager = get_entity_manager()
        
        return WorldStateResponse(
            world_id=world.id,
            world_time=world.world_time.to_dict() if world.world_time else {},
            zones=[z.model_dump() for z in world.zones],
            objects=[o.to_dict() for o in obj_manager.all()],
        )


@router.get("/npcs")
async def get_npcs(x_api_key: str = Header(...)):
    """获取所有 NPC 列表"""
    from agent_world.db import get_session, NPCDB
    
    agent_id = _verify_api_key(x_api_key)
    if not agent_id:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    with get_session() as conn:
        npc_db = NPCDB(conn)
        npcs = npc_db.get_all_npcs()
        return {
            "success": True,
            "data": [
                {
                    "id": npc.id,
                    "name": npc.name,
                    "role": npc.role,
                    "position": npc.position.model_dump() if hasattr(npc.position, 'model_dump') else npc.position,
                    "level": npc.level,
                    "status": npc.status.value,
                }
                for npc in npcs
            ],
            "count": len(npcs),
        }


@router.get("/me", response_model=AgentStateResponse)
async def get_my_state(x_api_key: str = Header(...)):
    """获取自身状态"""
    agent_id = _verify_api_key(x_api_key)
    if not agent_id:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    agent = _agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return AgentStateResponse(
        agent_id=agent["agent_id"],
        name=agent["name"],
        role=agent["role"],
        position=agent["position"],
        inventory=agent["inventory"],
        memory=agent["memory"],
    )


@router.post("/act", response_model=ActResponse)
async def act(req: ActRequest, x_api_key: str = Header(...)):
    """执行动作"""
    from agent_world.db import get_session, WorldDB
    from agent_world.entities import WorldObjectManager, ObjectType
    
    agent_id = _verify_api_key(x_api_key)
    if not agent_id:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    agent = _agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    action = req.action
    target = req.target
    
    # === 移动 ===
    if action == "move" and target:
        with get_session() as conn:
            world_db = WorldDB(conn)
            world = world_db.get_world()
            
            if not world:
                return ActResponse(success=False, message="World not found")
            
            # 查找目标 zone
            target_zone = None
            for zone in world.zones:
                if zone.id == target:
                    target_zone = zone
                    break
            
            if not target_zone:
                return ActResponse(success=False, message=f"Zone '{target}' not found")
            
            # 检查连通性
            current_zone_id = agent["position"]["zone_id"]
            current_zone = None
            for zone in world.zones:
                if zone.id == current_zone_id:
                    current_zone = zone
                    break
            
            if current_zone and target_zone.id not in current_zone.connected_zones:
                return ActResponse(
                    success=False, 
                    message=f"Cannot move from {current_zone_id} to {target_zone.id}: not connected"
                )
            
            # 执行移动
            agent["position"]["zone_id"] = target_zone.id
            agent["position"]["x"] = (target_zone.bounds["min_x"] + target_zone.bounds["max_x"]) / 2
            agent["position"]["y"] = (target_zone.bounds["min_y"] + target_zone.bounds["max_y"]) / 2
            
            return ActResponse(
                success=True,
                message=f"Moved to {target_zone.name}",
                state_change={"zone_id": target_zone.id},
            )
    
    # === 交互（与物体）===
    elif action == "interact" and target:
        with get_session() as conn:
            world_db = WorldDB(conn)
            world = world_db.get_world()
            
            if not world:
                return ActResponse(success=False, message="World not found")
            
            # 使用共享实体管理器
            from agent_world.entities import get_entity_manager, init_entity_manager
            zone_dicts = [z.model_dump() for z in world.zones]
            init_entity_manager(zone_dicts)
            obj_manager = get_entity_manager()
            
            # 查找物体
            obj = obj_manager.get(target)
            if not obj:
                return ActResponse(success=False, message=f"Object '{target}' not found")
            
            # 检查是否在同一 zone
            if obj.zone_id != agent["position"]["zone_id"]:
                return ActResponse(
                    success=False, 
                    message=f"Cannot interact: agent in {agent['position']['zone_id']}, object in {obj.zone_id}"
                )
            
            # 检查是否可交互
            can_do, reason = obj.can_interact(agent_id, req.extra.get("interact_type", "use") if req.extra else "use")
            if not can_do:
                return ActResponse(success=False, message=reason)
            
            # 执行交互
            result = obj.interact(agent_id, req.extra.get("interact_type", "use") if req.extra else "use")
            
            if result.success:
                if result.loot:
                    agent["inventory"].extend(result.loot)
                
                # 记录记忆
                agent["memory"].append({
                    "event": f"与 {obj.name} 交互: {result.description}",
                    "timestamp": time.time(),
                    "loot": result.loot,
                })
                
                return ActResponse(
                    success=True,
                    message=result.description,
                    reward=result.loot,
                )
            else:
                return ActResponse(success=False, message=result.description)
    
    # === 与 NPC 对话 ===
    elif action == "talk" and target:
        from agent_world.db import NPCDB
        
        with get_session() as conn:
            npc_db = NPCDB(conn)
            npc = npc_db.get_npc(target)
            
            if not npc:
                return ActResponse(success=False, message=f"NPC '{target}' not found")
            
            # 检查是否在同一 zone
            if npc.position.zone_id != agent["position"]["zone_id"]:
                return ActResponse(
                    success=False,
                    message=f"Cannot talk: agent in {agent['position']['zone_id']}, NPC in {npc.position.zone_id}"
                )
            
            # 简单的 NPC 对话响应
            dialogue = f"你好，我是 {npc.name}，是个 {npc.role}。"
            recent_info = npc.attributes.get("_recent_info", "")
            if recent_info:
                dialogue += f" 最近: {recent_info}"
            
            # 记录对话到 agent 记忆
            agent["memory"].append({
                "event": f"与 NPC {npc.name} 对话: {dialogue}",
                "timestamp": time.time(),
            })
            
            return ActResponse(
                success=True,
                message=dialogue,
            )
    
    return ActResponse(success=False, message=f"Unknown action: {action}")
