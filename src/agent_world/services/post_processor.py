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
from .prompt_assembler import assemble

logger = logging.getLogger("post_processor")


class PostProcessor:
    """
    LLM #4a: 拓扑层操作 (delta / system_delta / recipe)
    LLM #4b: 内容层操作 (attr + recent_info)

    两个独立的 LLM 调用，顺序执行。
    #4a 先执行（边操作），#4b 后执行（节点属性）。
    """

    def __init__(self, resolver=None, adapter=None):
        self._resolver = resolver  # InteractionResolver（复用 LLM 调用能力）
        self._adapter = adapter  # VillageDomainAdapter

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
        feedback: str = "",
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
            feedback=feedback,
        )

        if not self._resolver:
            logger.warning("[LLM #4a] 无 LLM resolver")
            return []

        raw = self._resolver._call_llm(prompt)
        self._last_raw_topo_response = raw  # ← 存档供重试用
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
        feedback: str = "",
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
            feedback=feedback,
        )

        if not self._resolver:
            logger.warning("[LLM #4b] 无 LLM resolver")
            return [], {}

        raw = self._resolver._call_llm(prompt)
        self._last_raw_attr_response = raw  # ← 存档供重试用
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
        feedback: str = "",
    ) -> str:
        """构建 LLM #4a prompt：拓扑层操作。"""
        if self._adapter:
            # 构建 entities list + topo pool
            entities = []
            topo_pool: set[str] = set()
            for npc_eid in npc_plans:
                ent = graph_engine.get_entity(npc_eid)
                if ent:
                    entities.append(ent)
                    topo_pool.add(npc_eid)
                    # 扩展：zone + zone内其他NPC + 物品
                    for conn_eid in ent.connected_entity_ids:
                        topo_pool.add(conn_eid)
                        ce = graph_engine.get_entity(conn_eid)
                        if ce and has_role(ce.type_id, "region"):
                            for other in graph_engine.all_entities():
                                if has_role(other.type_id, "actor") \
                                   and other.entity_id not in topo_pool \
                                   and other.is_connected_to(conn_eid):
                                    topo_pool.add(other.entity_id)
                                    for oconn in other.connected_entity_ids:
                                        ce2 = graph_engine.get_entity(oconn)
                                        if ce2 and has_role(ce2.type_id, "thing"):
                                            topo_pool.add(oconn)
            return assemble(
                "llm4a_topo", self._adapter, graph_engine,
                _caller="llm4a",
                time_str=world_time_str,
                tick_str=tick_duration_str,
                entities=entities,
                npc_plans=npc_plans,
                stories=stories,
                topo_eids=list(topo_pool),
                feedback=feedback,
            )

        # 旧版回退
        parts = ["你是一个世界模拟引擎的**拓扑变化推理模块**（LLM #4a）。","","你的任务：根据 NPC 的计划、故事叙事以及当前拓扑状态，","推理出本次 tick 的**图结构变化**（边的数量增删）。",""]
        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        if world_time_str or tick_duration_str:
            parts.append("")
        parts.append("==== 当前 NPC 状态 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            if ent:
                eid = ent.entity_id
                inv = graph_engine.get_inventory_view(eid)
                inv_str = "、".join(f"{i['item_name']}x{i['quantity']}" for i in inv) if inv else "空手"
                zone_name = "?"
                for conn in ent.connected_entity_ids:
                    e = graph_engine.get_entity(conn)
                    if e and has_role(e.type_id, "region"):
                        zone_name = e.name
                        break
                parts.append(f"- {ent.name}（{ent.role or '?'}）@{zone_name} | "
                    f"体力{ent.attributes.get('vitality', 100):.0f}/100 "
                    f"饱腹{ent.attributes.get('satiety', 50):.0f}/100 "
                    f"心情{ent.attributes.get('mood', 50):.0f}/100 | 持有：{inv_str}")
            else:
                parts.append(f"- {npc_eid}（？）")
        parts.append("")
        parts.append("==== 每位 NPC 的本轮计划 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            parts.append(f"- {ent.name if ent else npc_eid}：{plan[:300]}")
        parts.append("")
        parts.append("==== 本轮故事叙事 ====")
        for i, story in enumerate(stories, 1):
            parts.append(f"--- 事件 {i} ---")
            parts.append(story)
        parts.append("")
        return "\n".join(parts)  # placeholder for fallback

    def _build_content_prompt(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
        feedback: str = "",
    ) -> str:
        """构建 LLM #4b prompt：内容层操作。"""
        if self._adapter:
            # 只包含故事中提到的 NPC（大幅减少 prompt 大小）
            story_text = " ".join(stories)
            from ..config.config_loader import get_all_label_mappings
            known_names = set(get_all_label_mappings().keys())
            story_names = set()
            for name in known_names:
                if name in story_text:
                    story_names.add(name)
            # 也保留 zone 和 item（它们不是 NPC 但需要状态上下文）
            all_ents = graph_engine.all_entities()
            zone_names = {e.name for e in all_ents if e.type_id == "region"}
            item_names = {e.name for e in all_ents if e.type_id == "thing"}
            active_names = story_names | zone_names | item_names

            entities = []
            active_plans = {}
            for npc_eid in npc_plans:
                ent = graph_engine.get_entity(npc_eid)
                if ent and ent.name in active_names:
                    entities.append(ent)
                    active_plans[npc_eid] = npc_plans[npc_eid]
            # 如果过滤后没有 NPC，回退到全部
            if not any(e.type_id == "actor" for e in entities):
                entities = [graph_engine.get_entity(eid) for eid in npc_plans if graph_engine.get_entity(eid)]
                active_plans = dict(npc_plans)

            return assemble(
                "llm4b_content", self._adapter, graph_engine,
                _caller="llm4b",
                time_str=world_time_str,
                tick_str=tick_duration_str,
                entities=entities,
                npc_plans=active_plans,
                stories=stories,
                feedback=feedback,
            )
        # 旧版回退
        parts = ["你是一个世界模拟引擎的**内容变化推理模块**（LLM #4b）。","","你的任务：根据 NPC 的计划、故事叙事以及已执行的拓扑变更，","推理出本次 tick 的**属性变化和近况摘要**。",""]
        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        if world_time_str or tick_duration_str:
            parts.append("")
        parts.append("==== 当前 NPC 状态 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            if ent:
                eid = ent.entity_id
                inv = graph_engine.get_inventory_view(eid)
                inv_str = "、".join(f"{i['item_name']}x{i['quantity']}" for i in inv) if inv else "空手"
                zone_name = "?"
                for conn in ent.connected_entity_ids:
                    e = graph_engine.get_entity(conn)
                    if e and has_role(e.type_id, "region"):
                        zone_name = e.name
                        break
                parts.append(f"- {ent.name}（{ent.role or '?'}）@{zone_name} | "
                    f"体力{ent.attributes.get('vitality', 100):.0f}/100 "
                    f"饱腹{ent.attributes.get('satiety', 50):.0f}/100 "
                    f"心情{ent.attributes.get('mood', 50):.0f}/100 | 持有：{inv_str}")
        parts.append("")
        parts.append("==== 每位 NPC 的本轮计划 ====")
        for npc_eid, plan in npc_plans.items():
            ent = graph_engine.get_entity(npc_eid)
            parts.append(f"- {ent.name if ent else npc_eid}：{plan[:300]}")
        parts.append("")
        parts.append("==== 本轮故事叙事 ====")
        for i, story in enumerate(stories, 1):
            parts.append(f"--- 事件 {i} ---")
            parts.append(story)
        parts.append("")
        return "\n".join(parts)  # placeholder for fallback

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
                    entry = {"op": "delta", "src": real_src, "tgt": real_tgt, "delta": delta}
                    # 透传 group 字段（LLM 输出可选）
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
        """从文本中提取 JSON

        处理顺序：
        1. markdown ```json...``` 代码块
        2. 找到第一个 { 或 [，截取到匹配的 } 或 ]
        3. 找到第一个 { 或 [（跳过前导非 JSON 文本，如 "当前拓扑状态显示...{...}"）
        4. 兜底返回原文本
        """
        # Step 1: markdown code blocks
        if '```' in text:
            blocks = text.split('```')
            for block in blocks:
                block = block.strip()
                if block.startswith('json'):
                    block = block[4:].strip()
                if block.startswith('[') or block.startswith('{'):
                    return block

        # Step 2: find first '{' or '[' in the text (跳过前导非 JSON 文本)
        start_idx = -1
        brace = ''
        for opener in ('{', '['):
            idx = text.find(opener)
            if idx != -1 and (start_idx == -1 or idx < start_idx):
                start_idx = idx
                brace = opener

        if start_idx >= 0:
            close = '}' if brace == '{' else ']'
            stack = []
            for i in range(start_idx, len(text)):
                ch = text[i]
                if ch == brace:
                    stack.append(ch)
                elif ch == close:
                    if stack:
                        stack.pop()
                        if not stack:
                            return text[start_idx:i + 1]

        return text
