# NPC REST API

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import NPCDB, get_session
from ..db.schemas import (
    NPCCreate, NPCUpdate, NPCResponse,
    SuccessResponse, ErrorResponse, ListResponse
)
from ..models.npc import NPC, NPCStatus

router = APIRouter(prefix="/npc", tags=["NPC"])


class NPCCreateRequest(BaseModel):
    name: str
    role: str


class NPCUpdateRequest(BaseModel):
    name: str | None = None
    level: int | None = None
    status: NPCStatus | None = None
    inventory: list[str] | None = None


@router.get("", response_model=ListResponse)
def list_npcs(
    zone_id: str | None = None,
    role: str | None = None,
    limit: int = Query(default=100, le=500)
):
    """列出 NPC，可按 zone_id 或 role 筛选"""
    with get_session() as conn:
        db = NPCDB(conn)
        npcs = db.list_npcs(zone_id=zone_id, role=role, limit=limit)
        return ListResponse(
            data=[npc.model_dump() for npc in npcs],
            count=len(npcs)
        )


@router.get("/{npc_id}", response_model=NPCResponse)
def get_npc(npc_id: str):
    """获取单个 NPC"""
    with get_session() as conn:
        db = NPCDB(conn)
        npc = db.get_npc(npc_id)
        if not npc:
            raise HTTPException(status_code=404, detail="NPC not found")
        return NPCResponse(**npc.model_dump())


@router.post("", response_model=NPCResponse)
def create_npc(req: NPCCreateRequest):
    """创建新 NPC"""
    npc = NPC(name=req.name, role=req.role)
    with get_session() as conn:
        db = NPCDB(conn)
        db.create_npc(npc)
    return NPCResponse(**npc.model_dump())


@router.patch("/{npc_id}", response_model=NPCResponse)
def update_npc(npc_id: str, req: NPCUpdateRequest):
    """更新 NPC"""
    with get_session() as conn:
        db = NPCDB(conn)
        npc = db.get_npc(npc_id)
        if not npc:
            raise HTTPException(status_code=404, detail="NPC not found")
        
        if req.name is not None:
            npc.name = req.name
        if req.level is not None:
            npc.level = req.level
        if req.status is not None:
            npc.status = req.status
        if req.inventory is not None:
            npc.inventory = req.inventory
        
        npc.updated_at = datetime.now()
        db.update_npc(npc)
    return NPCResponse(**npc.model_dump())


@router.delete("/{npc_id}", response_model=SuccessResponse)
def delete_npc(npc_id: str):
    """删除 NPC"""
    with get_session() as conn:
        db = NPCDB(conn)
        npc = db.get_npc(npc_id)
        if not npc:
            raise HTTPException(status_code=404, detail="NPC not found")
        db.delete_npc(npc_id)
    return SuccessResponse(success=True, message=f"NPC {npc_id} deleted")
