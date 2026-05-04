"""
NPCWorldAdapter — NPC/Agent 世界的域适配器实现。

当前策略：薄壳模式。
- 继承 domain/adapter.py 的 DomainAdapter 抽象接口
- 内容层委托给 services/domain_adapter.DomainAdapter（数据驱动，读 domain.json）
- build_prompt() 委托给 prompt_assembler.assemble()

逐步迁移：每 Step 将一段域逻辑从旧代码搬进此类，最终摆脱对 services/ 的依赖。
"""
from __future__ import annotations
from typing import Any, Optional

from ..adapter import (
    DomainAdapter as AbstractDomainAdapter,
    GraphOp,
    NodeDescriptor,
    NodeRole,
    StateChange,
)
from ...services.domain_adapter import DomainAdapter as OldDomainAdapter


class NPCWorldAdapter(AbstractDomainAdapter):
    """NPC/Agent 世界域适配器"""

    def __init__(self, domain_path: str | None = None):
        self._old_adapter = OldDomainAdapter(domain_path=domain_path)

    def set_graph_engine(self, ge):
        self._old_adapter.set_graph_engine(ge)

    def get_graph_engine(self):
        return getattr(self._old_adapter, '_ge', None)

    # ── 元数据 ──

    @property
    def domain_name(self) -> str:
        return "NPC世界"

    # ── 实体系统 ──

    def get_node_role(self, entity_id: str, graph) -> NodeRole:
        from ...config.config_loader import has_role
        ent = graph.get_entity(entity_id)
        if not ent or not hasattr(ent, 'type_id'):
            return NodeRole.ACTOR
        tid = ent.type_id
        if has_role(tid, "region"):
            return NodeRole.LOCATION
        if has_role(tid, "thing"):
            return NodeRole.RESOURCE
        return NodeRole.ACTOR

    def get_node_descriptor(self, entity_id: str, graph) -> NodeDescriptor:
        ent = graph.get_entity(entity_id)
        if not ent:
            return NodeDescriptor(display_name=entity_id, type_label="?")
        return NodeDescriptor(
            display_name=getattr(ent, 'name', entity_id),
            type_label=getattr(ent, 'role', '?') or '?',
            attrs=dict(getattr(ent, 'attributes', {})),
            role=self.get_node_role(entity_id, graph),
            entity_id=entity_id,
        )

    # ── Slot 渲染（委托给旧 adapter）──

    def render_slot(self, slot_name: str, engine=None, **kw) -> str:
        """渲染内容层槽位。

        优先委托旧数据驱动 adapter；旧 adapter 未处理时回退到本地方法。
        """
        if engine and 'engine' not in kw:
            kw = dict(kw, engine=engine)
        result = self._old_adapter.render_slot(slot_name, **kw)
        if result:
            return result
        # 回退：旧 adapter 未实现 → 本地 handler
        local_handler = getattr(self, f'_slot_{slot_name}', None)
        if local_handler:
            return local_handler(**kw)
        return ""

    def _slot_available_recipes(self, **kw) -> str:
        """可用配方（旧 build_one_npc_prompt 未传 recipes，保留空占位）"""
        return ""

    def _slot_entity_constraints(self, **kw) -> str:
        """实体约束 — 旧 build_one_npc_prompt 中独立的约束段"""
        from ...config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        if not allow_unreg:
            return (
                '【约束】计划中只能引用当前持有清单或当前区域其他角色持有的物品名称。\n'
                '  不得创造图中不存在的实体（包括虚构的角色、不存在的物品名称）。\n'
                '  想吃东西只能写现有食物（面包、小麦等），不能说"买肉包子""馒头"等不存在的食物。\n'
                '  没有例外。\n\n'
            )
        return ""

    def get_zones(self) -> list[dict]:
        return self._old_adapter.get_zones()

    def get_recipes(self) -> list[dict]:
        return self._old_adapter.get_recipes()

    def get_npc_initial_zones(self) -> dict[str, str]:
        return self._old_adapter.get_npc_initial_zones()

    def get_all_entity_names(self) -> set[str]:
        return self._old_adapter.get_all_entity_names()

    # ── DomainAdapter 接口实现 ──

    def build_prompt(
        self,
        stage: int,
        context: Any,
        graph: "GraphEngine" = None,
        label_map: Optional[dict[str, str]] = None,
        **kw: Any,
    ) -> str:
        """
        构建指定 LLM 阶段的 prompt。委托给 prompt_assembler.assemble()。
        """
        from ...services.prompt_assembler import assemble

        stage_map = {
            1: "llm1_plan", 2: "llm2_structure",
            3: "llm3_story", 4: "llm4a_topo", 5: "llm4b_content",
        }
        tmpl = stage_map.get(stage)
        if not tmpl:
            return f"<!-- unknown stage {stage} -->"

        return assemble(
            tmpl, self, engine=graph or self.get_graph_engine(),
            label_map=label_map, **kw
        )

    def parse_llm_output(
        self,
        stage: int,
        raw_text: str,
        label_map: Optional[dict[str, str]],
        graph: "GraphEngine",
    ) -> list[GraphOp]:
        # 暂不实现（Step 2 处理）
        return []

    def validate_ops(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[GraphOp]:
        return ops

    def apply_state_updates(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[StateChange]:
        return []

    @property
    def interaction_rules(self) -> dict[str, Any]:
        return {
            "allowed_actor_actor": True,
            "allowed_actor_resource": True,
            "allowed_actor_location": True,
            "chase_distance": 1,
        }

    @property
    def conservation_rules(self) -> dict[str, Any]:
        return {
            "enforce_delta_conservation": True,
            "system_delta_allowed": True,
            "check_resource_sufficiency": True,
        }
