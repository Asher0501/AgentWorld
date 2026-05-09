# Agent World 项目全景

> 基于 LLM 驱动的多 NPC 世界模拟引擎 — **分量前置、并行管线**

---

## 一、系统架构

### 整体流程（每 Tick）

```
现实 DB ─→ 图构建 ─→ 分量分割 ─→ [分量0: LLM #1→#3→#4a→#5] ──→ 归并 ─→ 写回 DB
            (nodes)        (连通分量)    ──→ asyncio.gather ──→     (sync_graph_to_nodes)
                              ↓              [分量1: ...]
                            并行加速         [分量2: ...]
```

### 数据持久化：统一 `nodes` 表

**所有实体**（NPC、Zone、Item、Recipe、Object）存在同一张 `nodes` 表中：

```sql
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,          -- entity_id（如 npc_abc123）
    type TEXT NOT NULL,            -- "npc" / "zone" / "item" / "recipe" / "object"
    name TEXT NOT NULL,            -- 人类可读名（如"杰洛特"）
    data TEXT NOT NULL DEFAULT '{}',  -- JSON: 所有属性/库存/关系
    updated_at TEXT                -- ISO 8601
);
```

`data` 字段存储实体完整的 JSON 状态，包括 attributes、inventory、relationships、zone 等。无任何独立表（已删除 `world`、`npcs`、`world_objects` 等遗留表）。

### 5 层 LLM 管线

```
           ┌─────────────────── 每分量独立并行 ───────────────────┐
           │                                                     │
   LLM #1 (规划) —— 各 NPC 读自身状态，输出自然语言行动计划
           │
   exec_results (代码) —— 基于计划+图引擎生成 NPC 执行结果（无 LLM）
           │
   LLM #3 (故事) —— 基于执行结果+拓扑生成叙事文本
           │
   LLM #4a (拓扑增量) —— 故事+图状态 → 结构化 JSON delta 操作
           │  ├── 校验通过 → 落地
           │  └── 校验失败 → 带反馈重试
           │
   LLM #5 (状态投影) —— 拓扑增量+图状态 → 属性变化 + recent_info
           │  └── 归并 → 写回图引擎
           └─────────────────────────────────────────────────────┘
```

### 核心文件职责

| 文件 | 职责 |
|------|------|
| `db/db.py` | 统一 `nodes` 表 CRUD + `NodeDB` 工厂 + SQLite 持久化 |
| `db/converters.py` | `node_to_npc()` / `npc_to_node_dict()` — 节点↔模型互转 |
| `entities/base_entity.py` | `Entity` 类：图节点基类（属性、边、邻居） |
| `entities/manager.py` | `EntityManager`：运行时实体池（管理 entity_id → Entity） |
| `services/graph_engine.py` | 图引擎：实体管理、边拓扑、邻接查询、分量分割、库存视图 |
| `services/graph_adapter.py` | DB/Config → 图构建 + `sync_graph_to_nodes()` 持久化 |
| `services/graph_npc_engine.py` | 顶层入口：驱动 tick、构建图、实例化管线 |
| `services/pipeline_orchestrator.py` | **主线编排**：分量分割 → 逐分量 `LLM #1→#3→#4a→#5` → 归并 |
| `services/pipeline_engine.py` | 阶段引擎：LLM 调用封装、IO 记录、计时 |
| `services/prompt_assembler.py` | Slot 式 prompt 组装（`assemble()`）+ 翻译层 LLM 调用 |
| `services/interaction_resolver.py` | LLM API 封装（MiniMax/OpenAI），含重试、超时 |
| `services/interaction_layer.py` | LLM #3 故事生成（`_build_story_prompt()`） |
| `services/post_processor.py` | **LLM #4a** 拓扑增量解析 + **LLM #5** 状态投影解析 |
| `services/verification_layer.py` | 校验编排器：8 项校验（含 json_format） |
| `services/verification_registry.py` | 校验注册器：8 项可配校验（mask 控制） |
| `services/conservation_validator.py` | 度守恒校验器（被 verification_layer 调用） |
| `domain/adapter.py` | 域适配器抽象基类：NodeClassification / PipelineStage / SlotDef |
| `domain/npc_world/adapter.py` | 猎魔人域的具体适配器：prompt slot 渲染、LLM 输出解析 |

---

## 二、分量前置管线（Component-First Pipeline）

### 为什么分量前置？

旧流程：所有 NPC 一起跑 LLM #1 → 全局意图解析 → 执行 → **再按连通分量分割** → 逐分量故事/更新。

问题：分量分割后发现 A 分量只含 2 个 NPC，B 分量含 8 个，但所有 NPC 已经跑了 LLM #1——全局阶段浪费算力在小分量上，且大分量没法拆开并行。

**新流程**：先做连通分量分割 → 每个分量独立并行跑完整管线（LLM #1 → #3 → #4a → #5）。

优势：
- 大分量和小分量互不影响（asyncio.gather 并行）
- 每个分量的 prompt 只包含其内部 NPC，不携带无关上下文
- 验证失败仅重跑单个分量，不波及全局

### 分量分割算法

```
graph_engine.build_components()
  → 以所有 NPC 节点为起点 BFS
  → 收集 NPC + 同 Zone NPC + NPC 物品 → 形成连通子图
  → 过滤无 NPC 的空分量
```

Zone 节点作为**枢纽**连接同一区域的 NPC。NPC 通过持有边（→物品）和同区域边（↔Zone ↔ 其他 NPC）自然形成分量。

### 并行调度

```python
# pipeline_orchestrator.py
tasks = [self._run_component_full(ctx, comp, npcs) for comp in ctx.components]
await asyncio.gather(*tasks)
```

每个分量内部虽然是串行（LLM #1 → #3 → #4a → #5），但不同分量之间完全并行。

---

## 三、LLM 管线详解

### Stage 0：图构建 + 分量分割

**不调用 LLM**。从 DB `nodes` 表读取所有实体，构建 `GraphEngine` 运行时图，BFS 计算连通分量。

### Stage 1：LLM #1 — 规划（Planning）

**每 NPC 一条 prompt**，包含：
- `time_info` — 当前世界时间
- `entity_identity` — 名称、角色、描述（domain.json）
- `survival_needs` — 体力/饱腹/心境的当前值和警戒说明
- `inventory` — 持有物品 + 数量
- `topology` — 所在区域、同区 NPC、可到达区域
- `memories` — 最近的 recent_info 条目
- `decision_guidance` — domain.json 中的行为指引

**输出**：自然语言计划（"去狐狸与鹅酒馆吃点东西，顺便打听消息"）

**prompt 组装**：`prompt_assembler.assemble("llm1_plan", ...)` — Slot 式，每槽有 provider。

### Stage 2：Exec Results（代码层）

**不调用 LLM**。基于 LLM #1 的计划 + 图引擎生成执行结果字典：
- 正在哪个 zone
- 是否移动
- 与谁交互
- 当前 inventory view
- 属性状态（文字版 + 数值）
- 最近经历 / 性格标签

### Stage 3：LLM #3 — 故事（Narrative）

**每分量一个 prompt**，包含分量内所有 NPC 的执行结果 + 拓扑信息。
输出自然语言故事，以 `【场景】` 开头，一段生动叙事。

**系统 prompt 强调**：
- 故事中不得出现图中不存在的实体
- 交互范围受拓扑边约束
- 不输出 JSON

### Stage 4：LLM #4a — 拓扑增量（Topo Delta）

**每分量一个 prompt**，输入包含：
- NPC 状态 + 计划
- LLM #3 故事文本
- 当前图拓扑（tagged 形式）

**输出格式**：结构化 JSON
```json
{
  "operations": [
    {"op": "delta", "src": "{entity_id}", "tgt": "{entity_id}", "delta": 5},
    {"op": "system_delta", "tgt": "{entity_id}", "item": "{item_id}", "delta": -2},
    {"op": "recipe", "src": "{entity_id}", "consumes": {...}, "produces": {...}},
    {"op": "set_qty", "tgt": "{entity_id}", "item": "{item_id}", "qty": 5}
  ]
}
```

**校验 + 重试**：
1. `entity_existence` — src/tgt 都是真实图节点？
2. `capacity_upper_bound` — 负 delta ≤ 当前边 qty？（如酒馆 food=0 不能卖出去）
3. `degree_conservation` — 分组 Σ=0？

校验失败 → `build_feedback()` 生成修正说明 → 带 feedback 重试 LLM #4a。

### Stage 5：LLM #5 — 状态投影（Attribute Projection）

**每分量一个 prompt**，输入包含：
- 所有 plan + 执行结果 + 故事
- LLM #4a 落地后的 topo 变化摘要（topo_diff）

**输出**：
- `attr_ops` — 属性变化（{entity_id, attribute, value}）
- `recent_info` — 每条 NPC 的近况文本（写入 memory 用）

**校验 + 重试**：
- `entity_existence` / `story_consistency` / `quantity_accuracy`
- 失败后同 LLM #4a 的反馈重试机制

### 归并（Merge）

所有分量的结果合并后一次性通过 `graph_engine.apply_edge_operations()` 落地，然后 `sync_graph_to_nodes()` 写回 DB。

---

## 四、数据模型

### 图结构

```
图中的一等节点：

NPC 节点 (npc_xxx)
  ├── name: "杰洛特"
  ├── role: "witcher"
  ├── attributes: {vitality: 77, satiety: 43, mood: 54, ...}
  └── edges:
        ├── npc_zone → zone_白果园 (qty=1)         ← 所在区域
        ├── npc_item → item_金币 (qty=8)            ← 持有物品
        ├── npc_item → item_草药 (qty=6)
        └── ...

Zone 节点 (zone_xxx)
  ├── name: "白果园"
  ├── type_id: "region"
  └── edges:
        ├── zone_zone → zone_狐狸与鹅酒馆 (qty=1)    ← 区域连通
        └── zone_npc ← npc_杰洛特                    ← 反向边（自维护）

Item 节点 (item_xxx)
  ├── name: "金币"
  ├── type_id: "item"
  └── is_conserved: true  (度守恒校验启用)
```

### 边类型

| 边类型 | 表示 | qty 含义 |
|--------|------|---------|
| `npc_zone` | NPC 所在的区域 | 1（唯一） |
| `zone_zone` | 区域之间的通路 | 1 |
| `npc_item` | NPC 持有物品 | 持有数量 |
| `zone_item` | 区域内有此物品 | 该区域该物品存量 |
| `npc_npc` | NPC 之间的交互（tick 内） | 交互强度 |
| `zone_npc` | 区域内的 NPC（反向自维护） | 1 |
| `item_zone` | 物品归属区域（反向自维护） | 同 zone_item |
| `npc_object` | NPC 在使用物体 | 1 |
| `config_zone` | 配置定义的区域连通（seed 时创建） | 1 |

### DB 持久化

```
nodes 表（唯一持久化层）
  ├── npc_xxx: {attributes, inventory, zone, ...}
  ├── zone_xxx: {connected_zones, ...}
  ├── item_xxx: {is_conserved, ...}
  ├── recipe_xxx: {inputs, outputs, ...}
  └── object_xxx: {description, zone, ...}
```

加载时：`load_or_seed()` → 从 `nodes` 表读取 seed 数据构建图。
保存时：`sync_graph_to_nodes()` → 运行时图状态写回 `nodes` 表。

---

## 五、校验系统

### 8 项可配校验

| 索引 | 校验项 | 说明 |
|:----:|--------|------|
| **0** | `entity_existence` | 所有引用的实体在图中存在 |
| **1** | `quantity_accuracy` | LLM 输出的数量与图事实一致 |
| **2** | `capacity_upper_bound` | 负 delta ≤ 当前边 qty |
| **3** | `entity_coverage` | 所有实体在翻译层输出中出现 |
| **4** | `direction_pairing` | 双向流交替方向 |
| **5** | `story_consistency` | 操作与故事内容一致 |
| **6** | `degree_conservation` | 守恒物品 Σdelta=0 |
| **7** | `json_format` | **原始 LLM 输出是否合法 JSON**（LLM #4a 和 LLM #5 均启用） |

**校验项 7（json_format）** 作用在原始 LLM 文本上（`raw_llm_output`），分四级：
1. 输出包含 `{` 或 `[`（非纯文本）
2. `json.loads()` 语法解析通过
3. 根节点是 dict 且有 `operations` 字段
4. `operations` 是数组

通过 `topology_layer_mask[7]` 和 `projection_layer_mask[7]` 分别控制拓扑和投影层的开关。

每项通过 `domain.json` 中的 mask 控制启用/禁用。
`verification_registry.py` 是注册器，`verification_layer.py` 是编排器。

### 度守恒（Degree Conservation）

受热力学第二定律启发：

```
系统内部（守恒）：NPC↔NPC 交易 → Σdelta=0
系统边界（不守恒）：进食、采集、体力自然衰减 → Σ≠0
```

只有标记为 `is_conserved` 的物品（如金币、交易品）参与守恒校验。
进食（消耗食物）属于系统边界，不校验。

---

## 六、设计哲学

### 1. LLM 是大脑，代码是骨架

```
代码负责：构建上下文、约束输出格式、执行 LLM 的决定
LLM 负责：理解状态、做出判断、创造叙事
代码不替 LLM 做判断，只提供判断所需的信息和边界
```

### 2. 自然语言 > 硬编码阈值

**反模式（已废弃）：**
```python
if npc.vitality < 30: go_rest()
```

**当前模式：**
```python
# Prompt 注入：
# ⚠️ 体力 < 30：极度疲劳，必须休息恢复
# NPC 自主决定：是否休息？去哪里休息？休息多久？
```

### 3. 分层约束光谱

```
松 ──────────────────────────────────────────── 紧
LLM #1 (决策)    LLM #3 (故事)    LLM #4a (拓扑)    LLM #5 (投影)
自由文本         自由叙事         结构化 JSON       结构化 JSON
无输出格式       无输出格式       有 schema         直接读 DB 状态
```

| 层 | 约束度 | 允许的幻觉 | 兜底机制 |
|---|--------|-----------|---------|
| LLM #1 | 很低 | 记错库存、自创背景 | LLM #4a/5 读 DB 不看它 |
| LLM #3 | 低 | 创造不存在的人物、编对话 | 数据层不提取属性变更 |
| LLM #4a | 高 | 漏写操作、写错 zone | 校验器 + 重试反馈 |
| LLM #5 | 高 | 偶尔漏条目 | DB 保留上一 tick，自动回退 |

### 4. 图结构是唯一数据源

**为什么用图而不是关系型 JOIN？**

- 邻接查询是 O(1)
- 库存天然正确（不存在 npc.inventory 和 DB 不一致）
- 拓扑隔离自动限制社交范围
- 实体 ID 解耦（display name 可改，eid 不变）

### 5. 拓扑-内容解耦

引擎内核操作抽象节点 ID（`npc_a1b2c3d`），所有语义内容在 `domain.json`。
换域 = 换配置文件，不改代码。

---

## 七、当前状态（截至 2026-05-09）

| 组件 | 状态 |
|------|------|
| 统一 `nodes` 表 | ✅ 已上线（Phase 2 commit `9bbf5a4`） |
| 分量前置并行管线 | ✅ 6+ 分量并行（asyncio.gather） |
| LLM #1 规划 | ✅ 稳定产出，per-NPC prompt 优化完成 |
| LLM #3 故事 | ✅ 标记匹配修复，分量级生成 |
| LLM #4a 拓扑增量 | ✅ 反馈重试机制（commit `4726418`） |
| LLM #5 状态投影 | ✅ 反馈重试机制已修复 |
| 校验系统 | ✅ 6 项校验 + mask 控制 |
| 度守恒 | ✅ 启用于 `is_conserved` 物品 |
| 反馈重试 | ✅ 校验失败→build_feedback→带反馈重试 |
| 翻译层 | ✅ 抽象字母→NL 翻译 + 翻译校验 |
| 域适配器模式 | ✅ NPCWorldAdapter + DomainAdapter 抽象基类 |

### 已知问题

- **LLM #3 循环重复**：当分量内所有 NPC 都在"原地驻足"时，LLM 有时陷入输出循环（tick_003 story 6）
- **MiniMax 超时**：大分量 prompt（>8KB）的 LLM 调用偶尔超时（`The read operation timed out`）
- **分量数量不稳定**：4~6 个分量，依赖 NPC 实时位置重新聚类
- **自动对称未集成**：`_auto_symmetry` 代码存在但当前不走

### 已修复问题

- **World time 覆盖 bug**（commit `d28a6e6`）：`save_world_time()` 在 `sync_graph_to_nodes()` 之前调用，后者写 _system_world 时不包含 world_time → 每 tick 世界时间重置为 08:30 → `recent_info` 全部带同一时间戳。修复：交换顺序，先 sync 后 save。
- **`recent_info` 累积**：上限 3→10，配合世界时间修复后每次 tick 积累带真实时间戳的条目。
- **Feedback 重试变量缺失**（commit `4726418`）：orchestrator 中 feedback 变量名与参数名冲突，反馈从未传给 LLM。
- **LLM #4a JSON 格式不可靠**（Round 1 `1d8b161` → Round 2 `1eac879`）：从 post-processing 兜底改为 slot+verification 机制，通过 `json_format` 校验 + 重试反馈让 LLM 自修正。
- **LLM #5 也有纯文本输出问题**（commit `87f895f`）：`projection_layer_mask[7]` 未启用 → 静默 0 attr ops。修复后 LLM #5 的 JSON 输出也受 `json_format` 校验保护。

### 已知问题

- **MiniMax 300s 超时**：大分量 prompt（>8KB）偶发 `The read operation timed out`，导致整个 tick 失败（tick_003, tick_010）。
- **LLM #3 循环重复**：当分量内 NPC 无新事件时，LLM 偶现输出循环。
- **LLM #4a 位置幻觉**：NPC 本已在目标 zone，LLM #4a 仍生成 zone move ops（因故事叙述「走进酒馆」字面理解）。tick_009 为经典案例。
- **分量数量不稳定**：4~6 个分量，依赖 NPC 实时位置重新聚类。
- **LLM 自省 story-graph 不一致**：tick_011 中 LLM 自己写出
