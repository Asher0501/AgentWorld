# Patch: 度守恒集成进 LLM #5 预写校验层

## 问题

度守恒校验（ConservationValidator）原来是独立步骤，在 LLM #4a 和 LLM #4b 之间静默过滤坏操作。
不生成反馈、不触发重试，模型永远不会知道自己的操作被丢弃了一部分。

## 改动

### 1. verification_registry.py — 注册 code=5 degree_conservation 校验

- 新增 `@register(code=5, name="degree_conservation")` 校验函数
- 调用 `ConservationValidator.validate_deltas()` 检测分组度守恒
- 用 `graph_engine.get_entity().name` 反解 entity_id → 人类可读名
- label_map 作为二次反解来源（不写死名称）
- 返回 `CheckFailure`，含：
  - message: 摘要（哪些组/物品 Σ≠0）
  - details: 格式化后的逐项详情（含通过/失败/警告）

### 2. verification_registry.py — 错误码表加 code=5

```python
5: {
    "title": "degree_conservation",
    "description": "分组度守恒违反 — 组内某物品的 delta 之和不为 0",
    "fix_hint": "补充缺失的对端 delta，使组内每种物品的 Σ(delta) = 0",
}
```

### 3. domain.json — mask 扩展

- `check_names` 增加 `"degree_conservation"`
- `prewrite_layer_mask` 从 6 位扩展为 7 位: `[true, false, true, false, false, false, true]`

### 4. node_config.json — 校验配置

新增 `degree_conservation` 条目，type=dead_code, code=5, enabled=true

### 5. graph_npc_engine.py — 移除独立过滤步骤

- 移除 Step 5.5 的独立 ConservationValidator 块（~20 行）
- 保留**降级过滤**：LLM #5 重试耗尽后仍失败 → 静默移除坏操作（作为安全网）

### 6. verification_layer.py — docstring 更新

列明 index 6 为 degree_conservation

## 集成后的流程

```
LLM #4a → topo_ops（坏的留着）
LLM #4b → attr_ops（用坏的 ops 跑一次，不致命）
LLM #5 → degree_conservation 检测:

  错误码 5 (degree_conservation): tavern_trade 组内食物 Σ=+2
  → 修正: 补充缺失的对端 delta，使 Σ(delta) = 0
    · tavern_trade: 食物 Σ=+2 ❌ 凭空创造
    · 需修正的 组 [tavern_trade]

  ===== 只修不分析 =====
  ...

→ LLM #4a 重试（带反馈）→ LLM #4b 重试
→ 重试耗尽仍失败 → 降级过滤移除坏操作
```

## 验证

- 函数测试：用已知坏 ops（午夜酒馆 tavern_trade 食物 Σ=+2）跑 `run(mask, ctx)` → 返回 1 个 `CheckFailure`，消息含 "tavern_trade" 组 + "食物" 名称
- 反馈格式化：`build_feedback()` 正确输出错误码 5 的描述 + 修正提示 + 逐项详情
- 全量 tick 测试（09:00 白天）：LLM #5 正确运行，capacity_upper_bound 触发的重试流程正常（度守恒此次静默通过——白天 ops 平衡）
