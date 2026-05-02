"""
VillageDomainAdapter — 村庄经济世界的域适配器。

每个 slot_* 方法对应一个 content 槽位的渲染。
不实现的 slot 自动返回空字符串（DomainAdapter 兜底）。
"""
from __future__ import annotations
from typing import Any

from ..config.config_loader import has_role
from .domain_adapter import DomainAdapter


class VillageDomainAdapter(DomainAdapter):

    def set_graph_engine(self, ge):
        self._ge = ge

    # ─── 辅助 ───

    def _get_zone_name(self, entity) -> str:
        if hasattr(self, '_ge') and self._ge:
            for conn in entity.connected_entity_ids:
                e = self._ge.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    return e.name
        for conn in entity.connected_entity_ids:
            if conn.startswith("zone_"):
                return conn.replace("zone_", "")
        return "?"

    # ════════════════════════════════════════════
    # LLM #1: 计划生成
    # ════════════════════════════════════════════

    def slot_survival_needs(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        return (
            "━━━ 基本生存需求 ━━━\n"
            "你有3项基本生存属性。任何一项降到0，你都会出局（死亡/崩溃/消失）。\n"
            "你的性格决定了你的风险承受能力——\n"
            "谨慎的角色会提前行动，粗心的角色可能拖到最后一刻。\n"
            "但无论什么性格，掉到0就完了。\n"
            f"\n当前: 体力 {v:.0f}/100 | 饱腹 {s:.0f}/100 | 心情 {m:.0f}/100\n\n"
            "根据你的角色和性格，自己判断：\n"
            "  - 你的体力还能撑多久？\n"
            "  - 你的饱腹需要什么时候补充？\n"
            "  - 你的心情需要什么来调节？\n"
            "\n工作、交易、社交——所有目标都在活下去的前提下进行。\n"
        )

    def slot_entity_identity(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        v = entity.attributes.get("vitality", 100)
        s = entity.attributes.get("satiety", 50)
        m = entity.attributes.get("mood", 50)
        zone = self._get_zone_name(entity)
        return (
            f"## NPC: {entity.name}\n"
            f"角色: {entity.role or '?'}  |  位置: {zone}\n"
            f"体力: {v:.0f}/100  |  饱腹: {s:.0f}/100  |  心情: {m:.0f}/100\n\n"
        )

    def slot_personality(self, **kw) -> str:
        tags = kw.get("personality_tags", [])
        if tags:
            return f"### 性格标签\n{'、'.join(tags)}\n\n"
        return ""

    def slot_recent_info(self, **kw) -> str:
        entity = kw.get("entity")
        memories = kw.get("memories", [])
        if not entity:
            return ""
        if entity.recent_info:
            return f"### 最近信息\n  {entity.recent_info}\n\n"
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
                return f"### 当前持有\n{items}\n\n"
        return "### 当前持有\n空手\n\n"

    def slot_zone_others(self, **kw) -> str:
        zone_npcs = kw.get("zone_npcs")
        if zone_npcs:
            others = "、".join(f"{z['name']}（{z['role']}）" for z in zone_npcs)
            return f"### 当前区域还有\n{others}\n\n"
        return ""

    def slot_available_recipes(self, **kw) -> str:
        recipes = kw.get("recipes")
        if not recipes:
            return ""
        parts = ["### 可用配方"]
        for r in recipes:
            inp = " + ".join(f"{k}x{v}" for k, v in r.get("inputs", {}).items())
            out = " + ".join(f"{k}x{v}" for k, v in r.get("outputs", {}).items())
            req_obj = r.get("required_object_type", "")
            req_zone = r.get("zone_id", "")
            extra = (f"  @{req_zone}" if req_zone else "") + (f" 需[{req_obj}]" if req_obj else "")
            parts.append(f"  - {r['name']}: {inp} → {out}{extra}")
        return "\n".join(parts) + "\n\n"

    def slot_decision_guidance(self, **kw) -> str:
        entity = kw.get("entity")
        if not entity:
            return ""
        zone = self._get_zone_name(entity)
        return (
            f"### 决策\n"
            f"请为 {entity.name} 决定下一步行动。\n"
            "用自然语言描述：你想做什么？在哪里做？有什么影响（体力/库存变化）？为什么？\n"
            "不用输出 JSON，不用关心格式，直接说你想干嘛。\n\n"
            f"格式示例：我叫{entity.name}，我是{entity.role or '?'}。"
            f"我目前在{zone}，持有小麦x21，体力7。\n"
            "我决定去market卖掉5小麦换金币，体力会消耗一些。\n"
            "用第一人称说人话就行。\n"
        )

    # ════════════════════════════════════════════
    # LLM #2: 结构变更 共用 slot
    # ════════════════════════════════════════════

    def slot_system_role(self, **kw) -> str:
        caller = kw.get("_caller", "")
        if caller == "llm2":
            return (
                "你是一个世界模拟引擎的拓扑结构变更模块（LLM #2）。\n"
                "你的任务：根据每个 NPC 的自然语言计划，输出拓扑结构变更操作。\n\n"
            )
        if caller == "llm3":
            return (
                "你是世界模拟引擎的故事叙事层。\n"
                "你的任务：为以下场景写一段生动的故事。\n"
                "完全自由发挥，不要输出任何 JSON 或结构化格式。只写故事。\n\n"
            )
        if caller == "llm4a":
            return (
                "你是一个世界模拟引擎的**拓扑变化推理模块**（LLM #4a）。\n\n"
                "你的任务：根据 NPC 的计划、故事叙事以及当前拓扑状态，\n"
                "推理出本次 tick 的**图结构变化**（边的数量增删）。\n\n"
            )
        if caller == "llm4b":
            return (
                "你是一个世界模拟引擎的**内容变化推理模块**（LLM #4b）。\n\n"
                "你的任务：根据 NPC 的计划、故事叙事以及已执行的拓扑变更，\n"
                "推理出本次 tick 的**属性变化和近况摘要**。\n\n"
            )
        return ""

    def slot_combined_header(self, **kw) -> str:
        count = kw.get("count", 0)
        return (
            "每个 NPC 的 prompt 分为 [拓扑]（纯结构，无名称）"
            "和 [内容]（语义信息+标签映射）两段。\n"
            "输出时使用 [内容] 段的 entity_id。\n\n"
            f"共 {count} 个 NPC。\n"
        )

    def slot_npc_block(self, **kw) -> str:
        entity = kw.get("entity")
        plan = kw.get("plan", "")
        engine = kw.get("engine")
        if not entity or not engine:
            return ""
        topo_eids = kw.get("topo_eids", [entity.entity_id])
        topo_text, label_map = engine.build_tagged_topology(list(topo_eids))
        from .graph_engine import build_label_mapping_text
        mapping = build_label_mapping_text(label_map, engine)
        inv = engine.get_inventory_view(entity.entity_id)
        inv_str = "、".join(
            f"{i['item_name']}x{i['quantity']}" for i in inv
        ) if inv else "空手"
        return (
            f"==== [拓扑] ====\n"
            f"拓扑信息是客观数据层的唯一事实来源。\n\n"
            f"{topo_text}\n\n"
            f"==== [内容] ====\n"
            f"标签映射：\n{mapping}\n"
            f"自身描述：\n{entity.to_prompt_block()}\n\n"
            f"持有：{inv_str}\n\n"
            f"计划：{plan}\n\n"
            f"=== 指令 ===\n"
            f"输出你需要执行的拓扑操作。用 [内容] 标签映射中的 entity_id 作为 src/tgt。\n"
            f"格式（JSON 数组）：\n"
            f'  [{{"op":"connect","src":"{entity.entity_id}","tgt":"zone_南集市","qty":0}}]\n'
            f"可用的 op：\n"
            f'  "connect"    \u2014 建立连接\n'
            f'  "disconnect" \u2014 断开连接\n'
            f"重要规则：\n"
            f"1. 你只负责拓扑结构，不改数值。物品持有变更由 LLM #4 处理。\n"
            f"2. 区域连接用 qty=-1，NPC\u2194NPC 连接用 qty=0。\n"
            f"3. 输出纯 JSON，不要多余文字，不要 markdown。\n"
        )

    def slot_output_instructions(self, **kw) -> str:
        caller = kw.get("_caller", "")
        if caller == "llm2":
            return (
                "==== 最终输出格式 ====\n"
                "输出一个 JSON 对象，key 为 NPC entity_id，value 为操作数组：\n"
                '{\n'
                '  "npc_abc123": [\n'
                '    {"op": "connect", "src": "npc_abc123", "tgt": "zone_南集市", "qty": 0},\n'
                '    {"op": "disconnect", "src": "npc_abc123", "tgt": "zone_酒馆"}\n'
                '  ],\n'
                '  "npc_def456": [...]\n'
                '}\n'
                "不要多余文字，不要 markdown 代码块。\n"
            )
        if caller == "llm3":
            return (
                "请为以上场景写一段生动的故事。一段即可。\n"
                "用【场景】开头，后面不要加标题名称。\n"
                "示例：\n"
                "【场景】清晨的阳光洒在市集的石板路上...\n"
            )
        return ""

    # ════════════════════════════════════════════
    # LLM #3: 故事叙事
    # ════════════════════════════════════════════

    def slot_entity_blocks(self, **kw) -> str:
        entity_blocks = kw.get("entity_blocks", [])
        blocks = []
        for ent, er in entity_blocks:
            lines = [f"\u00b7 {ent.name}"]
            if ent.entity_type:
                lines.append(f"  类型: {ent.entity_type}")
            if ent.desc:
                lines.append(f"  描述: {ent.desc}")
            if ent.role:
                lines.append(f"  身份: {ent.role}")
            if er:
                for key, label in [
                    ("vitality_text", "体力"),
                    ("satiety_text", "饱腹"),
                    ("mood_text", "心情"),
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
        return "【角色和物体】\n\n" + text + "\n\n"

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
        return "【库存】\n" + "\n".join(f"  \u00b7 {l}" for l in lines) + "\n\n"

    def slot_event_input(self, **kw) -> str:
        component = kw.get("component")
        if not component or not component.edges:
            return "  （无交互事件）\n"
        lines = []
        for e in component.edges:
            if e.edge_type == "npc_zone" and e.stayed:
                lines.append(f"  \u00b7 {e.source} 在原地驻足")
            elif e.edge_type == "npc_zone":
                lines.append(f"  \u00b7 {e.source} 来到此处")
            elif e.edge_type == "npc_npc" and not e.success:
                lines.append(f"  \u00b7 {e.source} 试图找 {e.target}，但没成功")
            elif e.edge_type == "npc_npc":
                lines.append(f"  \u00b7 {e.source} 与 {e.target} 有互动")
            elif e.edge_type == "npc_object":
                lines.append(f"  \u00b7 {e.source} 使用 {e.target}")
        return "\n".join(lines) + "\n"

    # ════════════════════════════════════════════
    # LLM #4a: 拓扑操作
    # ════════════════════════════════════════════

    def slot_npc_state_section(self, **kw) -> str:
        entities = kw.get("entities", [])
        engine = kw.get("engine")
        lines = ["==== 当前 NPC 状态 ===="]
        for ent in entities:
            inv = engine.get_inventory_view(ent.entity_id) if engine else []
            inv_str = "、".join(
                f"{i['item_name']}x{i['quantity']}" for i in inv
            ) if inv else "空手"
            zone = self._get_zone_name(ent)
            lines.append(
                f"- {ent.name}（{ent.role or '?'}）@{zone} | "
                f"体力{ent.attributes.get('vitality', 100):.0f}/100 "
                f"饱腹{ent.attributes.get('satiety', 50):.0f}/100 "
                f"心情{ent.attributes.get('mood', 50):.0f}/100 | "
                f"持有：{inv_str}"
            )
        return "\n".join(lines) + "\n\n"

    def slot_plans_section(self, **kw) -> str:
        npc_plans = kw.get("npc_plans", {})
        entities = kw.get("entities", [])
        lines = ["==== 每位 NPC 的本轮计划 ===="]
        for ent in entities:
            plan = npc_plans.get(ent.entity_id, "")
            if plan:
                lines.append(f"- {ent.name}：{plan[:300]}")
        return "\n".join(lines) + "\n\n"

    def slot_stories_section(self, **kw) -> str:
        stories = kw.get("stories", [])
        lines = ["==== 本轮故事叙事 ===="]
        for i, story in enumerate(stories, 1):
            lines.append(f"--- 事件 {i} ---")
            lines.append(story)
        return "\n".join(lines) + "\n\n"

    def slot_guidance_syntax(self, **kw) -> str:
        return (
            "==== [拓扑] 引导 ====\n"
            "支持的操作类型（可以同时出现，不是互斥）：\n"
            "· recipe：配方转换（消耗一组物品生产另一组）\n"
            '  {"op": "recipe", "src": "铁匠王", '
            '"consumes": {"item_铁锭": 3}, '
            '"produces": {"item_武器": 1}}\n'
            "  \u2014 守恒校验跳过（配方内部自平衡）\n"
            "· system_delta：系统间物品转移\n"
            '  {"op": "system_delta", "tgt": "老张", '
            '"item": "item_小麦", "delta": -5}\n'
            "  \u2014 守恒校验跳过\n"
            "· delta：修改边的数量（必须成对，\u03a3=0）\n"
            '  {"op": "delta", "src": "老陈", '
            '"tgt": "item_金币", "delta": 10}\n'
            "  src/tgt 用名称，系统自动映射到实体 ID\n"
            "· set_qty：设置边的数量\n"
            '  {"op": "set_qty", "src": "老陈", '
            '"tgt": "item_面包", "qty": 2}\n'
            "· connect/disconnect 格式由 LLM #2 处理"
            " \u2014 LLM #4a 不输出拓扑变更。\n\n"
        )

    def slot_output_format(self, **kw) -> str:
        caller = kw.get("_caller", "")
        if caller == "llm4a":
            return (
                '==== 输出格式 ====\n'
                '{ "operations": [...] }\n\n'
                "只输出 JSON，不要多余文字，不要 markdown 代码块。\n\n"
                "示例（delta + system_delta 共存）：\n"
                '{"operations": [\n'
                '  {"op": "delta", "src": "铁匠王", '
                '"tgt": "item_金币", "delta": -2},\n'
                '  {"op": "delta", "src": "张大娘", '
                '"tgt": "item_金币", "delta": 2},\n'
                '  {"op": "delta", "src": "张大娘", '
                '"tgt": "item_面包", "delta": -2},\n'
                '  {"op": "delta", "src": "铁匠王", '
                '"tgt": "item_面包", "delta": 2},\n'
                '  {"op": "system_delta", "tgt": "铁匠王", '
                '"item": "item_面包", "delta": -1}\n'
                "]}\n\n"
                "示例（recipe 单独）：\n"
                '{"operations": [\n'
                '  {"op": "recipe", "src": "铁匠王", '
                '"consumes": {"item_铁锭": 3}, '
                '"produces": {"item_武器": 1}}\n'
                "]}\n"
            )
        if caller == "llm4b":
            return (
                '==== [内容] 输出格式 ====\n'
                "根据故事中的活动输出 attr + recent_info：\n"
                "1. 诚实反映故事里的属性变化\n"
                "2. 只要 NPC 有活动，就输出 attr\n"
                "3. 不需要输出任何物品边操作\n"
                "4. 所有 attr 都必须在 recent_info 中有对应叙述\n\n"
                "属性常识：\n"
                "- 交易成功\u2192心情+，交易失败\u2192心情-\n"
                "- 活动消耗体力，休息恢复体力\n"
                "- 吃东西增加饱腹\n\n"
                "recent_info 写作：\n"
                "- NPC 用第一人称\n"
                "- zone 写「这里发生了什么」\n"
                "- 每条 20-60 字\n\n"
                '输出格式：\n'
                '{\n'
                '  "operations": [{"op": "attr", '
                '"target": "实体名", "attr": "属性名", '
                '"delta": N, "description": "描述"}],\n'
                '  "recent_info": {"实体名": "近况摘要"}\n'
                '}\n\n'
                "示例：\n"
                '{\n'
                '  "operations": [\n'
                '    {"op": "attr", "target": "老张", '
                '"attr": "vitality", "delta": -5, '
                '"description": "摆摊卖小麦"},\n'
                '    {"op": "attr", "target": "老张", '
                '"attr": "mood", "delta": -3, '
                '"description": "还没开张"}\n'
                '  ],\n'
                '  "recent_info": {\n'
                '    "老张": "我在南集市摆摊卖小麦，吆喝半天还没开张",\n'
                '    "南集市": "集市熙熙攘攘热闹非凡"\n'
                '  }\n'
                '}\n'
            )
        return ""

    # ════════════════════════════════════════════
    # LLM #4b: 内容属性
    # ════════════════════════════════════════════

    def slot_recent_info_guidance(self, **kw) -> str:
        return (
            "==== 节点近况投影 ====\n"
            "请根据本轮故事，为故事中涉及到的节点生成近况摘要。\n"
            "近况会成为该节点下个 tick 的上下文。\n\n"
            "规则：\n"
            "- 每条 20-60 字，简洁但有信息量\n"
            "- NPC 用第一人称（「我做了什么」），zone 写「这里发生了什么」\n"
            "- 没有事件可以不写\n"
            "- 不是 NPC 的节点也可以写（zone、item 等）\n\n"
        )

    def slot_attr_constraints(self, **kw) -> str:
        return (
            "==== [内容] 约束 ====\n"
            "□ attr 值域：vitality / satiety / mood 均在 0-100 范围内\n"
            "□ 每条 attr 必须有对应的 recent_info 条目\n"
            "□ recent_info 每条 20-60 字\n"
            "□ 至少输出 1 条 attr 操作\n\n"
        )

    def slot_attr_knowledge(self, **kw) -> str:
        return (
            "==== [内容] 引导 ====\n"
            "根据故事中的活动输出 attr + recent_info：\n"
            "1. 诚实反映故事里的属性变化"
            "（体力消耗、饱腹变化、心情波动）\n"
            "2. 只要 NPC 有活动，就输出 attr\n"
            "3. 不需要输出任何物品边操作"
            "（delta / system_delta / recipe）\n"
            "4. 所有 attr 都必须在 recent_info 中有对应叙述\n\n"
            "属性常识：\n"
            "- 交易成功\u2192心情+，交易失败\u2192心情-\n"
            "- 活动消耗体力，休息恢复体力\n"
            "- 吃东西增加饱腹\n\n"
            "recent_info 写作：\n"
            "- NPC 用第一人称（「我做了什么」）\n"
            "- zone 写「这里发生了什么」\n"
            "- 每条 20-60 字，有信息量\n"
            "- 不是 NPC 的节点也可以写（zone、item 等）\n\n"
        )
