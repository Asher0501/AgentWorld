# State: Phase I ✅ — 接口通用化

## 已完成
### Phase I — 接口通用化
**Step A** ✅ — 重写 adapter.py 抽象接口
- 验证 tick: 610.6s 通过

**Step B** ✅ — Prompt 模板脱离全局字典
- 验证 tick: 679.4s 通过

**Step C** ✅ — NPCWorldAdapter 脱离 OldDomainAdapter
- domain.json 直接读取，20+ slot 方法内联
- 删掉 src/agent_world/services/domain_adapter.py (349 行)
- 验证 tick: 417.6s 通过

## 已完成里程碑
- [x] Step 0: 创建 DomainAdapter 接口（domain/adapter.py）
- [x] Step 1: Prompt 模板抽取（LLM #1 从 build_prompt_for_side → assemble("llm1_plan")）
- [x] Step 2: LLM #2 + #4a + 度守恒校验移入 adapter
- [x] Phase I: 接口通用化 + prompt 模板 + 脱离旧 adapter

## 下一个 Phase
### Phase II — PipelineEngine 通用编排
通用编排引擎，不再使用 hardcoded 5 步循环。
