# Agent World 项目全景

> 基于 LLM 驱动的多 NPC 世界模拟引擎

---

## 一、系统架构

### 整体流程（每 Tick）

```
现实 DB ─→ 实体构建 ─→ LLM 管线 ─→ 验证 ─→ 写回 DB
                 ↑                        |
                 └──── 图引擎状态持久化 ────┘
```

### 4 层 LLM 管线

```
                 LLM #1 (决策)
 每个 NPC 读取自身状态 → 独立输出自然语言行动计划
                    ↓
                 LLM #2 (意图)
 自然语言 → {interact_with[], narrative}
                    ↓
            IntentExecutor (拓扑执行)
 移动NPC/连接NPC/创建边 — 不更新属性
                    ↓
           InteractionLayer ─→ LLM #3 (故事)
 收集所有边 → 统一生成每条边的自然语言故事
                    ↓
             PostProcessor ─→ LLM #4 (批处理)
 所有NPC状态 + 故事 → 批量生成属性/库存/记忆/关系更新
                    ↓
            ConservationValidator
 检查所有交易守恒（Σgold=0, Σitem=0）
                    ↓
               Apply Updates
 回写图引擎 + NPC 模型 + DB
```

### 核心文件职责

| 文件 | 角色 |
|------|------|
| `graph_npc_engine.py` | 主编排引擎，驱动整个 tick 管线 |
| `graph_engine.py` | 图引擎：实体管理、边拓扑、库存视图 |
| `graph_adapter.py` | 从 DB+Entity Manager 构建交互图 |
| `npc_prompt_builder.py` | LLM #1 的 prompt 构建 |
| `intent_executor.py` | IntentResolver（LLM #2）+ 拓扑执行 |
| `interaction_layer.py` | 交互边管理 + LLM #3 故事生成 |
| `post_processor.py` | LLM #4 批量更新 + 自动对称 + 合并 |
| `conservation_validator.py` | 守恒校验器 |
| `interaction_resolver.py` | LLM 调用封装 |

### 数据模型

```
World (主世界)
  ├── zones[8] (farm/market/tavern/barracks/library/temple/forest/village_square)
  └── world_time (春秋周天 时:分)

NPC (16 个)
  ├── attributes: vitality/satiety/mood (0-100)
  ├── position.zone_id → zone
  ├── inventory: List[str] (物品名重复)
  ├── memory: List[MemoryEntry] (最多 20 条)
  └── relationships: Dict[npc_name -> int]

Entity Manager (运行时图)
  ├── NPC 节点 (npc_xxx)
  ├── Zone 节点 (zone_farm)
  ├── Object 节点 (obj_xxx)
  └── Item 节点 (item_小麦)
```

---

## 二、设计哲学

### 1. LLM 是大脑，代码是骨架

```
代码负责：构建上下文、约束输出格式、执行 LLM 的决定
LLM 负责：理解状态、做出判断、创造叙事
代码不替 LLM 做判断，只提供判断所需的信息和边界
```

### 2. 自然语言 > 硬编码阈值

**反模式（已废弃）：**
```python
if npc.vitality < 30:
    go_rest()  # 硬编码阈值，NPC 没有选择
```

**当前模式：**
```python
# Prompt 注入：
# ⚠️  体力 < 30：极度疲劳，必须休息恢复
# NPC 自主决定：是否休息？去哪里休息？休息多久？
```

### 3. 分层解耦

每一层只做一件事，输出给下一层：

```
决策层 (LLM#1)  →  意图解析 (LLM#2)  →  拓扑执行 (Executor)
→  故事层 (LLM#3)  →  批更新 (LLM#4)  →  校验 (Validator)
```

**为什么 4 层而不是 1 层？**
- 每一层的上下文集中且清晰（不是把所有 NPC 的所有信息塞给一次 LLM 调用）
- 每层可以独立错误处理和降级
- 故事和生产更新分离 → 故事可以自由发挥，不污染数据一致性

### 4. 图结构作为"共享状态" — 为什么用它？

#### 为什么选择图结构？

传统游戏模拟的常见做法：每个 NPC 维护自己的 `current_zone`、`inventory[]`、`nearby_npcs[]` 等字段。这在点对点查询时问题不大，但一旦需要「世界级」查询（谁在这个区？谁拿着这个东西？有没有人能在这个区找到那把剑？），就必须写跨实体 JOIN 逻辑，数据一致性靠手动维护。

图结构解决了这个问题：**一切都是一等节点，关联就是边。**

#### 节点类型

| 节点 | 示例 ID | 表示 |
|------|---------|------|
| NPC | `npc_老张` | 每个角色 |
| Zone | `zone_farm` | 8 个地图区域 |
| Object | `obj_tavern_bench` | 场景中的物体 |
| Item | `item_小麦` | 可持有的物品（抽象种类，不按实例拆分） |

#### 边类型

| 边类型 | 示例 | 语义 |
|--------|------|------|
| `npc_zone` | 老张 ↔ farm | NPC 在哪个区（只有一条，移动时更换目标节点） |
| `npc_npc` | 老张 ↔ 王老板 | 本轮发生了社交/交易交互 |
| `npc_object` | 铁匠王 ↔ market_stall_1x1 | NPC 在使用/交互某个物体 |
| 持有边 | 老张 ↔ item_小麦 | NPC 持有该物品，qty 边权重表示数量 |
| 所有权边 | 铁匠王 ↔ market_stall_1x1 | 特殊持有边（qty=1），表示拥有关系 |

#### 它如何驱动整个管线？

每条 LLM 层都从图中读取或写入信息：

1. **LLM #1 决策前** — `graph_engine.get_entity()` 拉取 NPC 的 zone 邻居（同区其他 NPC）和 inventory 边（持有物品）构建 prompt
2. **IntentExecutor 执行后** — 在图上完成拓扑变更：移动 NPC 边到新 zone，根据 `interact_with` 创建社交边和物体边
3. **InteractionLayer（LLM #3）** — 遍历当前 tick 生成的所有边，按类型写故事
4. **LLM #4 + Validator** — 从图引擎读状态，更新结果也通过 `set_attr`/边操作写回
5. **DB 持久化** — 每 tick 末，Entity Manager 将整个图序列化写入 SQLite

#### 核心优势

**1. 统一数据模型**
移动（npc_zone）、交互（npc_npc）、持有（边+权重）、所有权（特殊边）全部是同一套「节点+边」模型。没有三套数据结构互相追赶同步。

**2. 邻接查询零成本**

```python
# 问：「老张当前在哪个区？」
zone_edge = graph.get_edge(entity=eid, edge_type="npc_zone")
# → 直接返回：老张 ↔ zone_farm

# 问：「farm 区有哪些 NPC？」
zone_ent = graph.get_entity("zone_farm")
npcs_in_farm = [e for e in zone_ent.get_neighbors() if e.is_npc()]
# → 直接返回：[老张, 田嫂]

# 问：「田嫂身上有什么？」
items = [
    (eid, qty) for eid, qty in tan_sao_ent.peek_edges("npc_item")
]
# → 直接返回：[(item_蔬菜, 5), (item_金币, 25), (item_小麦, 3)]
```

以上查询都不需要 SQL JOIN 或跨数据结构手动关联。图的邻接性质让「谁在哪儿、有什么」变成 O(1) 查询。

**3. 库存天然正确**

- 库存不是 `{item: qty}` 字典，而是 `item_xxx` 节点上的边权重
- 移动物品=修改权重，不需要从 NPC 的 inventory 里 `pop()` 再 `append()`
- 引擎内没有冗余的「快照」— 只有一份边数据，不可能出现 NPC.inventory 和 DB 不一致

**4. 拓扑隔离**

- 物品所有权边自动隔离非所有人对物体的操作
- 同区 NPC 通过 zone 节点自然连接，不需要手动维护 `nearby_npcs` 列表
- LLM #2 解析出的 `interact_with` 直接映射到边创建，不经过状态机

**5. 实体 ID 解耦**

- NPC 的 ID 是 `npc_` 前缀（例如 `npc_4fd8e083`），而「老张」只是 display name
- 关系和记忆中引用的是 display name（人类可读），但引擎层全部用 eid 操作
- 改名/角色变化不影响任何已存的边关系

#### 对比传统方案

| 场景 | 传统做法 | 图结构做法 |
|------|---------|-----------|
| NPC 移动 | `npc.current_zone = "farm"` | 把 `npc_zone` 边从 zone_farm 移到 zone_market |
| 检查区域人数 | 遍历所有 NPC 查 `current_zone` | `zone_farm.get_neighbors()` 筛选 NPC |
| 交易 | NPC A.inventory.pop("小麦", 5), NPC B.inventory.add("金币", 15) | 减少 item_小麦→老张 的边权重，增加 item_金币→老张 的边权重 |
| 物品不唯一 | 列表 `["小麦", "小麦"]` | 节点 `item_小麦`，边权重=2 |

**总结：图不是炫技——它是消除「数据在哪里」这个问题的方案。** 所有信息都位于唯一的位置（边或节点上），不需要担心 DB 和运行时状态不一致，不需要手动同步多个 NPC 的 inventory 列表，不需要跨表 JOIN 来回答「谁在哪儿」这种高频问题。

### 5. 自动对称 + 守恒校验

```
LLM 产出：方统领 +50 金币（忘记写对手方）
  ↓
_auto_symmetry 自动补全：王老板 -50 金币
  ↓
ConservationValidator 检查 Σ=0 ✅
  ↓
不可能漏掉交易对手（即使 LLM 忘记）
```

### 6. 分层约束 — 用不同紧度管理 LLM 幻觉

LLM 天生会幻觉（编故事、记错数字、创造不存在的人物）。**不试图消灭幻觉——而是在不同层施加不同约束，让幻觉被体系兜住。**

#### 4 层的约束光谱

```
松 ──────────────────────────────────────────── 紧
LLM #1 (决策)        LLM #3 (故事)       LLM #2 (意图)       LLM #4 (后处理)
自由文本             自由叙事            结构化 JSON         结构化 JSON
无输出格式           无输出格式          有 schema           读 DB 不读 LLM
允许个人化           允许创造人物        中度约束            紧约束
```

| 层 | 约束度 | 允许的幻觉 | 被谁兜住 |
|-----|--------|-----------|---------|
| LLM #1 决策 | 很低 | 记错库存、瞎报金数、自创背景（比如田嫂凭空有个闺女） | LLM #4 读 DB 不看它 |
| LLM #3 故事 | 很低 | 创造不存在的人物（老陈、铁匠同行）、编造对话、添加环境细节 | 数据层（LLM #4）不从故事中提取属性变更 |
| LLM #2 意图 | 中等 | 漏写交互对象、写错 zone 名、把闲逛写成交易 | IntentExecutor 的节点解析和拓扑执行兜底 |
| LLM #4 后处理 | 高 | 偶尔漏掉某个 NPC 条目 | DB 保留上一 tick 状态，Engine 自动回退 |

#### 为什么不把所有层都紧约束？

因为**性格和叙事性来自宽松层**。

- 铁匠王看到自己 47 金，LLM #1 写成 94 金——**他在吹牛**。就像人经常高估自己钱包里有多少钱。这不是 bug，是人格体现。
- 田嫂逛市场时想到「给闺女扯块花布做衣裳」——NPC 设定里从未说过她有闺女，是 LLM 自主创造的背景故事。这不影响数据层，但让世界更有人情味。

#### 约束链：上层吹牛，下层查账

关键的架构选择：**LLM #4 不从 LLM #1 读数据，只从 DB 读。**

```
LLM #1 (决策): 铁匠王说我兜里有94金！
    ↓ (LLM #4 不看这个)
LLM #4 (后处理): 我查一下账本…DB显示47金。好的就按47处理。
```

上游层（LLM #1, LLM #3）负责**性格**和**故事**——允许夸张、记错、想象。
下游层（LLM #4）负责**事实**——从 DB 读、写 DB，不看上游层的脑补。

这是人的真实运作方式：你的性格和想象力让你觉得自己是主角，但银行柜员只查你的账户余额。

### 7. 热力学第二定律 — 守恒边界

**核心想法：在系统内部强制守恒（交易/配方），在系统边界允许不守恒（进食/采集）。**

热力学第二定律启发了守恒校验环节的设计：一个完全孤立的系统，内部能量总和不变。但现实中没有完全封闭的系统——热力学允许热量进出边界。

映射到 Agent World：

```
                        ┌──────────────────────┐
                        │    系统内部（守恒）     │
                        │                       │
                        │  老张 ──萝卜──→ 王老板  │  ← 交易：Σ=0 ✅
                        │  +1萝卜 -1萝卜         │
                        │  +14金  -14金          │
                        │                       │
                        │  铁匠王 ──铁锭→工具    │  ← 配方：Σ=0 ✅
                        │  -2铁锭  +1工具        │
                        │  -8体力               │
                        └────────┬──────────────┘
                                 │
                   系统边界───────┼────────────────
                                 │
                        ┌────────┴──────────────┐
                        │  外部世界（不守恒）     │
                        │                       │
                        │  进食：面包×1 消失了    │  ← Σ≠0 允许
                        │  采集：地里冒出萝卜×1    │  ← Σ≠0 允许
                        │  体力自然衰减 -V/tick   │  ← Σ≠0 允许
                        └───────────────────────┘
```

#### 守恒校验器只检查内部交易

```python
class ConservationValidator:
    def check_gold(self):
        assert sum(deltas) == 0  # NPC 之间的金流转必须守恒

    def check_items(self):
        for item in all_items:
            assert sum(deltas) == 0  # NPC 之间的物品流转必须守恒
```

它从**不检查** NPC 进食面包后面包去哪了——因为那是 NPC 与外部世界的交互，不是系统内部交易。

#### 为什么这是热力学第二定律？

热力学第一定律说「能量守恒」，第二定律说「孤立系统熵增」。

- 第一定律 → **ConservationValidator**：内部交易守恒，对应封闭子系统
- 第二定律 → **系统边界**：允许能量/物质进入和离开系统。NPC 进食=从外部吸收能量，体力消耗=热量散失到外部

没有校验器，NPC 可以凭空造金币（LLM 幻觉）。没有边界，NPC 活不了多久（无法进食和采集）。

> **没有热力学第二定律的启发，我们可能会写一个「全量守恒」系统——禁止一切不守恒的操作。那样 NPC 永远无法进食（面包从哪来？），永远无法采集（萝卜凭空出现？）。现实世界的经验告诉我们：需要区分系统内部和系统边界。**

### 8. 配方系统 — NPC 通过物体交互转换物品

NPC 可以通过与世界的物体交互，将库存中的原料转换为新物品。这不是魔法——NPC 必须持有到目标物体的边，交互后扣减原料、增加产物、消耗体力。

#### 内置配方（预注册）

| 配方名 | 输入 | 输出 | 需要物体 | 区域限制 |
|--------|------|------|---------|---------|
| 烘焙面包 | 小麦×2 | 面包×1 | 烤炉 | farm |
| 碾磨面粉 | 小麦×3 | 面粉×2 | 磨具 | farm |
| 锻造工具 | 铁锭×2 | 工具×1 | 铁砧 | market |
| 酿酒 | 小麦×3+蔬菜×1 | 酒×2 | 酿酒桶 | tavern |
| 制作药水 | 草药×3 | 药水×1 | 药臼 | temple |
| 加工皮毛 | 皮毛×2 | 衣物×1 | 缝纫台 | forest |
| 建造家具 | 木材×3 | 家具×1 | 工作台 | market |

#### 架构

```
RecipeRegistry (中央注册表)
  ├── 内置配方（init_defaults 时加载）
  └── LLM 动态发现（executor 中 auto-register）
        ↓
RecipeEngine (执行引擎)
  ├── find_interaction_edge() — 检测物体连接
  ├── can_execute() — 库存/体力/物体/区域四维检查
  ├── detect_recipe_from_instruction() — 从 LLM 指令检测配方意图
  └── execute() — 生成完整交互指令
         ├── 扣减输入原料
         ├── 增加输出产物
         ├── 消耗体力
         └── 返回标准 instruction dict
```

#### 与 LLM 管线的集成

- **LLM #1 当前不展示配方**：设计决策（`npc_prompt_builder.py` 注释原文：「不暴露图结构细节（边/物体/配方），那都是 LLM #2 解析层判断的」）
- **LLM #2 意图解析后**：`RecipeEngine.detect_recipe_from_instruction()` 检测配方意图（精准匹配名/模糊匹配产物/qty_changes 模式）
- **Executor 执行时**：`RecipeEngine.can_execute()` 检查可行性 → `execute()` 生成指令
- **LLM 还支持动态发现新配方**：如果 LLM 的 `action` 引用了一个不存在的配方，系统自动注册（`RecipeRegistry.register_llm_recipe()`），并同步到 `DerivationRegistry`

#### 当前已知问题

- 配方系统已实现但**未实际集成到 tick 管线中**（处于「可调用但默认不走」的状态）——LLM 输出的 `action` 目前直接走 execute_npc_instruction() 的通用路径，不是配方执行路径
- 物体节点（烤炉/铁砧等）在初始图中没有被创建，物体间的边也无法通过 `npc_object` 正确触发
- `detect_recipe_from_instruction()` 的召回率未经实测

### 9. 配方权限 — Recipe 作为全局图节点（待实现）

#### 为什么 recipe 应该是图节点？

Recipe 是**全局共享的实体**——一个 `recipe_forge_steel` 对所有 NPC 都一样（输入铁锭×2+炭×1→输出钢锭×1）。它在图里应该是一个独立节点，全图只有一份，不是某个 NPC 的私有属性。

每个 NPC 通过图边连接到 recipe，表达「我有权执行这个配方」。这不是冗余——**权限显式表示为图中的边，而不是隐式的属性匹配**。

#### 只增加一种新实体类型

| 实体类型 | 示例 ID | 表示 |
|---------|---------|------|
| `recipe` | `recipe_forge_steel` | 物品转换配方（全局唯一） |

不引入 skill 节点——NPC 直接连到 recipe，减少一层中间跳转。

#### 新边类型

| 边类型 | 示例 | 语义 |
|--------|------|------|
| `can_craft` | 铁匠王 ↔ recipe_forge_steel | NPC 有权限执行此配方 |
| `requires_object` | recipe_forge_steel ↔ obj_anvil | 配方需要此物体才能执行 |
| `requires_zone` | recipe_forge_steel ↔ zone_market | 配方限定在该区域执行 |

#### 完整图结构

```
           ┌────────────────── 图 ──────────────────┐
           │                                        │
           │  npc_tiejiang_wang                     │
           │    ├── can_craft ──→ recipe_forge_steel  │
           │    ├── can_craft ──→ recipe_repair_tool  │
           │    ├── npc_zone  ──→ zone_market          │
           │    ├── npc_object → obj_anvil             │
           │    └── npc_item  ──→ item_铁锭 (qty=10)   │
           │                                        │
           │  npc_tian_sao                          │
           │    ├── can_craft ──→ recipe_bake_bread   │
           │    ├── can_craft ──→ recipe_碾磨面粉     │
           │    ├── npc_zone  ──→ zone_farm           │
           │    └── npc_item  ──→ item_小麦 (qty=5)    │
           │                                        │
           │  recipe_forge_steel (attrs: {...})     │ ← 全图只有一个
           │    ├── requires_object → obj_anvil      │
           │    └── requires_zone  → zone_market     │
           │                                        │
           │  recipe_bake_bread (attrs: {...})      │ ← 全图只有一个
           │    ├── requires_object → obj_烤炉       │
           │    └── requires_zone  → zone_farm       │
           └────────────────────────────────────────┘
```

关键观察：铁匠王有 `can_craft→recipe_forge_steel`，田嫂没有。不需要任何 `if role ==` 判断——**图结构本身就是权限声明。**

#### 三层分离

```
权限层（谁可以）    定义层（怎么做）         执行层（怎么做出来）
NPC ──can_craft──→ Recipe ──requires_object──→ Object
                      │── attributes ──→ {inputs, outputs, vitality_cost}
                      └──requires_zone──→ Zone
```

- **权限** = NPC 到 recipe 的 `can_craft` 边（初始化时配好，运行时改图不动代码）
- **定义** = recipe 节点自身的 attributes（输入输出、体力消耗）
- **约束** = recipe 到 object/zone 的边（需要什么工具、在哪儿做）

#### RecipeEngine 的图查询

```python
def get_available_recipes(npc_eid, graph):
    """NPC 能用的配方 = 遍历 can_craft 边直接得到"""
    recipe_eids = [
        edge.target
        for edge in graph.get_edges(src=npc_eid, type="can_craft")
    ]
    recipes = [graph.get_entity(eid) for eid in recipe_eids]
    return filter_executable(recipes, npc_eid, graph)

def filter_executable(recipes, npc_eid, graph):
    """从可用的配方中，筛出当前能执行的"""
    zone_id = graph.get_entity(npc_eid).get_attr("zone_id")
    inv = graph.get_inventory_view(npc_eid)
    npc_objects = [e.eid for e in graph.get_neighbors(npc_eid, type="npc_object")]

    available = []
    for r in recipes:
        attrs = r.attributes

        # 原料检查
        if not all(inv.get(item, 0) >= qty
                   for item, qty in attrs.get("inputs", {}).items()):
            continue

        # 区域检查
        zone_edges = graph.get_edges(src=r.eid, type="requires_zone")
        if zone_edges and not any(z.target.endswith(zone_id) for z in zone_edges):
            continue

        # 工具检查
        obj_edges = graph.get_edges(src=r.eid, type="requires_object")
        if obj_edges and not any(o.target in npc_objects for o in obj_edges):
            continue

        available.append(r)
    return available
```

查询路径只有一层：`NPC ──can_craft──→ Recipe`，不需要经过 skill 中转。

#### 执行机制：Recipe 作为数据喂给 LLM 管线

Recipe 的权限、输入输出、物体要求**不写入执行代码**——而是作为结构化数据注入 LLM #1 和 LLM #4 的 prompt，让 LLM 基于数据做判断。

**四层管线中的 Recipe 数据流：**

```
LLM #1 (决策)
  → prompt 注入：可用配方列表（含输入输出、需要什么物体、在哪个区域）
  → NPC 看到：「我有铁锭×10、在市场，我可以执行 recipe_forge_steel」
  → 输出：「用两铁锭和炭打把钢锭」

LLM #2 (意图解析)
  → 看到「用铁锭和炭打钢锭」「市场」「铁砧」
  → 输出结构化 intent：
    {
      "interact_with": [
        {"type": "zone", "id": "market"},
        {"type": "object", "id": "铁砧"}
      ],
      "narrative": "铁匠王在市场锻造钢锭"
    }
  → 不需要 action 字段——图拓扑自明

IntentExecutor (拓扑执行)
  → 移动 NPC 到 zone_market
  → 连接 NPC 到 obj_anvil（npc_object 边）
  → 不涉及任何 inventory 变更

LLM #3 (故事)
  → 基于边写叙事
  → 得知 npc_zone(market) + npc_object(anvil)
  → 输出：「铁匠王把铁锭放进炭火中烧得通红，铁锤叮当响……」

LLM #4 (后处理 — 基于数据判断)
  → 从 DB 读：铁匠王 @market，铁锭×10，炭×3
  → 从图查：有 can_craft 边 → 哪些 recipe？
    recipe_forge_steel: requires_object→铁砧 ✅（已连接）
                        requires_zone→market ✅
                        inputs: {铁锭:2, 炭:1} ✅ 库存够
  → 匹配成功 → 执行转换：
    - NPC→item_铁锭 边权重 -2
    - NPC→item_炭 边权重 -1
    - NPC→item_钢锭 边权重 +1
    - NPC.vitality -8
  → 同时更新心情、记忆、关系
```

#### 与纯代码执行的区别

| 环节 | 传统做法 | 本方案 |
|------|---------|-------|
| 配方数据 | 硬编码 switch/case | Recipe 节点 attributes（图内存储） |
| 权限判断 | `if role == "blacksmith"` | `can_craft` 边存在与否 |
| 执行校验 | RecipeEngine.can_execute() 写死的四维检查 | LLM #4 基于注入的 recipe 数据做判断 |
| 库存变更 | RecipeEngine.execute() 改图边 | LLM #4 输出 inventory_changes → 引擎执行 |
| 新增配方 | 加一个 case | 建 recipe 节点 + 连 can_craft/object/zone 边 |

#### Recipe 作为图的两种读法

```python
# 读法 1：「铁匠王能做什么配方？」
for edge in graph.get_edges(src="npc_tiejiang_wang", type="can_craft"):
    recipe = graph.get_entity(edge.target)
    print(recipe.attributes["outputs"])  # → {钢锭: 1}

# 读法 2：「谁会锻造钢锭？」
for edge in graph.get_edges(tgt="recipe_forge_steel", type="can_craft"):
    npc = graph.get_entity(edge.source)
    print(npc.name)  # → 铁匠王
```

两种查询都不需要额外索引——图的反向遍历天然支持「这个配方谁可以用」。

## 三、关键改进历程

| 改进前 | 改进后 |
|--------|--------|
| 一次 LLM 调用决定所有 | 4 层分工：决策→意图→故事→更新 |
| 属性/库存/故事混在一起 | 拓扑执行（IntentExecutor）先跑，再生成故事和更新 |
| 没有独立校验 | ConservationValidator 校验守恒 |

### ✅ 阶段 2：硬编码阈值 → 自然语言警戒

**改了什么：**
- 移除 `npc_prompt_builder.py`, `post_processor.py`, `graph_npc_engine.py` 中全部 `if vitality < X` 判断
- 替换为 prompt 中 `⚠️  属性警戒值` 描述块
- 兜底路径统一去 tavern，不替 LLM 做分支判断

**效果：** NPC 开始自主做出生存决策：
- 铁匠王（⚡16）→ "在 tavern 躺下休息到 50 以上"
- 老陈（低饱腹）→ "去点碗热汤面"
- 王老板（⚡32）→ "在市场摆摊零售"

### ✅ 阶段 3：LLM 不可用 → 崩溃而非静默降级

- `_derive_and_execute()` 在 LLM 不可用时 `raise RuntimeError`
- 静默降级（fallback 兜底）会掩盖问题
- 兜底路径保留但变为死代码（BFS 可达性所需）

### ✅ 阶段 4：Hunger → Satiety 重构

- `hunger: int`（0=不饿）→ `satiety: int`（0=饿死, 100=饱）
- 7 个源文件 + DB migration
- 语义更直观（饱腹值从 100 向下衰减）

### ✅ 阶段 5：故事匹配修复

**问题：** `_assign_stories()` 按 `paragraphs[i] → edges[i]` 顺序匹配。LLM 输出乱序时，故事和边错位。

**修复：**
- Prompt 要求每条故事以 `【源↔目标】` 开头
- `_assign_stories()` 改为按标记匹配（不只是 index）
- 正则兜底：如果 `---` 拆分覆盖不足，直接从原文按 `【X↔Y】` 提取

---

## 四、从项目中学习到的

### 🔑 1. LLM 不可控是特性不是 Bug

LLM 会：
- 乱序输出故事 → 不要依赖顺序
- 编造物品/交易 → 用 Validator 兜住
- 忘记写交易对手 → 用 `_auto_symmetry` 补全
- 批量模板化输出 → 接受或增加区分信号

**应对原则：**
- 接受 LLM 的不可靠性
- 用代码层（校验器/自动对称/regex 兜底）兜住不可靠性
- 不要试图让 LLM "完美"——而是让系统在 LLM 不完美时依然正确

### 🔑 2. 硬编码阈值 vs LLM 判断

```
硬编码阈值：
  ✓ 确定性强（if vital < 30: rest）
  ✗ NPC 没有个性（所有人都一样）
  ✗ 违背"模拟"的初衷

LLM 判断：
  ✗ 不确定性（有时 LLM 忽视低属性）
  ✓ NPC 有个性选择（铁匠王去 tavern，老陈吃面）
  ✓ 更接近真实世界模拟
```

**取舍：** 选择 LLM 判断，接受偶尔的"不合理"行为作为真实感的一部分。

### 🔑 3. 图结构的威力

- 同一个图：支持移动（NPC↔Zone）、交互（NPC↔NPC）、持有（NPC↔Item）
- 边的 quantity 天然支持库存计数
- 区域拓扑决定了 NPC 社交网络
- 所有权边自动隔离非所有者对物体的访问

### 🔑 4. 4 层管线的合理性证明

每一层的输入和输出清晰定义，可以独立替换：
- LLM #1 可以换规则引擎（如果不想用 LLM 做决策）
- LLM #2 就是 JSON parser
- LLM #3 可以关掉（用模板故事）
- LLM #4 是整个系统的核心——它连接"发生了什么"和"属性怎么变"

### 🔑 5. 测试策略

- 8-NPC 快速验证（~3min）→ 16-NPC 全量（~6min）
- Validator 是最后一道防线（校验失败不会静默）
- 每次改一行代码就跑一次 tick（迭代速度 > 完美度）

### 🔑 6. LLM Prompt 工程经验

**好的 prompt 设计：**
- 告诉 LLM 后果（体力<30:会累倒），而不是告诉它具体行为（体力<30:去休息）
- 给例子（比如交易格式必须包含 zone + npc）
- 在输出端做校验，不在输入端做过滤

**Prompt 的"硬约束"：**
- `每条故事的第一行必须以【X↔Y】开头`
- 标 3 个感叹号强调，并在后面加例子
- 解析端同样支持降级（LLM 不遵守时 regex 兜底）

---

## 五、当前状态

| 层 | 状态 |
|----|------|
| LLM #1 决策 | ✅ 16/16 NPC 稳定产出 |
| LLM #2 意图 | ✅ 自然语言→结构化交互 |
| IntentExecutor | ✅ 移动/连接/建边 |
| LLM #3 故事 | ✅ `【X↔Y】` 标记匹配修复完成 |
| LLM #4 批更新 | ✅ 集中式处理，含自动对称 |
| Validator | ✅ 守恒校验 |
| DB 持久化 | ✅ 每 tick 写回 |

### 已知未修复的问题

- **追逐系统平衡**：移动 -5⚡ vs 恢复 +0/+3（用户认为非关键）
- **LLM #4 批量模板化**：无交互 NPC 属性更新趋于一致（非关键）
- **LLM 幻觉交易**：NPC 编造不存在的交易（非关键，现实也可接受）

### 下一步

- 生产 50-tick 仿真
- 进一步优化 prompt（减少 LLM 幻觉）
- 经济系统平衡调整

---

## 六、改进时间线

### M1: 单层 → 4 层管线
一次 LLM 调用拆为决策 → 意图 → 故事 → 更新。各层可独立降级、替换、校验。——状态：✅ 稳定

### M2: 硬编码阈值 → 自然语言警戒
`if vitality < 30: rest` 等全部数值判断从代码中移除。Prompt 只写「掉到 0 就出局，性格决定风险承受」，不写具体阈值。铁匠王低体力去 tavern 躺着，王老板优先生意拖到最后一刻才吃。——状态：✅ 稳定

### M3: LLM 不可用时崩溃（非静默降级）
移除兜底时静默执行 LLM 工作。`_derive_and_execute()` 在 LLM 不可用时 `raise RuntimeError`，让系统尽早暴露问题而非掩盖。——状态：✅ 稳定

### M4: Hunger → Satiety 重构
`hunger: int`（0=不饿）→ `satiety: int`（0=饿死）。7 个文件 + DB migration。语义更直观（饱腹值从 100 向下衰减）。——状态：✅ 已完成

### M5: 故事标记匹配修复
问题：`_assign_stories()` 按段落顺序匹配边，LLM 乱序导致故事错位。修复：强制「每条故事以 `【X↔Y】` 开头」，按标记匹配 + 正则兜底提取。16-NPC 生产环境验证通过。——状态：✅ 稳定

### M6: 修复「驻留无故事」漏洞
NPC 原地休息不生成边 → 故事层空白 → LLM #4 瞎编。方案A：`EdgeResult` 加 `stayed=True` 驻足边；方案B：LLM #4 prompt 注入 NPC 原始计划。Tick 17 验证 4 NPC 全部正确。——状态：✅ 稳定

#### 修复细节

- 方案A（`interaction_layer.py`）：`_build_edges()` 新增 `zone_changed=False` + 无交互时生成 `npc_zone(stayed=True)` 边，LLM #3 为此写故事，降级路径也区分行走/停留
- 方案B（`post_processor.py` + `graph_npc_engine.py`）：`process_batch()` 新增 `npc_plans` 参数，prompt 追加「每位 NPC 的本轮计划」区块展示 LLM #1 原文
- Tick 17 验证：田嫂 V+5 S+15「在家休息恢复」，铁匠王 V+10「休息喝酒」——不再瞎编

### 三个体系改进

1. **基本需求 ≠ 记忆**：生存欲望作为固定 prompt 区块永不裁剪，不进入 memory buffer
2. **4 层管线的合理性持续验证**：每层可独立降级/替换/测试。LLM #3 可关用模板，LLM #4 是连接「发生了什么」和「属性怎么变」的核心
3. **边列表是所有下游的命脉**：连静止状态也要生成基线边，否则故事层和后处理都在空中楼阁编造


