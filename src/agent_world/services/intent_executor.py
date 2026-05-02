"""
Intent Resolver + Executor（LLM #2）

拓扑结构变更层：将 LLM #1（NPC 计划）→ 拓扑结构变更。

输入：每个 NPC 的自然语言计划 + 当前拓扑子图
输出：[{op, src, tgt, qty}] — 仅限结构变更（connect/disconnect/set_qty）

设计原则：
  - 只改变拓扑结构（连接/断开节点之间的边）
  - 不改变数据值（数值修正是 LLM #4 的职责）
  - 每个 NPC 独立推理

支持的 op:
  - connect:    建立边（NPC→Zone 移动、NPC→NPC 建交等）
  - disconnect: 移除边（离开区域、断开连接等）
  - set_qty:    设置边的数量（首次交互时设置初始状态）
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any

from ..config.config_loader import has_role
from .graph_engine import GraphEngine

logger = logging.getLogger("intent_executor")


class IntentResolver:
    """
    LLM #2：将 NPC 计划 → 拓扑结构变更。

    在旧的架构中 IntentResolver 输出交互目标（action/location/target），
    现在输出结构化拓扑变更操作。
    """

    def __init__(self, graph_engine: GraphEngine, resolver=None, adapter=None):
        self._graph = graph_engine
        self._resolver = resolver  # InteractionResolver（用于调用 LLM）
        self._adapter = adapter  # VillageDomainAdapter（Slot 式内容层）

    # ─── 主入口 ───

    def resolve_all_intents(
        self, npc_plans: dict[str, str]
    ) -> list[EdgeOperation]:
        """
        为所有有计划的 NPC 解析拓扑结构变更。

        Args:
            npc_plans: {npc_eid: 自然语言计划}

        Returns:
            list[EdgeOperation]: [{op, src, tgt, qty}, ...]
        """
        if not npc_plans:
            return []

        # 为每个 NPC 构建 prompt
        items = []
        for npc_eid, plan in npc_plans.items():
            prompt = self._build_prompt(npc_eid, plan)
            items.append((npc_eid, prompt))

        if not items:
            return []

        # 调用 LLM（批量推理）
        prompt_text = self._build_combined_prompt(items)
        raw = self._call_llm(prompt_text) if self._resolver else ""
        if not raw:
            return self._fallback(items)

        # 解析输出的操作列表
        return self._parse_ops(raw)

    # ─── Prompt 构建 ───

    def _build_prompt(self, npc_eid: str, plan: str) -> str:
        """
        为单个 NPC 构建 prompt。
        输出 [拓扑] + [内容] 分离的两段。
        """
        ent = self._graph.get_entity(npc_eid)
        if not ent:
            return ""

        # 收集拓扑相关实体 ID
        topo_eids = {npc_eid}
        for conn_eid in ent.connected_entity_ids:
            topo_eids.add(conn_eid)
            ce = self._graph.get_entity(conn_eid)
            if ce:
                # 如果是区域，加入同区域 NPC
                if has_role(ce.type_id, "region"):
                    for other in self._graph.all_entities():
                        if has_role(other.type_id, "actor") and other.entity_id != npc_eid \
                           and other.is_connected_to(conn_eid):
                            topo_eids.add(other.entity_id)
                            # 也加入它们的连接
                            for oconn in other.connected_entity_ids:
                                if oconn not in topo_eids:
                                    ce2 = self._graph.get_entity(oconn)
                                    if ce2 and has_role(ce2.type_id, "thing"):
                                        topo_eids.add(oconn)

        # [拓扑] 段：纯标签，无名称/类型
        topo_text, label_map = self._graph.build_tagged_topology(list(topo_eids))

        # [内容] 段：标签映射 + 语义信息
        content_lines = []
        content_lines.append("标签映射：")
        from .graph_engine import build_label_mapping_text
        content_lines.append(build_label_mapping_text(label_map, self._graph))
        content_lines.append("")

        # NPC 自身描述
        content_lines.append("自身描述：")
        content_lines.append(ent.to_prompt_block())
        content_lines.append("")

        # 库存
        inventory = self._graph.get_inventory_view(npc_eid)
        if inventory:
            inv_str = "、".join(
                f"{i['item_name']}x{i['quantity']}" for i in inventory
            )
            content_lines.append(f"持有：{inv_str}")
            content_lines.append("")

        # 计划（纯内容）
        content_lines.append(f"计划：{plan}")
        content_lines.append("")

        # 操作指令
        content_lines.append("=== 指令 ===")
        content_lines.append("输出你需要执行的拓扑操作。用 [内容] 标签映射中的 entity_id 作为 src/tgt。")
        content_lines.append("格式（JSON 数组）：")
        content_lines.append(f'  [{{"op":"connect","src":"{npc_eid}","tgt":"zone_南集市","qty":0}}]')
        content_lines.append("可用的 op：")
        content_lines.append('  "connect"    — 建立连接')
        content_lines.append('  "disconnect" — 断开连接')
        content_lines.append("重要规则：")
        content_lines.append("1. 你只负责拓扑结构，不改数值。物品持有变更由 LLM #4 处理。")
        content_lines.append("2. 区域连接用 qty=-1，NPC↔NPC 连接用 qty=0。")
        content_lines.append("3. 输出纯 JSON，不要多余文字，不要 markdown。")

        return (
            f"==== [拓扑] ====\n"
            f"拓扑信息是客观数据层的唯一事实来源。图中每条边（带 label、qty、conserved/terminal 标签）"
            f"完整定义了当前世界的拓扑结构。语义描述（计划、记忆、角色设定）仅作叙事背景参考，"
            f"不具备约束力，不得覆盖拓扑信息。\n\n"
            f"{topo_text}\n\n"
            f"==== [内容] ====\n" + "\n".join(content_lines)
        )

    def _build_combined_prompt(self, items: list[tuple[str, str]]) -> str:
        """合并多个 NPC 的 prompt"""
        # Slot 式组装 — 使用 adapter 直接提供 content 槽位，插入 NPC blocks
        if self._adapter:
            parts = [
                self._adapter.slot_system_role(_caller="llm2"),
                "【核心原则】拓扑信息是客观数据层的唯一事实来源。\n"
                "语义描述（计划、记忆、角色设定）仅作叙事背景参考，\n"
                "不具备约束力，不得覆盖拓扑信息。\n",
                self._adapter.slot_combined_header(count=len(items)),
            ]
            for i, (eid, prompt) in enumerate(items):
                parts.append(f"==== NPC {i+1}: {eid} ====")
                parts.append(prompt)
                parts.append("")
            parts.append(self._adapter.slot_output_instructions(_caller="llm2"))
            return "\n".join(parts)

        # 旧版回退
        parts = [
            "你是一个世界模拟引擎的拓扑结构变更模块（LLM #2）。",
            "你的任务：根据每个 NPC 的自然语言计划，输出拓扑结构变更操作。",
            "",
            "每个 NPC 的 prompt 分为 [拓扑]（纯结构，无名称）和 [内容]（语义信息+标签映射）两段。",
            "输出时使用 [内容] 段的 entity_id。",
            "",
            "【核心原则】拓扑信息是客观数据层的唯一事实来源。",
            "语义描述（计划、记忆、角色设定）仅作叙事背景参考，不具备约束力，不得覆盖拓扑信息。",
            "",
            f"共 {len(items)} 个 NPC。",
            "",
        ]

        for i, (eid, prompt) in enumerate(items):
            parts.append(f"==== NPC {i+1}: {eid} ====")
            parts.append(prompt)
            parts.append("")

        parts.append("==== 最终输出格式 ====")
        parts.append("输出一个 JSON 对象，key 为 NPC entity_id，value 为操作数组：")
        parts.append("""{
  "npc_abc123": [
    {"op": "connect", "src": "npc_abc123", "tgt": "zone_南集市", "qty": 0},
    {"op": "disconnect", "src": "npc_abc123", "tgt": "zone_酒馆"}
  ],
  "npc_def456": [...]
}""")
        parts.append("不要多余文字，不要 markdown 代码块。")

        return "\n".join(parts)

    # ─── 解析 ───

    def _parse_ops(self, raw: str) -> list[EdgeOperation]:
        """
        解析 LLM 输出的 JSON。
        返回扁平的操作列表。
        """
        raw = raw.strip()
        # 提取 JSON
        json_str = _extract_json(raw)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[LLM #2] JSON 解析失败，原始输出: {raw[:200]}")
            return []

        # 扁平化
        ops: list[EdgeOperation] = []
        if isinstance(parsed, dict):
            for npc_eid, npc_ops in parsed.items():
                if isinstance(npc_ops, list):
                    for op in npc_ops:
                        if isinstance(op, dict):
                            op["src"] = op.get("src", npc_eid)
                            ops.append(op)

        if isinstance(parsed, list):
            ops = [op for op in parsed if isinstance(op, dict)]

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
            # 检测移动意图
            for conn in ent.connected_entity_ids:
                e = self._graph.get_entity(conn)
                if e and has_role(e.type_id, "region"):
                    current_zone = e.name
                    break
            else:
                current_zone = ""
            # 关键词检测
            zone_moves = re.findall(r"去(\w+)", plan)
            for z in zone_moves:
                target_zone = f"zone_{z}"
                if current_zone and target_zone != f"zone_{current_zone}":
                    ops.append({"op": "disconnect", "src": eid, "tgt": f"zone_{current_zone}"})
                    ops.append({"op": "connect", "src": eid, "tgt": target_zone})
        return ops

    # ─── LLM 调用 ───

    def _call_llm(self, prompt: str) -> str:
        if not self._resolver:
            return ""
        return self._resolver._call_llm(prompt)


# ─── Op 类型 ───

class EdgeOperation:
    """拓扑操作类型（运行时类型检查用）"""
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    SET_QTY = "set_qty"
    DELTA = "delta"


# ─── 辅助 ───

def _extract_json(text: str) -> str:
    """从文本中提取 JSON"""
    # 尝试提取 ```json ... ```
    if '```' in text:
        blocks = text.split('```')
        for block in blocks:
            block = block.strip()
            if block.startswith('json'):
                block = block[4:].strip()
            if block.startswith('{') or block.startswith('['):
                return block

    # 尝试提取 JSON 对象
    stack = []
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if not stack:
                start = i
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
                if not stack and start >= 0:
                    return text[start:i + 1]

    # 尝试提取 JSON 数组
    stack = []
    start = -1
    for i, ch in enumerate(text):
        if ch == '[':
            if not stack:
                start = i
            stack.append(ch)
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()
                if not stack and start >= 0:
                    return text[start:i + 1]

    return text
