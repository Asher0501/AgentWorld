# State: Phase I Step B ✅ — Prompt 模板移入 Adapter

## 已完成
### Phase I — 接口通用化
**Step A** ✅ — 重写 adapter.py 抽象接口
- 验证 tick: 610.6s 通过

**Step B** ✅ — Prompt 模板脱离全局字典
- PROMPT_TEMPLATES 从 prompt_assembler.py 移入 NPCWorldAdapter
- prompt_assembler.assemble() 改为调用 adapter.get_prompt_template(name)
- prompt_assembler 纯渲染器：不拥有任何模板定义
- SlotDef 新增 provider 字段（content/topology/runtime）
- 渲染逻辑（_render_topo_slot / _render_runtime_slot）留在 prompt_assembler
- 验证 tick: 679.4s 通过

**Next: Step C** — NPCWorldAdapter 脱离 OldDomainAdapter
- 现状: NPCWorldAdapter 仍然委托 _old_adapter 做 slot 渲染
- 目标: domain.json + node_config.json 直接读取 → 删除 OldDomainAdapter
