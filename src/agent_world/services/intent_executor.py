"""
Intent Resolver + Executor（LLM #2）

拓扑结构变更层：将 LLM #1（NPC 计划）→ 拓扑结构变更。

输入：每个目标节点的计划 + 当前拓扑子图（已翻译）
输出：[{op, src, tgt, qty}] — 仅限结构变更（connect/disconnect）

设计原则：
  - 纯图拓扑视角：prompt 中不出现具体领域术语（Actor/Zone/Item）
  - 通过标签映射（{A} → 实体ID）连接抽象层与实际数据层
  - 翻译层（prompt_assembler）在拓扑输入中提供连接引导
  - 输出操作中的 src/tgt 使用抽象标签，解析时翻译回实体 ID

支持的 op:
  - connect:    建立边
  - disconnect: 移除边
  - set_qty:    设置边的数量
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any

from ..config.config_loader import has_role
from .graph_engine import GraphEngine, build_label_mapping_text

logger = logging.getLogger("intent_executor")


class IntentResolver:
    """
    LLM #2：将自然语言计划 → 图拓扑结构变更。
    """

    def __init__(self, graph_engine: GraphEngine, resolver=None, adapter=None):
        self._graph = graph_engine
        self._resolver = resolver  # InteractionResolver（用于调用 LLM）
        self._adapter = adapter  # DomainAdapter（Slot 式内容层）

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

        # 构建一张全局标签映射（所有节点共享）
        global_label_map = self._build_global_label_map()

        # 构建每个节点的 prompt（使用全局标签）
        items = []
        for eid, plan in npc_plans.items():
            prompt = self._build_prompt(eid, plan, global_label_map)
            if not prompt:
                continue
            items.append((eid, prompt))

        if not items:
            return []

        # 合并 LLM prompt
        prompt_text = self._build_combined_prompt(items, global_label_map)

        # 调用 LLM
        raw = self._call_llm(prompt_text) if self._resolver else ""
        if not raw:
            return self._fallback(items)

        # 解析 + 标签反查（单张全局映射表）
        return self._parse_ops(raw, global_label_map)

    # ─── Prompt 构建 ───

    def _build_prompt(self, node_eid: str, plan: str,
                      global_label_map: dict[str, str] | None = None) -> str:
        """
        为单个节点构建 domain-agnostic prompt。
        使用全局标签映射（所有节点共享同一套）。
        返回 prompt_text。
        """
        ent = self._graph.get_entity(node_eid)
        if not ent:
            return ""

        # 收集拓扑相关实体 ID（该节点邻域子图）
        topo_eids = {node_eid}
        for conn_eid in ent.connected_entity_ids:
            topo_eids.add(conn_eid)
            ce = self._graph.get_entity(conn_eid)
            if ce:
                # 如果是区域类型节点，加入同区域的其他 actor 节点
                if has_role(ce.type_id, "region"):
                    for other in self._graph.all_entities():
                        if has_role(other.type_id, "actor") and other.entity_id != node_eid \
                           and other.is_connected_to(conn_eid):
                            topo_eids.add(other.entity_id)
                            for oconn in other.connected_entity_ids:
                                if oconn not in topo_eids:
                                    ce2 = self._graph.get_entity(oconn)
                                    if ce2 and has_role(ce2.type_id, "thing"):
                                        topo_eids.add(oconn)

        # [拓扑] 段：使用全局标签（非逐节点分配）
        topo_text, _label_map = self._graph.build_tagged_topology(
            list(topo_eids), global_label_map=global_label_map
        )

        # 获取当前节点在全局标签中的抽象标签
        node_label = None
        if global_label_map:
            reverse = {v: k for k, v in global_label_map.items()}
            node_label = reverse.get(node_eid)

        # [内容] 段：标签映射 + 属性信息
        content_lines = []
        content_lines.append("标签映射：")
        if global_label_map:
            content_lines.append(build_label_mapping_text(global_label_map, self._graph))
        content_lines.append("")

        # 节点属性
        content_lines.append("自身描述：")
        content_lines.append(ent.to_prompt_block())
        content_lines.append("")

        # 持有
        inventory = self._graph.get_inventory_view(node_eid)
        if inventory:
            inv_str = "、".join(
                f"{i['item_name']}x{i['quantity']}" for i in inventory
            )
            content_lines.append(f"持有：{inv_str}")
            content_lines.append("")

        # 计划
        content_lines.append(f"计划：{plan}")
        content_lines.append("")

        # 指令 — 纯图拓扑术语
        content_lines.append("=== 指令 ===")
        content_lines.append("输出此节点需要执行的图拓扑操作。")
        content_lines.append("使用标签映射中的标签作为 src/tgt。")
        content_lines.append("所有节点共享同一套全局标签，标签在全图中含义一致。")
        content_lines.append("")
        if node_label:
            content_lines.append(f"格式示例（在最终 JSON 的 operations 对象中）：")
            content_lines.append(f'  {{"op":"connect","src":"{{{node_label}}}","tgt":"{{A}}","qty":-1}}')
        content_lines.append("可用的 op：")
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

    def _build_global_label_map(self) -> dict[str, str]:
        """
        全局标签映射：为图中所有实体分配全局唯一的抽象标签（A-Z, a-z）。
        所有 NPC 子图共享同一套标签，LLM 输出时用 {A} 引用任意外部节点。
        返回 {label: entity_id}。
        """
        result: dict[str, str] = {}
        # 按类型排序：区域优先，NPC 其次，物品最后
        region_ents = []
        npc_ents = []
        item_ents = []
        other_ents = []
        for ent in self._graph.all_entities():
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

        # 分配 A-Z, 然后 a-z（覆盖 52 个实体，绰绰有余）
        for i, ent in enumerate(all_sorted):
            if i < 26:
                label = chr(65 + i)  # A-Z
            elif i < 52:
                label = chr(97 + i - 26)  # a-z
            else:
                # 超过 52 个时用 AA, AB...（当前设计不会触及）
                label = chr(65 + (i - 26) // 26) + chr(65 + (i - 26) % 26)
            result[label] = ent.entity_id

        return result

    def _build_combined_prompt(self, items: list[tuple[str, str]],
                               global_label_map: dict[str, str] | None = None) -> str:
        """合并多个节点的 prompt"""
        if self._adapter:
            # 通过 slot 机制获取全局概览
            from .prompt_assembler import _render_topo_slot
            global_overview = _render_topo_slot("global_overview", engine=self._graph, global_label_map=global_label_map)
            parts = [
                # 系统角色（domain-agnostic）
                "你是一个图拓扑结构变更模块。\n"
                "你的任务：根据每个目标节点的邻域连通图及语义信息，\n"
                "输出该节点所需的图结构变更操作（建立或断开节点间连接）。\n",
                "【核心原则】图拓扑信息是客观数据层的唯一事实来源。\n"
                "语义描述（状态、计划、记忆）仅作辅助参考，"
                "不具备约束力，不得覆盖拓扑信息。\n",
                "",
            ]
            if global_overview:
                parts.append(global_overview)
                parts.append("")
            parts.append(f"共 {len(items)} 个节点需要结构变更。\n")
            for i, (eid, prompt_text) in enumerate(items):
                parts.append(f"==== 节点 {i+1}: {eid} ====")
                parts.append(prompt_text)
                parts.append("")
            # 最终输出格式（domain-agnostic，与 LLM #4a 的方案 A 一致）
            parts.append(
                "==== 最终输出格式 ====\n"
                "输出一个 JSON 对象，包含两个字段：\n"
                "1. \"thinking\" (string)：你的推理分析，不受格式限制\n"
                "2. \"operations\" (object)：key 为节点标签，value 为操作数组\n"
                "\n"
                "示例：\n"
                "{\n"
                '  "thinking": "该节点计划离开当前区域，需要连接到全局列表中的目标节点...",\n'
                '  "operations": {\n'
                '    "{A}": [\n'
                '      {"op":"connect","src":"{A}","tgt":"{M}","qty":-1},\n'
                '      {"op":"disconnect","src":"{A}","tgt":"{C}"}\n'
                '    ],\n'
                '    "{B}": []\n'
                '  }\n'
                "}\n"
                "只输出这个 JSON，不要多余文字，不要 markdown 代码块。\n"
            )
            return "\n".join(parts)

        # 旧版回退（无 adapter）
        parts = [
            "你是一个图拓扑结构变更模块。",
            "你的任务：根据每个目标节点的邻域连通图及语义信息，输出该节点所需的图结构变更操作。",
            "",
            f"共 {len(items)} 个节点需要结构变更。",
            "",
        ]
        for i, (eid, prompt_text) in enumerate(items):
            parts.append(f"==== 节点 {i+1}: {eid} ====")
            parts.append(prompt_text)
            parts.append("")
        parts.append(
            "==== 最终输出格式 ====\n"
            "输出 JSON 对象，key 为节点标签，value 为操作数组。\n"
            "不要多余文字，不要 markdown 代码块。"
        )
        return "\n".join(parts)

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        if not self._resolver:
            return ""
        return self._resolver._call_llm(prompt)

    # ─── 解析 ───

    def _parse_ops(
        self, raw: str, global_tag_to_eid: dict[str, str] | None = None
    ) -> list[EdgeOperation]:
        """
        解析 LLM 输出的 JSON，将抽象标签翻译回实体 ID。

        Args:
            raw: LLM 原始输出
            global_tag_to_eid: {label: entity_id} 全局标签映射

        Returns:
            list[EdgeOperation]
        """
        tag_to_eid: dict[str, str] = global_tag_to_eid or {}

        raw = raw.strip()
        json_str = _extract_json(raw)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[LLM #2] JSON 解析失败，原始输出: {raw[:200]}")
            return []

        def _resolve(val: str) -> str:
            """将抽象标签（如 {A}）翻译回实体 ID"""
            if not val:
                return val
            stripped = val.strip("{}")
            if stripped in tag_to_eid:
                return tag_to_eid[stripped]
            return val  # 保留原值，可能是实体 ID 本身

        # 扁平化 — 先检查是否有 operations 字段（方案 A 格式）
        ops: list[EdgeOperation] = []
        if isinstance(parsed, dict):
            # 方案 A：{"thinking": ..., "operations": {"{A}": [...], ...}}
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
                # 直接键值：{"{A}": [...], ...}
                for key, npc_ops in parsed.items():
                    resolved_key = _resolve(key)
                    if isinstance(npc_ops, list):
                        for op in npc_ops:
                            if isinstance(op, dict):
                                op["src"] = _resolve(op.get("src", key))
                                op["tgt"] = _resolve(op.get("tgt", ""))
                                ops.append(op)

        if isinstance(parsed, list):
            ops = []
            for op in parsed:
                if isinstance(op, dict):
                    op["src"] = _resolve(op.get("src", ""))
                    op["tgt"] = _resolve(op.get("tgt", ""))
                    ops.append(op)

        # 验证
        valid_ops = []
        for op in ops:
            if op.get("op") in ("connect", "disconnect", "set_qty"):
                if op.get("src") and op.get("tgt"):
                    valid_ops.append(op)

        logger.info(f"[LLM #2] 解析到 {len(valid_ops)}/{len(ops)} 有效操作")
        return valid_ops

    # ─── 降级 ───

    def _fallback(self, items: list[tuple[str, str]]) -> list[EdgeOperation]:
        """LLM 不可用时，根据计划关键词做简单推理"""
        ops = []
        for eid, plan in items:
            ent = self._graph.get_entity(eid)
            if not ent:
                continue
            for conn in ent.connected_entity_ids:
                e = self._graph.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    current_zone = e.name
                    # 检查是否需要移动
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


# ═══════════════════════════════════════════
# 类型别名
# ═══════════════════════════════════════════

EdgeOperation = dict[str, Any]


def _extract_json(text: str) -> str:
    """
    从文本中提取第一个 JSON 对象或 JSON 数组。
    跳过前导非 JSON 文本，找到第一个 { 或 [，再匹配对应的闭合 ]}。

    增强版：清除调 markdown 代码块标记和多余的引号。
    """
    # 清理 markdown
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    # 找到第一个 { 或 [
    start_idx = -1
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            start_idx = i
            break

    if start_idx == -1:
        return text

    text = text[start_idx:]

    # 匹配括号
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
