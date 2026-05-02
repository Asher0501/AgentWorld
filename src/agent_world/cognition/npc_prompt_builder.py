"""
NPC Prompt Builder —— 为每个 NPC 构建推理上下文（LLM #1 输入）。

每个 NPC 的 prompt 包含：
  - 自身属性（体力/饱腹/心情/位置/角色）
  - 最近经历（记忆）
  - 性格标签
  - 库存
  - 同区域 NPC

LLM #1 只读内容（属性/库存/近况），不读拓扑。
拓扑信息由 LLM #2 独立处理。
输出自然语言计划，供 LLM #2 解析为拓扑结构变更。
"""

from __future__ import annotations

from typing import Any
from ..entities.base_entity import Entity


def build_one_npc_prompt(
    npc_entity: Entity,
    npc_name: str,
    npc_role: str,
    memories: list[dict],
    personality_tags: list[str],
    inventory: list[dict],
    zone_npcs: list[dict] | None = None,
    world_time_str: str | None = None,
    tick_duration_str: str | None = None,
    recipes: list[dict] | None = None,
) -> str:
    """
    为一个 NPC 构建完整的推理 prompt。

    Args:
        npc_entity: 图引擎中的 NPC Entity
        npc_name: NPC 显示名称
        npc_role: 角色
        memories: 最近记忆列表
        personality_tags: 性格标签列表
        inventory: 库存 [{item_name, quantity, item_id}]
        zone_npcs: 同区域的其他 NPC

    Returns:
        格式化的 prompt 字符串
    """
    vitality = npc_entity.attributes.get("vitality", 100)
    satiety = npc_entity.attributes.get("satiety", 50)
    mood = npc_entity.attributes.get("mood", 50)

    # 找当前区域
    zone_id = "?"
    for conn in npc_entity.connected_entity_ids:
        e = None
        # 在图引擎外部，无法直接查 Entity
        # 从 ID 推断
        if conn.startswith("zone_"):
            zone_id = conn.replace("zone_", "")
            break

    parts = [
        "━━━ 基本生存需求 ━━━",
        "你有3项基本生存属性。任何一项降到0，你都会出局（死亡/崩溃/消失）。",
        "你的性格决定了你的风险承受能力——",
        "谨慎的角色会提前行动，粗心的角色可能拖到最后一刻。",
        "但无论什么性格，掉到0就完了。",
        "",
        f"当前: 体力 {vitality:.0f}/100 | 饱腹 {satiety:.0f}/100 | 心情 {mood:.0f}/100",
        "",
        "根据你的角色和性格，自己判断：",
        "  - 你的体力还能撑多久？",
        "  - 你的饱腹需要什么时候补充？",
        "  - 你的心情需要什么来调节？",
        "",
        "工作、交易、社交——所有目标都在活下去的前提下进行。",
        "",
    ]
    if world_time_str:
        parts.append(f"当前时间：{world_time_str}")
    if tick_duration_str:
        parts.append(f"本 tick 时长：{tick_duration_str}")
    parts.append("")

    # 自我描述
    parts.append(f"## NPC: {npc_name}")
    parts.append(f"角色: {npc_role}  |  位置: {zone_id}")
    parts.append(f"体力: {vitality:.0f}/100  |  饱腹: {satiety:.0f}/100  |  心情: {mood:.0f}/100")
    parts.append("")

    # 性格标签
    if personality_tags:
        parts.append(f"### 性格标签\n{'、'.join(personality_tags)}\n")

    # 最近信息（LLM #4b 近况投影，类型无关）
    if npc_entity.recent_info:
        parts.append("### 最近信息")
        parts.append("  " + npc_entity.recent_info)
        parts.append("")
    elif memories:
        # 兼容旧版：无 recent_info 时回退到旧记忆系统
        parts.append("### 最近经历")
        for entry in memories:
            ts = entry.get("timestamp", "")
            if ts and hasattr(ts, 'strftime'):
                ts = ts.strftime("%H:%M")
            else:
                ts = ""
            event = entry.get("event", "")
            loc = entry.get("location", "")
            if loc:
                parts.append(f"  {ts} 在{loc} {event}" if ts else f"  在{loc} {event}")
            else:
                parts.append(f"  {ts} {event}" if ts else f"  {event}")
        parts.append("")

    # 库存
    if inventory:
        inv_str = "、".join(f"{i['item_name']}x{i['quantity']}" for i in inventory if i.get("quantity", 0) > 0)
        parts.append(f"### 当前持有\n{inv_str}\n")
    else:
        parts.append("### 当前持有\n空手\n")

    # 同区域的其他 NPC（纯内容：角色名）
    if zone_npcs:
        other_str = "、".join(f"{zn['name']}（{zn['role']}）" for zn in zone_npcs)
        parts.append(f"### 当前区域还有\n{other_str}\n")

    # 可用配方
    if recipes:
        parts.append("### 可用配方")
        for r in recipes:
            inp = " + ".join(f"{k}x{v}" for k, v in r.get("inputs", {}).items())
            out = " + ".join(f"{k}x{v}" for k, v in r.get("outputs", {}).items())
            req_obj = r.get("required_object_type", "")
            req_zone = r.get("zone_id", "")
            parts.append(f"  - {r['name']}: {inp} → {out}" + (f"  @{req_zone}" if req_zone else "") + (f" 需[{req_obj}]" if req_obj else ""))
        parts.append("")

    # ── 实体约束（受 config 开关控制）──
    from ..config.config_loader import get_world_config
    allow_unreg = get_world_config("allow_unregistered_entity", False)
    if not allow_unreg:
        parts.append(
            '【约束】计划中只能引用当前持有清单或当前区域其他角色持有的物品名称。\n'
            '  不得创造图中不存在的实体（包括虚构的角色、不存在的物品名称）。\n'
            '  想吃东西只能写现有食物（面包、小麦等），不能说"买肉包子""馒头"等不存在的食物。\n'
            '  没有例外。\n'
        )

    # 决策指令
    parts.append("### 决策")
    parts.append(f"请为 {npc_name} 决定下一步行动。")
    parts.append("用自然语言描述：你想做什么？在哪里做？有什么影响（体力/库存变化）？为什么？")
    parts.append("不用输出 JSON，不用关心格式，直接说你想干嘛。")
    parts.append("")
    parts.append(f"格式示例：我叫{npc_name}，我是{npc_role}。我目前在{zone_id}，持有小麦x21，体力7。")
    parts.append("我决定去market卖掉5小麦换金币，体力会消耗一些。")
    parts.append("用第一人称说人话就行。")

    return "\n".join(parts)


def build_one_fallback_prompt(
    npc_entity: Entity,
    npc_name: str,
    npc_role: str,
    zone_id: str,
) -> dict:
    """兜底模式的 NPC 决策。"""
    neid = npc_entity.entity_id
    return {
        "edge_id": "",
        "action": "闲逛",
        "effects": [
            {
                "target_entity_id": neid,
                "attribute_name": "vitality",
                "operation": "sub",
                "value": 3,
                "description": f"{npc_name}闲逛消耗体力",
            },
            {
                "target_entity_id": neid,
                "attribute_name": "satiety",
                "operation": "sub",
                "value": 2,
                "description": "闲逛消耗饱腹",
            },
        ],
        "edge_qty_changes": [],
        "result_text": f"{npc_name}在{zone_id}闲逛",
    }


# ═══════════════════════════════════════════
# 记忆总结——按重要度分层压缩
# ═══════════════════════════════════════════


