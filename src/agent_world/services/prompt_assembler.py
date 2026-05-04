"""
PromptAssembler — Slot 式 Prompt 组装器。

每个 LLM prompt 由有序 slot 列表定义。每个 slot 分属三类提供者：
  - "content"  → DomainAdapter 的 `get_slot(name)` 方法
  - "topology" → 引擎固定渲染
  - "runtime"  → 运行时信息（时间等）

换域 = 换 DomainAdapter。slot 列表几乎不变。
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from typing import Any
import httpx


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
        ("global_overview",      "topology"),
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

    "llm4b_content": [
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
}


# ─── 拓扑层渲染函数（引擎固定，跨域不变） ───────────

def _render_topo_slot(slot_name: str, engine, **kw) -> str:
    if slot_name == "core_principle":
        return (
            "【核心原则】拓扑信息是客观数据层的唯一事实来源。\n"
            "语义描述（计划、记忆、角色设定）仅作叙事背景参考，\n"
            "不具备约束力，不得覆盖拓扑信息。\n\n"
        )

    if slot_name == "global_overview":
        """构建全局目标节点列表（使用全局标签映射，A-Z）"""
        if not engine:
            return ""
        from ..config.config_loader import has_role

        # 优先使用传入的全局标签映射（所有节点共享同一套 A-Z）
        global_label_map = kw.get("global_label_map")
        if global_label_map is not None:
            # 用全局标签映射构建全局概览（仅显示区域节点）
            eid_to_tag = {v: k for k, v in global_label_map.items()}
            tag_to_eid = dict(global_label_map)
        else:
            # 回退：构建 Z-prefix 标签（旧行为，兼容性保留）
            region_eids = set()
            for ent in engine.all_entities():
                if has_role(ent.type_id, "region"):
                    region_eids.add(ent.entity_id)
                    for conn in ent.connected_entity_ids:
                        region_eids.add(conn)
            if not region_eids:
                return ""
            tag_to_eid = {}
            for i, eid in enumerate(sorted(region_eids), start=1):
                tag_to_eid[f"Z{i}"] = eid
            eid_to_tag = {v: k for k, v in tag_to_eid.items()}

        lines = ["==== [全局目标节点列表] ===="]
        lines.append("以下是世界中所有可连接的目标节点。如果节点的计划提及前往一个新的地点，")
        lines.append("请从此列表中选择目标节点并输出 connect 操作。")
        lines.append("")
        # 只显示区域（region）类型的节点
        for tag in sorted(tag_to_eid.keys()):
            eid = tag_to_eid[tag]
            ent = engine.get_entity(eid)
            if not ent:
                continue
            # 只在 global_label_map 模式下过滤 region（Z-prefix 模式不过滤以保持兼容）
            if global_label_map is not None and not has_role(ent.type_id, "region"):
                continue
            name = ent.name if hasattr(ent, 'name') else eid[:12]
            conns = []
            for conn_eid in ent.connected_entity_ids:
                conn_tag = eid_to_tag.get(conn_eid)
                if conn_tag:
                    conns.append(f"{{{conn_tag}}}")
            conn_str = " ↔ " + ", ".join(sorted(conns)) if conns else ""
            lines.append(f"  {{{tag}}} = {name}{conn_str}")
        lines.append("")
        lines.append("规则：")
        if global_label_map is not None:
            lines.append("- 使用全局标签映射中的标签（{A}, {B}, ...）引用目标节点")
        else:
            lines.append("- 如果节点计划前往一个新地点，用 connect 连接到对应的 Z-prefix 标签")
        lines.append("- 不要连接到不在这个列表中的节点")
        lines.append("- 不要编造不存在的地点")
        lines.append("")
        return "\n".join(lines)

    if slot_name == "topology_principle":
        return (
            "拓扑信息是客观数据层的唯一事实来源。图中每条边（带 label、qty、conserved/terminal 标签）\n"
            "完整定义了当前世界的拓扑结构。语义描述（计划、记忆、角色设定）仅作叙事背景参考，\n"
            "不具备约束力，不得覆盖拓扑信息。\n\n"
        )

    if slot_name == "topology_graph":
        topo_eids = kw.get("topo_eids", [])
        if engine:
            topo_text, label_map = engine.build_tagged_topology(topo_eids)
            # 翻译层: 将抽象拓扑转换为自然语言
            if _should_translate():
                translated = _translate_topology(topo_text, label_map=label_map, engine=engine)
                if translated:
                    return translated + "\n\n"
            # 回退到原始格式
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
            "□ **累计流出约束（同一有向边在多操作下的上限）**\n"
            "  若在同一 tick 内有多条 delta 操作作用于同一有向边 (X→Y)，\n"
            "  且这些操作对 (X→Y) 的累计净变化为负（Σdelta < 0），则：\n"
            "    · 该累计净减少的绝对值**不得超过**边 (X→Y) 的当前数量（qty）。\n"
            "    · 若违反，涉及该边的全部操作将被等比例缩减，\n"
            "      对应的对端正操作也同步调减以维持 Σ=0。\n"
            "  例：X→Y qty=8，本 tick 有三条 op：\n"
            "    δ₁=-5、δ₂=-4、δ₃=+9（对端 Z→Y）\n"
            "    → Σ负 delta = 9 > 8，违反约束\n"
            "    → 所有负 delta 等比例缩减：-5→-4.44, -4→-3.56\n"
            "    → 正 delta 同步调减：+9→+8\n"
        )
        parts.append(
            "□ delta 操作描述的是两个节点之间的直接数量变化，非路径路由。\n"
            "  — 若两节点都与同一枢纽 Hub 相连，需交换某标签的数量时，\n"
            "    应直接在两者之间建立 delta 操作，而非分别与 Hub 做两段 delta。\n"
            "  — 例：A ↔ H ↔ C 时，若 A 要给 C 数量 5 的某标签，\n"
            "    应直接 src=A→tgt=C delta=-5（配合 src=C→tgt=A delta=+5），\n"
            "    而非 src=A→H⁻5 + H→C⁺5。Hub 是结构共享点，不是数量路由节点。\n"
            "□ **同一连通分量内的任意节点对均可达**\n"
            "  — 两个节点连通当且仅当存在一条路径连接它们（允许经由中间节点中转）。\n"
            "  — 直接边不是必要条件。连通即可进行任意两个节点间的数量转移操作。\n"
            "  — **边不存在 ≠ 不可达。**\n"
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


# ─── 拓扑翻译层 ──────────────────────────────────

_topo_translate_cache: dict[str, str] = {}
_topology_translator_enabled: bool | None = None


def _should_translate() -> bool:
    """检查翻译层是否启用（从 domain.json 配置）。"""
    global _topology_translator_enabled
    if _topology_translator_enabled is not None:
        return _topology_translator_enabled
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "domain.json")
        with open(cfg_path) as f:
            data = json.load(f)
        tc = data.get("topology_translator", {})
        _topology_translator_enabled = bool(tc.get("entity_types", ""))
    except Exception:
        _topology_translator_enabled = False
    return _topology_translator_enabled


def _get_translator_config() -> dict:
    """从 domain.json 获取翻译器配置。"""
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "domain.json")
        with open(cfg_path) as f:
            data = json.load(f)
        return data.get("topology_translator", {})
    except Exception:
        return {}


def _find_api_creds() -> tuple[str, str]:
    """查找 LLM API 凭证（同 interaction_resolver）。"""
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    base_url = os.environ.get("MINIMAX_BASE_URL", "").strip()
    if api_key and base_url:
        return base_url, api_key
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return base_url, api_key
    for path in [
        os.path.expanduser("~/.openclaw/agents/coder/agent/models.json"),
        os.path.expanduser("~/.openclaw/openclaw.json"),
    ]:
        try:
            with open(path) as f:
                raw = f.read()
            idx = raw.find('"providers"')
            if idx < 0: continue
            brace_idx = raw.find('{', idx)
            if brace_idx < 0: continue
            partial = raw[brace_idx:]
            depth = end = 0
            for i,c in enumerate(partial):
                if c == '{': depth+=1
                elif c == '}':
                    depth-=1
                    if depth==0: end=i+1; break
            providers = json.loads(partial[:end])
            mm = providers.get("minimax",{})
            k = mm.get("apiKey","") or mm.get("api_key","")
            u = mm.get("baseUrl","") or mm.get("base_url","")
            if k and u: return u, k
            oa = providers.get("openai",{})
            k = oa.get("apiKey","") or oa.get("api_key","")
            u = oa.get("baseUrl","") or oa.get("base_url","https://api.openai.com/v1")
            if k: return u, k
        except: pass
    return "", ""


def _call_translate_llm(prompt: str) -> str:
    """调用 LLM 翻译拓扑。"""
    base_url, api_key = _find_api_creds()
    if not api_key:
        return ""
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=300.0) as client:
            resp = client.post(
                "/v1/messages",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                    "anthropic-dangerous-direct-browser-access": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-M2.7",
                    "max_tokens": 2000,
                    "temperature": 0.1,
                    "system": (
                        "你是拓扑翻译器。"
                        "规则（严格遵守，不允许任何例外）：\n"
                        "1. 输出必须是纯自然语言中文，每行一句话。\n"
                        "2. 禁止输出英文（人名、地名除外），发现即判失败。\n"
                        "3. 禁止任何推理、分析、思考、解释（包括'我们需要''首先''这个'等元语言）。\n"
                        "4. 只输出描述本身，没有前言后语。\n"
                        "5. 直接输出，不要反问、不要总结、不要感叹。\n"
                        "例：\n"
                        "杰洛特在白果园，持有武器1件、金币8枚。\n"
                        "丹德里恩在狐狸与鹅酒馆，持有金币20枚。"
                    ),
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        result = ""
        for block in data.get("content", []):
            t = block.get("text", "") or block.get("thinking", "")
            if t.strip():
                result = t
        return result.strip()
    except Exception:
        return ""


def _is_valid_translation(text: str) -> bool:
    """校验 LLM 翻译输出是否为合法中文描述（非分析模式）。"""
    if not text or len(text) < 10:
        return False
    # 检测英文推理 — 以英文大写字母开头的非标签行
    english_sig = 0
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s[0].isascii() and s[0].isalpha() and s[0].isupper():
            non_cjk = sum(1 for c in s if c.isascii())
            if non_cjk > len(s) * 0.6:
                english_sig += 1
    if english_sig > 2:
        return False
    # 检测中文推理关键词
    forbidden = ["我们需要", "我们首先", "该拓扑", "Let's", "we need",
                 "we should", "First,", "The user", "We have", "Now we"]
    for w in forbidden:
        if w in text:
            return False
    # 必须有中文字符
    cjk = sum(1 for c in text if ord(c) > 0x4E00 and ord(c) < 0x9FFF)
    return cjk > 0


# ─── 校验层 ──────────────────────────────────────

_TopoGroundTruth = dict  # 结构化的 ground truth


def _parse_topo_gt(raw_text: str) -> dict:
    """从原始拓扑文本解析 ground truth（域无关版本）。

    不依赖 npc_/item_/zone_ 前缀，只提取名称映射和边关系。
    """
    label_map = {}       # {letter: name}
    edges = []           # [(src, tgt, qty, bidirectional)]

    for line in raw_text.split('\n'):
        m = re.match(r'\s*\{(\w+)\} = (.+?) → (\S+)', line)
        if m:
            letter = m.group(1); name = m.group(2).strip()
            label_map[letter] = name

    for line in raw_text.split('\n'):
        line = line.strip()
        m = re.match(r'\{(\w+)\} ↔ \{(\w+)\}', line)
        if m:
            edges.append((m.group(1), m.group(2), None, True))
            continue
        m = re.match(r'\{(\w+)\} → \{(\w+)\}(?:\s+qty:(\d+))?', line)
        if m:
            q = int(m.group(3)) if m.group(3) else None
            edges.append((m.group(1), m.group(2), q, False))

    # 所有带 qty 的边（不区分实体类型）
    edge_quantities = {}  # {(src_name, tgt_name): qty}
    for s, t, q, bi in edges:
        sn = label_map.get(s)
        tn = label_map.get(t)
        if sn and tn and q is not None:
            edge_quantities[(sn, tn)] = q

    return {
        "names": set(label_map.values()),
        "edges": edges,
        "edge_quantities": edge_quantities,
    }


# ── 翻译校验层 mask（从 domain.json 加载）──
# 索引: 0=entity_existence, 1=quantity_accuracy, 2=capacity_upper_bound,
#        3=entity_coverage, 4=direction_pairing, 5=story_consistency
from .verification_registry import load_layer_mask as _load_layer_mask
_TRANSLATION_CHECK_MASK: list[bool] = _load_layer_mask("translation_layer_mask")


def _build_translation_context(nl_text: str, gt: dict) -> dict:
    """构建翻译校验 context（域无关版本）。

    使用泛化字段：edge_quantities 替代 qt_npc_items，
    names 替代 npc_names。不假设实体类型。
    """
    all_gt_names = gt.get("names", set())
    candidates = []
    for name in sorted(all_gt_names, key=len, reverse=True):
        if name in nl_text:
            candidates.append(name)
    return {
        "entity_existence_candidates": candidates,
        "entity_existence_whitelist": all_gt_names,
        "entity_existence_label": "NL翻译",
        "nl_text": nl_text,
        "edge_quantities": gt.get("edge_quantities", {}),
        "gt_entity_names": gt.get("names", set()),
    }


def _build_translation_feedback(failures) -> str:
    """将校验失败转换为翻译重试反馈。"""
    lines = ["翻译结果不准确，需要修正："]
    for f in failures[:5]:
        lines.append(f"  [{f.check_name}] {f.message}")
    if len(failures) > 5:
        lines.append(f"  ...（共 {len(failures)} 项）")
    lines.append("")
    lines.append("请重新输出，修正上述错误。只输出修正后的描述，不要分析。")
    return "\n".join(lines)


def _translate_topology(raw_text: str, label_map: dict | None = None,
                      engine=None) -> str:
    """将抽象拓扑翻译为自然语言描述。带校验 + 重试。"""
    h = hashlib.md5(raw_text.encode()).hexdigest()
    if h in _topo_translate_cache:
        return _topo_translate_cache[h]

    tc = _get_translator_config()
    entity_types = tc.get("entity_types", "")
    interaction_rules = tc.get("interaction_rules", "")

    # 将抽象字母替换为中文名
    display_text = raw_text
    letter_to_name = {}
    if label_map and engine:
        for letter, eid in sorted(label_map.items(), key=lambda x: len(x[0]), reverse=True):
            entity = engine.get_entity(eid)
            cname = entity.name if entity else eid
            letter_to_name[letter] = cname
        # 替换文本中所有 {X} → 中文名
        for letter, cname in sorted(letter_to_name.items(), key=lambda x: len(x[0]), reverse=True):
            display_text = display_text.replace("{"+letter+"}", cname)

    # 剥离噪声行，只保留连接线和标签映射
    clean_lines = []
    for line in display_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("拓扑信息") or stripped.startswith("图中每条"):
            continue
        if stripped.endswith("]") and ": " in stripped and stripped.count(":") == 1:
            continue
        if stripped in ("标签：", "连接：", "标签:", "连接:"):
            continue
        if " → " in stripped and "=" in stripped:
            continue  # 标签映射行（如 "杰洛特 = 杰洛特 → npc_xxxx"）
        clean_lines.append(line)
    # 去重（保留顺序）
    seen = set()
    unique_lines = []
    for line in clean_lines:
        s = line.strip()
        if s and s not in seen:
            seen.add(s)
            unique_lines.append(line)
    display_text = "\n".join(unique_lines)

    base_prompt = f"""将以下图拓扑翻译成自然语言描述。

{entity_types}
{interaction_rules}

描述要求：
- 按顶点的类型分组描述
- 有数量(qty)的关系需要列出具体数值
- 只输出描述，不要分析，不要推理过程

输入拓扑：

{display_text}
"""

    gt = _parse_topo_gt(raw_text)
    max_retries = 2

    from .verification_registry import run as run_checks

    prompt = base_prompt

    for attempt in range(max_retries + 1):
        result = _call_translate_llm(prompt)
        if not result:
            continue

        ctx = _build_translation_context(result, gt)
        failures = run_checks(_TRANSLATION_CHECK_MASK, ctx)
        if not failures:
            _topo_translate_cache[h] = result
            return result

        if attempt < max_retries:
            feedback = _build_translation_feedback(failures)
            prompt = base_prompt + f"\n\n--- 需要修正 ---\n{feedback}"

    # 最后一次尝试：校验非分析模式才缓存，否则回退到原始格式
    if result and _is_valid_translation(result):
        _topo_translate_cache[h] = result
        return result
    # 翻译失败（分析模式）→ 回退到原始拓扑格式
    return _build_fallback_topo(display_text)


def _build_fallback_topo(display_text: str) -> str:
    """翻译失败时的落策略：返回中文名替换后的原始拓扑格式。"""
    lines = [line.rstrip() for line in display_text.split("\n") if line.strip()]
    # 去重（保留顺序）
    seen = set()
    unique = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return "\n".join(unique)


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
    if slot_name == "feedback":
        fb = kw.get("feedback", "")
        if fb:
            return f"{fb}\n\n"
        return ""
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
