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
from .post_processor import PostProcessor as _PostProcessor
from .interaction_layer import InteractionLayer as _InteractionLayer
from .verification_layer import VerificationLayer as _VerificationLayer
from .graph_engine import TopoComponent
from ..config.config_loader import has_role, get_verification_config

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
        self.stories: list[str] = []  # 旧模式：全局故事（保留向后兼容）

        # ——— 连通分量分割（新增） ———
        self.components: list[TopoComponent] = []

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
            "components": len(self.components),
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
        self._pp = _PostProcessor(resolver=resolver, adapter=adapter, engine=self._engine)
        self._il = _InteractionLayer(resolver=resolver, adapter=adapter, engine=self._engine)
        self._vl = _VerificationLayer(
            resolver=resolver, adapter=adapter,
            graph_engine=graph_engine, llm_available=llm_available,
        )

    def get_timing(self) -> dict[str, float]:
        return self._engine.get_timing()

    # ═══════════════════════════════════════════
    # 完整 Tick 编排
    # ═══════════════════════════════════════════

    async def run_tick(
        self,
        npcs: list,
        world_time_str: str = "",
        tick_duration_str: str = "",
    ) -> list[dict]:
        """
        执行完整管线 tick（LLM #1 → #2 → IntentExecutor → 分量分割 →
            每分量: #3 → #4a → #4b → #5 → 归并 → 执行）。

        Args:
            npcs: DB NPC 模型列表
            world_time_str: 当前世界时间
            tick_duration_str: tick 持续时间标签

        Returns:
            tick_results: 与旧 _execute_4llm_pipeline 返回格式一致
        """
        ctx = PipelineContext()
        ctx.world_time_str = world_time_str
        ctx.tick_duration_str = tick_duration_str

        if not self.llm_available or not self.resolver:
            raise RuntimeError("LLM 不可用")

        # Step 1: LLM #1 — 计划生成（不变，per-NPC 并发）
        await self._run_stage_plan(npcs, ctx)
        if not ctx.plan_map:
            return []
        logger.info(f"[LLM #1] {len(ctx.plan_map)} 个 NPC 的计划已生成")

        # Step 2: LLM #2 — 拓扑结构变更（不变，全局视图）
        await self._stage_topo_structure(ctx)

        # Step 3: IntentExecutor — 执行结构变更（不变）
        ctx.exec_results = self._execute_intents(npcs, ctx)

        # Step 4: 连通分量分割（新增）
        ctx.components = self._split_components(ctx)

        # Step 5: 全分量 LLM #3 → #4a → #4b → #5（并行）
        tasks = [self._run_one_component(ctx, comp) for comp in ctx.components]
        await asyncio.gather(*tasks)

        # Step 6: 归并并执行最终操作
        self._merge_commit_components(ctx)

        # Step 7: 构建返回结果
        ctx.tick_results = self._build_tick_results(ctx)
        logger.info(f"[Orchestrator] 完成，{len(ctx.tick_results)} 条结果 | "
                     f"状态: {ctx.snapshot()}")
        return ctx.tick_results

    # ═══════════════════════════════════════════
    # Step 1: LLM #1 — per-NPC 计划生成
    # ═══════════════════════════════════════════

    async def _run_stage_plan(self, npcs: list, ctx: PipelineContext) -> None:
        """per-NPC 并发计划生成，填充 ctx.plan_map 和 ctx.npc_info。"""
        from .graph_adapter import _make_eid
        from .prompt_assembler import assemble
        PLAN_TIMEOUT = 300.0

        npc_prompts: list[tuple[str, str]] = []

        for npc in npcs:
            neid = _make_eid("npc", npc.name)
            ent = self.graph_engine.get_entity(neid)
            if not ent:
                continue

            inv = self.graph_engine.get_inventory_view(neid)
            memories = []

            personality_tags = []
            for tag in (getattr(npc, 'persona_tags', []) or []):
                if hasattr(tag, 'tag'):
                    personality_tags.append(tag.tag)

            zone_npcs = []
            for conn in ent.connected_entity_ids:
                e = self.graph_engine.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    for other_ent in self.graph_engine.all_entities():
                        if has_role(other_ent.type_id, "actor") and other_ent != ent \
                           and other_ent.is_connected_to(e.entity_id):
                            zone_npcs.append({"name": other_ent.name, "role": other_ent.role or "?"})
                    break

            prompt = assemble(
                "llm1_plan", self.adapter, self.graph_engine,
                _caller="llm1", entity=ent, npc_name=npc.name,
                npc_role=ent.role or "", memories=memories,
                personality_tags=personality_tags, inventory=inv,
                zone_npcs=zone_npcs,
                time_str=ctx.world_time_str, tick_str=ctx.tick_duration_str,
            )

            npc_prompts.append((neid, prompt))
            ctx.npc_info[neid] = {
                "name": npc.name,
                "model": npc,
                "entity": ent,
                "zone_npcs": zone_npcs,
            }

        if not npc_prompts:
            return

        raw_plans = await asyncio.wait_for(
            self._engine.run_stage_plan_combined(npc_prompts, "plan"),
            timeout=PLAN_TIMEOUT,
        )

        for neid, _ in npc_prompts:
            plan = raw_plans.get(neid, "")
            if isinstance(plan, str) and plan.strip():
                ctx.plan_map[neid] = plan
            else:
                info = ctx.npc_info.get(neid, {})
                ent = info.get("entity")
                zone_name = "?"
                if ent:
                    for conn in ent.connected_entity_ids:
                        e = self.graph_engine.get_entity(conn)
                        if e and has_role(e.type_id, "region"):
                            zone_name = e.name
                            break
                ctx.plan_map[neid] = f"我在{zone_name}看看有什么可以做的。"

    # ═══════════════════════════════════════════
    # Step 2: LLM #2
    # ═══════════════════════════════════════════

    async def _stage_topo_structure(self, ctx: PipelineContext):
        self.adapter.set_graph_engine(self.graph_engine)
        global_label_map = self.adapter.build_global_label_map(self.graph_engine)
        kw = dict(
            npc_plans=ctx.plan_map,
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
            global_label_map=global_label_map,
            count=len(ctx.plan_map),
        )
        result = await self._engine.run_stage("topo_structure", kw, label_map=global_label_map)
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
            if e and has_role(e.type_id, "region"):
                return e.name
        return "?"

    def _collect_interacted(self, ops: list[dict], self_eid: str) -> list[str]:
        names = []
        for op in ops:
            tgt = op.get("tgt", "")
            if tgt == self_eid:
                continue
            te = self.graph_engine.get_entity(tgt)
            if te and (has_role(te.type_id, "actor")
                       or has_role(te.type_id, "fixture")):
                names.append(te.name)
        return names

    # ═══════════════════════════════════════════
    # Step 4: LLM #3
    # ═══════════════════════════════════════════

    async def _stage_narrative_component(
        self, ctx: PipelineContext, comp: TopoComponent
    ) -> list[str]:
        """
        为单个分量生成故事。只处理该分量的 exec_results。
        InteractionLayer.process 已改为 async，直接 await 不阻塞。
        """
        if not comp.exec_results:
            return []
        edge_results = await self._il.process(
            [er for er in comp.exec_results],
            graph_engine=self.graph_engine,
            world_time_str=ctx.world_time_str,
            tick_duration_str=ctx.tick_duration_str,
        )
        stories = list(dict.fromkeys(e.description for e in edge_results))
        logger.info(f"[分量 {comp.id}] LLM #3: {len(edge_results)} 条边 → "
                     f"{len(stories)} 个唯一故事")
        return stories

    # ═══════════════════════════════════════════
    # Step 4.5: 连通分量分割
    # ═══════════════════════════════════════════

    def _split_components(self, ctx: PipelineContext) -> list[TopoComponent]:
        """
        将 exec_results 按拓扑连通分量切分。
        """
        components = self.graph_engine.build_components()

        # 把 exec_results 按 NPC 归属分配到各分量
        npc_to_comp: dict[str, TopoComponent] = {}
        for comp in components:
            for neid in comp.npc_eids:
                npc_to_comp[neid] = comp

        for er in ctx.exec_results:
            neid = er.get("npc_eid", "")
            comp = npc_to_comp.get(neid)
            if comp is not None:
                comp.exec_results.append(er)
            else:
                # NPC 不在任何分量（理论上不应发生）→ 归入分量 0
                if components:
                    components[0].exec_results.append(er)

        # 移除空分量（无交互结果）
        active = [c for c in components if c.exec_results or c.npc_eids]
        # 重置 id
        for i, c in enumerate(active):
            c.id = i
        logger.info(f"[分量] {len(active)} 个活跃分量")
        return active

    # ═══════════════════════════════════════════
    # Step 4.6: 单分量管线
    # ═══════════════════════════════════════════

    async def _run_one_component(self, ctx: PipelineContext, comp: TopoComponent):
        """
        执行单个连通分量的完整 #3 → #4a → #4b → #5 管线。
        失败时自动 retry，仅限于本分量内部。
        """
        max_retries = get_verification_config("max_retries", 1)

        # LLM #3 for this component（异步）
        comp.stories = await self._stage_narrative_component(ctx, comp)

        # 筛选本分量的 npc_plans
        comp_plans = {eid: ctx.plan_map[eid] for eid in comp.npc_eids if eid in ctx.plan_map}

        for attempt in range(max_retries + 1):
            # LLM #4a — 拓扑操作（异步）
            comp.topo_ops = await self._pp.resolve_topology_changes_async(
                npc_plans=comp_plans,
                stories=comp.stories,
                graph_engine=self.graph_engine,
                world_time_str=ctx.world_time_str,
                tick_duration_str=ctx.tick_duration_str,
                topo_pool=comp.eids,
                label_map=comp.label_map,
            )
            logger.info(f"[分量 {comp.id}] LLM #4a: {len(comp.topo_ops)} 个拓扑操作")

            # LLM #4b — 属性 + 近况（异步）
            comp.attr_ops, comp.recent_info = await self._pp.resolve_attr_and_recent_async(
                npc_plans=comp_plans,
                stories=comp.stories,
                graph_engine=self.graph_engine,
                world_time_str=ctx.world_time_str,
                tick_duration_str=ctx.tick_duration_str,
            )
            logger.info(f"[分量 {comp.id}] LLM #4b: {len(comp.attr_ops)} attr, "
                         f"{len(comp.recent_info)} ri")

            # LLM #5 — 校验
            comp.failures = self._vl.check_all(
                comp.stories, comp.topo_ops, comp.attr_ops, comp.recent_info,
            )

            if not comp.failures:
                logger.info(f"[分量 {comp.id}] LLM #5 校验通过 (attempt {attempt + 1})")
                break

            if attempt >= max_retries:
                logger.warning(f"[分量 {comp.id}] 已达最大重试次数 ({max_retries})")
                break

            # 构造 feedback，只包含本分量的失败项
            first_topo = getattr(self._pp, "_last_raw_topo_response", "")
            first_attr = getattr(self._pp, "_last_raw_attr_response", "")
            feedback = self._vl.build_feedback(
                comp.failures,
                previous_topo_output=first_topo,
                previous_attr_output=first_attr,
            )
            logger.warning(f"[分量 {comp.id}] 校验失败 ({len(comp.failures)} 项)，"
                           f"重试 #{attempt + 2}")

        # 降级（按分量）
        if comp.topo_ops:
            filtered = self.adapter.validate_ops(comp.topo_ops, self.graph_engine)
            removed = len(comp.topo_ops) - len(filtered)
            if removed:
                logger.warning(f"[分量 {comp.id}] 降级移除 {removed}/{len(comp.topo_ops)} 操作")
            comp.topo_ops = filtered

    # ═══════════════════════════════════════════
    # Step 5.5: 归并分量
    # ═══════════════════════════════════════════

    def _merge_commit_components(self, ctx: PipelineContext):
        """归并所有分量结果到全局 context 并执行。"""
        for comp in ctx.components:
            ctx.topo_delta_ops.extend(comp.topo_ops or [])
            ctx.attr_ops.extend(comp.attr_ops or [])
            ctx.recent_info_map.update(comp.recent_info or {})
            ctx.stories.extend(comp.stories or [])

        logger.info(f"[归并] {len(ctx.components)} 个分量合并: "
                     f"{len(ctx.topo_delta_ops)} topo ops, "
                     f"{len(ctx.attr_ops)} attr ops, "
                     f"{len(ctx.recent_info_map)} ri entries")

        self._apply_final_ops(ctx)

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
            from ..config.config_loader import has_recent_info
            import json as _json
            w = 0
            for eid, txt in ctx.recent_info_map.items():
                ent = self.graph_engine.get_entity(eid)
                if ent and has_recent_info(ent.type_id):
                    history = []
                    if ent.recent_info:
                        try:
                            history = _json.loads(ent.recent_info)
                        except (_json.JSONDecodeError, TypeError):
                            history = []
                    if not isinstance(history, list):
                        history = []
                    history.insert(0, {"t": ctx.world_time_str, "text": txt})
                    history = history[:3]
                    ent.recent_info = _json.dumps(history, ensure_ascii=False)
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
