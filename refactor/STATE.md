# State: Phase II ✅ — PipelineEngine 通用编排

## Phase II 进展
### Step A ✅ — 类型系统扩展
- `StageOutputType` 枚举 + `PipelineStage.output_type` 字段
- `StageResult` 扩展（plan_map, narratives, state_changes, text_output, extra）

### Step B ✅ — PipelineOrchestrator 框架
- `services/pipeline_orchestrator.py` 创建
- `PipelineContext` 统一上下文
- `run_tick()` 编排 Step 2-7，委托 LLM #1 外部

### Step C ✅ — LLM #1 迁移
- `_build_npc_plans()` (128 行) 从 graph_npc_engine 移入 orchestrator._run_stage_plan()
- `_execute_intents()` (130 行) 从 graph_npc_engine 删除（orchestrator 已有完整版本）
- `_val_to_*_text()` 3 个辅助函数删除
- `run_tick()` 不再需要 plan_map/npc_info 参数——内部自建

### Steps D+E+F ✅ — 旧类引用清理
- `_lazy_import()` 删除 → 直接顶部导入 PostProcessor, InteractionLayer, VerificationLayer
- 所有 `self._IL/_PP/_VL/_has_role` 替换为直接引用
- graph_npc_engine.py: 819 → 370 行（-449 行）
- orchestrator: 514 行，pipeline_engine: 252 行

## 当前管线架构（Phase II 完成）
```
tick()
  ├─ 构建图（build_world_graph）
  ├─ orchestrator.run_tick(npcs, ...)
  │   ├─ _run_stage_plan()          ← LLM #1
  │   ├─ _stage_topo_structure()    ← LLM #2
  │   ├─ _execute_intents()         ← 非 LLM
  │   ├─ _stage_narrative()         ← LLM #3
  │   ├─ _stage_verification_loop() ← #4a/#4b/#5
  │   ├─ _apply_final_ops()
  │   └─ _build_tick_results()
  ├─ _decay_and_sync()
  └─ _sync_back_to_nodes()
```

## 已完成里程碑
- [x] Phase I: 接口通用化 + prompt 模板 + 脱离旧 adapter
- [x] Phase II: PipelineEngine 通用编排（全部 6 步）

## 待办（未来）
- 运行验证 tick 确认行为不变
- 考虑是否删除 PostProcessor/InteractionLayer/VerificationLayer 文件（共 ~43KB 代码）——当前架构下 orchestrator 直接实例化它们，无中间层

## Phase I 历史
### Phase I — 接口通用化
- **Step A** ✅ — 重写 adapter.py 抽象接口（610.6s 验证通过）
- **Step B** ✅ — Prompt 模板脱离全局字典（679.4s 验证通过）
- **Step C** ✅ — NPCWorldAdapter 脱离 OldDomainAdapter（417.6s 验证通过）
- 删除 src/agent_world/services/domain_adapter.py (349 行)
