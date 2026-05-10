"""
图引擎 NPC 服务 —— 4-LLM 纯拓扑流水线

每 tick 流程：
  1. 从数据库加载 NPC → 构建纯拓扑图（无接口边）
  2. LLM #1: 拓扑子图 → NPC 自然语言计划
  3. LLM #2: 计划 + 拓扑 → 拓扑结构变更（connect/disconnect/set_qty）
  4. IntentExecutor: 执行拓扑结构变更 → exec_results
  5. LLM #3 (InteractionLayer): exec_results + 拓扑 → 故事文本
  6. LLM #4 (PostProcessor): 故事 + 拓扑 → 数值增量（{src, tgt, delta}）
  7. GraphEngine: 执行数值增量 → 拓扑更新
  8. 同步回 NPC 模型 → 写回数据库
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable

from ..db import NodeDB, get_session, node_to_npc
from ..models.npc import NPC, Position
from ..models.world import WorldTime

from ..config.config_loader import has_role

from .graph_engine import GraphEngine
from .graph_adapter import build_world_graph, build_graph_from_nodes, sync_graph_to_nodes, sync_entity_to_db, _make_eid
from .interaction_resolver import InteractionResolver
from ..domain.npc_world.adapter import NPCWorldAdapter as _DefaultAdapter
from .pipeline_orchestrator import PipelineOrchestrator


# ─── 数值→文字辅助（供 LLM #3 叙事使用） ───

logger = logging.getLogger("graph_npc_engine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(_h)


class GraphNPCEngine:
    """
    基于 4-LLM 纯拓扑流水线的 NPC 引擎。

    设计原则：
      - LLM 从不直接写入数据
      - GraphEngine 是唯一的数据写入路径
      - LLM #2 输出拓扑结构变更，LLM #4 输出数值增量
      - 边无类型标签，语义从节点描述推断
    """

    def __init__(self, llm_available: bool = False, llm_callback=None,
                 llm_model: str | None = None, llm_temperature: float = 0.7,
                 small: bool = False, adapter=None):
        self.llm_available = llm_available
        self.llm_callback = llm_callback
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._small_mode = small
        self._resolver: InteractionResolver | None = None
        self._listeners: list[Callable] = []
        self._running = False
        self.tick_count = 0
        self.graph_engine = GraphEngine()
        self._world_initialized = False
        self._current_world_time_str = ""
        self._current_time_of_day = ""
        self._tick_duration_str = ""
        self._adapter = adapter or _DefaultAdapter()
        self._io_dir = ""
        self._pending_new_entities: list = []  # LLM 自动注册的新实体缓冲

    def add_listener(self, listener: Callable):
        self._listeners.append(listener)

    async def _notify(self, tick_results: list[dict]):
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(tick_results)
                else:
                    listener(tick_results)
            except Exception:
                pass

    def _ensure_world_initialized(self):
        """
        确保实体管理器已初始化 + nodes 表已播种。
        首次运行时，NodeDB.load_or_seed() 会从 config 填充
        NPC/zone/item/recipe + 世界时间到 nodes 表。
        """
        if self._world_initialized:
            return
        from ..config.config_loader import build_zone_models
        zones = build_zone_models()

        with get_session() as conn:
            node_db = NodeDB(conn)
            node_db.load_or_seed()

        self._world_initialized = True

    def _init_resolver(self):
        """延迟初始化 InteractionResolver"""
        if self._resolver is not None:
            return
        try:
            self._resolver = InteractionResolver(
                model=self._llm_model,
                temperature=self._llm_temperature,
            )
            logger.info("InteractionResolver 初始化成功")
        except ValueError as e:
            logger.warning(f"InteractionResolver 初始化失败: {e}，退回到兜底模式")
            self.llm_available = False
        except Exception as e:
            logger.error(f"InteractionResolver 异常: {e}，退回到兜底模式")
            self.llm_available = False

    # ═══════════════════════════════════════════
    # Tick 核心
    # ═══════════════════════════════════════════

    async def tick(self) -> list[dict]:
        """执行一次交互图 tick"""
        self.tick_count += 1
        self._ensure_world_initialized()

        with get_session() as conn:
            node_db = NodeDB(conn)

            # 1. 从 nodes 表加载 NPC 模型
            npc_nodes = node_db.get_nodes(type_filter="npc")
            db_npcs = [node_to_npc(nd) for nd in npc_nodes if nd]

            # 2. 世界时间
            wt_dict = node_db.get_world_time()
            world_time = WorldTime(**wt_dict)
            h = world_time.hour
            if h >= 21 or h < 6:
                tick_minutes = 360
                tick_duration_label = "6 小时"
            else:
                tick_minutes = 30
                tick_duration_label = "30 分钟"
            world_time.tick(minutes=tick_minutes)
            self._current_world_time_str = world_time.to_display_str()
            self._current_time_of_day = world_time.get_time_of_day()
            self._tick_duration_str = tick_duration_label
            logger.info(f"[WorldTime] → {self._current_world_time_str} ({self._current_time_of_day}) +{tick_duration_label}")

            # 3. 加载所有节点（建图用）
            node_data = node_db.load_or_seed()

        if not db_npcs:
            return []

        # 4. 建图
        self.graph_engine = GraphEngine()
        self._pending_new_entities = []
        self.graph_engine.set_on_entity_created(self._on_graph_entity_created)
        build_graph_from_nodes(self.graph_engine, node_data)

        # 5. 配置补充边
        from ..config.config_loader import get_npc_defs, get_items, get_domain_zones, _load_domain
        from .graph_adapter import process_config_edges
        process_config_edges(
            self.graph_engine,
            npc_config_dicts=get_npc_defs(),
            item_config_dicts=get_items(),
            zone_config_dicts=get_domain_zones(),
            recipe_config_dicts=_load_domain().get("recipes", []),
        )

        # 6. 从 NPC 模型恢复位置 + 库存边
        zone_lookup: dict[str, str] = {}
        for nd in node_data:
            if nd["type"] == "zone":
                d = nd["data"] if isinstance(nd["data"], dict) else json.loads(nd["data"])
                zone_lookup[nd["name"]] = nd["id"]
                cid = d.get("attributes", {}).get("_config_id", "")
                if cid:
                    zone_lookup[cid] = nd["id"]
        for npc in db_npcs:
            npc_eid = _make_eid("npc", npc.name)
            if not self.graph_engine.get_entity(npc_eid):
                continue

            zone_key = npc.position.zone_id
            if zone_key:
                tgt = None
                if self.graph_engine.get_entity(zone_key):
                    tgt = zone_key
                else:
                    clean = zone_key.replace("zone_", "")
                    if clean in zone_lookup:
                        tgt = zone_lookup[clean]
                if tgt:
                    self.graph_engine.connect(npc_eid, tgt, qty=1)

            inv = npc.inventory or []
            inv_counts: dict[str, int] = {}
            for item_name in inv:
                inv_counts[item_name] = inv_counts.get(item_name, 0) + 1
            for item_name, qty in inv_counts.items():
                item_eid = _make_eid("item", item_name)
                if not self.graph_engine.get_entity(item_eid):
                    from .graph_adapter import item_to_entity
                    ent = item_to_entity(item_name, qty)
                    self.graph_engine.register_entity(ent)
                self.graph_engine.connect(npc_eid, item_eid, qty=qty)

        # 7. 4-LLM 流水线
        if self.llm_available:
            self._init_resolver()
        results = await self._execute_4llm_pipeline(db_npcs)

        # 8. 被动衰减
        self._decay_and_sync(db_npcs)

        # 9. 同步回 NPC 模型
        self._sync_back_to_nodes(db_npcs)

        # 10. 持久化到 nodes 表
        with get_session() as conn:
            node_db = NodeDB(conn)
            # 所有实体（先写，不包含 world_time）
            sync_graph_to_nodes(node_db, self.graph_engine)
            # 世界时间（后写，确保不会被 sync_graph_to_nodes 覆盖）
            node_db.save_world_time({
                "year": world_time.year,
                "month": world_time.month,
                "day": world_time.day,
                "hour": world_time.hour,
                "minute": world_time.minute,
            })

        # 11. 补充持久化 pending 新实体
        if self._pending_new_entities:
            with get_session() as conn:
                node_db = NodeDB(conn)
                for ent in self._pending_new_entities:
                    sync_entity_to_db(node_db, ent)
            self._pending_new_entities.clear()

        return results

    def _on_graph_entity_created(self, entity):
        """图引擎自动创建新实体时的回调 — 缓冲后持久化"""
        self._pending_new_entities.append(entity)





    # ═══════════════════════════════════════════
    # 4-LLM 流水线
    # ═══════════════════════════════════════════

    async def _execute_4llm_pipeline(self, npcs) -> list[dict]:
        """由 PipelineOrchestrator 编排的完整 5-LLM 流水线。"""
        if not self.llm_available or not self._resolver:
            raise RuntimeError("LLM 不可用 — 引擎停止")

        orchestrator = PipelineOrchestrator(
            adapter=self._adapter,
            resolver=self._resolver,
            graph_engine=self.graph_engine,
            io_dir=self._io_dir or "",
            llm_available=self.llm_available,
        )
        results = await orchestrator.run_tick(
            npcs=npcs,
            world_time_str=self._current_world_time_str,
            tick_duration_str=self._tick_duration_str,
        )

        # 给结果打上 tick 编号
        for r in results:
            r["tick"] = self.tick_count

        return results



    # ═══════════════════════════════════════════
    # 记忆 & 衰减
    # ═══════════════════════════════════════════

    def _decay_and_sync(self, npcs):
        """被动衰减 + 同步。
        
        记忆写入已移除（由 LLM #5 recent_info 代替）。
        仅保留属性衰减逻辑。
        """
        for npc in npcs:
            neid = _make_eid("npc", npc.name)
            ent = self.graph_engine.get_entity(neid)
            if not ent:
                continue

            # 被动衰减
            satiety = ent.attributes.get("satiety")
            mood = ent.attributes.get("mood")
            if satiety is not None:
                ent.attributes["satiety"] = max(0, satiety - 1)
            if mood is not None:
                ent.attributes["mood"] = max(0, mood - 0.5)

    # ═══════════════════════════════════════════
    # 写回
    # ═══════════════════════════════════════════

    def _sync_back_to_nodes(self, npcs):
        """
        将 entity 状态同步回持久层。
        当前支持 NPC 的位置/属性/库存/近况投影。
        后续扩展其他类型时在此函数统一处理。
        """
        from .graph_adapter import _make_eid
        for npc in npcs:
            neid = _make_eid("npc", npc.name)
            ent = self.graph_engine.get_entity(neid)
            if not ent:
                continue

            # 位置：从 1-hop 子图中找 zone 连接
            # 写 eid（如 zone_狐狸与鹅酒馆）而非 display name，
            # 保证下个 tick init_graph_edges_from_adapter 能正确解析。
            for conn in ent.connected_entity_ids:
                e = self.graph_engine.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    npc.position.zone_id = e.entity_id
                    break

            # 属性
            v = ent.attributes.get("vitality")
            if v is not None:
                npc.vitality = max(0, min(100, int(v)))
            s = ent.attributes.get("satiety")
            if s is not None:
                npc.satiety = max(0, min(100, int(s)))
            m = ent.attributes.get("mood")
            if m is not None:
                npc.mood = max(0, min(100, int(m)))

            # 库存：从出边中取 qty>0 的
            inv_view = self.graph_engine.get_inventory_view(neid)
            if inv_view:
                new_inv = []
                for item in inv_view:
                    new_inv.extend([item["item_name"]] * item["quantity"])
                npc.inventory = new_inv

            # 近况投影（统一接口，类型无关）
            if ent.recent_info:
                if not hasattr(npc, 'attributes') or npc.attributes is None:
                    npc.attributes = {}
                npc.attributes["_recent_info"] = ent.recent_info

    # ═══════════════════════════════════════════
    # 运行循环
    # ═══════════════════════════════════════════

    async def run(self):
        self._running = True
        self.tick_count = 0
        self._ensure_world_initialized()

        logger.info("GraphNPCEngine 启动 (纯拓扑 4-LLM 流水线)")

        while self._running:
            try:
                tick_start = time.time()
                results = await self.tick()
                tick_end = time.time()

                if results:
                    for r in results:
                        nm = r.get("npc_name", "?")
                        act = r.get("action", "")
                        vt = r.get("vitality", 0)
                        z = r.get("zone", "?")
                        inv = r.get("inventory", {})
                        inv_str = " ".join(f"{k}x{v}" for k, v in inv.items())[:60]
                        logger.info(f"[Tick] {nm} | {act[:40]} @ {z} | vit={vt} | {inv_str}")
                else:
                    logger.info("[Tick] 无NPC")

                await self._notify(results)
                elapsed = tick_end - tick_start
                await asyncio.sleep(max(0, 600 - elapsed))

            except asyncio.CancelledError:
                break
            except RuntimeError:
                raise
            except Exception as e:
                logger.error(f"Tick 异常: {e}")
                raise

        logger.info("GraphNPCEngine 停止")

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════
# 记忆重要度计算
# ═══════════════════════════════════════════════════


