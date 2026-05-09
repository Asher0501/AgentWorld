# NPC REST API

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import NodeDB, get_session, node_to_npc, npc_to_node_dict
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
        ndb = NodeDB(conn)
        npc_nodes = ndb.get_nodes(type_filter="npc")
        npcs = [node_to_npc(nd) for nd in npc_nodes if nd]
        # 可选筛选
        if zone_id:
            npcs = [n for n in npcs if n.position.zone_id == zone_id]
        if role:
            npcs = [n for n in npcs if n.role == role]
        return ListResponse(
            data=[n.model_dump() for n in npcs[:limit]],
            count=len(npcs)
        )


@router.get("/{npc_id}", response_model=NPCResponse)
def get_npc(npc_id: str):
    """获取单个 NPC"""
    with get_session() as conn:
        ndb = NodeDB(conn)
        nd = ndb.get_node(npc_id)
        if not nd or nd["type"] != "npc":
            raise HTTPException(status_code=404, detail="NPC not found")
        npc = node_to_npc(nd)
        if not npc:
            raise HTTPException(status_code=404, detail="NPC not found")
        return NPCResponse(**npc.model_dump())


@router.post("", response_model=NPCResponse)
def create_npc(req: NPCCreateRequest):
    """创建新 NPC"""
    npc = NPC(name=req.name, role=req.role)
    with get_session() as conn:
        ndb = NodeDB(conn)
        from ..services.graph_adapter import _make_eid
        from ..db import NodeType
        eid = _make_eid("npc", npc.name)
        data = npc_to_node_dict(npc)
        ndb.upsert_node(eid, NodeType.NPC, npc.name, data)
    return NPCResponse(**npc.model_dump())


@router.patch("/{npc_id}", response_model=NPCResponse)
def update_npc(npc_id: str, req: NPCUpdateRequest):
    """更新 NPC"""
    with get_session() as conn:
        ndb = NodeDB(conn)
        nd = ndb.get_node(npc_id)
        if not nd or nd["type"] != "npc":
            raise HTTPException(status_code=404, detail="NPC not found")
        npc = node_to_npc(nd)
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

        # 写回 nodes
        from ..services.graph_adapter import _make_eid
        from ..db import NodeType
        eid = _make_eid("npc", npc.name)
        data = npc_to_node_dict(npc)
        data["connected_entity_ids"] = nd["data"].get("connected_entity_ids", [])
        data["recent_info"] = nd["data"].get("recent_info", "")
        ndb.upsert_node(eid, NodeType.NPC, npc.name, data)
    return NPCResponse(**npc.model_dump())


@router.delete("/{npc_id}", response_model=SuccessResponse)
def delete_npc(npc_id: str):
    """删除 NPC"""
    with get_session() as conn:
        ndb = NodeDB(conn)
        nd = ndb.get_node(npc_id)
        if not nd or nd["type"] != "npc":
            raise HTTPException(status_code=404, detail="NPC not found")
        ndb.delete_node(npc_id)
    return SuccessResponse(success=True, message=f"NPC {npc_id} deleted")
