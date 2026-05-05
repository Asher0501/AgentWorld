# Patch: 连通分量分割管线 (Component-Split Pipeline)

**日期:** 2026-05-05  
**范围:** `37b99e7` (Phase III Step G) → HEAD  
**统计:** 8 文件，+292/-221 行（含分量分割 + Async 重构 + 死代码清理 + LLM #1 统一入口）

---

## 目录

1. [目标](#目标)
2. [设计原则](#设计原则)
3. [架构变更](#架构变更)
4. [文件变更详解](#文件变更详解)
5. [新旧流程对比](#新旧流程对比)
6. [组件说明](#组件说明)
7. [验证方法](#验证方法)

---

## 目标

原有管线是一个全局串行流程：
```
LLM #1 → #2 → 执行 → #3(全部) → #4a(全部) → #4b(全部) → #5(全部) → retry(全部)
```

问题：
- LLM #4a prompt 包含 30+ 实体，LLM 容量易超
- 任一分量校验失败，**全部**重跑（包括已通过的分量）
- 重跑只重试 #4a/#4b，不重跑 #3 → story 与 topo 不一致

**分量分割管线**将世界按连通分量切分，每个分量独立跑 #3 → #4a → #4b → #5：
```
LLM #1 → #2 → 执行 → 分量分割 → 分量0 #3→#4a→#4b→#5
                                → 分量1 #3→#4a→#4b→#5  (可并行)
                                → 分量2 #3→#4a→#4b→#5
```

---

## 设计原则

1. **分量是连通子图**: BFS 遍历，zone `same_type_block=true` 阻断穿透 → 每 zone 一分量
2. **失败隔离**: 只有失败的分量重跑 #3→#4a→#4b→#5
3. **label_map 分量级**: 每分量独立标签映射 `{A}→{B}`，LLM 只看本分量实体
4. **prompt 缩小**: LLM #4a 从 30+ 实体降到 3~8 个，LLM 容量不再超
5. **全量并行**: 通过 `asyncio.gather()` 并行执行所有分量（实测 770s→504s，-35%）
6. **向后兼容**: 同步接口保留至第二阶段清理，`PostProcessor` 已有 async 和 sync 两套
7. **统一 LLM 入口**: 全部 5 条 LLM 调用路径通过 `PipelineEngine.call_llm_async` 单点

---

## 架构变更

### 新增类型

```python
@dataclass
class TopoComponent:
    id: int                    # 分量编号
    eids: set[str]             # 分量内全部 entity_id
    npc_eids: set[str]         # 仅 NPC
    zone_eid: str | None       # 归属 zone
    label_map: dict[str, str]  # {A → entity_id}
    exec_results: list[dict]   # 本分量的交互结果（运行时填充）
    stories: list[str]         # LLM #3 为本分量生成的故事
    topo_ops: list[dict]       # LLM #4a 拓扑操作
    attr_ops: list[dict]       # LLM #4b 属性操作
    recent_info: dict          # LLM #4b 近况投影
    failures: list             # LLM #5 校验失败列表
```

### 新增方法

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `build_components()` | `GraphEngine` | BFS 遍历全图，返回 `list[TopoComponent]` |
| `_split_components(ctx)` | `PipelineOrchestrator` | 分割 exec_results 到各分量 |
| `_run_one_component(ctx, comp)` | `PipelineOrchestrator` | 单分量 #3→#4a→#4b→#5 管线 + retry + 降级 |
| `_stage_narrative_component(ctx, comp)` | `PipelineOrchestrator` | 分量级故事生成 |
| `_merge_commit_components(ctx)` | `PipelineOrchestrator` | 归并所有分量结果 + 执行 |

---

## 文件变更详解

### `src/agent_world/services/graph_engine.py` (+74 行)

- 新增 `TopoComponent` 数据类
- 新增 `build_components()`：BFS 遍历，`same_type_block` 阻断，独立 label_map
- BFS 逻辑与 `post_processor.py` 中原 BFS 完全一致

### `src/agent_world/services/pipeline_orchestrator.py` (+87/-45 行)

- `PipelineContext`：新增 `components: list[TopoComponent]`
- `run_tick()`：注入分量分割 #4 → 全分量并行 #5 → 归并 #6
- 新增 `_split_components()`：从 `graph_engine.build_components()` 获取分量，分配 exec_results
- 新增 `_run_one_component()`：分量级 LLM #3 → #4a → #4b → #5 → retry → 降级
  - 传递 `topo_pool=comp.eids` 和 `label_map=comp.label_map` 跳过 BFS
- 新增 `_stage_narrative_component()`：只处理分量级 exec_results
- 新增 `_merge_commit_components()`：归并 topo/attr/ri/stories → 调用 `_apply_final_ops()`
- 删除 `_stage_verification_loop`（被分量版本取代）
- 删除 `_stage_narrative` 全局版（被 per-component 版本取代）
- `run_tick()` 内全分量通过 `asyncio.gather()` 并行

### `src/agent_world/services/post_processor.py` (+80/-75 行)

- 新增 `_call_llm_async()` 异步 LLM 入口
- 新增 `resolve_topology_changes_async()` / `resolve_attr_and_recent_async()` 异步版
- 删除 `_call_llm`(sync)、`resolve_topology_changes`(sync)、`resolve_attr_and_recent`(sync) 死代码
- `topo_pool` / `label_map` 可选参数在异步版中保留

### `src/agent_world/services/pipeline_engine.py` (+45/-30 行)

- 新增 `call_llm_async()` 异步入口（统一 5 条 LLM 调用）
- 新增 `run_stage_plan_combined()` 合并 prompt → call_llm_async → 解析返回
- 删除 `call_llm`(sync)、`call_batch_llm`(sync) 死代码
- `run_stage`、`run_stage_raw`、`run_stage_plan` 改为 async def，使用 call_llm_async

### `src/agent_world/services/interaction_layer.py` (+4/-3 行)

- `process()` 改为 async def
- 新增 engine 参数，通过 `engine.call_llm_async()` 调用 LLM #3

### `src/agent_world/services/interaction_resolver.py` (-51 行)

- 删除 `resolve_all_npcs()` 和 `resolve_all_npcs_async()`（被 `engine.run_stage_plan_combined` 取代）
- 保留 `_build_combined_prompt()` 和 `_parse_combined_response()` 供 engine 使用

---

## 新旧流程对比

### 旧流程

```
run_tick()
  ├── _run_stage_plan()           ← LLM #1（13 个 NPC 并发）
  ├── _stage_topo_structure()     ← LLM #2（全局）
  ├── _execute_intents()          ← 非 LLM（全局）
  ├── _stage_narrative()          ← LLM #3（全局，6 个故事）
  ├── _stage_verification_loop()  ← #4a/#4b/#5（全局）
  │     ├── resolve_topology_changes()   ← LLM #4a（30+ 实体 BFS）
  │     ├── resolve_attr_and_recent()    ← LLM #4b（全 NPC）
  │     ├── check_all()                  ← LLM #5（全局校验）
  │     └── retry (all)                 ← 全部重跑
  ├── _apply_final_ops()
  └── _build_tick_results()
```

**问题**: BFS 遍历全部 13 NPC → prompt 30+ 实体 → LLM 超容量 → retry 重跑全部 130+ 秒

### 新流程

```
run_tick()
  ├── _run_stage_plan()             ← LLM #1（13 个 NPC 并发，不变）
  ├── _stage_topo_structure()       ← LLM #2（全局，不变）
  ├── _execute_intents()            ← 非 LLM（不变）
  ├── _split_components()           ← GraphEngine BFS 分割
  │     └── build_components()      ← 返回 6 个分量（每 zone 一个）
  │
  ├── asyncio.gather(components)    ← 全量并行
  │     ├── _stage_narrative_component()     ← LLM #3（async, 2~4 条边）
  │     ├── resolve_topology_changes_async() ← LLM #4a（async, 3~8 实体）
  │     ├── resolve_attr_and_recent_async()  ← LLM #4b（async, 本分量 NPC）
  │     ├── check_all()                      ← LLM #5（本分量校验）
  │     └── retry (component only)           ← 只重跑本分量
  │
  ├── _merge_commit_components()   ← 归并 + _apply_final_ops()
  └── _build_tick_results()
```

**收益**:
- LLM #4a prompt: 30+ 实体 → 3~8 实体
- Retry: 250s → ~50s（只重跑一个小分量）
- 并行实测: 770s → 504s（-35%），LLM 入口统一后 435s

---

## 组件说明

### TopoComponent

```
TopoComponent {
  id: 0,                  # 如 维吉玛分量
  eids: {"npc_xxx", "zone_vizima", "item_food", ...}
  npc_eids: {"npc_xxx", "npc_yyy"}
  zone_eid: "zone_vizima"
  label_map: {"A" → "npc_xxx", "B" → "item_food", ...}
  exec_results: [{...}, {...}]  # 本 zone 的交互结果
}
```

### 分量级 label_map

每个分量使用独立标签映射，`A` / `B` 仅在分量内有意义。这天然避免了全局映射的标签冲突。

### BFS 阻断策略

```
zone (same_type_block=true)      → 加入但不穿透 zone→zone
item (terminal=true)              → 加入但不扩展
NPC (terminal=false, same=false)  → 正常扩展
```

当前 7 个 zone 中 6 个有 NPC → 6 个连通分量。无 NPC 的 zone 不构成分量。

---

## 验证方法

1. 单元测试验证 `build_components()` 按 zone 正确分割
2. 运行 tick，确认旧 `_stage_verification_loop` 仍可调用（向后兼容）
3. 运行并行分量管线 tick（`async-test-01`：504.8s，6 分量全部成功）
4. 死代码清理后验证（`cleanup-verify-01`：379.8s，0 重试）
5. LLM #1 统一入口后验证（`llm1-integration-01`：435.7s）
6. 验证 `_call_log` 正确捕获全部 5 条 LLM 调用路径

当前为 `asyncio.gather()` **全量并行**执行：

```python
# 实际（并行）:
tasks = [self._run_one_component(ctx, comp) for comp in ctx.components]
await asyncio.gather(*tasks)
```

---

## 文件变更索引

| 文件 | +行 | -行 | Σ |
|------|-----|-----|---|
| `services/graph_engine.py` | 74 | 0 | +74 |
| `services/pipeline_orchestrator.py` | 87 | 45 | +42 |
| `services/pipeline_engine.py` | 45 | 30 | +15 |
| `services/post_processor.py` | 80 | 75 | +5 |
| `services/interaction_layer.py` | 4 | 3 | +1 |
| `services/interaction_resolver.py` | 0 | 51 | -51 |
| `run_1tick.py` | 0 | 12 | -12 |
| `services/graph_adapter.py` | 2 | 5 | -3 |
| **合计（分量分割 + Async 重构 + 死代码清理 + LLM #1 统一入口）** | **292** | **221** | **+71** |

（注：行数包含 Phase IV 补丁、分量分割核心、Async 重构、死代码清理、LLM #1 统一入口的全部变更。）

---

## 附录：Async 重构详情

### 背景

重构前 5 条 LLM 调用路径有 3 种不同入口：

| LLM | 旧入口 | 问题 |
|-----|--------|------|
| #1 | `resolver.resolve_all_npcs_async` → sync `_call_llm` | 不经过 engine，无统一监控 |
| #2 | `engine.run_stage` → `engine.call_llm` (sync) | sync 方法阻塞事件循环 |
| #3 | `InteractionLayer.process` (sync) → `engine.call_llm` (sync) | sync 阻塞 |
| #4a/#4b | `PostProcessor._call_llm` (sync) | sync 阻塞 |

### 统一后

| LLM | 统一入口 | 调用链 |
|-----|---------|--------|
| #1 | `engine.run_stage_plan_combined` | → `call_llm_async` → `to_thread` → `_call_llm` |
| #2 | `engine.run_stage` | → `call_llm_async` |
| #3 | `InteractionLayer.process` (async) | → `engine.call_llm_async` |
| #4a | `PostProcessor.resolve_topology_changes_async` | → `engine.call_llm_async` |
| #4b | `PostProcessor.resolve_attr_and_recent_async` | → `engine.call_llm_async` |

所有路径通过单一 `PipelineEngine.call_llm_async()` 入口，内部使用 `asyncio.to_thread()` 不阻塞事件循环。

### 监控收益

单点监控可做：
- 计时（`_timing` dict 自动累加）
- IO 日志（`_save_io` 统一写入）
- 速率限制（在 `call_llm_async` 插入一个 token bucket）
- Token 审计（单点记录 cost）
- 失败重试（通用 retry wrapper）

### 关键技术选择

- `asyncio.to_thread()` 而非 `loop.run_in_executor()`——Python 3.12+ 原生
- `ContextVar` 传递分量 ID——仅 event loop 线程可见，不跨 `to_thread` 边界
- 模块级 list 跨线程共享阶段名——`_current_stage[0]` 在 `to_thread` 线程中可见（Python 线程共享进程内存）

### run_1tick.py 监控架构

```python
_current_stage = [""]          # 模块级 list，跨线程共享
_comp_var = ContextVar('comp', default=-1)
_call_log = []                  # 全部 LLM 调用的 stage/comp/timing
_component_timings = {}         # per-component 各阶段耗时

# 最低层补丁（捕获 ALL 调用）
ir.InteractionResolver._call_llm = _wrapped_call_llm
  # → 写 _call_log + 写 IO 文件

# call_llm_async 补丁（捕获 per-component 计时）
PipelineEngine.call_llm_async = _patched_call_llm_async
  # → 记录 _component_timings

# _run_one_component 补丁（设置 _comp_var）
PipelineOrchestrator._run_one_component = _patched_run_one_comp
  # → 设置 _comp_var → 归零 → 计算 total
```
