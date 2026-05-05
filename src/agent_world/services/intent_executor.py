"""
Intent Resolver + Executor（LLM #2）

拓扑结构变更层：将 LLM #1（NPC 计划）→ 拓扑结构变更。

输入：每个目标节点的计划 + 当前拓扑子图（已翻译）
输出：[{op, src, tgt, qty}] — 仅限结构变更（connect/disconnect）

设计原则：
  - 纯编排层：prompt 构建 → 域适配器，操作解析 → 域适配器
  - 不包含任何 NPC/Zone/Item 特定逻辑
  - 换域只需换 adapter
"""

from __future__ import annotations
import logging
from typing import Any

from .graph_engine import GraphEngine
from ..domain.npc_world.adapter import NPCWorldAdapter

logger = logging.getLogger("intent_executor")


EdgeOperation = dict[str, Any]


class IntentResolver:
    """
    LLM #2：将自然语言计划 → 图拓扑结构变更。
    """

    def __init__(self, graph_engine: GraphEngine, resolver=None, adapter=None):
        self._graph = graph_engine
        self._resolver = resolver  # InteractionResolver（用于调用 LLM）
        self._adapter: NPCWorldAdapter | None = adapter

    # ─── 主入口 ───

    def resolve_all_intents(
        self, npc_plans: dict[str, str]
    ) -> list[EdgeOperation]:
        """
        为所有有计划的节点解析拓扑结构变更。
        所有节点共享同一套全局标签映射。

        Args:
            npc_plans: {entity_id: 自然语言计划}

        Returns:
            list[EdgeOperation]: [{op, src, tgt, qty}, ...]
        """
        if not npc_plans:
            return []

        if self._adapter is None:
            return self._fallback_old(npc_plans)

        # 1. 构建 entity_id 恒等标签映射
        id_map = {e.entity_id: e.entity_id for e in self._graph.all_entities()}

        # 2. 构建 prompt → 域适配器（通过 prompt_assembler slot 系统）
        from .prompt_assembler import assemble

        prompt_text = assemble(
            "llm2_structure", self._adapter, engine=self._graph,
            npc_plans=npc_plans, global_label_map=id_map,
            count=len(npc_plans), _caller="llm2",
        )

        # 3. 调用 LLM
        raw = self._call_llm(prompt_text) if self._resolver else ""
        if not raw:
            return self._fallback_items(npc_plans, id_map)

        # 4. 解析 → 域适配器
        return self._adapter.parse_llm_output(
            stage=2, raw_text=raw, label_map=id_map, graph=self._graph,
        )

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        if not self._resolver:
            return ""
        return self._resolver._call_llm(prompt)

    # ─── 降级（无 adapter / LLM 不可用）───

    def _fallback_old(self, npc_plans: dict[str, str]) -> list[EdgeOperation]:
        """无 adapter 时的回退：使用 entity_id 恒等映射"""
        id_map = {e.entity_id: e.entity_id for e in self._graph.all_entities()}
        return self._fallback_items(npc_plans, id_map)

    def _fallback_items(
        self, npc_plans: dict[str, str], global_label_map: dict[str, str]
    ) -> list[EdgeOperation]:
        """LLM 不可用时，根据计划关键词做简单推理"""
        from ...config.config_loader import has_role

        ops = []
        for eid, plan in npc_plans.items():
            ent = self._graph.get_entity(eid)
            if not ent:
                continue
            for conn in ent.connected_entity_ids:
                e = self._graph.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    current_zone = e.name
                    for target_zone in ["market", "tavern", "trade"]:
                        if target_zone in plan.lower():
                            target_eid = None
                            for e2 in self._graph.all_entities():
                                if has_role(e2.type_id, "region") and target_zone in e2.name.lower():
                                    target_eid = e2.entity_id
                                    break
                            if target_eid and target_eid != conn:
                                ops.append({
                                    "op": "disconnect",
                                    "src": eid,
                                    "tgt": conn,
                                })
                                ops.append({
                                    "op": "connect",
                                    "src": eid,
                                    "tgt": target_eid,
                                    "qty": -1,
                                })
                            break
                    break
        return ops
