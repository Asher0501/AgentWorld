"""
PipelineEngine — 通用 LLM Pipeline 编排引擎。

职责：
  - 按 adapter 定义运行 pipeline stage（prompt → LLM → parse）
  - 统一的 IO 日志记录 + 计时
  - 校验器链
  - 域特定编排（retry loop、合并复杂类型）由调用方处理
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Any, Callable, Optional

from ..domain.adapter import (
    DomainAdapter, GraphOp, StateChange,
    StageOutputType, NodeDescriptor,
)

logger = logging.getLogger("PipelineEngine")


class StageResult:
    """单个阶段的输出结果——支持多类型输出。"""

    def __init__(
        self,
        key: str,
        label: str,
        raw: str = "",
        ops: list[GraphOp] | None = None,
        plan_map: dict[str, str] | None = None,
        narratives: list[str] | None = None,
        state_changes: list[StateChange] | None = None,
        text_output: str = "",
        extra: dict[str, Any] | None = None,
        time_s: float = 0.0,
        status: str = "ok",
        output_type: StageOutputType = StageOutputType.RAW_TEXT,
    ):
        self.key = key
        self.label = label
        self.raw = raw
        self.text_output = text_output
        self.ops = ops or []
        self.plan_map = plan_map or {}
        self.narratives = narratives or []
        self.state_changes = state_changes or []
        self.extra = extra or {}
        self.time_s = time_s
        self.status = status
        self.output_type = output_type

    @property
    def has_ops(self) -> bool:
        return bool(self.ops)

    @property
    def has_plans(self) -> bool:
        return bool(self.plan_map)

    def __repr__(self):
        return (f"StageResult(key={self.key}, status={self.status}, "
                f"time={self.time_s:.1f}s, ops={len(self.ops)}, "
                f"plans={len(self.plan_map)}, narratives={len(self.narratives)})")

    # ─── 旧接口兼容 ───
    def to_stage_result_legacy(self) -> "StageResult":
        """返回与旧 run_stage() 调用方兼容的结果（ops 在顶层）。"""
        return self


class PipelineEngine:
    """通用编排引擎。

    用法：
        engine = PipelineEngine(adapter, resolver, graph_engine, io_dir)
        result = await engine.run_stage("topo_delta", ctx)
    """

    def __init__(
        self,
        adapter: DomainAdapter,
        resolver: Any,
        graph_engine: Any,
        io_dir: str = "",
    ):
        self.adapter = adapter
        self.resolver = resolver
        self.graph_engine = graph_engine
        self.io_dir = io_dir
        self._timing: dict[str, float] = {}
        self._stages: dict[str, Any] = {}
        for s in adapter.get_pipeline_stages():
            self._stages[s.key] = s
        # 读取 stage_settings（system prompt + temperature），域配置驱动，隔离拓扑-内容
        self._stage_settings: dict[str, dict] = {}
        try:
            raw = adapter._adapter_data.get("stage_settings", {})
            if isinstance(raw, dict):
                self._stage_settings = raw
        except Exception:
            pass

    def get_timing(self) -> dict[str, float]:
        return dict(self._timing)

    def _save_io(self, prompt: str, response: str, stage_key: str, suffix: str) -> None:
        if not self.io_dir:
            return
        os.makedirs(self.io_dir, exist_ok=True)
        base = os.path.join(self.io_dir, f"{stage_key}{suffix}")
        try:
            with open(base + "_prompt.txt", "w") as f:
                f.write(prompt)
            with open(base + "_response.txt", "w") as f:
                f.write(response)
        except OSError as e:
            logger.warning(f"[IO] 写入失败 ({base}): {e}")

    async def call_llm_async(self, prompt: str, stage_key: str, suffix: str = "") -> str:
        """异步 LLM 调用 + IO 记录 + 计时（事件循环不阻塞）。

        按 stage_key 从 stage_settings 中读取 system prompt 和 temperature，
        传递给 resolver。无配置项时使用 resolver 默认值。
        """
        t0 = time.time()
        self._save_io(prompt, "<!-- waiting -->", stage_key, suffix)
        settings = self._stage_settings.get(stage_key, {})
        sp = settings.get("system") if isinstance(settings, dict) else None
        temp = settings.get("temperature") if isinstance(settings, dict) else None
        raw = await asyncio.to_thread(self.resolver.call_llm, prompt, sp, temp)
        elapsed = time.time() - t0
        self._timing[stage_key] = self._timing.get(stage_key, 0) + elapsed
        self._save_io(prompt, raw, stage_key, suffix)
        return raw

    def parse_ops(self, stage_key: str, raw: str, label_map: dict | None = None) -> list[GraphOp]:
        """解析阶段 LLM 输出 → GraphOps。

        对于需要标签解析的阶段（如 topo_structure），传入 label_map
        以便将 LLM 输出的标签 {entity_id} 解析回实体 ID。
        """
        stage = self._stages.get(stage_key)
        if not stage:
            logger.warning(f"[parse] 未知阶段: {stage_key}")
            return []
        if stage.output_type == StageOutputType.PLANS_MAP:
            return []
        if stage.output_type == StageOutputType.NARRATIVES:
            return []
        if stage.output_type == StageOutputType.ATTR_UPDATE:
            return []
        if stage.output_type == StageOutputType.VERIFY_RESULT:
            return []
        if stage.output_type == StageOutputType.RAW_TEXT:
            return []
        # topo_structure: 注入 label_map 解析单字母标签
        if stage_key == "topo_structure" and label_map:
            from ..domain.npc_world.adapter import _parse_topostruct_ops
            return _parse_topostruct_ops(raw, label_map)
        return stage.parser(raw, self.graph_engine)

    def validate_ops(self, ops: list[GraphOp]) -> list[GraphOp]:
        """运行 adapter 注册的所有校验器。"""
        for v in self.adapter.get_validators():
            ops = v.check(ops, self.graph_engine)
        return ops

    async def run_stage(
        self,
        stage_key: str,
        prompt_kwargs: dict,
        label_map: dict[str, str] | None = None,
        suffix: str = "",
    ) -> StageResult:
        """完整批量阶段：build_prompt → LLM → parse → validate → StageResult。

        结果的 output_type 由 stage 定义决定。
        仅适用于 GRAPH_OPS 类型阶段。
        """
        stage = self._stages.get(stage_key)
        if not stage:
            logger.warning(f"[run] 未知阶段: {stage_key}")
            return StageResult(key=stage_key, label="?", status="no_stage")

        t0 = time.time()
        prompt = self._build_prompt(stage, prompt_kwargs, label_map)
        raw = await self.call_llm_async(prompt, stage_key, suffix)
        ops = self.parse_ops(stage_key, raw, label_map=label_map)
        validated = self.validate_ops(ops)

        if len(validated) < len(ops):
            logger.info(f"[{stage_key}] 校验器移除 {len(ops) - len(validated)}/{len(ops)} 操作")

        elapsed = time.time() - t0
        self._timing[stage_key] = self._timing.get(stage_key, 0) + elapsed

        return StageResult(
            key=stage_key, label=stage.label, raw=raw,
            ops=validated, time_s=elapsed,
            status="ok" if validated or not ops else "empty",
            output_type=StageOutputType.GRAPH_OPS,
        )

    async def run_stage_raw(
        self,
        stage_key: str,
        prompt_kwargs: dict,
        label_map: dict[str, str] | None = None,
        suffix: str = "",
    ) -> StageResult:
        """仅 prompt → LLM（不含 parse/validate），返回原始文本。"""
        stage = self._stages.get(stage_key)
        if not stage:
            return StageResult(key=stage_key, label="?", status="no_stage")
        t0 = time.time()
        prompt = self._build_prompt(stage, prompt_kwargs, label_map)
        raw = await self.call_llm_async(prompt, stage_key, suffix)
        elapsed = time.time() - t0
        self._timing[stage_key] = self._timing.get(stage_key, 0) + elapsed
        return StageResult(
            key=stage_key, label=stage.label, raw=raw, time_s=elapsed,
            output_type=StageOutputType.RAW_TEXT,
        )

    async def run_stage_plan(
        self,
        stage_key: str,
        prompt: str,
        suffix: str = "",
    ) -> StageResult:
        """为 per-NPC 计划阶段单次 LLM 调用（手动 prompt 后调用）。"""
        stage = self._stages.get(stage_key)
        t0 = time.time()
        raw = await self.call_llm_async(prompt, stage_key, suffix)
        elapsed = time.time() - t0
        self._timing[stage_key] = self._timing.get(stage_key, 0) + elapsed
        return StageResult(
            key=stage_key, label=stage.label if stage else stage_key,
            raw=raw, time_s=elapsed,
            output_type=StageOutputType.RAW_TEXT,
        )

    async def run_stage_plan_combined(
        self,
        npc_prompts: list[tuple[str, str]],
        stage_key: str,
        suffix: str = "",
    ) -> dict[str, str]:
        """
        LLM #1: 合并多 NPC prompt 为一次 LLM 调用 → 按 NPC 解析结果。
        通过 call_llm_async（统一入口）走，确保单点监控/计时。

        Args:
            npc_prompts: [(npc_entity_id, prompt_string), ...]
            stage_key: 阶段标识（如 "plans"）
            suffix: IO 文件后缀

        Returns:
            {npc_entity_id: plan_text}
        """
        t0 = time.time()
        combined = self.resolver._build_combined_prompt(npc_prompts)
        raw = await self.call_llm_async(combined, stage_key, suffix)
        elapsed = time.time() - t0
        self._timing[stage_key] = self._timing.get(stage_key, 0) + elapsed

        results: dict[str, str] = {}
        if not raw or not raw.strip():
            logger.warning(f"[run_stage_plan_combined] LLM 返回空")
            return results

        parsed = self.resolver._parse_combined_response(raw)
        if isinstance(parsed, dict):
            for eid, instr in parsed.items():
                if isinstance(instr, str) and instr.strip():
                    results[eid] = instr
                elif isinstance(instr, dict):
                    action = instr.get("action", "") or instr.get("result_text", str(instr))
                    results[eid] = action
                else:
                    logger.warning(f"NPC {eid} 指令格式无效: {type(instr).__name__}")

        logger.info(f"[run_stage_plan_combined] {len(results)}/{len(npc_prompts)} 条计划")
        return results

    # ─── 内部 ───

    def _build_prompt(self, stage, prompt_kwargs, label_map) -> str:
        stage_num = self._stage_number(stage.key)
        return self.adapter.build_prompt(
            stage_num, prompt_kwargs, graph=self.graph_engine,
            label_map=label_map, **prompt_kwargs,
        )

    def _stage_number(self, key: str) -> int:
        for i, s in enumerate(self.adapter.get_pipeline_stages()):
            if s.key == key:
                return i + 1
        return 0
