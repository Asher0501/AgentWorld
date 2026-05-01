"""
Conservation Validator — 度守恒校验

校验 LLM #4 输出的 delta 操作中，对守恒量（conserved=True）节点的是否满足
度守恒（Σ(delta) ≈ 0）。

和 GraphEngine 无关，只校验操作列表本身的守恒性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .graph_engine import GraphEngine
from ..config.config_loader import has_role

logger = logging.getLogger(__name__)


class ValidationResult(Enum):
    PASS = "pass"
    SOFT_WARN = "soft_warn"
    HARD_FAIL = "hard_fail"


@dataclass
class ValidationOutcome:
    result: ValidationResult
    message: str = ""
    details: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.result in (ValidationResult.PASS, ValidationResult.SOFT_WARN)


class ConservationValidator:
    """
    校验 delta 操作列表是否满足度守恒。

    原理：
      - 收集所有 delta 操作
      - 按 tgt（物品节点）分组 Σ(delta) ≈ 0
      - 只检查 conserved=True 的节点
      - 检查每个 NPC 的最终持有量 ≥ 0
    """

    def __init__(self, graph_engine: GraphEngine | None = None):
        self._graph = graph_engine

    # ═══════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════

    def validate_deltas(self, ops: list[dict], epsilon: float = 0.001) -> ValidationOutcome:
        """
        校验一组 delta 操作。

        Args:
            ops: [{op: "delta"|"system_delta"|"recipe"|"attr", ...}, ...]
            epsilon: 浮点误差容限

        op 类型校验规则:
          - delta: Σ=0（同前）
          - system_delta: 跳过守恒检查（系统间转移）
          - recipe: 跳过守恒检查（配方转换内部自平衡）
          - attr: 跳过（非守恒量）

        Returns:
            ValidationOutcome
        """
        if not ops:
            return ValidationOutcome(ValidationResult.PASS, "无操作，自动通过")

        # system_delta 和 recipe 跳过守恒检查
        bypassed = [op for op in ops if op.get("op") in ("system_delta", "recipe")]
        for b in bypassed:
            logger.debug(f"[度守恒] 跳过 {b.get('op')}: {b}")

        # 只提取 delta 类型的操作进行守恒校验
        deltas = [op for op in ops if op.get("op") == "delta"]
        if not deltas:
            msg = "无 delta 操作"
            if bypassed:
                msg += f"，{len(bypassed)} 条已跳过守恒检查"
            return ValidationOutcome(ValidationResult.PASS, msg)

        details: list[str] = []

        # 1. 按 target (item_eid) 分组 Σ(delta) — 只检查守恒量
        item_sums: dict[str, int] = {}
        for d in deltas:
            tgt = d.get("tgt", "")
            delta = d.get("delta", 0)
            if not tgt:
                continue
            # 非守恒量跳过检查
            if self._graph and not self._graph.is_conserved(tgt):
                details.append(f"  {tgt}: 非守恒量，跳过")
                continue
            if not self._graph:
                # 无图引擎时默认所有 tgt 都是守恒量
                pass
            item_sums[tgt] = item_sums.get(tgt, 0) + delta

        imbalances = {
            item: delta for item, delta in item_sums.items()
            if abs(delta) > epsilon
        }

        if imbalances:
            hard_fail = False
            for item, delta in imbalances.items():
                item_name = item.removeprefix("item_")
                if delta > 0:
                    # 正不平衡 = 凭空创造 → 永远是硬错误
                    details.append(f"  {item}: Σ = {delta:+d} ❌ 正不平衡（凭空创造）！")
                    hard_fail = True
                else:
                    # 负不平衡 = 消耗 → 仅警告，允许（消费/使用是合理行为）
                    details.append(f"  {item}: Σ = {delta:+d} ⚠️ 负不平衡（消耗/使用）")
                    details.append(f"    → 允许（物品消耗是合理行为）")

            if hard_fail:
                fail_items = [f"{i}=Σ{delta:+d}" for i, delta in imbalances.items() if delta > 0]
                fail_msg = "度守恒校验失败: " + "; ".join(fail_items)
                return ValidationOutcome(ValidationResult.HARD_FAIL, fail_msg, details)
            else:
                # 只有负不平衡（消耗）→ 仅警告，不过滤操作
                warn_items = [f"{i}=Σ{delta:+d}" for i, delta in imbalances.items() if delta < 0]
                return ValidationOutcome(ValidationResult.SOFT_WARN, "度守恒警告（物品消耗）: " + "; ".join(warn_items), details)

        for item, delta in item_sums.items():
            if abs(delta) <= epsilon:
                details.append(f"  {item}: Σ = 0 ✅ 度守恒")

        # 2. 检查每个 NPC 的最终持有量 ≥ 0（如果有图引擎的话）
        if self._graph:
            for d in deltas:
                src = d.get("src", "")
                tgt = d.get("tgt", "")
                delta = d.get("delta", 0)
                if not src or not tgt:
                    continue
                ent = self._graph.get_entity(src)
                if not ent or not has_role(ent.type_id, "actor"):
                    continue
                # 只检查守恒量的透支
                if not self._graph.is_conserved(tgt):
                    continue
                current = self._graph.get_held_quantity(src, tgt)
                if current < 0:
                    details.append(f"  {src} 持有 {tgt} = {current} < 0 ❌")
                    return ValidationOutcome(
                        ValidationResult.HARD_FAIL,
                        f"{src} 持有守恒量 {tgt} 为负 ({current})",
                        details,
                    )

        details_str = "\n".join(details)
        if details:
            logger.debug(f"[度守恒]\n{details_str}")
        return ValidationOutcome(ValidationResult.PASS, "度守恒校验通过 ✅", details)
