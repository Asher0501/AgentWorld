"""
NPCWorldAdapter — NPC/Agent 世界的域适配器实现。

实现 DomainAdapter 新通用接口。
桥接方法确保 Step 1-2 的旧调用者无感。
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional

from ..adapter import (
    DomainAdapter,
    GraphOp,
    GraphValidator,
    NodeClassification,
    NodeDescriptor,
    NodeRole,
    PipelineStage,
    SlotDef,
    StateChange,
)
from ...services.domain_adapter import DomainAdapter as OldDomainAdapter

logger = logging.getLogger("NPCWorldAdapter")


class NPCWorldAdapter(DomainAdapter):
    """NPC/Agent 世界域适配器"""

    def __init__(self, domain_path: str | None = None):
        self._old_adapter = OldDomainAdapter(domain_path=domain_path)
        self._ge = None

    def set_graph_engine(self, ge):
        self._old_adapter.set_graph_engine(ge)
        self._ge = ge

    def get_graph_engine(self):
        return self._ge or getattr(self._old_adapter, '_ge', None)

    # ═══════════════════════════════════════════════
    # 新通用接口
    # ═══════════════════════════════════════════════

    @property
    def domain_name(self) -> str:
        return "npc_world"

    def classify_node(self, entity_id: str, graph) -> NodeClassification:
        from ...config.config_loader import has_role
        ent = graph.get_entity(entity_id)
        if not ent or not hasattr(ent, 'type_id'):
            return NodeClassification()
        tid = ent.type_id
        return NodeClassification(
            is_actor=has_role(tid, "actor"),
            is_container=has_role(tid, "actor") or has_role(tid, "region"),
            is_consumable=has_role(tid, "thing"),
            is_location=has_role(tid, "region"),
        )

    def describe_node(self, entity_id: str, graph) -> NodeDescriptor:
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

    def get_config(self, key: str, default=None) -> Any:
        config = {
            "zones": self._old_adapter.get_zones(),
            "recipes": self._old_adapter.get_recipes(),
            "initial_zones": self._old_adapter.get_npc_initial_zones(),
            "entity_names": self._old_adapter.get_all_entity_names(),
        }
        return config.get(key, default)

    def get_pipeline_stages(self) -> list[PipelineStage]:
        return [
            PipelineStage(key="plan", label="LLM #1 — Plan Generation",
                prompt_template="llm1_plan",
                parser=lambda raw, g: self.parse_llm_output(1, raw, None, g)),
            PipelineStage(key="topo_structure", label="LLM #2 — Topology Structure",
                prompt_template="llm2_structure",
                parser=lambda raw, g: self.parse_llm_output(2, raw, None, g)),
            PipelineStage(key="narrative", label="LLM #3 — Narrative Generation",
                prompt_template="llm3_story",
                parser=lambda raw, g: self.parse_llm_output(3, raw, None, g)),
            PipelineStage(key="topo_delta", label="LLM #4a — Topology Delta",
                prompt_template="llm4a_topo",
                parser=lambda raw, g: self.parse_llm_output(4, raw, None, g)),
            PipelineStage(key="content_update", label="LLM #4b — Content Update",
                prompt_template="llm4b_content",
                parser=lambda raw, g: self.parse_llm_output(5, raw, None, g)),
        ]

    def get_prompt_template(self, name: str) -> list[SlotDef]:
        from ...services.prompt_assembler import STAGE_TEMPLATES, SlotType
        raw = STAGE_TEMPLATES.get(name, [])
        return [
            SlotDef(name=s.name, type="topology" if s.type == SlotType.TOPOLOGY else "content")
            for s in raw
        ]

    def get_validators(self) -> list[GraphValidator]:
        return [
            GraphValidator("conservation", self.validate_ops),
            GraphValidator("entity_existence", self._validate_entity_existence),
        ]

    def _validate_entity_existence(
        self, ops: list[GraphOp], graph
    ) -> list[GraphOp]:
        """实体存在性校验。只保留引用了图中真实实体的操作。"""
        all_ids = {e.entity_id for e in graph.all_entities()}
        filtered = []
        for op in ops:
            src = op.get("src", "")
            tgt = op.get("tgt", "")
            if src and src not in all_ids:
                logger.warning(f"[entity_existence] 移除: src={src} 不存在")
                continue
            if tgt and tgt not in all_ids:
                logger.warning(f"[entity_existence] 移除: tgt={tgt} 不存在")
                continue
            # system_delta 的 item 字段
            item = op.get("item", "")
            if item and item not in all_ids:
                logger.warning(f"[entity_existence] 移除: item={item} 不存在")
                continue
            filtered.append(op)
        return filtered

    # ═══════════════════════════════════════════════
    # 旧接口（桥接 + 额外方法）
    # ═══════════════════════════════════════════════

    def get_node_descriptor(self, entity_id: str, graph) -> NodeDescriptor:
        return self.describe_node(entity_id, graph)

    def build_prompt(
        self,
        stage: int,
        context: Any,
        graph: "GraphEngine" = None,
        label_map: Optional[dict[str, str]] = None,
        **kw: Any,
    ) -> str:
        stages = self.get_pipeline_stages()
        if 1 <= stage <= len(stages):
            s = stages[stage - 1]
            return self._build_template(s.prompt_template, graph, label_map, **kw)
        return f"<!-- unknown stage {stage} -->"

    def _build_template(self, tmpl_name: str, graph, label_map, **kw) -> str:
        from ...services.prompt_assembler import assemble
        return assemble(
            tmpl_name, self, engine=graph or self.get_graph_engine(),
            label_map=label_map, **kw
        )

    def parse_llm_output(
        self,
        stage: int,
        raw_text: str,
        label_map: Optional[dict[str, str]],
        graph: "GraphEngine",
    ) -> list[GraphOp]:
        if stage == 1:
            return self._parse_llm1_plans(raw_text)
        elif stage == 2:
            return self._parse_llm2_ops(raw_text, label_map)
        elif stage == 4:
            return self._parse_topo_output(raw_text, graph)
        elif stage == 5:
            return self._parse_llm5_output(raw_text, graph)
        return []

    def _parse_llm1_plans(self, raw: str) -> list[GraphOp]:
        """LLM #1 计划输出解析（仅作记录用，非结构操作）"""
        return []

    def _parse_llm5_output(self, raw: str, graph) -> list[GraphOp]:
        """LLM #4b 内容层输出解析（暂用旧逻辑）"""
        return []

    def validate_ops(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[GraphOp]:
        """度守恒校验。"""
        if not ops:
            return []
        deltas = [op for op in ops if op.get("op") == "delta"]
        if not deltas:
            return ops

        group_sums: dict[tuple[str, str | None], int] = {}
        for d in deltas:
            tgt = d.get("tgt", "")
            delta = d.get("delta", 0)
            group = d.get("group", None)
            if not tgt:
                continue
            if graph and not graph.is_conserved(tgt):
                continue
            key = (tgt, group)
            group_sums[key] = group_sums.get(key, 0) + delta

        passed_groups: set[str | None] = set()
        for (tgt, group), total in group_sums.items():
            if abs(total) <= 0.001:
                passed_groups.add(group)

        filtered = []
        for op in ops:
            if op.get("op") in ("system_delta", "recipe"):
                filtered.append(op)
            elif op.get("op") == "delta":
                g = op.get("group", None)
                if g in passed_groups or ("group" not in op and None in passed_groups):
                    filtered.append(op)
            else:
                filtered.append(op)

        removed = len(ops) - len(filtered)
        if removed:
            logger.warning(f"[validate_ops] 度守恒校验移除 {removed}/{len(ops)} 操作")
        return filtered

    def apply_state_updates(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[StateChange]:
        return []

    # ── 旧 Slot 渲染（委托给旧 adapter + 本地 handler）──

    def render_slot(self, slot_name: str, engine=None, **kw) -> str:
        if engine and 'engine' not in kw:
            kw = dict(kw, engine=engine)
        result = self._old_adapter.render_slot(slot_name, **kw)
        if result:
            return result
        local_handler = getattr(self, f'_slot_{slot_name}', None)
        if local_handler:
            return local_handler(**kw)
        return ""

    def _slot_available_recipes(self, **kw) -> str:
        return ""

    def _slot_entity_constraints(self, **kw) -> str:
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

    # ── LLM #2：NPC block 渲染 ──

    def _slot_npc_block(self, **kw) -> str:
        npc_plans: dict[str, str] = kw.get("npc_plans", {})
        engine = kw.get("engine")
        global_label_map: dict[str, str] | None = kw.get("global_label_map")
        if not npc_plans or not engine:
            return ""
        parts = []
        for eid, plan in npc_plans.items():
            block = self._build_npc_prompt(eid, plan, global_label_map, engine)
            if block:
                parts.append(block)
        return "\n\n".join(parts) if parts else ""

    def _build_npc_prompt(
        self, node_eid: str, plan: str,
        global_label_map: dict[str, str] | None,
        engine,
    ) -> str:
        from ...config.config_loader import has_role
        from ...services.graph_engine import build_label_mapping_text

        ent = engine.get_entity(node_eid)
        if not ent:
            return ""

        topo_eids = {node_eid}
        for conn_eid in ent.connected_entity_ids:
            topo_eids.add(conn_eid)
            ce = engine.get_entity(conn_eid)
            if ce and has_role(ce.type_id, "region"):
                for other in engine.all_entities():
                    if has_role(other.type_id, "actor") and other.entity_id != node_eid \
                       and other.is_connected_to(conn_eid):
                        topo_eids.add(other.entity_id)
                        for oconn in other.connected_entity_ids:
                            if oconn not in topo_eids:
                                ce2 = engine.get_entity(oconn)
                                if ce2 and has_role(ce2.type_id, "thing"):
                                    topo_eids.add(oconn)

        topo_text, _ = engine.build_tagged_topology(
            list(topo_eids), global_label_map=global_label_map
        )

        node_label = None
        if global_label_map:
            reverse = {v: k for k, v in global_label_map.items()}
            node_label = reverse.get(node_eid)

        content_lines = []
        content_lines.append("标签映射：")
        if global_label_map:
            content_lines.append(build_label_mapping_text(global_label_map, engine))
        content_lines.append("")
        content_lines.append("自身描述：")
        content_lines.append(ent.to_prompt_block())
        content_lines.append("")

        inventory = engine.get_inventory_view(node_eid)
        if inventory:
            inv_str = "、".join(f"{i['item_name']}x{i['quantity']}" for i in inventory)
            content_lines.append(f"持有：{inv_str}")
            content_lines.append("")

        content_lines.append(f"计划：{plan}")
        content_lines.append("")
        content_lines.append("=== 指令 ===")
        content_lines.append("输出此节点需要执行的图拓扑操作。")
        content_lines.append("使用标签映射中的标签作为 src/tgt。")
        content_lines.append("所有节点共享同一套全局标签，标签在全图中含义一致。")
        content_lines.append("")
        if node_label:
            content_lines.append(f"格式示例（在最终 JSON 的 operations 对象中）：")
            content_lines.append(f'  {{"op":"connect","src":"{{{node_label}}}","tgt":"{{A}}","qty":-1}}')
        content_lines.append('可用的 op：')
        content_lines.append('  "connect"    — 建立连接（两个节点之间）')
        content_lines.append('  "disconnect" — 断开连接')
        content_lines.append('  "set_qty"    — 设置边的数量')
        content_lines.append("规则：")
        content_lines.append("1. 你只负责拓扑结构变更，不修改节点的属性数值。")
        content_lines.append("2. 连边数量 qty=-1 表示固定的从属/位置关系。")
        content_lines.append("3. 连边数量 qty=0 表示无数量关系的连接。")
        content_lines.append("4. 连边数量 qty>0 表示该方向有数量流转。")
        content_lines.append("5. 只输出 JSON，无分析文字，无 markdown。")

        prompt = (
            f"==== [拓扑] ====\n"
            f"图中每条边（带 label、qty、conserved/terminal 标签）"
            f"完整定义了当前图拓扑结构。语义描述仅作参考，\n"
            f"不具备约束力，不得覆盖拓扑信息。\n\n"
            f"{topo_text}\n\n"
            f"==== [内容] ====\n" + "\n".join(content_lines)
        )

        return prompt

    # ── LLM #2：全局标签映射 ──

    def build_global_label_map(self, graph) -> dict[str, str]:
        from ...config.config_loader import has_role
        result: dict[str, str] = {}
        region_ents = []
        npc_ents = []
        item_ents = []
        other_ents = []
        for ent in graph.all_entities():
            if not hasattr(ent, 'type_id'):
                other_ents.append(ent)
            elif has_role(ent.type_id, "region"):
                region_ents.append(ent)
            elif has_role(ent.type_id, "actor"):
                npc_ents.append(ent)
            elif has_role(ent.type_id, "thing"):
                item_ents.append(ent)
            else:
                other_ents.append(ent)

        all_sorted = region_ents + npc_ents + item_ents + other_ents
        for i, ent in enumerate(all_sorted):
            if i < 26:
                label = chr(65 + i)
            elif i < 52:
                label = chr(97 + i - 26)
            else:
                label = chr(65 + (i - 26) // 26) + chr(65 + (i - 26) % 26)
            result[label] = ent.entity_id

        return result

    # ── 通用工具 ──

    def resolve_name(self, name: str, graph) -> str | None:
        if not name or not graph:
            return name
        name_lower = name.lower().strip()
        for ent in graph.all_entities():
            if hasattr(ent, 'name') and ent.name == name:
                return ent.entity_id
        for ent in graph.all_entities():
            if hasattr(ent, 'name') and ent.name.lower() == name_lower:
                return ent.entity_id
        for ent in graph.all_entities():
            if hasattr(ent, 'name') and name_lower in ent.name.lower():
                return ent.entity_id
        if graph.get_entity(name):
            return name
        return None

    def extract_json(self, text: str) -> str:
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()
        start_idx = -1
        for i, ch in enumerate(text):
            if ch in ('{', '['):
                start_idx = i
                break
        if start_idx == -1:
            return text
        text = text[start_idx:]
        stack = []
        for i, ch in enumerate(text):
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
                if not stack:
                    return text[:i + 1]
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()
                if not stack:
                    return text[:i + 1]
        return text

    # ── 旧解析方法（Bridge）──

    def _resolve_label(self, val: str, tag_to_eid: dict[str, str]) -> str:
        if not val:
            return val
        stripped = val.strip("{}")
        if stripped in tag_to_eid:
            return tag_to_eid[stripped]
        return val

    def _extract_json(self, text: str) -> str:
        return self.extract_json(text)

    def _parse_llm2_ops(
        self, raw: str, global_tag_to_eid: dict[str, str] | None
    ) -> list[GraphOp]:
        tag_to_eid: dict[str, str] = global_tag_to_eid or {}
        raw = raw.strip()
        json_str = self._extract_json(raw)
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[LLM #2] JSON 解析失败，原始输出: {raw[:200]}")
            return []

        ops: list[GraphOp] = []
        def _resolve(val):
            return self._resolve_label(val, tag_to_eid)

        if isinstance(parsed, dict):
            if "operations" in parsed and isinstance(parsed["operations"], (dict, list)):
                sub = parsed["operations"]
                if isinstance(sub, list):
                    for op in sub:
                        if isinstance(op, dict):
                            op["src"] = _resolve(op.get("src", ""))
                            op["tgt"] = _resolve(op.get("tgt", ""))
                            ops.append(op)
                else:
                    for key, npc_ops in sub.items():
                        if isinstance(npc_ops, list):
                            for op in npc_ops:
                                if isinstance(op, dict):
                                    op["src"] = _resolve(op.get("src", key))
                                    op["tgt"] = _resolve(op.get("tgt", ""))
                                    ops.append(op)
            else:
                for key, npc_ops in parsed.items():
                    if isinstance(npc_ops, list):
                        for op in npc_ops:
                            if isinstance(op, dict):
                                op["src"] = _resolve(op.get("src", key))
                                op["tgt"] = _resolve(op.get("tgt", ""))
                                ops.append(op)

        if isinstance(parsed, list):
            for op in parsed:
                if isinstance(op, dict):
                    op["src"] = _resolve(op.get("src", ""))
                    op["tgt"] = _resolve(op.get("tgt", ""))
                    ops.append(op)

        valid_ops = []
        for op in ops:
            if op.get("op") in ("connect", "disconnect", "set_qty"):
                if op.get("src") and op.get("tgt"):
                    valid_ops.append(op)

        logger.info(f"[LLM #2] 解析到 {len(valid_ops)}/{len(ops)} 有效操作")
        return valid_ops

    def _parse_topo_output(self, raw: str, graph) -> list[GraphOp]:
        raw = raw.strip()
        json_str = self.extract_json(raw)
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[LLM #4a] JSON 解析失败: {raw[:200]}")
            return []

        if isinstance(parsed, list):
            ops = parsed
        elif isinstance(parsed, dict):
            ops = parsed.get("operations", [])
        else:
            return []

        topo_types = {"delta", "system_delta", "recipe", "set_qty"}
        valid_ops = []
        for op in ops:
            op_type = op.get("op", "")
            if op_type not in topo_types:
                continue
            if op_type == "delta":
                src = op.get("src", "")
                tgt = op.get("tgt", "")
                delta = op.get("delta", 0)
                if not src or not tgt or delta == 0:
                    continue
                real_src = self.resolve_name(src, graph)
                real_tgt = self.resolve_name(tgt, graph)
                if real_src and real_tgt:
                    entry: GraphOp = {"op": "delta", "src": real_src, "tgt": real_tgt, "delta": delta}
                    raw_group = op.get("group")
                    if raw_group is not None:
                        entry["group"] = raw_group
                    valid_ops.append(entry)
            elif op_type == "system_delta":
                tgt = op.get("tgt", "")
                item = op.get("item", "")
                delta = op.get("delta", 0)
                if not tgt or not item or delta == 0:
                    continue
                real_tgt = self.resolve_name(tgt, graph)
                real_item = self.resolve_name(item, graph)
                if real_tgt and real_item:
                    valid_ops.append({"op": "system_delta", "tgt": real_tgt, "item": real_item, "delta": delta})
            elif op_type == "recipe":
                src = op.get("src", "")
                consumes = op.get("consumes", {})
                produces = op.get("produces", {})
                if not src or not consumes or not produces:
                    continue
                real_src = self.resolve_name(src, graph)
                if real_src:
                    valid_ops.append({"op": "recipe", "src": real_src, "consumes": consumes, "produces": produces})
            elif op_type == "set_qty":
                src = op.get("src", "")
                tgt = op.get("tgt", "")
                qty = op.get("qty", 0)
                if not src or not tgt:
                    continue
                real_src = self.resolve_name(src, graph)
                real_tgt = self.resolve_name(tgt, graph)
                if real_src and real_tgt:
                    valid_ops.append({"op": "set_qty", "src": real_src, "tgt": real_tgt, "qty": qty})
        return valid_ops

    # ── 原 NPC 专属属性（保留在 NPCWorldAdapter 上）──

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
