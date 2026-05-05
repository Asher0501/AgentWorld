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

    def __init__(self, resolver=None, adapter=None, engine=None):
        self._resolver = resolver
        self._adapter = adapter
        self._engine = engine  # PipelineEngine（提供 IO 日志 + 计时）

    # ─── LLM 调用统一入口（异步）───

    async def _call_llm_async(self, prompt: str, stage_label: str) -> str:
        """异步 LLM 调用，通过 engine 或 resolver。"""
        if self._engine:
            return await self._engine.call_llm_async(prompt, stage_label)
        import asyncio
        return await asyncio.to_thread(self._resolver._call_llm, prompt)

    # ════════════════════════════════════════════════════════════════
    # LLM #4a: 拓扑层操作 (delta / system_delta / recipe)
    # ════════════════════════════════════════════════════════════════

    async def resolve_topology_changes_async(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
        feedback: str = "",
        topo_pool: set[str] | None = None,
        label_map: dict[str, str] | None = None,
    ) -> list[dict]:
        """异步版 LLM #4a。与 resolve_topology_changes 共享 prompt/解析逻辑。"""
        if not stories or not npc_plans:
            return []

        # BFS 遍历（同同步版）
        if topo_pool is None:
            from collections import deque
            topo_pool = set()
            for npc_eid in npc_plans:
                if npc_eid in topo_pool:
                    continue
                queue: deque[str] = deque([npc_eid])
                visited: set[str] = set()
                while queue:
                    cur = queue.popleft()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    topo_pool.add(cur)
                    cur_ent = graph_engine.get_entity(cur)
                    if not cur_ent:
                        continue
                    for conn_eid in cur_ent.connected_entity_ids:
                        if conn_eid in visited:
                            continue
                        conn_ent = graph_engine.get_entity(conn_eid)
                        if not conn_ent:
                            continue
                        if conn_ent.is_leaf:
                            topo_pool.add(conn_eid)
                            continue
                        if conn_ent.no_same_type and conn_ent.type_id == cur_ent.type_id:
                            topo_pool.add(conn_eid)
                            continue
                        queue.append(conn_eid)

        if label_map is None:
            _, label_map = graph_engine.build_tagged_topology(list(topo_pool))

        prompt = self._build_topo_prompt(
            npc_plans=npc_plans,
            stories=stories,
            graph_engine=graph_engine,
            world_time_str=world_time_str,
            tick_duration_str=tick_duration_str,
            feedback=feedback,
            topo_pool=topo_pool,
        )

        if not self._resolver:
            logger.warning("[LLM #4a] 无 LLM resolver")
            return []

        raw = await self._call_llm_async(prompt, "topo_delta")
        self._last_raw_topo_response = raw
        if not raw or not raw.strip():
            return []

        if self._adapter:
            ops = self._adapter.parse_llm_output(
                stage=4, raw_text=raw, label_map=label_map, graph=graph_engine,
            )
        else:
            ops = self._parse_topo_output(raw, graph_engine)

        logger.info(f"[LLM #4a] 解析到 {len(ops)} 个拓扑操作")
        return ops

    async def resolve_attr_and_recent_async(
        self,
        npc_plans: dict[str, str],
        stories: list[str],
        graph_engine: GraphEngine,
        world_time_str: str | None = None,
        tick_duration_str: str | None = None,
        feedback: str = "",
    ) -> tuple[list[dict], dict[str, str]]:
        """异步版 LLM #4b。与 resolve_attr_and_recent 共享 prompt/解析逻辑。"""
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

        raw = await self._call_llm_async(prompt, "content_update")
        self._last_raw_attr_response = raw
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
        topo_pool: set[str] | None = None,
    ) -> str:
        """构建 LLM #4a prompt：拓扑层操作。"""
        entities = []
        if topo_pool is None:
            topo_pool = set()
        for npc_eid in npc_plans:
            ent = graph_engine.get_entity(npc_eid)
            if ent:
                entities.append(ent)
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
        zone_names = {e.name for e in all_ents if has_role(e.type_id, "region")}
        item_names = {e.name for e in all_ents if has_role(e.type_id, "thing")}
        active_names = story_names | zone_names | item_names

        entities = []
        active_plans = {}
        for npc_eid in npc_plans:
            ent = graph_engine.get_entity(npc_eid)
            if ent and ent.name in active_names:
                entities.append(ent)
                active_plans[npc_eid] = npc_plans[npc_eid]
        # 如果过滤后没有 NPC，回退到全部
        if not any(has_role(e.type_id, "actor") for e in entities):
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
        """将实体名/ID 解析为 entity_id"""
        from ..config.config_loader import get_all_prefixes
        if any(name_or_id.startswith(pfx) for pfx in get_all_prefixes()):
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
