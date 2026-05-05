# Patch: 连通分量分割管线 (Component-Split Pipeline)

**日期:** 2026-05-05  
**范围:** `37b99e7` (Phase III Step G) → HEAD  
**统计:** 4 文件，+139/-43 行（分量分割核心）+ 整体 +496/-292 行（含 Phase IV 补丁）

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
5. **先串行再并行**: 实现先串行跑通，后续改为 `asyncio.gather()`
6. **向后兼容**: `_stage_verification_loop` 保留，`PostProcessor.resolve_topology_changes` 接受可选参数

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

### `src/agent_world/services/pipeline_orchestrator.py` (+87/-5 行)

- `PipelineContext`：新增 `components: list[TopoComponent]`
- `run_tick()`：注入分量分割 #4 → 逐分量管线 #5 → 归并 #6
- 新增 `_split_components()`：从 `graph_engine.build_components()` 获取分量，分配 exec_results
- 新增 `_run_one_component()`：分量级 LLM #3 → #4a → #4b → #5 → retry → 降级
  - 传递 `topo_pool=comp.eids` 和 `label_map=comp.label_map` 跳过 BFS
- 新增 `_stage_narrative_component()`：只处理分量级 exec_results
- 新增 `_merge_commit_components()`：归并 topo/attr/ri/stories → 调用 `_apply_final_ops()`
- `_stage_verification_loop` 保留（向后兼容）

### `src/agent_world/services/post_processor.py` (+6/-0 行)

- `resolve_topology_changes()` 新增可选参数 `topo_pool` / `label_map`
- 有则跳过 BFS 和 label_map 构建（分量模式）

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
  ├── for comp in components:       ← 逐分量（串行，可并行化）
  │     ├── _stage_narrative_component()   ← LLM #3（2~4 条边）
  │     ├── resolve_topology_changes()     ← LLM #4a（3~8 实体，跳过 BFS）
  │     ├── resolve_attr_and_recent()      ← LLM #4b（本分量 NPC）
  │     ├── check_all()                    ← LLM #5（本分量校验）
  │     └── retry (component only)         ← 只重跑本分量
  │
  ├── _merge_commit_components()   ← 归并 + _apply_final_ops()
  └── _build_tick_results()
```

**收益**:
- LLM #4a prompt: 30+ 实体 → 3~8 实体
- Retry: 250s → ~50s（只重跑一个小分量）
- 未来并行 `asyncio.gather()`: ~650s → ~200s

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
3. 运行新分量管线 tick，对比输出一致性
4. 验证单分量 retry（故意让一个分量失败）不影响其他分量

当前为**串行**逐分量执行。切换并行的改动点仅在 `_run_component_pipeline` 一处：

```python
# 当前（串行）:
for comp in ctx.components:
    await self._run_one_component(ctx, comp)

# 未来（并行）:
async def _run_component_pipeline(self, ctx):
    async with asyncio.TaskGroup() as tg:
        for comp in ctx.components:
            tg.create_task(self._run_one_component(ctx, comp))
```

---

## 文件变更索引

| 文件 | +行 | -行 | Σ |
|------|-----|-----|---|
| `services/graph_engine.py` | 74 | 0 | +74 |
| `services/pipeline_orchestrator.py` | 87 | 5 | +82 |
| `services/post_processor.py` | 6 | 0 | +6 |
| `services/graph_adapter.py` | 2 | 5 | -3 |
| **小计（分量分割核心）** | **169** | **10** | **+159** |

（注：行数统计包含 Phase IV 补丁的 recent_info 滚动窗口 + P0/P1/zone/{} fix、__init__.py 清理等前置变更，实际分量分割核心 +87 行）
