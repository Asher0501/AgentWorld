"""
NPC Prompt Builder —— 已废弃。

LLM #1 的 prompt 构建已迁移到 slot 系统：
  - 模板内容：config/domain.json → adapter["survival_needs"]
  - 槽位注册：domain/npc_world/adapter.py → slot_survival_needs()
  - prompt 组装：services/prompt_assembler.py → assemble("llm1_plan", ...)
  - 编排调用：services/pipeline_orchestrator.py → _run_stage_plan()

旧版 build_one_npc_prompt() / build_one_fallback_prompt() 已移除。
"""
