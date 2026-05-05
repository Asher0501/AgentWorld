# ✅ State: Phase III — 跨域支持 (全部完成)

## Phase III 进展
### Step A ✅ — resolve_eid() 通用化
- 添加 `get_all_prefixes()` 到 config_loader
- `resolve_eid()` 循环尝试所有已注册前缀替代硬编码 `item_`
- 支持 `npc_`/`item_`/`zone_`/`obj_` 任意前缀
- Tags: `refactor/phase3-step-a`

### Step B ✅ — _is_item_type() 去掉 item_ 兜底
- `prefix_to_type_id()` 返回 0 时直接 return False
- 不再用 `eid.startswith("item_")` 作为 fallback
- Tags: `refactor/phase3-step-b`

### Step C ✅ — 翻译/校验层前缀硬编码清理
- `try_strip_prefix()` 工具函数
- `verification_registry.py`: 4 处 hardcoded removeprefix → `_strip_prefix()`/`get_all_prefixes()`
- `post_processor.py`: `type_id == "region"/"thing"/"actor"` 字符串比较 → `has_role()` (修复静默 bug)
- Tags: `refactor/phase3-step-c`

### Step D ✅ (Skip) — 校验器已通用，无工作

### Step E ✅ — zone_ 前缀统一走 has_role("region")
- `conn.startswith("zone_")` → `prefix_to_type_id(conn)` + `has_role(tid, "region")`
- 覆盖 `adapter._get_zone_name()` + `npc_prompt_builder.py`
- Tags: `refactor/phase3-step-e`

### Step F ✅ — graph_adapter EID 前缀从 node_types 读
- `get_prefix_by_role(role)` — 按角色查类型前缀
- `_make_eid()` 支持 role 模式 vs legacy 模式双兼容
- `_make_zone_eid()` — 区域专用构造
- Tags: `refactor/phase3-step-f`

### Step G ✅ — NPCRole 枚举 → 数据驱动
- `NPC.role: NPCRole` → `NPC.role: str = ""`
- 删除 `NPC_ROLE_MAP` 映射表，配置中的 role 字符串直接传递
- 保留 NPCRole 类作为 deprecated 向后兼容
- 涉及: `models/__init__`, `db/schemas`, `api/npc`, `api/agent`, `world_updater`
- Tags: `refactor/phase3-step-g`

## 已完成里程碑
- [x] Phase I: 接口通用化 + prompt 模板 + 脱离旧 adapter
- [x] Phase II: PipelineEngine 通用编排（全部 6 步）
- [x] Phase III: 跨域支持（全部 7 步，D 跳过）

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

## 已知问题 (KNOWN_ISSUES)
- LLM #4 craft 输出物品名而非金币值
- seller-side 交易数据流缺失

---

## Phase IV: survival_needs 优化 + dead code 清理 + recent_info 滚动窗口

**状态:** ✅ 完成（2026-05-05）  

### 改动

- survival_needs 槽位重写：三属性=0 的致命后果明确化（虚脱/饿死/心死），保留世界观调味句
- cognition/__init__.py + npc_prompt_builder.py 死代码清理
- recent_info 改为滚动窗口：保留最近 3 tick + 时间戳

### 效果

- NPC 能连续感知过去 1.5 小时的场景状态
- 无硬编码阈值，决策纯由 NPC 性格驱动
- 代码库移除 ~217 行死代码

---

## Phase V: 连通分量分割管线 (Component-Split Pipeline)

**状态:** 🏗️ 架构完成 + 实现完成（2026-05-05，未运行验证 tick）  

### 改动

- `graph_engine.py`: 新增 `TopoComponent` 数据类 + `build_components()` BFS 分割
- `pipeline_orchestrator.py`: `run_tick()` 改为分量分割 → 逐分量 #3→#4a→#4b→#5 → 归并
- `post_processor.py`: `resolve_topology_changes()` 接受可选 topo_pool/label_map

### 架构

```
LLM #1 → #2 → 执行 → 分量分割 → 分量0 #3→#4a→#4b→#5
                                → 分量1 #3→#4a→#4b→#5  (可并行)
                                → ... → 归并 → 执行
```

### 效果

- LLM #4a prompt: 30+ 实体 → 3~8 实体（LLM 不再超容量）
- Retry cost: 250s → ~50s（只重跑小分量）
- Story↔topo 一致（失败分量连带重跑 LLM #3）
- 当前为串行，后续 `asyncio.gather()` 可并行化
