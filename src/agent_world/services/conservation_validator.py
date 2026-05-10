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
    passed_groups: set[str | None] | None = None

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

        每组独立校验守恒，一组失败不影响其他组。
        group 字段由 LLM 在拓扑操作中标注，业务含义如"一笔交易"。
        无 group 的操作归入全局组（group=None）。

        Args:
            ops: [{op: "delta"|"system_delta"|"recipe"|"attr", ...}, ...]
            epsilon: 浮点误差容限

        op 类型校验规则:
          - delta: 按 (item, group) 分组 Σ=0
          - system_delta: 跳过守恒检查（系统间转移）
          - recipe: 跳过守恒检查（配方转换内部自平衡）
          - attr: 跳过（非守恒量）

        Returns:
            ValidationOutcome:
              - result: "pass" | "soft_warn" | "hard_fail"
              - message: 摘要
              - details: 逐项结果
              - passed_groups: 通过校验的 group 集合（调用方据此过滤 ops）
        """
        self._passed_groups: set[str | None] = set()

        if not ops:
            return ValidationOutcome(ValidationResult.PASS, "无操作，自动通过")

        bypassed = [op for op in ops if op.get("op") in ("system_delta", "recipe")]
        for b in bypassed:
            logger.debug(f"[度守恒] 跳过 {b.get('op')}: {b}")

        deltas = [op for op in ops if op.get("op") == "delta"]
        if not deltas:
            msg = "无 delta 操作"
            if bypassed:
                msg += f"，{len(bypassed)} 条已跳过守恒检查"
            return ValidationOutcome(ValidationResult.PASS, msg, passed_groups=set())

        # 按 (item, group) 分组求和
        group_sums: dict[tuple[str, str | None], int] = {}
        for d in deltas:
            tgt = d.get("tgt", "")
            delta = d.get("delta", 0)
            group = d.get("group", None)
            if not tgt:
                continue
            if self._graph and not self._graph.is_conserved(tgt):
                continue
            key = (tgt, group)
            group_sums[key] = group_sums.get(key, 0) + delta

        details: list[str] = []
        hard_fail = False

        # 按 group 汇总每个物品的 Σ
        group_items: dict[str | None, dict[str, int]] = {}
        for (tgt, group), total in group_sums.items():
            if group not in group_items:
                group_items[group] = {}
            group_items[group][tgt] = total

        for group, gitems in group_items.items():
            group_tag = f"[group={group}]" if group is not None else "[全局]"
            group_ok = True
            for tgt, total in gitems.items():
                if abs(total) <= epsilon:
                    details.append(f"  {tgt}: Σ = 0 ✅ {group_tag} 度守恒")
                elif total > 0:
                    details.append(f"  {tgt}: Σ = {total:+d} ❌ {group_tag} 正不平衡（凭空创造）！")
                    hard_fail = True
                    group_ok = False
                else:
                    details.append(f"  {tgt}: Σ = {total:+d} ⚠️ {group_tag} 负不平衡（消耗/使用）")
                    details.append(f"    → 允许（物品消耗是合理行为）")
            if group_ok:
                self._passed_groups.add(group)

        # 负平衡检查（全局，不分组）：任何人持有量 ≥ 0
        if self._graph:
            for d in deltas:
                src = d.get("src", "")
                tgt = d.get("tgt", "")
                if not src or not tgt:
                    continue
                ent = self._graph.get_entity(src)
                if not ent or not ent.is_starter:
                    continue
                if not self._graph.is_conserved(tgt):
                    continue
                current = self._graph.get_held_quantity(src, tgt)
                if current < 0:
                    details.append(f"  {src} 持有 {tgt} = {current} < 0 ❌")
                    return ValidationOutcome(
                        ValidationResult.HARD_FAIL,
                        f"{src} 持有守恒量 {tgt} 为负 ({current})",
                        details,
                        passed_groups=set(),
                    )

        details_str = "\n".join(details)
        if details:
            logger.debug(f"[度守恒]\n{details_str}")

        if hard_fail:
            fail_items = []
            for group, gitems in group_items.items():
                for tgt, total in gitems.items():
                    if total > 0:
                        gtag = f"[group={group}]" if group is not None else "[全局]"
                        fail_items.append(f"{tgt}={total:+d}{gtag}")
            return ValidationOutcome(
                ValidationResult.HARD_FAIL,
                "部分分组度守恒校验失败: " + "; ".join(fail_items),
                details,
                passed_groups=self._passed_groups,
            )

        if any(val < 0 for gitems in group_items.values() for val in gitems.values()):
            warn_items = []
            for group, gitems in group_items.items():
                for tgt, total in gitems.items():
                    if total < 0:
                        gtag = f"[group={group}]" if group is not None else "[全局]"
                        warn_items.append(f"{tgt}={total:+d}{gtag}")
            return ValidationOutcome(
                ValidationResult.SOFT_WARN,
                "度守恒警告（物品消耗）: " + "; ".join(warn_items),
                details,
                passed_groups=self._passed_groups,
            )

        return ValidationOutcome(ValidationResult.PASS, "度守恒校验通过 ✅", details,
                                  passed_groups=self._passed_groups)
