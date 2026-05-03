"""
DomainAdapter — 数据驱动的域适配器。

所有世界观特定文字、区域布局、配方定义都从 config/domain.json 读取。
换世界观 = 换 domain.json，不碰 .py 文件。
"""
from __future__ import annotations
import json
import os
from typing import Any

from ..config.config_loader import has_role


_DOMAIN_PATH = os.path.join(os.path.dirname(__file__), "../config/domain.json")


class DomainAdapter:
    """数据驱动的域适配器。

    从 domain.json 加载所有世界观特定内容。
    支持 4 类 slot：
      A — 纯静态文本：直接按 slot_name 返回 JSON 字符串
      B — 按调用者区分的文本：system_role / output_instructions / output_format 按 _caller 返回
      C — 模板文本 + 运行时数据：Python slot 方法用 JSON 模板 + .format() 填充
      D — 结构数据：区域布局、配方（由外部调用者通过 config_loader 访问）
    """

    def __init__(self, domain_path: str | None = None):
        path = domain_path or _DOMAIN_PATH
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._adapter = self._data.get("adapter", {})
        self._ge = None

    def set_graph_engine(self, ge):
        """注入 GraphEngine 引用（部分 slot 需要查询拓扑）"""
        self._ge = ge

    # ─── 配置数据查询 ───

    def get_zones(self) -> list[dict]:
        """返回 domain.json 中的区域定义列表"""
        return self._data.get("zones", [])

    def get_recipes(self) -> list[dict]:
        """返回 domain.json 中的配方定义列表"""
        return self._data.get("recipes", [])

    def get_npc_initial_zones(self) -> dict[str, str]:
        """返回 {npc_name: zone_id} 初始位置映射"""
        return self._data.get("npc_initial_zones", {})

    def get_all_entity_names(self) -> set[str]:
        """返回域中所有合法实体名称（NPC + 区域 + 物品）的并集。

        从 domain.json 的 NPC 配置 + node_config.json 的区域/物品定义收集。
        """
        names = set()

        # NPC 名称
        names.update(self._data.get("npc_initial_zones", {}).keys())

        # 区域 + 物品（从 node_config.json 的 entities 段获取）
        from ..config.config_loader import get_zones, get_items, get_all_npc_defs
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

        # 配方产出/消耗物（兜底）
        for r in self._data.get("recipes", []):
            p = r.get("produces", "")
            if p:
                names.add(p)
            for c in r.get("consumes", {}):
                names.add(c)

        return names

    # ─── render_slot 分发 ───

    def render_slot(self, slot_name: str, **kw) -> str:
        """分发 slot 到具体渲染方法。不支持的 slot → 空字符串。"""
        method_name = f"slot_{slot_name}"
        handler = getattr(self, method_name, None)
        if handler is not None:
            return handler(**kw)
        # Fallback to plain text from JSON
        return self._adapter.get(slot_name, "")

    # ─── 辅助 ───

    def _get_zone_name(self, entity) -> str:
        if self._ge:
            for conn in entity.connected_entity_ids:
                e = self._ge.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    return e.name
        for conn in entity.connected_entity_ids:
            if conn.startswith("zone_"):
                return conn.replace("zone_", "")
        return "?"

    # ════════════════════════════════════════════
    # 类别 B — 按调用者区分的文本
    # ════════════════════════════════════════════

    def slot_system_role(self, **kw) -> str:
        caller = kw.get("_caller", "")
        roles = self._adapter.get("system_role", {})
        return roles.get(caller, "")

    def slot_output_instructions(self, **kw) -> str:
        caller = kw.get("_caller", "")
        inst = self._adapter.get("output_instructions", {})
        return inst.get(caller, "")

    def slot_output_format(self, **kw) -> str:
        caller = kw.get("_caller", "")
        fmt = self._adapter.get("output_format", {})
        return fmt.get(caller, "")

    # ════════════════════════════════════════════
    # 类别 C — 模板文本 + 运行时数据
    # ════════════════════════════════════════════

    def slot_survival_needs(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        template = self._adapter.get("survival_needs", "")
        return template.format(v=v, s=s, m=m)

    def slot_entity_identity(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        zone = self._get_zone_name(entity)
        template = self._adapter.get("entity_identity", "")
        return template.format(name=entity.name, role=entity.role or "?", zone=zone, v=v, s=s, m=m)

    def slot_personality(self, **kw) -> str:
        tags = kw.get("personality_tags", [])
        if tags:
            template = self._adapter.get("personality_format", "")
            return template.format(t="、".join(tags))
        return ""

    def slot_recent_info(self, **kw) -> str:
        entity = kw.get("entity")
        memories = kw.get("memories", [])
        if not entity:
            return ""
        if entity.recent_info:
            template = self._adapter.get("recent_info_default", "")
            return template.format(info=entity.recent_info)
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
                template = self._adapter.get("inventory", "")
                return template.format(items=items)
        return self._adapter.get("inventory_empty", "")

    def slot_zone_others(self, **kw) -> str:
        zone_npcs = kw.get("zone_npcs")
        if zone_npcs:
            others = "、".join(f"{z['name']}（{z['role']}）" for z in zone_npcs)
            template = self._adapter.get("zone_others", "")
            return template.format(others=others)
        return ""

    def slot_decision_guidance(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        zone = self._get_zone_name(entity)
        template = self._adapter.get("decision_guidance", "")
        return template.format(name=entity.name, role=entity.role or "?", zone=zone)

    def slot_combined_header(self, **kw) -> str:
        count = kw.get("count", 0)
        template = self._adapter.get("combined_header", "")
        return template.format(count=count)

    def slot_npc_state_section(self, **kw) -> str:
        entities = kw.get("entities", [])
        engine = kw.get("engine")
        header = self._adapter.get("npc_state_header", "")
        line_template = self._adapter.get("npc_state_line", "")
        lines = [header]
        for ent in entities:
            inv = engine.get_inventory_view(ent.entity_id) if engine else []
            inv_str = "、".join(
                f"{i['item_name']}x{i['quantity']}" for i in inv
            ) if inv else "空手"
            zone = self._get_zone_name(ent)
            lines.append(line_template.format(
                name=ent.name,
                role=ent.role or "?",
                zone=zone,
                v=ent.attributes.get("vitality", 100),
                s=ent.attributes.get("satiety", 50),
                m=ent.attributes.get("mood", 50),
                inv=inv_str,
            ))
        return "".join(lines) + "\n\n"

    def slot_plans_section(self, **kw) -> str:
        npc_plans = kw.get("npc_plans", {})
        entities = kw.get("entities", [])
        header = self._adapter.get("plans_header", "")
        lines = [header]
        for ent in entities:
            plan = npc_plans.get(ent.entity_id, "")
            if plan:
                lines.append(f"- {ent.name}：{plan[:300]}")
        return "\n".join(lines) + "\n\n"

    def slot_stories_section(self, **kw) -> str:
        stories = kw.get("stories", [])
        header = self._adapter.get("stories_header", "")
        sep_template = self._adapter.get("story_separator", "")
        lines = [header]
        for i, story in enumerate(stories, 1):
            lines.append(sep_template.format(i=i))
            lines.append(story)
        return "\n".join(lines) + "\n\n"

    def slot_guidance_syntax(self, **kw) -> str:
        return self._adapter.get("guidance_syntax", "")

    def slot_recent_info_guidance(self, **kw) -> str:
        return self._adapter.get("recent_info_guidance", "")

    def slot_attr_constraints(self, **kw) -> str:
        return self._adapter.get("attr_constraints", "")

    def slot_attr_knowledge(self, **kw) -> str:
        return self._adapter.get("attr_knowledge", "")

    def slot_event_input(self, **kw) -> str:
        component = kw.get("component")
        if not component or not component.edges:
            return self._adapter.get("event_input_empty", "")
        lines_map = self._adapter.get("event_input_lines", {})
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
        header = self._adapter.get("entity_blocks_header", "")
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
        template = self._adapter.get("inventory_block_format", "")
        return template.format(lines="\n".join(f"  · {l}" for l in lines))
