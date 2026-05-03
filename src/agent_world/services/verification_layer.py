"""
VerificationLayer (预写校验层) — 纯校验器。

在图操作落地前，批量检查操作是否合法。
校验项从 verification_registry 按 mask 位自取。
不再是修正器，而是纯检查器。发现失败后返回 Failure[]，
由上层（graph_npc_engine.py）决定是否重跑 LLM #4。

校验项由 verification_registry 统一注册：
  [0] entity_existence        — 通用实体存在性（域无关）
  [1] quantity_accuracy       — (翻译专用，域无关)
  [2] capacity_upper_bound    — delta 输出量不超过边 qty
  [3] entity_coverage         — (翻译专用，域无关)
  [4] direction_pairing       — (预留 LLM)
  [5] story_consistency       — (预留 LLM)
  [6] degree_conservation     — 分组度守恒 Σ(delta)=0
"""
from __future__ import annotations

import json
import logging

from .verification_registry import CheckFailure, run as run_checks

logger = logging.getLogger(__name__)


# ── 预写校验层 mask（从 domain.json 加载）──
# 索引: 0=entity_existence, 1=quantity_accuracy, 2=capacity_upper_bound,
#        3=entity_coverage, 4=direction_pairing, 5=story_consistency
from .verification_registry import load_layer_mask as _load_layer_mask
_PREWRITE_CHECK_MASK: list[bool] = _load_layer_mask("prewrite_layer_mask")


class VerificationLayer:

    def __init__(self, resolver, adapter, graph_engine, llm_available=False):
        self._resolver = resolver
        self._adapter = adapter
        self._ge = graph_engine
        self._llm_available = llm_available

    def _get_label_map(self) -> dict[str, str]:
        """获取当前图中所有实体的名称→标签映射"""
        from ..config.config_loader import get_all_label_mappings
        return dict(get_all_label_mappings())

    def _build_context(
        self,
        stories: list[str],
        topo_ops: list[dict],
        attr_ops: list[dict],
        recent_info_map: dict[str, str],
    ) -> dict:
        """构建统一 context dict。"""
        # entity_existence: 从操作中提取所有实体引用
        candidates = []
        for op in topo_ops:
            for k in ("src", "tgt", "target"):
                v = op.get(k, "")
                if v and v not in candidates:
                    candidates.append(v)
            # recipe 的 consumes/produces 也有名
            for d in ("consumes", "produces"):
                if d in op:
                    for k in op[d]:
                        if k not in candidates:
                            candidates.append(k)
        for op in attr_ops:
            v = op.get("target", "")
            if v and v not in candidates:
                candidates.append(v)
        for k in recent_info_map:
            if k not in candidates:
                candidates.append(k)

        return {
            # entity_existence (index 0) — 用适配器静态白名单
            "entity_existence_candidates": candidates,
            "entity_existence_whitelist": self._adapter.get_all_entity_names(),
            "entity_existence_label": "topo_ops/attr_ops/recent_info",

            # capacity_upper_bound (index 2)
            "topo_ops": topo_ops,
            "graph_engine": self._ge,

            # direction_pairing / story_consistency (index 4,5 — 预留 LLM)
            "stories": stories,
            "resolver": self._resolver,
            "label_map": self._get_label_map(),
        }

    def check_all(
        self,
        stories: list[str],
        topo_ops: list[dict],
        attr_ops: list[dict],
        recent_info_map: dict[str, str],
    ) -> list[CheckFailure]:
        """
        根据 mask 运行所有已激活的校验项。

        Returns:
            CheckFailure[] — 空列表表示全部通过
        """
        ctx = self._build_context(stories, topo_ops, attr_ops, recent_info_map)
        failures = run_checks(_PREWRITE_CHECK_MASK, ctx)

        for f in failures:
            logger.warning("[预写校验] FAIL: %s", f.to_text())

        return failures

    @staticmethod
    def build_feedback(failures: list[CheckFailure]) -> str:
        """
        将校验失败列表构建为反馈文本，供 LLM #4 重试时参考。

        设计原则：每个错误码附带拓扑描述 + 具体原因，LLM 通过查错误码表理解问题。
        """
        from .verification_registry import get_error_description

        lines = [
            "===== 上一轮校验未通过 (共 {} 项) =====".format(len(failures)),
            "",
        ]

        # 按错误码分组，每组输出描述 + 所有具体失败
        from collections import defaultdict
        by_code: dict[int, list[CheckFailure]] = defaultdict(list)
        for f in failures:
            by_code[f.code].append(f)

        for code in sorted(by_code):
            info = get_error_description(code)
            lines.append(f"错误码 {code} ({info['title']}): {info['description']}")
            lines.append(f"  → 修正: {info['fix_hint']}")
            for cf in by_code[code]:
                lines.append(cf.to_llm_feedback())
            lines.append("")

        lines += [
            "===== 只修不分析 =====",
            "  1. 只修改 JSON 内容，不要写任何分析/解释文字。",
            "  2. 直接输出纯 JSON，不要 markdown 代码块。",
            "  3. 保留原有操作结构，只修改失败字段的值。",
            "  4. 如果不确定怎么做，宁可不输出该操作，不要输出非法 JSON。",
            "",
        ]
        return "\n".join(lines)
