"""
PromptAssembler — Slot 式 Prompt 组装器。

每个 LLM prompt 由有序 slot 列表定义。每个 slot 分属三类提供者：
  - "content"  → DomainAdapter 的 `get_slot(name)` 方法
  - "topology" → 引擎固定渲染
  - "runtime"  → 运行时信息（时间等）

换域 = 换 DomainAdapter。slot 列表几乎不变。
"""
from __future__ import annotations
from typing import Any


# ─── Slot 列表定义 ───────────────────────────────────
# 每条 = (slot_name, provider_type)
# "content" → adapter.get_slot(slot_name, **kw) 提供
# "topology" → _render_topo_slot(slot_name, engine, **kw)
# "runtime" → 运行时（时间字符串） 由组装器直接处理

PROMPT_TEMPLATES: dict[str, list[tuple[str, str]]] = {

    "llm1_plan": [
        ("time_info",            "runtime"),
        ("survival_needs",       "content"),
        ("entity_identity",      "content"),
        ("personality",          "content"),
        ("recent_info",          "content"),
        ("inventory",            "content"),
        ("zone_others",          "content"),
        ("available_recipes",    "content"),
        ("label_mapping",        "topology"),
        ("topology_constraints_plan", "topology"),
        ("decision_guidance",    "content"),
    ],

    "llm2_structure": [
        ("system_role",          "content"),
        ("core_principle",       "topology"),
        ("combined_header",      "content"),
        ("npc_block",            "content"),   # 每个 NPC 一组 [拓扑]+[内容]
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

    "llm4b_content": [
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
}


# ─── 拓扑层渲染函数（引擎固定，跨域不变） ───────────

def _render_topo_slot(slot_name: str, engine, **kw) -> str:
    if slot_name == "core_principle":
        return (
            "【核心原则】拓扑信息是客观数据层的唯一事实来源。\n"
            "语义描述（计划、记忆、角色设定）仅作叙事背景参考，\n"
            "不具备约束力，不得覆盖拓扑信息。\n\n"
        )

    if slot_name == "topology_principle":
        return (
            "拓扑信息是客观数据层的唯一事实来源。图中每条边（带 label、qty、conserved/terminal 标签）\n"
            "完整定义了当前世界的拓扑结构。语义描述（计划、记忆、角色设定）仅作叙事背景参考，\n"
            "不具备约束力，不得覆盖拓扑信息。\n\n"
        )

    if slot_name == "topology_graph":
        topo_eids = kw.get("topo_eids", [])
        if engine:
            topo_text, _ = engine.build_tagged_topology(topo_eids)
            return topo_text + "\n\n"
        return "(拓扑图不可用)\n\n"

    if slot_name in ("label_mapping", "label_mapping_topo"):
        from .graph_engine import build_label_mapping_text
        label_map = kw.get("label_map", {})
        # 未显式传入 label_map 时，从 topo_eids 重建
        if not label_map and engine:
            topo_eids = kw.get("topo_eids", [])
            _, label_map = engine.build_tagged_topology(topo_eids)
        include_tags = slot_name == "label_mapping_topo"
        if engine:
            return build_label_mapping_text(label_map, engine, include_tags=include_tags) + "\n\n"
        return "(映射表不可用)\n\n"

    if slot_name == "topology_constraints":
        from ..config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        parts = []
        if not allow_unreg:
            parts.append(
                "□ **强制规则：每条操作的 src 和 tgt 必须引用当前图中已存在的节点。**\n"
                "  — 标签映射表列出了图中所有节点。不在该表中的名称不能出现在 src 或 tgt 中。\n"
                "  — 语义描述中出现的名称若不在映射表中，不得用作 src 或 tgt。\n"
                "  — 若目标节点在图中不存在，则放弃该操作。\n"
                "  — 没有例外。不存在的节点不能通过操作创建。\n"
            )
        parts.append(
            "□ terminal 标签的节点：不允许新增以该节点为 src 或 tgt 的边。\n"
            "□ 每条操作独立判断，可以同时存在，不是互斥：\n"
            "    · src→tgt 边数量减少，同时反向边数量增加（Σ=0） → delta 两条\n"
            "    · 边数量变化的一端在系统边界外               → system_delta 一条（跳过守恒）\n"
            "    · 一组 src→tgt 边的数量减少、另一组增加（内部平衡） → recipe 一条\n"
            "  **同一(src, tgt)可以同时出现在 delta 和 system_delta 中**\n"
        )
        parts.append(
            "□ 可选 group 字段：将同一批交易的 delta 操作标记为同一 group 标识。\n"
            "    · 系统会按 (item, group) 分别校验守恒，一组不平衡不影响其他组。\n"
            "    · 无 group 的操作归入全局组（等价于当前行为）。\n"
            "    · 示例：{\"op\":\"delta\",\"src\":\"田嫂\",\"tgt\":\"金币\",\"delta\":3,\"group\":\"g1\"}\n"
        )
        parts.append(
            "□ delta 操作描述的是两个节点之间的直接数量变化，非路径路由。\n"
            "  — 若两节点都与同一枢纽 Hub 相连，需交换某标签的数量时，\n"
            "    应直接在两者之间建立 delta 操作，而非分别与 Hub 做两段 delta。\n"
            "  — 例：A ↔ H ↔ C 时，若 A 要给 C 数量 5 的某标签，\n"
            "    应直接 src=A→tgt=C delta=-5（配合 src=C→tgt=A delta=+5），\n"
            "    而非 src=A→H⁻5 + H→C⁺5。Hub 是结构共享点，不是数量路由节点。\n"
        )
        return "\n".join(parts) + "\n\n"

    if slot_name == "topology_constraints_recent":
        from ..config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        if not allow_unreg:
            return (
                "□ **强制规则：recent_info 中不得引用图中不存在的实体。**\n"
                "  — 标签映射表列出了所有可用的实体。\n"
                "  — 不在映射表中的实体名称不得出现在 recent_info 中。\n"
                "  — 故事中出现的虚构人物（如客人、路人、买家等）不应被写入 recent_info。\n"
                "  — recent_info 只能引用映射表中已有的 NPC、物品、区域。\n"
                "  — 没有例外。\n\n"
            )
        return ""

    if slot_name == "topology_constraints_plan":
        from ..config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        if not allow_unreg:
            return (
                "□ **强制规则：计划中不得引用图中不存在的实体。**\n"
                "  — 标签映射表列出了所有可用的实体。\n"
                "  — 计划中的交互对象必须来自映射表已有实体。\n"
                "  — 不得创造新的角色或实体（如虚构买家、顾客、脚夫等）。\n"
                "  — 没有例外。\n\n"
            )
        return ""

    if slot_name == "topology_constraints_abstract":
        from ..config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        parts = []
        if not allow_unreg:
            parts.append(
                "□ **强制规则：故事中不得出现图中不存在的实体。**\n"
                "  — 标签映射表列出了所有可用的实体。\n"
                "  — 故事必须在映射表已有实体范围内创作。\n"
                "  — 不得创造新的角色或实体。\n"
                "  — 没有例外。\n"
            )
        parts.append(
            "□ 故事中角色的交互范围受拓扑边约束：\n"
            "  — 若 {A} 无 → {Y} 边，则 {A} 对应的角色在故事中不能与 {Y} 对应角色或物品发生交互。\n"
            "  — 若 {A} → {Y} 边上的 qty 为 0，则 {A} 无法使用或交付 {Y}。\n"
        )
        return "\n".join(parts) + "\n\n"

    # 分隔线槽位
    if slot_name == "gap_content_header":
        return "═" * 65 + "\n内容层 — 场景中的角色状态与库存\n" + "═" * 65 + "\n\n"
    if slot_name == "gap_topology_header":
        return "\n" + "═" * 65 + "\n拓扑层 — 客观数据约束（故事不得突破此图）\n" + "═" * 65 + "\n\n"
    if slot_name == "gap_event_header":
        return "\n" + "═" * 65 + "\n叙事输入 — 本 tick 触发的交互事件\n" + "═" * 65 + "\n"
    if slot_name == "gap_output_header":
        return "\n" + "═" * 65 + "\n输出\n" + "═" * 65 + "\n\n"
    if slot_name == "topology_section_header":
        return "==== [拓扑] 当前结构 ====\n"

    return f"<!-- unknown topology slot: {slot_name} -->\n"


def _render_runtime_slot(slot_name: str, **kw) -> str:
    if slot_name == "time_info":
        time_str = kw.get("time_str", "")
        tick_str = kw.get("tick_str", "")
        parts = []
        if time_str:
            parts.append(f"当前时间：{time_str}")
        if tick_str:
            parts.append(f"本 tick 时长：{tick_str}")
        return ("\n".join(parts) + "\n\n") if parts else ""
    return ""


# ─── 主组装函数 ──────────────────────────────────────

def assemble(
    template_name: str,
    adapter=None,
    engine=None,
    **kw,
) -> str:
    """
    按 slot 列表组装完整的 LLM prompt。

    Args:
        template_name: PROMPT_TEMPLATES 中的 key
        adapter: DomainAdapter 实例（提供 content 槽位）
        engine: GraphEngine 实例（可选，提供 topology 槽位）
        **kw: 透传给各槽位的额外参数

    Returns:
        完整 prompt 字符串
    """
    slots = PROMPT_TEMPLATES.get(template_name)
    if slots is None:
        raise ValueError(f"未知模板: {template_name}")

    parts = []
    for slot_name, provider in slots:
        if provider == "content":
            text = adapter.render_slot(slot_name, engine=engine, **kw)
        elif provider == "topology":
            text = _render_topo_slot(slot_name, engine, **kw)
        elif provider == "runtime":
            text = _render_runtime_slot(slot_name, **kw)
        else:
            text = f"<!-- unknown provider: {provider} -->\n"
        if text:
            parts.append(text)

    return "\n".join(parts)
