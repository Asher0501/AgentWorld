"""
校验注册器 — 统一管理所有校验项。

每层（翻译校验层、预写校验层）实例化时传入一个布尔数组 mask，
按位决定激活哪些校验项。

用法:
    registry.run(mask, context)
      → mask:  [True, False, True, ...] 与 CHECKS 长度一致
      → context: dict, 各校验函数自取所需字段
      → 返回 CheckFailure[]
"""
from __future__ import annotations

import json
import logging
import re
from ..config.config_loader import get_all_prefixes


def _strip_prefix(raw: str) -> str:
    for pfx in get_all_prefixes():
        if raw.startswith(pfx):
            return raw[len(pfx):]
    return raw

logger = logging.getLogger(__name__)


# ─── 错误码描述表（域无关，使用纯拓扑语言） ───
ERROR_CODE_MAP: dict[int, dict[str, str]] = {
    1: {
        "title": "entity_existence",
        "description": "节点不存在 — 操作引用了图中未注册的节点",
        "fix_hint": "确认节点名称拼写正确，或改用图中已有节点",
    },
    2: {
        "title": "quantity_accuracy",
        "description": "边权重不匹配 — 自然语言描述中的边权重与图中实际权值不一致",
        "fix_hint": "修正文本中的权值数字，使其与图拓扑一致",
    },
    5: {
        "title": "degree_conservation",
        "description": "分组度守恒违反 — 组内某物品的 delta 之和不为 0，存在凭空创造或无故消失",
        "fix_hint": "补充缺失的对端 delta，使组内每种物品的 Σ(delta) = 0",
    },
    3: {
        "title": "capacity_upper_bound",
        "description": "出边流量不足 — src→tgt 有向边当前权值小于负 delta 的绝对值",
        "fix_hint": "减小负 delta 的绝对值使 ≤ 边权值，或断开该边后从其他路径重建连接",
    },
    4: {
        "title": "entity_coverage",
        "description": "节点覆盖不全 — 图描述遗漏了部分已注册节点",
        "fix_hint": "补充文本中缺失的节点和对边关系",
    },
    6: {
        "title": "direction_pairing",
        "description": "双向边未配对 — A→B 负 delta 缺少对应的 B→A 正 delta",
        "fix_hint": "确保每一条 A→B 负操作都有一条对应的 B→A 正操作",
    },
    7: {
        "title": "story_consistency",
        "description": "操作方向与叙事语境矛盾",
        "fix_hint": "修正 delta 方向使其与自然语言描述中的语义一致",
    },
}


def get_error_description(code: int) -> dict:
    """按错误码获取描述信息，供 build_feedback 使用。"""
    return ERROR_CODE_MAP.get(code, {
        "title": f"unknown_{code}",
        "description": "未知错误",
        "fix_hint": "",
    })


def get_all_error_codes() -> dict[int, dict[str, str]]:
    """返回完整错误码表（供 LLM 配置查找用）。"""
    return dict(ERROR_CODE_MAP)


class CheckFailure:
    """单条校验失败记录"""

    def __init__(self, code: int, check_name: str, message: str, details: list[str] | None = None):
        self.code = code
        self.check_name = check_name
        self.message = message
        self.details = details or []

    def __repr__(self) -> str:
        return f"[{self.check_name}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "check_name": self.check_name,
            "message": self.message,
            "details": self.details,
        }

    def to_text(self) -> str:
        s = f"[{self.check_name}] {self.message}"
        for d in self.details[:3]:
            s += f"\n  · {d}"
        return s

    def to_llm_feedback(self) -> str:
        """返回供 LLM 重试时读取的详细错误说明。"""
        lines = [f"  · 错误: {self.message}"]
        for d in self.details:
            lines.append(f"    → {d}")
        return "\n".join(lines)


# ─── 注册器 ──────────────────────────────────────

_CHECKS: list[dict] = []  # [{code, name, desc, func}, ...]


def register(code: int, name: str, desc: str = ""):
    """装饰器：注册一个校验函数。"""
    def decorator(func):
        _CHECKS.append({
            "code": code,
            "name": name,
            "desc": desc,
            "func": func,
        })
        return func
    return decorator


def run(mask: list[bool], context: dict) -> list[CheckFailure]:
    """
    按 mask 运行激活的校验项。

    Args:
        mask: 布尔数组，与 _CHECKS 长度对齐（短则截断，长则忽略）
        context: 透传给各校验函数的上下文 dict

    Returns:
        CheckFailure[] — 空列表表示全部通过
    """
    failures: list[CheckFailure] = []
    for idx, active in enumerate(mask):
        if idx >= len(_CHECKS):
            break
        if not active:
            continue
        entry = _CHECKS[idx]
        try:
            result = entry["func"](context)
            if result:
                failures.extend(result)
                for f in result:
                    logger.warning("[校验] FAIL: %s", f.to_text())
        except Exception as e:
            logger.exception("[校验] %s 异常: %s", entry["name"], e)
            failures.append(CheckFailure(
                code=entry["code"],
                check_name=entry["name"],
                message=f"校验执行异常: {e}",
            ))
    return failures


def get_all_check_names() -> list[str]:
    """返回所有已注册的校验项名称（用于调试/显示）。"""
    return [e["name"] for e in _CHECKS]


def make_mask(*names: str) -> list[bool]:
    """按校验项名称快捷生成 mask。"""
    known = get_all_check_names()
    return [name in names for name in known]


_VERIFICATION_CONFIG_CACHE: dict | None = None


def _load_verification_config() -> dict:
    """从 domain.json 加载 verification 配置（带缓存）。"""
    global _VERIFICATION_CONFIG_CACHE
    if _VERIFICATION_CONFIG_CACHE is not None:
        return _VERIFICATION_CONFIG_CACHE
    import os, json
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "domain.json")
    try:
        with open(cfg_path) as f:
            data = json.load(f)
        _VERIFICATION_CONFIG_CACHE = data.get("verification", {})
    except Exception:
        _VERIFICATION_CONFIG_CACHE = {}
    return _VERIFICATION_CONFIG_CACHE


def load_layer_mask(layer_name: str) -> list[bool]:
    """
    从 domain.json 按层名加载 mask。

    Args:
        layer_name: "translation_layer_mask" 或 "prewrite_layer_mask"

    Returns:
        list[bool] — 与注册校验项长度一致，缺省布尔。
    """
    cfg = _load_verification_config()
    raw = cfg.get(layer_name, [])
    known = get_all_check_names()
    return [bool(raw[i]) if i < len(raw) else False for i in range(len(known))]


# ═══════════════════════════════════════════════════════════
# 校验项 — 索引 0+
# ═══════════════════════════════════════════════════════════


@register(code=1, name="entity_existence",
          desc="检查名称是否在合法范围内（白名单或图引擎）")
def _check_entity_existence(ctx: dict) -> list[CheckFailure]:
    """
    统一实体存在性检查（翻译层 + 预写层共用）。

    两种验证方式（按优先级）：
    1. 显式白名单:  ctx["entity_existence_whitelist"] 为 set[str]
    2. 图引擎兜底:  whitelist 不存在时（或只作为 ID 回退），用 graph_engine.get_entity()

    context 字段:
      entity_existence_candidates: list[str] — 待检查的名称列表
      entity_existence_whitelist:   set[str]  — 合法名称白名单（中/英）
      entity_existence_label:       str       — (可选) 描述前缀
      graph_engine:               object     — (可选) 兜底查图实体 ID
    """
    candidates = ctx.get("entity_existence_candidates", [])
    whitelist = ctx.get("entity_existence_whitelist")
    ge = ctx.get("graph_engine")
    label = ctx.get("entity_existence_label", "")

    failures = []

    if whitelist is not None:
        for name in candidates:
            if name in whitelist:
                continue
            # 兜底: 图引擎 ID 查
            if ge and (ge.get_entity(name) or ge.find_entity_by_name(name) is not None):
                continue
            failures.append(CheckFailure(
                code=1,
                check_name="entity_existence",
                message=f"\"{name}\" 不存在于白名单中" +
                        (f"（来源: {label}）" if label else ""),
                details=[f"白名单内名称数: {len(whitelist)}"],
            ))
    elif ge:
        # 无白名单，全靠图引擎（旧模式兼容）
        for name in candidates:
            if not name:
                continue
            cleaned = _strip_prefix(name)
            if not (ge.get_entity(cleaned) or ge.get_entity(name)
                    or ge.find_entity_by_name(cleaned) is not None):
                failures.append(CheckFailure(
                    code=1,
                    check_name="entity_existence",
                    message=f"\"{name}\" 在图引擎中不存在" +
                            (f"（来源: {label}）" if label else ""),
                    details=[],
                ))

    return failures


# ────────────────────────────────────────────────────────


@register(code=2, name="quantity_accuracy",
          desc="检查 NL 翻译中实体间数量关系与 ground truth 是否一致（域无关）")
def _check_quantity_accuracy(ctx: dict) -> list[CheckFailure]:
    """
    翻译校验专用：检查自然语言描述中所有实体间的数量关系。

    不假设 NPC→item 结构，检查任何 (src, tgt) 对的 qty。

    context 字段:
      nl_text:         str                            — LLM 翻译输出的自然语言
      edge_quantities: dict[tuple[str,str], int]      — {(src, tgt): 预期数量}
    """
    nl_text = ctx.get("nl_text", "")
    edge_qtys = ctx.get("edge_quantities", {})
    if not nl_text or not edge_qtys:
        return []

    failures = []
    for (src_name, tgt_name), expected_qty in edge_qtys.items():
        found = None
        for line in nl_text.split("\n"):
            if src_name not in line:
                continue
            for m in re.finditer(
                re.escape(tgt_name) + r"\s*[xX×*:]\s*(\d+)", line
            ):
                found = int(m.group(1))
        if found is not None and found != expected_qty:
            failures.append(CheckFailure(
                code=2,
                check_name="quantity_accuracy",
                message=f"{src_name}→{tgt_name}: 应为 {expected_qty}, 翻译为 {found}",
                details=[f"将 {tgt_name}x{found} 改为 {tgt_name}x{expected_qty}"],
            ))
    return failures


# ────────────────────────────────────────────────────────



def _entity_name(ge, eid_or_name: str) -> str:
    """entity_id → 人类可读名称（供 LLM 反馈用）"""
    if not eid_or_name:
        return eid_or_name
    ent = ge.get_entity(eid_or_name)
    if ent and ent.name:
        return ent.name
    cleaned = _strip_prefix(eid_or_name)
    ent = ge.find_entity_by_name(cleaned)
    if ent and ent.name:
        return ent.name
    return eid_or_name


@register(code=3, name="capacity_upper_bound",
          desc="检查负 delta 输出量是否超过边当前 qty")
def _check_capacity_upper_bound(ctx: dict) -> list[CheckFailure]:
    """
    预写校验专用：每条负 delta 的绝对值不能超过 src→tgt 边的当前 qty。

    context 字段:
      topo_ops:     list[dict] — 拓扑操作
      graph_engine: object     — GraphEngine 实例（需有 get_edge_by_name）
    """
    topo_ops = ctx.get("topo_ops", [])
    ge = ctx.get("graph_engine")
    if not ge or not topo_ops:
        return []

    failures = []
    for i, op in enumerate(topo_ops):
        if op.get("op") != "delta":
            continue
        delta_val = op.get("delta", 0)
        if delta_val >= 0:
            continue

        src_id = op.get("src", "")
        tgt_id = op.get("tgt", "")
        if not src_id or not tgt_id:
            continue

        needed = abs(delta_val)
        edge = ge.get_edge_by_name(src_id, tgt_id)
        current_qty = edge.quantity if edge else 0

        if current_qty < needed:
            # entity_id → 人类可读名称（LLM 一致使用名称）
            src_display = _entity_name(ge, src_id)
            tgt_display = _entity_name(ge, tgt_id)
            details = [
                f"    操作 topo_ops[{i}]: {json.dumps(op, ensure_ascii=False)}",
                f"    有向边 ({src_display}→{tgt_display}) 当前权值 {current_qty}",
                f"    建议: 减小负 delta 至 ≤ {current_qty} 或移除该操作",
            ]
            # 附加 src 节点出边列表（供 LLM 参考可选路径）
            out_lines = _format_out_edges(ge, src_id)
            if out_lines:
                details.append(f"    {src_display} 的出边 (权值>0):")
                details.extend(out_lines)
            failures.append(CheckFailure(
                code=3,
                check_name="capacity_upper_bound",
                message=f"({src_display}→{tgt_display}) 权值 {current_qty} < 负 delta 绝对值 {needed}",
                details=details,
            ))
    return failures


def _format_out_edges(ge, src_name: str) -> list[str]:
    """格式化 src 节点的出边（权值>0），供 LLM 重试参考。"""
    src_eid = ge.resolve_eid(src_name) or ge.find_entity_by_name(src_name)
    if not src_eid:
        return []
    src_eid_str = src_eid if isinstance(src_eid, str) else src_eid.entity_id
    edges = ge.get_outgoing_edges(src_eid_str)
    result = []
    for e in edges:
        if e.quantity <= 0:
            continue
        tgt_ent = ge.get_entity(e.target_entity_id)
        tgt_name = tgt_ent.name if tgt_ent else e.target_entity_id
        result.append(f"      → {tgt_name}: 权值 {e.quantity}")
    return result
# ────────────────────────────────────────────────────────


@register(code=4, name="entity_coverage",
          desc="检查 NL 翻译是否覆盖了所有实体（域无关）")
def _check_entity_coverage(ctx: dict) -> list[CheckFailure]:
    """
    翻译校验专用：所有 ground truth 中的实体名称必须在 NL 文本中出现。

    不假设实体类型（NPC/物品/区域），任何 missing 都报错。
    
    context 字段:
      nl_text:         str       — LLM 翻译输出
      gt_entity_names: set[str]  — ground truth 中所有实体名称
    """
    nl_text = ctx.get("nl_text", "")
    gt_names = ctx.get("gt_entity_names", set())
    if not gt_names:
        return []

    missing = {n for n in gt_names if n not in nl_text}
    if missing:
        return [CheckFailure(
            code=4,
            check_name="entity_coverage",
            message=f"遗漏 {len(missing)} 个实体: {', '.join(sorted(missing))}",
            details=["确保所有实体名称都出现在描述中"],
        )]
    return []


# ────────────────────────────────────────────────────────


@register(code=6, name="direction_pairing",
          desc="(预留 LLM) 双向交换中流向必须交替")
def _check_direction_pairing(ctx: dict) -> list[CheckFailure]:
    """LLM 批量校验 — 当前预留。"""
    return []


@register(code=7, name="story_consistency",
          desc="(预留 LLM) 操作方向与故事一致")
def _check_story_consistency(ctx: dict) -> list[CheckFailure]:
    """LLM 批量校验 — 当前预留。"""
    return []


# ────────────────────────────────────────────────────────


@register(code=5, name="degree_conservation",
          desc="校验每个 group 内每种 item 的 delta 之和为 0")
def _check_degree_conservation(ctx: dict) -> list[CheckFailure]:
    """
    预写校验专用：分组度守恒。

    每个 group 内的每种 item，其 delta 之和必须为 0。
    否则表示操作在改 item 上有凭空创造或无故消失。

    context 字段:
      topo_ops:     list[dict] — 拓扑操作
      graph_engine: object     — GraphEngine 实例
      label_map:    dict       — 名称→ID 映射（用于格式化输出）

    输出格式：使用 label_map 将 entity_id 反解为人类可读名称，不写死名称。
    """
    topo_ops = ctx.get("topo_ops", [])
    ge = ctx.get("graph_engine")
    if not topo_ops or not ge:
        return []

    from ..services.conservation_validator import ConservationValidator
    cv = ConservationValidator(ge)
    outcome = cv.validate_deltas(topo_ops)

    if outcome.result.value != "hard_fail":
        return []

    # ── 反解实体 ID → 人类可读名称 ──
    def _resolve(eid_or_name: str) -> str:
        """将 entity_id 反解为人类可读名称。
        优先使用 graph_engine.get_entity().name，
        次选 label_map 反向查找，
        回退原字符串。"""
        if not eid_or_name:
            return eid_or_name
        # 精确匹配
        ent = ge.get_entity(eid_or_name)
        if ent and ent.name:
            return ent.name
        # 去掉 item_ 前缀再查
        cleaned = _strip_prefix(eid_or_name)
        ent = ge.find_entity_by_name(cleaned)
        if ent:
            return ent.name
        # label_map 反向查找
        label_map = ctx.get("label_map", {}) or {}
        for name, eid in label_map.items():
            if eid == eid_or_name:
                return name
            # eid 可能是完整名称的 hash 版本
            if eid.endswith(cleaned):
                return name
        return eid_or_name

    def _resolve_group_name(group: str | None) -> str:
        """格式化 group 名。"""
        return f"组 [{group}]" if group else "全局组"

    # ── 解析失败详情中的每一行，替换 ID 为人类可读名 ──
    details_fmt: list[str] = []
    fail_parts: list[str] = []  # 用于主消息摘要
    for line in outcome.details:
        fmt_line = line
        # 提取行中的第一个 entity-like token（tab 后第一个词）
        stripped = line.strip()
        if stripped.startswith("  "):
            token = stripped.lstrip().split(":")[0].split()[0]
            if token and any(token.startswith(pfx) for pfx in get_all_prefixes()):
                resolved = _resolve(token)
                fmt_line = fmt_line.replace(token, resolved, 1)
                if "❌" in line or "正不平衡" in line:
                    # 提取 group 标签
                    g_tag = ""
                    if "[group=" in stripped:
                        g = stripped.split("[group=")[1].split("]")[0]
                        g_tag = f" {_resolve_group_name(g)}"
                    fail_parts.append(f"{resolved}{g_tag}")

        details_fmt.append(fmt_line)

    # ── 构建 CheckFailure ──
    message = "分组度守恒校验失败: " + "; ".join(fail_parts)
    # 附加过不了的 group 列表
    passed = outcome.passed_groups or set()
    all_groups = set()
    for op in topo_ops:
        if op.get("op") == "delta":
            all_groups.add(op.get("group", None))
    failed_groups = all_groups - passed
    if failed_groups:
        fg_show = [_resolve_group_name(g) for g in sorted(failed_groups, key=lambda x: x or "")]
        details_fmt.append(f"    需修正的 {', '.join(fg_show)}")

    return [CheckFailure(
        code=5,
        check_name="degree_conservation",
        message=message,
        details=details_fmt,
    )]
