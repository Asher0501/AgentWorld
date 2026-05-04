# State: Step 1/6 ✅ 完成 — prompt 模板抽取

## 输出产物
- ✅ `src/agent_world/domain/npc_world/adapter.py` — NPCWorldAdapter 薄壳
  - 继承 `domain/adapter.py` 的 DomainAdapter 抽象接口
  - 内容层委托旧 `services/domain_adapter.DomainAdapter`
  - 回退本地 handler: `_slot_available_recipes()`, `_slot_entity_constraints()`
- ✅ `src/agent_world/domain/npc_world/__init__.py`
- ✅ `prompt_assembler.py`: llm1_plan 模板新增 entity_constraints slot
- ✅ `graph_npc_engine.py`: 改用 NPCWorldAdapter + assemble() 替换 build_one_npc_prompt()

## 验证
- ✅ 跑 run_1tick step1-verify：697.9s，完整通过
- ✅ LLM #1: 12/12 计划生成
- ✅ LLM #2: 0 拓扑操作（正确）
- ✅ LLM #3: 7 个故事
- ✅ LLM #4a: 0 ops（已知 bug: 市场节点不存在）
- ✅ LLM #4b: 21 attr, 14 recent_info
- ✅ LLM #5: 校验通过

Tag: refactor/step-1-done
