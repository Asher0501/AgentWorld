"""
Post Processor —— 拆分为 LLM #4a（拓扑层）+ LLM #4b（内容层）

LLM #4a：根据 LLM #3 的故事描述 + 当前拓扑，输出拓扑层操作。
  - 输出：边数量变更 (delta / system_delta / recipe)
  - 不输出 attr，不输出 recent_info

LLM #4b：在 #4a 拓扑变更已执行后，输出节点内容变更。
  - 输出：属性变更 (attr) + 近况投影 (recent_info)
  - 不输出任何边操作

设计原则：
  - LLM 从不直接写入数据
  - 输出纯结构化指令，由 GraphEngine.apply_edge_operations() 执行
  - 拓扑层受度守恒约束，内容层不受
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .graph_engine import GraphEngine
from ..config.config_loader import has_role

logger = logging.getLogger("post_processor")


class PostProcessor:
    """
    LLM #4a: 拓扑层操作 (delta / system_delta / recipe)
    LLM #4b: 内容层操作 (attr + recent_info)

    两个独立的 LLM 调用，顺序执行。
    #4a 先执行（边操作），#4b 后执行（节点属性）。
    """

    def __init__(self, resolver=None):
        self._resolver = resolver  # InteractionResolver（复用 LLM 调用能力）

    # ════════════════════════════════════════════════════════════════
    # LLM #4a: 拓扑层操作 (delta / system_delta / recipe)
    # ════════════════════════════════════════════════════════════════

    def resolve_topology_changes(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
    ) -> list[dict]:
        """
        LLM #4a: 故事 + 拓扑 → 拓扑层操作。
        不输出 attr，不输出 recent_info。

        Returns:
            [{op: "delta"|"system_delta"|"recipe"|...}]
        """
        if not stories or not npc_plans:
            return []

        prompt = self._build_topo_prompt(
            npc_plans=npc_plans,
            stories=stories,
            graph_engine=graph_engine,
            world_time_str=world_time_str,
            tick_duration_str=tick_duration_str,
        )

        if not self._resolver:
            logger.warning("[LLM #4a] 无 LLM resolver")
            return []

        raw = self._resolver._call_llm(prompt)
        if not raw or not raw.strip():
            return []

        ops = self._parse_topo_output(raw, graph_engine)
        logger.info(f"[LLM #4a] 解析到 {len(ops)} 个拓扑操作")
        return ops

    # ════════════════════════════════════════════════════════════════
    # LLM #4b: 内容层操作 (attr + recent_info)
    # ════════════════════════════════════════════════════════════════

    def resolve_attr_and_recent(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
    ) -> tuple[list[dict], dict[str, str]]:
        """
        LLM #4b: 故事 + 已更新拓扑 → attr + recent_info。
        不输出任何边操作 (delta/system_delta/recipe)。

        Returns:
            (attr_ops, recent_info_map)
        """
        if not stories or not npc_plans:
            return [], {}

        prompt = self._build_content_prompt(
            npc_plans=npc_plans,
            stories=stories,
            graph_engine=graph_engine,
            world_time_str=world_time_str,
            tick_duration_str=tick_duration_str,
        )

        if not self._resolver:
            logger.warning("[LLM #4b] 无 LLM resolver")
            return [], {}

        raw = self._resolver._call_llm(prompt)
        if not raw or not raw.strip():
            return [], {}

        ops, recent_info_map = self._parse_output(raw, graph_engine)
        logger.info(f"[LLM #4b] 解析到 {len(ops)} attr, {len(recent_info_map)} 条近况")
        return ops, recent_info_map

    # ─── Prompt 构建（共享基础） ───

    def _build_topo_prompt(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
    ) -> str:
        """
        构建 LLM #4a prompt：拓扑层操作。

        输出：只输出边操作 (delta / system_delta / recipe / set_qty)
        不包含：attr、recent_info、节点近况投影
        """
        parts = [
            "你是一个世界模拟引擎的**拓扑变化推理模块**（LLM #4a）。",
            "",
            "你的任务：根据 NPC 的计划、故事叙事以及当前拓扑状态，",
            "推理出本次 tick 的**图结构变化**（边的数量增删）。",
            "",
        ]

        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        if world_time_str or tick_duration_str:
            parts.append("")

        # NPC 状态
        parts.append("==== 当前 NPC 状态 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            if ent:
                eid = ent.entity_id
                inv = graph_engine.get_inventory_view(eid)
                inv_str = "、".join(
                    f"{i['item_name']}x{i['quantity']}" for i in inv
                ) if inv else "空手"

                zone_name = "?"
                for conn in ent.connected_entity_ids:
                    e = graph_engine.get_entity(conn)
                    if e and has_role(e.type_id, "region"):
                        zone_name = e.name
                        break

                parts.append(
                    f"- {ent.name}（{ent.role or '?'}）@{zone_name} | "
                    f"体力{ent.attributes.get('vitality', 100):.0f}/100 "
                    f"饱腹{ent.attributes.get('satiety', 50):.0f}/100 "
                    f"心情{ent.attributes.get('mood', 50):.0f}/100 | "
                    f"持有：{inv_str}"
                )
            else:
                parts.append(f"- {npc_eid}（？）")
        parts.append("")

        # NPC 计划
        parts.append("==== 每位 NPC 的本轮计划 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            display_name = ent.name if ent else npc_eid
            parts.append(f"- {display_name}：{plan[:300]}")
        parts.append("")

        # 故事
        parts.append("==== 本轮故事叙事 ====")
        for i, story in enumerate(stories, 1):
            parts.append(f"--- 事件 {i} ---")
            parts.append(story)
        parts.append("")

        # [拓扑] 当前结构（纯标签，无名称/类型）
        topo_pool: set[str] = set()
        type_map: dict[str, str] = {}
        for npc_eid in npc_plans:
            ent = graph_engine.get_entity(npc_eid)
            if not ent:
                continue
            topo_pool.add(npc_eid)
            type_map[npc_eid] = ent.name or npc_eid
            for conn_eid in ent.connected_entity_ids:
                topo_pool.add(conn_eid)
                ce = graph_engine.get_entity(conn_eid)
                if ce:
                    type_map[conn_eid] = ce.name or conn_eid
                    if has_role(ce.type_id, "region"):
                        for other in graph_engine.all_entities():
                            if has_role(other.type_id, "actor") and other.entity_id not in topo_pool \
                               and other.is_connected_to(conn_eid):
                                topo_pool.add(other.entity_id)
                                type_map[other.entity_id] = other.name or other.entity_id
                                for oconn in other.connected_entity_ids:
                                    if oconn not in topo_pool:
                                        ce2 = graph_engine.get_entity(oconn)
                                        if ce2 and has_role(ce2.type_id, "thing"):
                                            topo_pool.add(oconn)
                                            type_map[oconn] = ce2.name or oconn

        topo_text, label_map = graph_engine.build_tagged_topology(list(topo_pool))
        parts.append("==== [拓扑] 当前结构 ====")
        parts.append(topo_text)
        parts.append("")

        # [内容] 实体 ID 映射
        parts.append("==== [内容] 实体 ID 映射（在 delta/recipe 的 src/tgt 中用 <= 左侧的名称） ====")
        mapped_ids: set[str] = set()
        for label, eid in sorted(label_map.items(), key=lambda x: x[0]):
            if eid in type_map:
                cons_tag = " (conserved)" if graph_engine.is_conserved(eid) else ""
                parts.append(f"  {{{label}}} = {type_map[eid]}  →  {eid}{cons_tag}")
                mapped_ids.add(eid)
        for npc_eid in npc_plans:
            ent = graph_engine.get_entity(npc_eid)
            if not ent or ent.entity_id in mapped_ids:
                continue
            cons_tag = " (conserved)" if graph_engine.is_conserved(ent.entity_id) else ""
            parts.append(f"  {ent.name} → {ent.entity_id}{cons_tag}")
            mapped_ids.add(ent.entity_id)
            for conn_eid in ent.connected_entity_ids:
                if conn_eid in mapped_ids:
                    continue
                ce = graph_engine.get_entity(conn_eid)
                if ce and has_role(ce.type_id, "thing"):
                    iname = ce.name or conn_eid
                    cons_tag = " (conserved)" if graph_engine.is_conserved(conn_eid) else ""
                    parts.append(f"  {iname} → {conn_eid}{cons_tag}")
                    mapped_ids.add(conn_eid)
        parts.append("")

        # ── [拓扑] 约束（并行分类，不互斥） ──
        parts.append("==== [拓扑] 约束 ====")
        parts.append("""□ terminal 节点：不可新增连接。
□ 逐条分析每笔物品流动，独立判断类型——可以同时存在，不是互斥：
    · 买/卖/换（有明确对手方）      → delta 两条（A- B+，Σ=0）
    · 消耗/发现/自然消失（系统边界） → system_delta 一条（跳过守恒）
    · 加工/合成（一组转另一组）     → recipe 一条（内部自平衡）
  **同一物品可以同时出现在 delta 和 system_delta 中**""")
        parts.append("")

        # ── [拓扑] 引导（语法格式） ──
        parts.append("==== [拓扑] 引导 ====")
        parts.append("""支持的操作类型（可以同时出现，不是互斥）：
· recipe：配方转换（消耗一组物品生产另一组）
  {"op": "recipe", "src": "铁匠王", "consumes": {"item_铁锭": 3}, "produces": {"item_武器": 1}}
  — 守恒校验跳过（配方内部自平衡）
· system_delta：系统间物品转移（来源/去向在系统外）
  {"op": "system_delta", "tgt": "老张", "item": "item_小麦", "delta": -5}
  — tgt 是 NPC，item 是物品，delta 正=获得 负=消耗
  — 守恒校验跳过
· delta：修改边的数量（必须成对，Σ=0）
  {"op": "delta", "src": "老陈", "tgt": "item_金币", "delta": 10}
  src/tgt 用名称，系统自动映射到实体 ID
· set_qty：设置边的数量
  {"op": "set_qty", "src": "老陈", "tgt": "item_面包", "qty": 2}
· connect/disconnect 格式由 LLM #2 处理 — LLM #4a 不输出拓扑变更。""")
        parts.append("")

        # ── 输出格式 ──
        parts.append("==== 输出格式 ====")
        parts.append("""{ "operations": [...] }

只输出 JSON，不要多余文字，不要 markdown 代码块。

示例（delta + system_delta 共存——同批物品同时交易和消耗）：
{"operations": [
  {"op": "delta", "src": "铁匠王", "tgt": "item_金币", "delta": -2},
  {"op": "delta", "src": "张大娘", "tgt": "item_金币", "delta": 2},
  {"op": "delta", "src": "张大娘", "tgt": "item_面包", "delta": -2},
  {"op": "delta", "src": "铁匠王", "tgt": "item_面包", "delta": 2},
  {"op": "system_delta", "tgt": "铁匠王", "item": "item_面包", "delta": -1}
]}
（注：铁匠王买了2个面包，当场吃了1个→delta 4条 + system_delta 1条）

示例（recipe 单独）：
{"operations": [
  {"op": "recipe", "src": "铁匠王", "consumes": {"item_铁锭": 3}, "produces": {"item_武器": 1}}
]}""")

        return "\n".join(parts)

    def _build_content_prompt(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
    ) -> str:
        """
        构建 LLM #4b prompt：内容层操作。

        输出：只输出 attr + recent_info
        不包含：delta / system_delta / recipe / [拓扑] 结构
        """
        parts = [
            "你是一个世界模拟引擎的**内容变化推理模块**（LLM #4b）。",
            "",
            "你的任务：根据 NPC 的计划、故事叙事以及已执行的拓扑变更，",
            "推理出本次 tick 的**属性变化和近况摘要**。",
            "",
        ]

        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        if world_time_str or tick_duration_str:
            parts.append("")

        # NPC 状态
        parts.append("==== 当前 NPC 状态 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            if ent:
                eid = ent.entity_id
                inv = graph_engine.get_inventory_view(eid)
                inv_str = "、".join(
                    f"{i['item_name']}x{i['quantity']}" for i in inv
                ) if inv else "空手"

                zone_name = "?"
                for conn in ent.connected_entity_ids:
                    e = graph_engine.get_entity(conn)
                    if e and has_role(e.type_id, "region"):
                        zone_name = e.name
                        break

                parts.append(
                    f"- {ent.name}（{ent.role or '?'}）@{zone_name} | "
                    f"体力{ent.attributes.get('vitality', 100):.0f}/100 "
                    f"饱腹{ent.attributes.get('satiety', 50):.0f}/100 "
                    f"心情{ent.attributes.get('mood', 50):.0f}/100 | "
                    f"持有：{inv_str}"
                )
            else:
                parts.append(f"- {npc_eid}（？）")
        parts.append("")

        # NPC 计划
        parts.append("==== 每位 NPC 的本轮计划 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            display_name = ent.name if ent else npc_eid
            parts.append(f"- {display_name}：{plan[:300]}")
        parts.append("")

        # 故事
        parts.append("==== 本轮故事叙事 ====")
        for i, story in enumerate(stories, 1):
            parts.append(f"--- 事件 {i} ---")
            parts.append(story)
        parts.append("")

        # 节点近况投影
        parts.append("==== 节点近况投影 ====")
        parts.append("""请根据本轮故事，为故事中涉及到的节点生成近况摘要。
近况会成为该节点下个 tick 的上下文。

规则：
- 每条 20-60 字，简洁但有信息量
- NPC 用第一人称（"我做了什么"），zone 写"这里发生了什么"
- 没有事件可以不写
- 不是 NPC 的节点也可以写（zone、item 等）
""")
        parts.append("")

        # ── [内容] 约束 ──
        parts.append("==== [内容] 约束 ====")
        parts.append("""□ attr 值域：vitality / satiety / mood 均在 0-100 范围内
□ 每条 attr 必须有对应的 recent_info 条目
□ recent_info 每条 20-60 字
□ 至少输出 1 条 attr 操作 — 不要输出空的 operations 数组""")
        parts.append("")

        # ── [内容] 引导 ──
        parts.append("==== [内容] 引导 ====")
        parts.append("""根据故事中的活动输出 attr + recent_info：
1. 诚实反映故事里的属性变化（体力消耗、饱腹变化、心情波动）
2. 只要 NPC 有活动，就输出 attr
3. 你不需要输出任何物品边操作（delta / system_delta / recipe）
4. 所有 attr 都必须在 recent_info 中有对应叙述

属性常识：
- 交易成功→心情+，交易失败→心情-
- 活动消耗体力，休息恢复体力
- 吃东西增加饱腹

recent_info 写作：
- NPC 用第一人称（"我做了什么"）
- zone 写"这里发生了什么"
- 每条 20-60 字，有信息量
- 不是 NPC 的节点也可以写（zone、item 等）

输出格式：
{
  "operations": [{"op": "attr", "target": "实体名", "attr": "属性名", "delta": N, "description": "描述"}],
  "recent_info": {"实体名": "近况摘要"}
}

示例：
{
  "operations": [
    {"op": "attr", "target": "老张", "attr": "vitality", "delta": -5, "description": "摆摊卖小麦"},
    {"op": "attr", "target": "老张", "attr": "mood", "delta": -3, "description": "还没开张"}
  ],
  "recent_info": {
    "老张": "我在南集市摆摊卖小麦，吆喝半天还没开张",
    "南集市": "集市熙熙攘攘热闹非凡"
  }
}

只输出 JSON，不要多余文字，不要 markdown 代码块。""")

        return "\n".join(parts)

    # ─── 解析 ───

    def _parse_topo_output(self, raw: str, graph_engine: GraphEngine) -> list[dict]:
        """
        解析 LLM #4a 输出：只提取拓扑操作。

        输出格式：{"operations": [...]}
        Returns: [{op, ...}] 不包含 attr 操作
        """
        raw = raw.strip()
        json_str = self._extract_json(raw)

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

        valid_ops = []
        topo_types = {"delta", "system_delta", "recipe", "set_qty"}
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
                real_src = self._resolve_name(src, graph_engine)
                real_tgt = self._resolve_name(tgt, graph_engine)
                if real_src and real_tgt:
                    valid_ops.append({"op": "delta", "src": real_src, "tgt": real_tgt, "delta": delta})

            elif op_type == "system_delta":
                tgt = op.get("tgt", "")
                item = op.get("item", "")
                delta = op.get("delta", 0)
                if not tgt or not item or delta == 0:
                    continue
                real_tgt = self._resolve_name(tgt, graph_engine)
                real_item = self._resolve_name(item, graph_engine)
                if real_tgt and real_item:
                    valid_ops.append({"op": "system_delta", "tgt": real_tgt, "item": real_item, "delta": delta})

            elif op_type == "recipe":
                src = op.get("src", "")
                consumes = op.get("consumes", {})
                produces = op.get("produces", {})
                if not src or not consumes or not produces:
                    continue
                real_src = self._resolve_name(src, graph_engine)
                if real_src:
                    valid_ops.append({"op": "recipe", "src": real_src, "consumes": consumes, "produces": produces})

            elif op_type == "set_qty":
                src = op.get("src", "")
                tgt = op.get("tgt", "")
                qty = op.get("qty", 0)
                if not src or not tgt:
                    continue
                real_src = self._resolve_name(src, graph_engine)
                real_tgt = self._resolve_name(tgt, graph_engine)
                if real_src and real_tgt:
                    valid_ops.append({"op": "set_qty", "src": real_src, "tgt": real_tgt, "qty": qty})

        return valid_ops

    def _parse_output(self, raw: str, graph_engine: GraphEngine) -> tuple[list[dict], dict[str, str]]:
        """
        解析 LLM #4b 输出：提取 attr + recent_info。

        Returns:
            (attr_ops, recent_info_map)
        """
        raw = raw.strip()
        json_str = self._extract_json(raw)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[LLM #4] JSON 解析失败: {raw[:200]}")
            return [], {}

        recent_info_map: dict[str, str] = {}

        if isinstance(parsed, list):
            ops = parsed
        elif isinstance(parsed, dict):
            ops = parsed.get("operations", [])
            raw_ri = parsed.get("recent_info", {})
            if isinstance(raw_ri, dict):
                for name, text in raw_ri.items():
                    if text and isinstance(text, str) and text.strip():
                        ent = graph_engine.find_entity_by_name(name)
                        if ent:
                            recent_info_map[ent.entity_id] = text.strip()
        else:
            return [], {}

        # 只保留 attr 操作
        valid_ops = []
        for op in ops:
            op_type = op.get("op", "")
            if op_type != "attr":
                continue
            target = op.get("target", "")
            attr = op.get("attr", "")
            delta = op.get("delta", 0)
            desc = op.get("description", "")
            if not target or not attr or delta == 0:
                continue
            real_target = self._resolve_name(target, graph_engine)
            if real_target:
                valid_ops.append({"op": "attr", "target": real_target, "attr": attr, "delta": delta, "description": desc})

        logger.info(f"[LLM #4b] 解析到 {len(valid_ops)} attr, {len(recent_info_map)} 条近况")
        return valid_ops, recent_info_map

    def _resolve_name(self, name_or_id: str, graph_engine: GraphEngine) -> str | None:
        """将 NPC 名/物品名 解析为 entity_id"""
        if name_or_id.startswith("npc_") or name_or_id.startswith("item_"):
            if graph_engine.get_entity(name_or_id):
                return name_or_id
            _, _, bare_name = name_or_id.partition("_")
            if bare_name:
                ent = graph_engine.find_entity_by_name(bare_name)
                if ent:
                    return ent.entity_id
                for eid, e in graph_engine._entities.items():
                    if e.name and bare_name in e.name:
                        return eid
        ent = graph_engine.find_entity_by_name(name_or_id)
        if ent:
            return ent.entity_id
        for eid, e in graph_engine._entities.items():
            if e.name and name_or_id in e.name:
                return eid
        return None

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON"""
        if '```' in text:
            blocks = text.split('```')
            for block in blocks:
                block = block.strip()
                if block.startswith('json'):
                    block = block[4:].strip()
                if block.startswith('[') or block.startswith('{'):
                    return block
        stripped = text.lstrip()
        if stripped.startswith('{'):
            stack = []
            start = -1
            for i, ch in enumerate(stripped):
                if ch == '{':
                    if not stack:
                        start = i
                    stack.append(ch)
                elif ch == '}':
                    if stack and stack[-1] == '{':
                        stack.pop()
                        if not stack and start >= 0:
                            return stripped[start:i + 1]
        elif stripped.startswith('['):
            stack = []
            start = -1
            for i, ch in enumerate(stripped):
                if ch == '[':
                    if not stack:
                        start = i
                    stack.append(ch)
                elif ch == ']':
                    if stack and stack[-1] == '[':
                        stack.pop()
                        if not stack and start >= 0:
                            return stripped[start:i + 1]
        return text
