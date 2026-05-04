# State: Step 2/6 ⏳ 进行中 — intent_executor + LLM #4a → adapter

## 前一步输出
- prompt_assembler slot 系统已就绪 (LLM #1 + #3)
- NPCWorldAdapter 薄壳已创建
- commit: 6a64113 / b9b3d34, tag: refactor/step-1-done

## 本轮任务

拆为 3 个子步骤：

### 2a — Zone 描述扩充 + zone_type 显式化（先配置后验证）
- 改写 node_config.json 中 7 个 zone 的 description，每句加功能关键词
- 在 global_overview slot 中展示 zone_type
- 跑 tick → 看 LLM #4a 能否自行将"市场"匹配到诺维格瑞

### 2b — LLM #2 prompt 移入 slot 系统
- intent_executor.py: _build_prompt() → 走 assemble("llm2_structure", ...)
- _build_combined_prompt() → 改为 slot 驱动的 NPC block
- _parse_ops() → adapter.parse_llm_output(stage=2)
- 跑 tick → 验证 LLM #2 输出一致

### 2c — LLM #4a + 校验移入 adapter
- PostProcessor.resolve_topology_changes() 走 assemble("llm4a_topo", ...)
- _parse_4a_ops() → adapter.parse_llm_output(stage=4)
- ConservationValidator → adapter.validate_ops()
- 跑 tick → 完整回归

## 全局配置（不变）
- DomainAdapter 接口签名
- GraphEngine 纯图论层
- DB 存储层
