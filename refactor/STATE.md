# State: Step 2/6 ✅ 完成 — intent_executor + LLM #4a → adapter

## 前一步输出
- NPCWorldAdapter 薄壳已创建, prompt_assembler slot 系统就绪 (LLM #1 + #3)
- commit: b9b3d34, tag: refactor/step-1-done

## 本轮完成项

### ✅ 2a — Zone 描述扩充 + zone_type 显式化
- node_config.json: 7 个 zone 描述扩充功能关键词
- global_overview slot: 展示 zone_type
- 效果: LLM #4a 不再将"市场"误解为不存在节点

### ✅ 2b — LLM #2 prompt + 解析移入 adapter
- NPCWorldAdapter: _slot_npc_block(), _build_npc_prompt(), build_global_label_map()
- parse_llm_output(stage=2): 标签解析+操作过滤
- intent_executor.py: 纯编排层 (4900B, 从 ~7000B 缩减)
- 验证 tick: 616.5s 通过
- commit: f99546e, tag: refactor/step-2b-done

### ✅ 2c — LLM #4a 解析 + 度守恒校验移入 adapter
- NPCWorldAdapter: resolve_name(), extract_json(), _parse_topo_output()
- parse_llm_output(stage=4): 命名解析+操作类型过滤
- validate_ops(): 度守恒校验 (delta group Σ ≈ 0)
- post_processor.py: 移除 ~200 行旧版回退代码
- graph_npc_engine.py: ConservationValidator → adapter.validate_ops()
- 验证 tick: 748.6s 通过 (LLM #4a: 7 ops)
- commit: 83a11bb, tag: refactor/step-2c-done

## 当前 adapter 覆盖度
- render_slot: 旧 adapter 委托 + 本地 slot handler
- build_prompt(stage=1..5): 通过 prompt_assembler.assemble()
- build_global_label_map: 全部实体分配 A-Z,a-z 标签
- parse_llm_output(stage=2): ✓ LLM #2 操作解析
- parse_llm_output(stage=4): ✓ LLM #4a 操作解析
- validate_ops(ops, graph): ✓ 度守恒校验
- resolve_name(name, graph): ✓ NPC/Item 名称→entity_id
- extract_json(text): ✓ JSON 提取
- get_node_role(entity_id, graph): ✓ ACTOR/LOCATION/RESOURCE
- get_node_descriptor(entity_id, graph): ✓ 完整描述

## 下一步 (Step 3)
- LLM #4b 内容层解析移入 adapter
- 验证层 (verification_layer.py) 移入 adapter
- NPCWorldAdapter 不再依赖 OldDomainAdapter