# 补丁：虚构实体防范 — Prompt 约束全链路统一

**日期**: 2026-05-02  
**涉及文件**:
- `src/agent_world/services/prompt_assembler.py`
- `src/agent_world/config/node_config.json`（已有 `allow_unregistered_entity`）
**改动量**: ~30 行

---

## 问题

### 现象

LLM #3 在故事中持续创造虚构人物（"灰布长衫的中年男子"、"穿着青色长衫的买主"、"衣着考究的客人"、卖酒时虚构"富商"、"风尘仆仆的年轻人"等），导致下游 LLM #4a 无法将交易映射到真实 NPC，输出单边 delta → 保守验证器拒绝 → 交易落空。

### 根因分析

经过多轮 tick 运行追踪，发现两条独立根因链：

#### 根因 1：记忆反馈循环（写端无约束）

```
Tick N:
  LLM #3 写故事 → 虚构了"衣着考究的客人"买蔬菜
  LLM #4b 生成 recent_info → "有衣着考究的客人停在摊前"    ← 写入 DB

Tick N+1:
  LLM #1 读取 NPC 状态 → 老张的 recent_info 有"中年男子"
  LLM #3 看到 recent_info → "还在和那个中年男子交易" → 延续虚构

Tick N+2: 自增强 → 越陷越深
```

DB 验证确认了这一循环：14:00 tick 的 recent_info 干净，14:30 出现"有中年男子来看麦子问价"，15:00 变成"正与中年男子议价"——每次 LLM #4b 都从故事中提取虚构人物写入 DB。

#### 根因 2：LLM #3 约束文字有例外口子（读端不绝对）

原始约束文字写：
> "虚构的无名过客可以出现在背景描写中，但不能与任何 NPC 发生物品交易或对话。"

LLM #3 钻了空子：认为"讨价还价不算交易、问价不算对话"，从而持续创造虚构买家。

#### 根因 3：LLM #3 即兴创作（即使输入全干净）

第三轮 tick 中，LLM #3 的输入经过验证完全干净（recent_info="我在南集市，正在做自己的事"、plan 未提虚构人物、event_input=空），它仍然在故事里凭空创造了"衣着考究的客人"。说明约束文字本身的绝对性不够。

---

## 解决思路

核心原则：**`allow_unregistered_entity` 一个开关控制全链路。**

| 层 | 之前 | 之后 |
|----|------|------|
| LLM #2 → 图操作 | 无约束 | 拓扑约束 |
| LLM #3 → 故事 | 有例外口子 | **绝对化：不得创造新实体** |
| LLM #4a → 拓扑 delta | 有约束 | 优化措辞 |
| **LLM #4b → recent_info** | **无约束** | **新增：recent_info 约束** |
| 引擎校验 → 过滤非法操作 | 已有 | 保持 |

---

## 改动

### 1. LLM #3 约束绝对化（`prompt_assembler.py:147-161`）

**旧版**（有"虚构过客"例外口子）：

```python
"□ **强制规则：故事中不能出现图中不存在的实体。**\n"
"  — 不在映射表中的名称（如虚构人物）不能作为故事中的交互对象出现。\n"
"  — 虚构的无名过客可以出现在背景描写中，\n"
"    但不能与任何 NPC 发生物品交易或对话。\n"
```

**新版**（无例外、绝对约束）：

```python
"□ **强制规则：故事中不得出现图中不存在的实体。**\n"
"  — 标签映射表列出了所有可用的实体。\n"
"  — 故事必须在映射表已有实体范围内创作。\n"
"  — 不得创造新的角色或实体。\n"
"  — 没有例外。\n"
```

### 2. LLM #4b 新增 `topology_constraints_recent` 约束（`prompt_assembler.py`）

在 `llm4b_content` slot 列表中插入 `label_mapping` + `topology_constraints_recent`：

```python
"llm4b_content": [
    ...
    ("stories_section",      "content"),
    ("label_mapping",        "topology"),        # 新增：告诉 LLM #4b 哪些实体存在
    ("topology_constraints_recent", "topology"), # 新增：recent_info 实体约束
    ("recent_info_guidance", "content"),
    ...
]
```

新增 handler：

```python
"□ **强制规则：recent_info 中不得引用图中不存在的实体。**\n"
"  — 标签映射表列出了所有可用的实体。\n"
"  — 不在映射表中的实体名称不得出现在 recent_info 中。\n"
"  — 故事中出现的虚构人物（如客人、路人、买家等）不应被写入 recent_info。\n"
"  — recent_info 只能引用映射表中已有的 NPC、物品、区域。\n"
"  — 没有例外。\n"
```

### 3. 已有配置 `node_config.json:13`

```json
"world": {
    "allow_unregistered_entity": false,
    ...
}
```

全链路统一读取 `config_loader.get_world_config("allow_unregistered_entity", False)`。

---

## 验证结果

约束加完后运行一 tick（16:00→16:30）：

| 维度 | 改动前 | 改动后 |
|------|--------|--------|
| 故事虚构人物 | 6/6 有（每轮必有） | 0/6 有具体虚构人物 ✅ |
| recent_info 虚构实体 | 每轮写入 DB 2-3 个 | 仅老陈含泛称"客人"（场景描写） ✅ |
| 时间开销 | 352s（含 LLM #4a 无 ops 空耗时） | 262s ✅ |

---

## 补丁 2：Hub 路由修复 — delta 是直接数量变化，非路径路由

**日期**: 2026-05-02 (17:00)
**涉及文件**:
- `src/agent_world/services/graph_engine.py`（connect/disconnect 双向修复）
- `src/agent_world/services/prompt_assembler.py`（Hub 路由规则）

---

## 问题

### 现象

LLM #4a 生成 zone 作为中间路由的交易模式：

```json
{"op": "delta", "src": "南集市", "tgt": "金币", "delta": 2},
{"op": "delta", "src": "张大娘", "tgt": "金币", "delta": -2},
{"op": "delta", "src": "南集市", "tgt": "面包", "delta": -2},
{"op": "delta", "src": "赵铁柱", "tgt": "面包", "delta": 2}
```

而非正确的 NPC↔NPC 直接交易：

```json
{"op": "delta", "src": "赵铁柱", "tgt": "金币", "delta": -2},
{"op": "delta", "src": "张大娘", "tgt": "金币", "delta": 2},
{"op": "delta", "src": "张大娘", "tgt": "面包", "delta": -2},
{"op": "delta", "src": "赵铁柱", "tgt": "面包", "delta": 2}
```

由于 zone 没有物品边（面包/金币），zone→item 的 delta 因 available=0 被 clip 到 0，交易落空。

### 根因分析

#### 根因 1：`connect()` 单向性导致组件断裂

`GraphEngine.connect()` 在 Phase 3.5+4 大重构（commit `d80fcaa`）中丢失了反向 `connect_to` 调用：

**旧代码**（d80fcaa 之前）：
```python
def connect(self, from_id: str, to_id: str):
    src.connect_to(to_id)      # ✅ 正向
    dst.connect_to(from_id)    # ✅ 反向
```

**新代码**（d80fcaa 之后）：
```python
def connect(self, src_eid, tgt_eid, qty):
    self._entities[src_eid].connect_to(tgt_eid)  # ❌ 只有正向
    # 反向连接完全丢失
```

连锁反应：
1. `ge.connect(npc, zone, -1)` 只加了 NPC→zone 的 `connected_entity_ids`，没加 zone→NPC
2. zone 的 `connected_entity_ids = set()` 永远为空
3. component builder 从 NPC 出发，走到 zone 后无法继续（zone 没有其他 NPC 的邻居信息）
4. 赵铁柱的 component 只包含 3 个实体（赵铁柱, 金币, 南集市），不含同 zone 的张大娘
5. LLM #3 只能写独角戏（自己的事），看不到同 zone 的其他 NPC
6. LLM #4a 看到 8 个独角戏 + 拓扑中 zone↔NPC 双向边 → 用 zone 路由交易

诊断验证：

```python
# 修复前
南集市 connected_entity_ids = set()           ← 空
赵铁柱 component = {赵铁柱, 金币, 南集市}    ← 孤岛

# 修复后
南集市 connected_entity_ids = {8 NPCs}         ← 全部 NPC
赵铁柱 component = 16 entities (所有 NPC+物品) ← 完整
```

#### 根因 2：LLM #4a 缺乏 Hub 路由约束

即便修复了 zone 的 `connected_entity_ids`，LLM #4a 仍然用 zone 路由：因为拓扑显示 `{B}(zone) ↔ {D}(张大娘)` 和 `{B} ↔ {R}(赵铁柱)`，LLM 认为
"两个 NPC 都与 zone 相连 → zone 可以是路由中间节点"。

---

## 解决思路

### 思路 1：双向连接修复（根因 1）

`connect()` 和 `disconnect()` 都应该双向更新 `connected_entity_ids`，使得 zone 能发现所有 NPC：

```python
# connect 中增加反向
self._entities[src_eid].connect_to(tgt_eid)
self._entities[tgt_eid].connect_to(src_eid)  # 新增反向

# disconnect 中增加反向
self._entities[src_eid].disconnect_from(tgt_eid)
self._entities[tgt_eid].disconnect_from(src_eid)  # 新增反向
```

### 思路 2：Hub 无路由规则（根因 2）

在 `topology_constraints` 约束段增加跨域通用规则：

> delta 操作描述的是两个节点之间的直接数量变化，非路径路由。若两节点都与同一枢纽 Hub 相连，需交换某标签的数量时，应直接在两者之间建立 delta 操作，而非分别与 Hub 做两段 delta。Hub 是结构共享点，不是数量路由节点。

这条规则用纯拓扑语言书写（节点、标签、Hub、delta），可适用于任何领域：
- 村庄经济：A=张大娘, H=南集市, C=赵铁柱
- 蛋白质网络：A=Kinase_A, H=Scaffold_Complex, C=Kinase_B
- 交通物流：A=仓库1, H=中转站, C=仓库2

---

## 改动

### 1. `graph_engine.py` — `connect()` 双向化

```python
# 第 148-149 行
# 实体间连接（双向）
self._entities[src_eid].connect_to(tgt_eid)
self._entities[tgt_eid].connect_to(src_eid)  # 新增
```

### 2. `graph_engine.py` — `disconnect()` 双向化

```python
# 第 157-160 行
if src_eid in self._entities:
    self._entities[src_eid].disconnect_from(tgt_eid)
if tgt_eid in self._entities:
    self._entities[tgt_eid].disconnect_from(src_eid)  # 新增
```

### 3. `prompt_assembler.py` — Hub 路由约束

在 `topology_constraints` handler 中新增一段：

```python
"□ delta 操作描述的是两个节点之间的直接数量变化，非路径路由。\n"
"  — 若两节点都与同一枢纽 Hub 相连，需交换某标签的数量时，\n"
"    应直接在两者之间建立 delta 操作，而非分别与 Hub 做两段 delta。\n"
"  — 例：A ↔ H ↔ C 时，若 A 要给 C 数量 5 的某标签，\n"
"    应直接 src=A→tgt=C delta=-5（配合 src=C→tgt=A delta=+5），\n"
"    而非 src=A→H⁻5 + H→C⁺5。Hub 是结构共享点，不是数量路由节点。\n"
```

---

## 验证结果

### Tick 10:30（修复后，组件正确但无 Hub 规则）

| 维度 | 值 |
|------|-----|
| LLM #3 故事 | 单一 scene 覆盖 8 NPC，但全是 setup（摆摊/溜达） |
| LLM #4a 操作 | 4 条 zone 路由 delta（张大娘→金币-2 + 南集市→面包-2 → 落空） |
| 实际交易 | 张大娘金币扣了 2，但面包没到赵铁柱 |
| 耗时 | 265s |

### Tick 11:30（Hub 规则生效后）🎉

| 维度 | 值 |
|------|-----|
| LLM #3 故事 | 赵铁柱"走到张大娘的摊前，掏出铜板想买两个" |
| LLM #4a 操作 | 4 条直接 NPC↔NPC delta（赵铁柱↔张大娘 × 金币+面包） |
| 实际交易 | 赵铁柱金币-2 ✓ 面包+2 ✓ 张大娘面包-2 ✓ |
| 耗时 | 204.6s（-23%） |

### 效果对比

```
修复前：                   修复后：
Δ 南集市 → 金币 +2  ❌     Δ 赵铁柱 → 金币 -2  ✅
Δ 张大娘 → 金币 -2  ⚠️     Δ 张大娘 → 金币 +2  ✅
Δ 南集市 → 面包 -2  ❌     Δ 张大娘 → 面包 -2  ✅
Δ 赵铁柱 → 面包 +2  ❌     Δ 赵铁柱 → 面包 +2  ✅
```

第一笔 NPC↔NPC 直接交易在春·第1天12:00 tick 成功执行：赵铁柱用 2 金币向张大娘买了 2 个面包，库存正确更新。🎉
