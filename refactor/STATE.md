# State: Phase II ✅ — PipelineEngine 通用编排

## Phase II 进展

### Step A ✅ — 类型系统扩展
- `StageOutputType` 枚举（RAW_TEXT, GRAPH_OPS, PLANS_MAP, NARRATIVES, ATTR_UPDATE, VERIFY_RESULT, INTENT_EXEC）
- `PipelineStage.output_type` 字段（各阶段声明自己的输出格式）
- `StageResult` 扩展：`plan_map`, `narratives`, `state_changes`, `text_output`, `extra`
- `PipelineEngine.run_stage_plan()` 新增（per-NPC 单次调用）
- `PipelineEngine.parse_ops()` 智能跳过非 GRAPH_OPS 类型
- NPCWorldAdapter 5 个 stage 全部声明 `output_type`

### Step B ✅ — PipelineOrchestrator 框架
- 创建 `services/pipeline_orchestrator.py` (446 行)
- `PipelineContext` 统一上下文（plan_map, npc_info, stories, ops 等）
- `PipelineOrchestrator.run_tick()` 编排 Step 2-7
- `_execute_4llm_pipeline()` 从 200 行硬编码 → 40 行委托调用
- 死导入清理（`PostProcessor`, `InteractionLayer`, `VerificationLayer`, `PipelineEngine`, `get_verification_config`, `build_one_npc_prompt`）
- `graph_npc_engine.py` 从 819 行减到 650 行

## 已完成里程碑
- [x] Phase I: 接口通用化 + prompt 模板 + 脱离旧 adapter
- [x] Phase II Step A: 类型系统扩展
- [x] Phase II Step B: PipelineOrchestrator 框架

## 下一个
### Step C — 迁移 LLM #1（_build_npc_plans → orchestrator._run_stage_plan）
### Step D — 迁移 LLM #3（InteractionLayer → orchestrator 直接管理）
### Step E — 迁移验证循环（PostProcessor + VerificationLayer → _run_verification_loop 内联）
### Step F — 清理：删除桥接、删除旧类、验证 tick

## Phase I 历史
### Phase I — 接口通用化
- **Step A** ✅ — 重写 adapter.py 抽象接口（610.6s 验证通过）
- **Step B** ✅ — Prompt 模板脱离全局字典（679.4s 验证通过）
- **Step C** ✅ — NPCWorldAdapter 脱离 OldDomainAdapter（417.6s 验证通过）
- 删除 src/agent_world/services/domain_adapter.py (349 行)
