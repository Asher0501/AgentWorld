"""
PipelineOrchestrator — 通用管线顶层编排器。

职责：
  - 按 adapter.get_pipeline_stages() 的声明顺序驱动完整 tick
  - 管理 per-NPC / per-edge 异步并发循环
  - 管理跨阶段 retry（LLM #4a+4b → LLM #5 → 重试 → 降级）
  - 管理非 LLM 阶段（IntentExecutor）
  - 传递阶段间上下文（plan_map, stories, exec_results 等）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from ..domain.adapter import (
    DomainAdapter, GraphOp, StateChange,
    StageOutputType, PipelineStage, NodeDescriptor,
)
from .pipeline_engine import PipelineEngine, StageResult

logger = logging.getLogger("PipelineOrchestrator")


class PipelineContext:
    """管线阶段间上下文。"""

    def __init__(self):
        # 时间
        self.world_time_str: str = ""
        self.tick_duration_str: str = ""

        # ——— 阶段输入 ———
        self.plan_map: dict[str, str] = {}      # npc_eid → plan_text
        self.npc_info: dict[str, dict] = {}     # npc_eid → {name, model, entity, ...}

        # ——— 阶段中间输出 ———
        self.topo_structure_ops: list[GraphOp] = []
        self.exec_results: list[dict] = []
        self.stories: list[str] = []

        # ——— LLM #4a / #4b 输出 ———
        self.topo_delta_ops: list[GraphOp] = []
        self.attr_ops: list[StateChange] = []
        self.recent_info_map: dict[str, str] = {}

        # ——— retry 存档 ———
        self.first_topo_raw: str = ""
        self.first_attr_raw: str = ""

        # ——— 最终结果 ———
        self.tick_results: list[dict] = []

    def snapshot(self) -> dict:
        return {
            "plans": len(self.plan_map),
            "npcs": len(self.npc_info),
            "topo_struct": len(self.topo_structure_ops),
            "exec": len(self.exec_results),
            "stories": len(self.stories),
            "topo_delta": len(self.topo_delta_ops),
            "attr": len(self.attr_ops),
            "ri": len(self.recent_info_map),
        }


class PipelineOrchestrator:
    """顶层编排器。"""

    def __init__(
        self,
        adapter: DomainAdapter,
        resolver: Any,
        graph_engine: Any,
        io_dir: str = "",
        llm_available: bool = True,
    ):
        self.adapter = adapter
        self.resolver = resolver
        self.graph_engine = graph_engine
        self.io_dir = io_dir
        self.llm_available = llm_available
        self._engine = PipelineEngine(adapter, resolver, graph_engine, io_dir=io_dir)

        # 延迟导入
        self._PP: Optional[type] = None
        self._IL: Optional[type] = None
        self._VL: Optional[type] = None
        self._has_role: Optional[Callable] = None

    def _lazy_import(self):
        if self._PP is not None:
            return
        from .post_processor import PostProcessor as PP
        from .interaction_layer import InteractionLayer as IL
        from .verification_layer import VerificationLayer as VL
        from ..config.config_loader import has_role
        self._PP, self._IL, self._VL = PP, IL, VL
        self._has_role = has_role

    def get_timing(self) -> dict[str, float]:
        return self._engine.get_timing()

    # ═══════════════════════════════════════════
    # 完整 Tick 编排
    # ═══════════════════════════════════════════

    async def run_tick(
        self,
        npcs: list,
        plan_map: dict[str, str],
        npc_info: dict[str, dict],
        world_time_str: str = "",
        tick_duration_str: str = "",
    ) -> list[dict]:
        """
        执行完整管线 tick（LLM #2 → IntentExecutor → LLM #3 → #4a/#4b/#5 → 执行）。

        Args:
            npcs: DB NPC 模型列表
            plan_map: LLM #1 输出的计划映射 {npc_eid: plan_text}
            npc_info: NPC 信息 {npc_eid: {name, model, entity, ...}}
            world_time_str: 当前世界时间
            tick_duration_str: tick 持续时间标签

        Returns:
            tick_results: 与旧 _execute_4llm_pipeline 返回格式一致
        """
        self._lazy_import()
        ctx = self._build_context(plan_map, npc_info, world_time_str, tick_duration_str)

        if not self.llm_available or not self.resolver:
            raise RuntimeError("LLM 不可用")

        if not ctx.plan_map:
            return []

        # Step 2: LLM #2 — 拓扑结构变更
        await self._stage_topo_structure(ctx)

        # Step 3: IntentExecutor — 执行结构变更
        ctx.exec_results = self._execute_intents(npcs, ctx)

        # Step 4: LLM #3 — 故事生成
        ctx.stories = self._stage_narrative(ctx)

        # Step 5: LLM #4a → #4b → 校验/重试/降级
        await self._stage_verification_loop(ctx)

        # Step 6: 执行最终操作
        self._apply_final_ops(ctx)

        # Step 7: 构建返回结果
        ctx.tick_results = self._build_tick_results(ctx)
        logger.info(f"[Orchestrator] 完成，{len(ctx.tick_results)} 条结果 | "
                     f"状态: {ctx.snapshot()}")
        return ctx.tick_results

    def _build_context(self, plan_map, npc_info, wts, tds) -> PipelineContext:
        ctx = PipelineContext()
        ctx.plan_map = plan_map
        ctx.npc_info = npc_info
        ctx.world_time_str = wts
        ctx.tick_duration_str = tds
        ctx.first_topo_raw = ""
        ctx.first_attr_raw = ""
        return ctx

    # ═══════════════════════════════════════════
    # Step 2: LLM #2
    # ═══════════════════════════════════════════

    async def _stage_topo_structure(self, ctx: PipelineContext):
        self.adapter.set_graph_engine(self.graph_engine)
        kw = dict(
            npc_plans=ctx.plan_map,
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
        )
        result = await self._engine.run_stage("topo_structure", kw)
        ctx.topo_structure_ops = result.ops
        logger.info(f"[LLM #2] {len(ctx.topo_structure_ops)} 个拓扑结构操作")

    # ═══════════════════════════════════════════
    # Step 3: IntentExecutor
    # ═══════════════════════════════════════════

    def _execute_intents(self, npcs: list, ctx: PipelineContext) -> list[dict]:
        """执行 LLM #2 的拓扑结构变更。"""
        npc_ops: dict[str, list[dict]] = {}
        for op in ctx.topo_structure_ops:
            npc_ops.setdefault(op.get("src", ""), []).append(op)

        results = []
        for neid, ops in sorted(npc_ops.items()):
            info = ctx.npc_info.get(neid)
            if not info or not ctx.plan_map.get(neid, ""):
                continue
            self.graph_engine.apply_edge_operations(ops)

            ent = info["entity"]
            zone_name = self._find_zone(ent)
            interacted = self._collect_interacted(ops, neid)

            results.append(self._exec_result_dict(
                info, neid, zone_name, ctx.plan_map[neid], interacted, ent,
            ))

        # 有计划但无操作的 NPC
        for neid, plan in ctx.plan_map.items():
            if neid in npc_ops:
                continue
            info = ctx.npc_info.get(neid)
            if not info:
                continue
            ent = info["entity"]
            zone_name = self._find_zone(ent)
            results.append(self._exec_result_dict(
                info, neid, zone_name, plan, [], ent,
            ))

        return results

    def _exec_result_dict(self, info, neid, zone, plan, interacted, ent):
        attrs = ent.attributes if ent else {}
        traits = ent.traits if ent and hasattr(ent, 'traits') else []
        model = info.get("model")
        mem_text = model.attributes.get("_recent_info", "") if model and hasattr(model, 'attributes') else ""

        interacted_npcs = [n for n in interacted
                           if self.graph_engine.get_entity(f"npc_{n[:8]}") is not None]
        interacted_objects = [n for n in interacted
                              if self.graph_engine.get_entity(f"object_{n[:8]}") is not None]

        return {
            "npc_name": info["name"],
            "npc_eid": neid,
            "npc_role": info["entity"].role if info.get("entity") else "?",
            "npc_id": info["model"].id if info.get("model") else "",
            "zone_after": zone,
            "zone_changed": False,
            "interacted_npcs": interacted_npcs,
            "interacted_objects": interacted_objects,
            "raw_intent": plan,
            "narrative": "",
            "memories": mem_text,
            "mood_text": self._val_text(attrs.get("mood"), _MOOD),
            "satiety_text": self._val_text(attrs.get("satiety"), _SAT),
            "vitality_text": self._val_text(attrs.get("vitality"), _VIT),
            "traits": traits,
        }

    def _find_zone(self, ent) -> str:
        if not ent:
            return "?"
        for conn in ent.connected_entity_ids:
            e = self.graph_engine.get_entity(conn)
            if e and self._has_role(e.type_id, "region"):
                return e.name
        return "?"

    def _collect_interacted(self, ops: list[dict], self_eid: str) -> list[str]:
        names = []
        for op in ops:
            tgt = op.get("tgt", "")
            if tgt == self_eid:
                continue
            te = self.graph_engine.get_entity(tgt)
            if te and (self._has_role(te.type_id, "actor")
                       or self._has_role(te.type_id, "fixture")):
                names.append(te.name)
        return names

    # ═══════════════════════════════════════════
    # Step 4: LLM #3
    # ═══════════════════════════════════════════

    def _stage_narrative(self, ctx: PipelineContext) -> list[str]:
        il = self._IL(resolver=self.resolver, adapter=self.adapter)
        edge_results = il.process(
            [er for er in ctx.exec_results],
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
        )
        stories = list(dict.fromkeys(e.description for e in edge_results))
        logger.info(f"[LLM #3] {len(edge_results)} 条边 → {len(stories)} 个唯一故事")
        return stories

    # ═══════════════════════════════════════════
    # Step 5: 验证/重试/降级
    # ═══════════════════════════════════════════

    async def _stage_verification_loop(self, ctx: PipelineContext):
        from ..config.config_loader import get_verification_config

        max_retries = get_verification_config("max_retries", 1)
        pp = self._PP(resolver=self.resolver, adapter=self.adapter, engine=self._engine)

        # 第一轮
        ctx.topo_delta_ops = pp.resolve_topology_changes(
            npc_plans=ctx.plan_map, stories=ctx.stories,
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
        )
        ctx.first_topo_raw = getattr(pp, "_last_raw_topo_response", "")
        logger.info(f"[LLM #4a] {len(ctx.topo_delta_ops)} 个拓扑操作")

        ctx.attr_ops, ctx.recent_info_map = pp.resolve_attr_and_recent(
            npc_plans=ctx.plan_map, stories=ctx.stories,
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
        )
        ctx.first_attr_raw = getattr(pp, "_last_raw_attr_response", "")
        logger.info(f"[LLM #4b] {len(ctx.attr_ops)} attr, {len(ctx.recent_info_map)} 条近况")

        # 验证 + 重试
        vl = self._VL(
            resolver=self.resolver, adapter=self.adapter,
            graph_engine=self.graph_engine, llm_available=self.llm_available,
        )

        for attempt in range(max_retries + 1):
            failures = vl.check_all(
                ctx.stories, ctx.topo_delta_ops, ctx.attr_ops, ctx.recent_info_map,
            )
            if not failures:
                logger.info(f"[LLM #5] 校验通过 (attempt {attempt + 1})")
                break
            if attempt >= max_retries:
                logger.warning(f"[LLM #5] 已达最大重试次数 ({max_retries})")
                break

            feedback = self._VL.build_feedback(
                failures, previous_topo_output=ctx.first_topo_raw,
                previous_attr_output=ctx.first_attr_raw,
            )
            logger.warning(f"[LLM #5] 校验失败 ({len(failures)} 项)，重试 #{attempt + 2}")

            ctx.topo_delta_ops = pp.resolve_topology_changes(
                npc_plans=ctx.plan_map, stories=ctx.stories,
                graph_engine=self.graph_engine,
                world_time_str=ctx.world_time_str,
                tick_duration_str=ctx.tick_duration_str,
                feedback=feedback,
            )
            ctx.attr_ops, ctx.recent_info_map = pp.resolve_attr_and_recent(
                npc_plans=ctx.plan_map, stories=ctx.stories,
                graph_engine=self.graph_engine,
                world_time_str=ctx.world_time_str,
                tick_duration_str=ctx.tick_duration_str,
                feedback=feedback,
            )

        # 降级
        if ctx.topo_delta_ops:
            filtered = self.adapter.validate_ops(ctx.topo_delta_ops, self.graph_engine)
            removed = len(ctx.topo_delta_ops) - len(filtered)
            if removed:
                logger.warning(f"[降级] 度守恒移除 {removed}/{len(ctx.topo_delta_ops)} 操作")
            ctx.topo_delta_ops = filtered

        logger.info(f"[LLM #5] 最终: {len(ctx.topo_delta_ops)} topo, "
                     f"{len(ctx.attr_ops)} attr, {len(ctx.recent_info_map)} ri")

    # ═══════════════════════════════════════════
    # Step 6: 执行
    # ═══════════════════════════════════════════

    def _apply_final_ops(self, ctx: PipelineContext):
        if ctx.topo_delta_ops:
            r = self.graph_engine.apply_edge_operations(ctx.topo_delta_ops)
            logger.info(f"[Engine] #4a 拓扑执行: {r['status']}")
            for err in (r.get("errors") or []):
                logger.warning(f"[Engine]   增量错误: {err}")

        if ctx.recent_info_map:
            from ..config.node_ontology import has_recent_info
            w = 0
            for eid, txt in ctx.recent_info_map.items():
                ent = self.graph_engine.get_entity(eid)
                if ent and has_recent_info(ent.type_id):
                    ent.recent_info = txt
                    w += 1
            if w:
                logger.info(f"[LLM #5] 近况投影写入 {w} 个实体")

        if ctx.attr_ops:
            r = self.graph_engine.apply_edge_operations(ctx.attr_ops)
            logger.info(f"[Engine] #4b attr 执行: {r['status']} ({len(r['results'])} 条)")
            for err in (r.get("errors") or []):
                logger.warning(f"[Engine]   增量错误: {err}")

    # ═══════════════════════════════════════════
    # Step 7: 构建返回
    # ═══════════════════════════════════════════

    def _build_tick_results(self, ctx: PipelineContext) -> list[dict]:
        results = []
        for er in ctx.exec_results:
            neid = er.get("npc_eid", "")
            name = er.get("npc_name", "?")
            ent = self.graph_engine.get_entity(neid)
            zone_now, vit_now, inv = "?", 100, {}
            if ent:
                zone_now = self._find_zone(ent)
                vit_now = int(ent.attributes.get("vitality", 100))
                inv = {iv["item_name"]: iv["quantity"]
                       for iv in self.graph_engine.get_inventory_view(neid)}
            plan_text = ctx.plan_map.get(neid, name)
            results.append({
                "npc_id": er.get("npc_id", ""),
                "npc_name": name,
                "zone": zone_now,
                "action": plan_text[:50],
                "action_text": plan_text,
                "vitality": vit_now,
                "inventory": inv,
                "tick": 0,
            })
        return results

    # ═══════════════════════════════════════════
    # 帮助
    # ═══════════════════════════════════════════

    def _val_text(self, val, labels):
        if val is None:
            return "未知"
        if val < 30:
            return labels[0]
        if val < 50:
            return labels[1]
        if val < 70:
            return labels[2]
        return labels[3]


_MOOD = ("很低落", "有点低落", "一般", "不错")
_SAT = ("很饿", "有点饿", "还行", "吃饱了")
_VIT = ("很疲惫", "有些累", "还行", "精力充沛")
