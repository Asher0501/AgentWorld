# State: Phase III 🔄 — 跨域支持

## Phase III 进展
### Step A ✅ — resolve_eid() 通用化
- 添加 `get_all_prefixes()` 到 config_loader
- `resolve_eid()` 循环尝试所有已注册前缀替代硬编码 `item_`
- 支持 `npc_`/`item_`/`zone_`/`obj_` 任意前线

## 已完成里程碑
- [x] Phase I: 接口通用化 + prompt 模板 + 脱离旧 adapter
- [x] Phase II: PipelineEngine 通用编排（全部 6 步）
- [x] Phase III Step A: resolve_eid() 通用化

## 下一步
### Step B — _infer_type() / _is_item_type() 去掉 `startswith("item_")` 兜底
### Step C — _parse_topo_gt() 去掉 `startswith("npc_")`
### Step D — 校验器通用化
### Step E — zone_ 前缀统一走 has_role("region")
### Step F — graph_adapter EID 前缀从 node_types 读
### Step G — NPCRole 枚举 → 数据驱动

## Phase II 历史
### Phase II — PipelineEngine 通用编排
- **Step A** ✅ — 类型系统扩展
- **Step B** ✅ — PipelineOrchestrator 框架
- **Step C** ✅ — LLM #1 迁移
- **Steps D+E+F** ✅ — 旧类引用清理

## Phase I 历史
### Phase I — 接口通用化
- **Step A** ✅ — 重写 adapter.py 抽象接口（610.6s 验证通过）
- **Step B** ✅ — Prompt 模板脱离全局字典（679.4s 验证通过）
- **Step C** ✅ — NPCWorldAdapter 脱离 OldDomainAdapter（417.6s 验证通过）
- 删除 src/agent_world/services/domain_adapter.py (349 行)
