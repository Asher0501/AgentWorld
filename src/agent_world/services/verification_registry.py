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

logger = logging.getLogger(__name__)


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
            cleaned = name.removeprefix("item_").removeprefix("npc_")
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

        src_name = op.get("src", "")
        tgt_name = op.get("tgt", "")
        if not src_name or not tgt_name:
            continue

        needed = abs(delta_val)
        edge = ge.get_edge_by_name(src_name, tgt_name)
        current_qty = edge.quantity if edge else 0

        if current_qty < needed:
            failures.append(CheckFailure(
                code=3,
                check_name="capacity_upper_bound",
                message=f"{src_name}→{tgt_name} qty={current_qty} < 输出量 {needed}",
                details=[
                    f"操作 topo_ops[{i}]: {json.dumps(op, ensure_ascii=False)}",
                    f"建议 delta → -{current_qty}",
                ],
            ))
    return failures


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
