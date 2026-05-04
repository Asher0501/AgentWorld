# State: Phase I Step A ✅ — 通用 DomainAdapter 接口

## 已完成
### Phase I — 接口通用化
**Step A** ✅ — 重写 adapter.py 抽象接口
- 新接口: classify_node / describe_node / get_config / get_pipeline_stages / get_prompt_template / get_validators
- NodeClassification 布尔旗替代枚举 NodeRole
- 所有 NPC 专属方法改为桥接（get_zones → get_config("zones") 等）
- NPCWorldAdapter 实现新接口 + 保留旧方法做桥接
- 验证 tick: 610.6s 通过（与 Step 2c tick 行为一致）

**Next: Step B** — prompt 模板脱离全局字典
- prompt_assembler.STAGE_TEMPLATES → adapter.get_prompt_template(name) 提供
- prompt_assembler 只做渲染器，不再是模板仓库
