"""
NPCWorldAdapter — NPC/Agent 世界的域适配器实现。

实现 DomainAdapter 新通用接口。所有世界观特定文本从 domain.json 加载。
桥接方法确保 Step 1-2 的旧调用者无感。
"""
from __future__ import annotations
import json
import logging
import os
import re as _re
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

logger = logging.getLogger("NPCWorldAdapter")

_DOMAIN_PATH = os.path.join(os.path.dirname(__file__), "../../config/domain.json")


def _has_role(tid: str, role: str) -> bool:
    from ...config.config_loader import has_role
    return has_role(tid, role)


def _parse_topostruct_ops(raw: str, label_map: dict[str, str]) -> list[GraphOp]:
    """解析 LLM #2 输出，用 label_map 将 {entity_id} 解析回 entity_id。"""
    tag_to_eid = dict(label_map)

    def _resolve(val):
        if not val:
            return val
        stripped = val.strip("{}")
        if stripped in tag_to_eid:
            return tag_to_eid[stripped]
        return val

    raw = raw.strip()
    # 提取第一个 JSON 对象/数组
    m = _re.search(r'[\[{]', raw)
    if not m:
        return []
    raw = raw[m.start():]
    # 找匹配的闭合括号
    depth = 0
    end = 0
    for i, ch in enumerate(raw):
        if ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    json_str = raw[:end] if end else raw
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning(f"[LLM #2] JSON 解析失败，原始输出: {raw[:200]}")
        return []

    ops: list[GraphOp] = []

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


def _get_zone_name(entity, ge) -> str:
    """获取实体所在区域名称。"""
    if ge:
        for conn in entity.connected_entity_ids:
            e = ge.get_entity(conn)
            if e and _has_role(e.type_id, "region"):
                return e.name
    from ...config.config_loader import prefix_to_type_id
    for conn in entity.connected_entity_ids:
        tid = prefix_to_type_id(conn)
        if tid and _has_role(tid, "region"):
            return conn.split("_", 1)[-1]
    return "?"


class NPCWorldAdapter(DomainAdapter):
    """NPC/Agent 世界域适配器"""

    def __init__(self, domain_path: str | None = None):
        path = domain_path or _DOMAIN_PATH
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._adapter_data = self._data.get("adapter", {})
        self._ge = None

    def set_graph_engine(self, ge):
        self._ge = ge

    def get_graph_engine(self):
        return self._ge

    # ═══════════════════════════════════════════════
    # 新通用接口
    # ═══════════════════════════════════════════════

    @property
    def domain_name(self) -> str:
        return "npc_world"

    def classify_node(self, entity_id: str, graph) -> NodeClassification:
        ent = graph.get_entity(entity_id)
        if not ent or not hasattr(ent, 'type_id'):
            return NodeClassification()
        tid = ent.type_id
        return NodeClassification(
            is_actor=_has_role(tid, "actor"),
            is_container=_has_role(tid, "actor") or _has_role(tid, "region"),
            is_consumable=_has_role(tid, "thing"),
            is_location=_has_role(tid, "region"),
        )

    def describe_node(self, entity_id: str, graph) -> NodeDescriptor:
        ent = graph.get_entity(entity_id) if graph else None
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
        if key == "zones":
            return self._data.get("zones", [])
        if key == "recipes":
            return self._data.get("recipes", [])
        if key == "initial_zones":
            return self._data.get("npc_initial_zones", {})
        if key == "entity_names":
            return self._get_all_entity_names()
        return default

    def _get_all_entity_names(self) -> set[str]:
        names = set()
        names.update(self._data.get("npc_initial_zones", {}).keys())
        from ...config.config_loader import get_zones, get_items, get_all_npc_defs
        for z in get_zones():
            n = z.get("name", "")
            if n:
                names.add(n)
        for item in get_items():
            n = item.get("name", "")
            if n:
                names.add(n)
        for npc in get_all_npc_defs():
            n = npc.get("name", "")
            if n:
                names.add(n)
        for r in self._data.get("recipes", []):
            p = r.get("produces", "")
            if p:
                names.add(p)
            for c in r.get("consumes", {}):
                names.add(c)
        return names

    def get_pipeline_stages(self) -> list[PipelineStage]:
        from ...domain.adapter import StageOutputType
        return [
            PipelineStage(key="plan", label="LLM #1 — Plan Generation",
                output_type=StageOutputType.PLANS_MAP,
                prompt_template="llm1_plan",
                parser=lambda raw, g: self.parse_llm_output(1, raw, None, g)),
            PipelineStage(key="topo_structure", label="LLM #2 — Topology Structure",
                output_type=StageOutputType.GRAPH_OPS,
                prompt_template="llm2_structure",
                parser=lambda raw, g: self.parse_llm_output(2, raw, None, g)),
            PipelineStage(key="narrative", label="LLM #3 — Narrative Generation",
                output_type=StageOutputType.NARRATIVES,
                prompt_template="llm3_story",
                parser=lambda raw, g: self.parse_llm_output(3, raw, None, g)),
            PipelineStage(key="topo_delta", label="LLM #4a — Topology Delta",
                output_type=StageOutputType.GRAPH_OPS,
                prompt_template="llm4a_topo",
                parser=lambda raw, g: self.parse_llm_output(4, raw, None, g)),
            PipelineStage(key="content_update", label="LLM #5 — Content Update",
                output_type=StageOutputType.ATTR_UPDATE,
                prompt_template="llm5_content",
                parser=lambda raw, g: self.parse_llm_output(5, raw, None, g)),
        ]

    PROMPT_TEMPLATES: dict[str, list[tuple[str, str]]] = {
        "llm1_plan": [
            ("time_info",            "runtime"),
            ("survival_needs",       "content"),
            ("entity_identity",      "content"),
            ("personality",          "content"),
            ("recent_info",          "content"),
            ("inventory",            "content"),
            ("zone_others",          "content"),
            ("zone_connections",     "content"),
            ("available_recipes",    "content"),
            ("entity_constraints",    "content"),
            ("label_mapping",        "topology"),
            ("topology_constraints_plan", "topology"),
            ("decision_guidance",    "content"),
        ],
        "llm2_structure": [
            ("system_role",          "content"),
            ("core_principle",       "topology"),
            ("global_overview",      "topology"),
            ("combined_header",      "content"),
            ("npc_block",            "content"),
            ("output_instructions",  "content"),
        ],
        "llm3_story": [
            ("system_role",          "content"),
            ("time_info",            "runtime"),
            ("gap_content_header",   "topology"),
            ("entity_blocks",        "content"),
            ("inventory_block",      "content"),
            ("gap_topology_header",  "topology"),
            ("topology_principle",   "topology"),
            ("topology_graph",       "topology"),
            ("label_mapping",        "topology"),
            ("topology_constraints_abstract", "topology"),
            ("gap_event_header",     "topology"),
            ("event_input",          "content"),
            ("gap_output_header",    "topology"),
            ("output_instructions",  "content"),
        ],
        "llm4a_topo": [
            ("feedback",             "runtime"),
            ("system_role",          "content"),
            ("time_info",            "runtime"),
            ("npc_state_section",    "content"),
            ("plans_section",        "content"),
            ("stories_section",      "content"),
            ("topology_section_header",   "topology"),
            ("topology_principle",        "topology"),
            ("topology_graph",            "topology"),
            ("label_mapping_topo",        "topology"),
            ("topology_constraints",      "topology"),
            ("guidance_syntax",           "content"),
            ("output_format",             "content"),
        ],
        "llm5_content": [
            ("feedback",             "runtime"),
            ("system_role",          "content"),
            ("time_info",            "runtime"),
            ("npc_state_section",    "content"),
            ("plans_section",        "content"),
            ("stories_section",      "content"),
            ("label_mapping",        "topology"),
            ("topology_constraints_recent", "topology"),
            ("recent_info_guidance", "content"),
            ("attr_constraints",     "content"),
            ("attr_knowledge",       "content"),
            ("output_format",        "content"),
        ],
        "llm5_projection": [
            ("feedback",             "runtime"),
            ("system_role",          "content"),
            ("time_info",            "runtime"),
            ("topo_diff_section",    "content"),
            ("npc_state_section",    "content"),
            ("plans_section",        "content"),
            ("stories_section",      "content"),
            ("label_mapping",        "topology"),
            ("topology_constraints_recent", "topology"),
            ("recent_info_guidance", "content"),
            ("attr_constraints",     "content"),
            ("attr_knowledge",       "content"),
            ("output_format",        "content"),
        ],
    }

    def get_prompt_template(self, name: str) -> list[SlotDef]:
        raw = self.PROMPT_TEMPLATES.get(name, [])
        return [SlotDef(name=s[0], provider=s[1]) for s in raw]

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
            item = op.get("item", "")
            if item and item not in all_ids:
                logger.warning(f"[entity_existence] 移除: item={item} 不存在")
                continue
            filtered.append(op)
        return filtered

    # ═══════════════════════════════════════════════
    # 旧接口（桥接）
    # ═══════════════════════════════════════════════

    def get_node_descriptor(self, entity_id: str, graph) -> NodeDescriptor:
        return self.describe_node(entity_id, graph)

    def get_zones(self) -> list[dict]:
        return self._data.get("zones", [])

    def get_recipes(self) -> list[dict]:
        return self._data.get("recipes", [])

    def get_npc_initial_zones(self) -> dict[str, str]:
        return self._data.get("npc_initial_zones", {})

    def get_all_entity_names(self) -> set[str]:
        return self._get_all_entity_names()

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
        kw.pop('engine', None)
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
            return self._parse_topo_output(raw_text, graph, label_map=label_map)
        elif stage == 5:
            return self._parse_llm5_output(raw_text, graph)
        return []

    def _parse_llm1_plans(self, raw: str) -> list[GraphOp]:
        return []

    def _parse_llm5_output(self, raw: str, graph) -> list[GraphOp]:
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

    # ═══════════════════════════════════════════════
    # render_slot — 所有 slot 方法的统一入口
    # ═══════════════════════════════════════════════

    def render_slot(self, slot_name: str, engine=None, **kw) -> str:
        if engine and 'engine' not in kw:
            kw = dict(kw, engine=engine)
        method_name = f"slot_{slot_name}"
        handler = getattr(self, method_name, None)
        if handler is not None:
            return handler(**kw)
        local_handler = getattr(self, f'_slot_{slot_name}', None)
        if local_handler:
            return local_handler(**kw)
        return self._adapter_data.get(slot_name, "")

    # ── 类别 B：按调用者区分的文本 ──

    def slot_system_role(self, **kw) -> str:
        caller = kw.get("_caller", "")
        roles = self._adapter_data.get("system_role", {})
        return roles.get(caller, "")

    def slot_output_instructions(self, **kw) -> str:
        caller = kw.get("_caller", "")
        inst = self._adapter_data.get("output_instructions", {})
        return inst.get(caller, "")

    def slot_output_format(self, **kw) -> str:
        caller = kw.get("_caller", "")
        fmt = self._adapter_data.get("output_format", {})
        return fmt.get(caller, "")

    # ── 类别 C：模板文本 + 运行时数据 ──

    def slot_survival_needs(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        template = self._adapter_data.get("survival_needs", "")
        return template.format(v=v, s=s, m=m)

    def slot_entity_identity(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        pg = entity.attributes.get("primary_goal", "暂无")
        zone = _get_zone_name(entity, kw.get("engine"))
        template = self._adapter_data.get("entity_identity", "")
        return template.format(name=entity.name, role=entity.role or "?", zone=zone, v=v, s=s, m=m, primary_goal=pg)

    def slot_personality(self, **kw) -> str:
        tags = kw.get("personality_tags", [])
        if tags:
            template = self._adapter_data.get("personality_format", "")
            return template.format(t="、".join(tags))
        return ""

    def slot_recent_info(self, **kw) -> str:
        entity = kw.get("entity")
        memories = kw.get("memories", [])
        if not entity:
            return ""
        if entity.recent_info:
            import json as _json
            lines = []
            raw = entity.recent_info
            try:
                history = _json.loads(raw)
                if isinstance(history, list):
                    for entry in history:
                        t = entry.get("t", "")
                        txt = entry.get("text", "")
                        if txt:
                            lines.append(f"  [{t}] {txt}" if t else f"  {txt}")
            except (_json.JSONDecodeError, TypeError):
                lines.append(f"  {raw}")
            info = "\n".join(lines)
            template = self._adapter_data.get("recent_info_default", "")
            return template.format(info=info)
        if memories:
            parts = ["### 最近经历"]
            for entry in memories[:5]:
                ts = entry.get("timestamp", "")
                event = entry.get("event", "")
                loc = entry.get("location", "")
                line = f"  {ts} 在{loc} {event}" if loc else f"  {ts} {event}"
                parts.append(line)
            return "\n".join(parts) + "\n\n"
        return ""

    def slot_inventory(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        ge = kw.get("engine")
        if ge:
            inv = ge.get_inventory_view(entity.entity_id)
            if inv:
                items = "、".join(
                    f"{i['item_name']}x{i['quantity']}"
                    for i in inv if i.get("quantity", 0) > 0
                )
                template = self._adapter_data.get("inventory", "")
                return template.format(items=items)
        return self._adapter_data.get("inventory_empty", "")

    def slot_zone_others(self, **kw) -> str:
        zone_npcs = kw.get("zone_npcs")
        if zone_npcs:
            others = "、".join(f"{z['name']}（{z['role']}）" for z in zone_npcs)
            template = self._adapter_data.get("zone_others", "")
            return template.format(others=others)
        return ""

    def slot_zone_connections(self, **kw) -> str:
        """
        渲染当前区域可连接的其他区域列表，供 LLM #1 plan 参考。
        """
        entity = kw.get("entity")
        ge = kw.get("engine")
        if not entity or not ge:
            return ""
        # 找到 NPC 所在的区域
        zone_ent = None
        for conn in entity.connected_entity_ids:
            e = ge.get_entity(conn)
            if e and _has_role(e.type_id, "region"):
                zone_ent = e
                break
        if not zone_ent:
            return ""
        # 收集该区域连接的其他区域
        neighbors = []
        for conn in zone_ent.connected_entity_ids:
            e = ge.get_entity(conn)
            if e and _has_role(e.type_id, "region") and e != zone_ent:
                neighbors.append(e.name)
        if not neighbors:
            return ""
        connections = f"{zone_ent.name} ↔ {'、'.join(sorted(neighbors))}"
        template = self._adapter_data.get("zone_connections", "")
        return template.format(connections=connections)

    def slot_decision_guidance(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        zone = _get_zone_name(entity, kw.get("engine"))
        template = self._adapter_data.get("decision_guidance", "")
        return template.format(name=entity.name, role=entity.role or "?", zone=zone)

    def slot_combined_header(self, **kw) -> str:
        count = kw.get("count", 0)
        template = self._adapter_data.get("combined_header", "")
        return template.format(count=count)

    def slot_npc_state_section(self, **kw) -> str:
        entities = kw.get("entities", [])
        engine = kw.get("engine")
        header = self._adapter_data.get("npc_state_header", "")
        line_template = self._adapter_data.get("npc_state_line", "")
        lines = [header]
        for ent in entities:
            inv = engine.get_inventory_view(ent.entity_id) if engine else []
            inv_str = "、".join(
                f"{i['item_name']}x{i['quantity']}" for i in inv
            ) if inv else "空手"
            zone = _get_zone_name(ent, engine)
            goal = ent.attributes.get("primary_goal", "-")
            lines.append(line_template.format(
                name=ent.name,
                role=ent.role or "?",
                zone=zone,
                v=ent.attributes.get("vitality", 100),
                s=ent.attributes.get("satiety", 50),
                m=ent.attributes.get("mood", 50),
                inv=inv_str,
                goal=goal,
            ))
        return "".join(lines) + "\n\n"

    def slot_plans_section(self, **kw) -> str:
        npc_plans = kw.get("npc_plans", {})
        entities = kw.get("entities", [])
        header = self._adapter_data.get("plans_header", "")
        lines = [header]
        for ent in entities:
            plan = npc_plans.get(ent.entity_id, "")
            if plan:
                lines.append(f"- {ent.name}：{plan[:300]}")
        return "\n".join(lines) + "\n\n"

    def slot_stories_section(self, **kw) -> str:
        stories = kw.get("stories", [])
        header = self._adapter_data.get("stories_header", "")
        sep_template = self._adapter_data.get("story_separator", "")
        lines = [header]
        for i, story in enumerate(stories, 1):
            lines.append(sep_template.format(i=i))
            lines.append(story)
        return "\n".join(lines) + "\n\n"

    def slot_guidance_syntax(self, **kw) -> str:
        return self._adapter_data.get("guidance_syntax", "")

    def slot_topo_diff_section(self, **kw) -> str:
        topo_diff = kw.get("topo_diff", "")
        if not topo_diff:
            return ""
        return "【本 tick 拓扑变化】\n" + topo_diff + "\n\n"

    def slot_recent_info_guidance(self, **kw) -> str:
        return self._adapter_data.get("recent_info_guidance", "")

    def slot_attr_constraints(self, **kw) -> str:
        return self._adapter_data.get("attr_constraints", "")

    def slot_attr_knowledge(self, **kw) -> str:
        return self._adapter_data.get("attr_knowledge", "")

    def slot_event_input(self, **kw) -> str:
        component = kw.get("component")
        if not component or not component.edges:
            return self._adapter_data.get("event_input_empty", "")
        lines_map = self._adapter_data.get("event_input_lines", {})
        lines = []
        for e in component.edges:
            if e.edge_type == "npc_zone" and e.stayed:
                lines.append(lines_map.get("npc_zone_stayed", "").format(source=e.source))
            elif e.edge_type == "npc_zone":
                lines.append(lines_map.get("npc_zone_arrived", "").format(source=e.source))
            elif e.edge_type == "npc_npc" and not e.success:
                lines.append(lines_map.get("npc_npc_failed", "").format(source=e.source, target=e.target))
            elif e.edge_type == "npc_npc":
                lines.append(lines_map.get("npc_npc_interact", "").format(source=e.source, target=e.target))
            elif e.edge_type == "npc_object":
                lines.append(lines_map.get("npc_object_use", "").format(source=e.source, target=e.target))
        return "\n".join(lines) + "\n"

    def slot_entity_blocks(self, **kw) -> str:
        entity_blocks = kw.get("entity_blocks", [])
        header = self._adapter_data.get("entity_blocks_header", "")
        blocks = []
        for ent, er in entity_blocks:
            lines = [f"· {ent.name}"]
            if ent.entity_type:
                lines.append(f"  类型: {ent.entity_type}")
            if ent.desc:
                lines.append(f"  描述: {ent.desc}")
            if ent.role:
                lines.append(f"  身份: {ent.role}")
            if er:
                for key, label in [
                    ("vitality_text", "活力"),
                    ("satiety_text", "饱食"),
                    ("mood_text", "心境"),
                ]:
                    val = er.get(key, "")
                    if val:
                        lines.append(f"  {label}: {val}")
                mems = er.get("memories", "")
                if mems:
                    ml = mems.strip().split("\n")[:3]
                    lines.append(f"  最近经历: {'；'.join(ml)}")
                traits = er.get("traits", [])
                if traits:
                    lines.append(f"  性格: {'、'.join(str(t) for t in traits[:3])}")
                intent = er.get("raw_intent", "")
                if intent:
                    lines.append(f"  想法: {intent}")
            blocks.append("\n".join(lines))
        text = "\n\n".join(blocks) if blocks else "(无节点信息)"
        return header + text + "\n\n"

    def slot_inventory_block(self, **kw) -> str:
        component = kw.get("component")
        exec_results = kw.get("exec_results", [])
        engine = kw.get("engine")
        if not component or not component.npc_names:
            return ""
        lines = []
        for npc_name in sorted(component.npc_names):
            er = next(
                (r for r in exec_results if r.get("npc_name") == npc_name), None
            )
            if er and "npc_eid" in er and engine:
                inv = engine.get_inventory_view(er["npc_eid"])
                if inv:
                    items = [f"{i['item_name']}x{i['quantity']}" for i in inv]
                    lines.append(f"{npc_name}带着: {'、'.join(items)}")
        if not lines:
            return ""
        template = self._adapter_data.get("inventory_block_format", "")
        return template.format(lines="\n".join(f"  · {l}" for l in lines))

    # ── 本地额外 slot handler ──

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
        from ...services.graph_engine import build_label_mapping_text

        ent = engine.get_entity(node_eid)
        if not ent:
            return ""

        topo_eids = {node_eid}
        for conn_eid in ent.connected_entity_ids:
            topo_eids.add(conn_eid)
            ce = engine.get_entity(conn_eid)
            if ce and _has_role(ce.type_id, "region"):
                for other in engine.all_entities():
                    if _has_role(other.type_id, "actor") and other.entity_id != node_eid \
                       and other.is_connected_to(conn_eid):
                        topo_eids.add(other.entity_id)
                        for oconn in other.connected_entity_ids:
                            if oconn not in topo_eids:
                                ce2 = engine.get_entity(oconn)
                                if ce2 and _has_role(ce2.type_id, "thing"):
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
            content_lines.append("格式示例（在最终 JSON 的 operations 对象中）：")
            content_lines.append(f'  {{"op":"connect","src":"{{{node_label}}}","tgt":"{{{conn_eid}}}","qty":-1}}')
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
            "==== [拓扑] ====\n"
            "图中每条边（带 label、qty、conserved/terminal 标签）"
            "完整定义了当前图拓扑结构。语义描述仅作参考，\n"
            "不具备约束力，不得覆盖拓扑信息。\n\n"
            f"{topo_text}\n\n"
            "==== [内容] ====\n" + "\n".join(content_lines)
        )

        return prompt

    # ── LLM #2：全局标签映射 ──

    def build_global_label_map(self, graph) -> dict[str, str]:
        """构建恒等标签映射（entity_id → entity_id），作为单字母标签的替代。"""
        return {e.entity_id: e.entity_id for e in graph.all_entities()}

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
        text = _re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = _re.sub(r'\s*```$', '', text)
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

    # ── 旧解析方法 ──

    def _resolve_label(self, val: str, tag_to_eid: dict[str, str]) -> str:
        """将标签解析回 entity_id。兼容 {entity_id} 和 entity_id 两种格式。"""
        if not val:
            return val
        stripped = val.strip("{}")
        if stripped in tag_to_eid:
            return tag_to_eid[stripped]
        return stripped

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

    @staticmethod
    def _resolve_tag(val: str, label_map: dict[str, str] | None) -> str:
        """将标签 {entity_id} 解析为 entity_id。兼容有/无括号格式。"""
        if not val:
            return val
        stripped = val.strip("{}")
        if label_map and stripped in label_map:
            return label_map[stripped]
        return stripped

    def _parse_topo_output(self, raw: str, graph, label_map: dict[str, str] | None = None) -> list[GraphOp]:
        from ...config.config_loader import get_world_config
        _allow_unreg = get_world_config("allow_unregistered_entity", False)
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

        # 标签 → entity_id 解析（如 {npc_xxx} → npc_xxx）
        _tag = lambda v: self._resolve_tag(v, label_map)

        topo_types = {"delta", "system_delta", "recipe", "set_qty"}
        valid_ops = []
        for op in ops:
            op_type = op.get("op", "")
            if op_type not in topo_types:
                continue
            if op_type == "delta":
                src = _tag(op.get("src", ""))
                tgt = _tag(op.get("tgt", ""))
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
                tgt = _tag(op.get("tgt", ""))
                item = _tag(op.get("item", ""))
                delta = op.get("delta", 0)
                if not tgt or not item or delta == 0:
                    continue
                real_tgt = self.resolve_name(tgt, graph)
                real_item = self.resolve_name(item, graph)
                if not real_item and _allow_unreg:
                    # allow_unregistered_entity 开启时，自动创建不存在的实体
                    from ...entities.base_entity import EntityNode
                    from ...config.config_loader import prefix_to_type_id, get_type_prefix
                    eid = item.replace("{", "").replace("}", "").strip()
                    # 无前缀时，system_delta 的 item 通常是 zone，补 zone_ 前缀
                    prefixes = get_type_prefix()
                    has_prefix = any(eid.startswith(p) for p in prefixes.values())
                    if not has_prefix:
                        eid = f"zone_{eid}"
                    type_id = prefix_to_type_id(eid)
                    # 已有实体可能用不同 ID（如 zone_白果园 ≠ 白果园），再查一次
                    existing = graph.get_entity(eid)
                    if existing:
                        real_item = existing.entity_id
                    else:
                        ent = EntityNode(entity_id=eid, type_id=type_id, name=item)
                        graph.register_entity(ent)
                        real_item = eid
                        logger.info(f"[Adapter] auto-registered entity: {eid} (type_id={type_id})")
                if real_tgt and real_item:
                    valid_ops.append({"op": "system_delta", "tgt": real_tgt, "item": real_item, "delta": delta})
            elif op_type == "recipe":
                src = _tag(op.get("src", ""))
                consumes = {_tag(k): v for k, v in op.get("consumes", {}).items()}
                produces = {_tag(k): v for k, v in op.get("produces", {}).items()}
                if not src or not consumes or not produces:
                    continue
                real_src = self.resolve_name(src, graph)
                if real_src:
                    valid_ops.append({"op": "recipe", "src": real_src, "consumes": consumes, "produces": produces})
            elif op_type == "set_qty":
                src = _tag(op.get("src", ""))
                tgt = _tag(op.get("tgt", ""))
                qty = op.get("qty", 0)
                if not src or not tgt:
                    continue
                real_src = self.resolve_name(src, graph)
                real_tgt = self.resolve_name(tgt, graph)
                if real_src and real_tgt:
                    valid_ops.append({"op": "set_qty", "src": real_src, "tgt": real_tgt, "qty": qty})
        return valid_ops

    # ── 原 NPC 专属属性 ──

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
