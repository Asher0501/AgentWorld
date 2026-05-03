# Agent World — 项目规划

## 目录结构

```
agent-world/
├── src/
│   └── agent_world/           # 项目根包
│       ├── __init__.py
│       ├── models/            # 数据模型层
│       │   ├── world.py
│       │   ├── npc.py
│       │   ├── npc_defaults.py
│       │   └── interaction.py     # 交互图核心模型 (Phase 3.5e)
│       ├── db/                # 数据库层
│       │   ├── db.py
│       │   └── schemas.py
│       ├── api/               # API 层（路由）
│       │   ├── world.py
│       │   ├── npc.py
│       │   └── agent.py
│       ├── services/          # 核心业务
│       │   ├── npc_engine.py      # [OBSOLETE] 旧引擎，待删除
│       │   ├── graph_engine.py    # 交互图引擎 (Phase 3.5e)
│       │   ├── graph_npc_engine.py# 图引擎服务器集成 (Phase 3.5e)
│       │   ├── graph_adapter.py   # 适配器：NPC/物体→Entity (Phase 3.5e)
│       │   ├── interaction_resolver.py # LLM 交互解析器 (Phase 3.5e)
│       │   └── world_updater.py   # 世界更新器
│       ├── cognition/         # 认知/推理引擎 (Phase 3.5a)
│       │   ├── persona.py     # 角色标签
│       │   ├── memory.py      # 记忆管理
│       │   ├── context.py     # 上下文构建
│       │   ├── reasoner.py    # LLM 推理引擎
│       │   ├── fallbacks.py   # [OBSOLETE] 旧兜底，待删除
│       │   ├── llm_decider.py # [OBSOLETE] 旧 LLM 决策，待删除
│       │   └── plan_parser.py # [OBSOLETE] 旧计划解析器，待删除
│       ├── entities/          # 世界实体系统 (Phase 3.5b)
│       │   ├── base.py        # 实体基类（旧）
│       │   ├── base_entity.py # 实体基类（新）：属性管理 + 接口管理 + 拓扑连接
│       │   ├── manager.py     # 实体管理器
│       │   └── world_objects.py# 具体物体（摊位/农田/矿脉...）
│       └── config.py
├── web/                       # 浏览器可视化
│   ├── world_viewer.py        # Python 静态文件服务器 + WebSocket
│   ├── index.html             # 可视化主页面
│   └── assets/
├── tests/                     # 测试
│   ├── test_npc_module.py     # [OBSOLETE] 旧 NPC 测试
│   ├── test_plan_parser.py    # [OBSOLETE] 旧计划解析器测试
│   └── test_system_integration.py # 系统集成测试
├── data/                      # SQLite
├── requirements.txt
└── README.md
```

---

## 阶段规划

### Phase 1 — 数据层 + 可视化骨架 ✅

**工具：** `pydantic` / `sqlite3` / `html/css/js`

**功能：**
- NPC 数据结构定义完毕（models/）
- 数据库初始化（空世界）
- 浏览器可打开 `index.html`，看到空白地图和 NPC 列表

**产出文件：**
- `src/agent_world/models/npc.py`
- `src/agent_world/models/world.py`
- `src/agent_world/db/db.py`
- `src/agent_world/db/schemas.py`
- `web/index.html`
- `web/world_viewer.py`（骨架）
- `data/agent_world.db`（空库）

---

### Phase 2 — API 层 + 实时推送 ✅

**工具：** `FastAPI` / `websockets` / `uvicorn`

**功能：**
- REST API：创建/查询/更新/删除 NPC
- 浏览器自动刷新显示世界状态
- WebSocket 连接管理

**产出文件：**
- `src/agent_world/api/npc.py`
- `src/agent_world/api/world.py`
- `web/world_viewer.py`（完整 WebSocket）

---

### Phase 3 — NPC Engine（行为树版）✅

**工具：** `openai` / `langchain` / `asyncio` / `行为树`

**功能：**
- NPC 根据时间 tick 自动行动
- 行为树驱动 NPC 状态转换
- NPC 记忆积累

**状态：** ✅ 已完成，已被 Phase 3.5e 替代

---

### Phase 3.5a — Cognition Module（认知模块）✅

**工具：** `openai` / LLM / Pydantic

**功能：**
- **Persona Tags** — NPC 角色标签
- **Memory Store** — 记忆管理
- **Context Builder** — 上下文构建
- **Goal Reasoner** — LLM 推理引擎
- **Fallback Engine** — 规则引擎兜底

**状态：** ✅ 已完成

---

### Phase 3.5b — Entities Module（实体系统）✅

**工具：** Pydantic / `asyncio`

**功能：**
- **WorldObject 基类** — 所有可交互实体的基类
- **实体类型** — Stall、FarmPlot、OreVein、BarCounter 等
- **WorldObjectManager** — 实体管理器
- **交互接口** — `can_interact()` / `interact()` / `get_affordances()`

**状态：** ✅ 已完成

---

### Phase 3.5c — NPC Engine 重构（Goal-Driven 版）✅

**工具：** cognition + entities + asyncio

**功能：**
- NPC Engine 接 cognition 模块 + entities 模块
- 行为循环：评估状态 → 形成 Goal → 找 Target → 移动 → 交互
- NPC 行为从"规则/状态机"变为"目的驱动"

**状态：** ✅ 已完成，已被 Phase 3.5e 替代

---

### Phase 3.5d — NPC 属性/记忆系统 ✅

**工具：** Pydantic / SQLite / LLM

**功能：**
- 体力/饥饿/心情等动态属性
- NPC 记忆存储与检索
- 属性恢复逻辑

**状态：** ✅ 已完成

---

### Phase 3.5e — 交互图引擎（核心重构）✅

**工具：** `Pydantic` / LLM（MiniMax / OpenAI）

**核心设计理念：** **所有派生/硬编码逻辑被交互图完全替代**

- LLM 读取实体图（Entity + 原子接口 + 边拓扑），推导哪些边激活
- LLM 一次性输出所有 attribute effects + edge_qty_changes（双向库存转移）
- Engine 是纯执行器：构建图 → 生成 LLM prompt → 解析 LLM 响应 → 应用副效应

**NPC 只有 4 个原子接口（无角色特化）：**
- `移动` — 连接到 Zone 的可抵达
- `交互` — 连接到物体/其他 NPC 的可交互
- `持有` — 连接到物品/有主物体的可持有
- `等待` — 自连接，表示待机

**物体有 2 个原子接口：**
- `可交互` — 与 NPC 交互边连接
- `可持有` — 与所有权/持有边连接

**物品是类型级实体（非实例级）：**
- 每种物品（"小麦"、"金币"、"铁锭"）一个 Entity
- 持有量通过边的 qty 值表示
- 物品转移 = LLM 驱动的边 qty 增减

**产出文件：**
- `src/agent_world/models/interaction.py` — InteractionEdge / AttributeEffect / InteractionGraph
- `src/agent_world/entities/base_entity.py` — Entity 基类（属性管理 + 接口管理 + 拓扑连接）
- `src/agent_world/services/graph_engine.py` — 构建图 / LLM prompt / 解析响应 / 执行副效应
- `src/agent_world/services/graph_adapter.py` — NPC / 物体 / Zone / 物品 → Entity 适配
- `src/agent_world/services/graph_npc_engine.py` — 服务器集成：主循环 / 拓扑构建 / 所有权管理
- `src/agent_world/services/interaction_resolver.py` — LLM 调用入口（MiniMax Anthropic API + OpenAI）

**数据流（每 tick）：**
```
世界状态 → Entity 图构建 → LLM prompt
                                   ↓ (每 60 tick)
                              GraphNPCEngine → LLM (MiniMax M2.7)
                                   ↓
                              parse_llm_response()
                                   ↓
                              execute_effects() → 属性变更 + 库存转移
                                   ↓ (其他 tick)
                              Fallback 兜底行为
```

**LLM 配置文件：**
- `~/.config/openclaw/openclaw.json` — 读取 `llm.minimax` / `llm.openai` 凭证

**状态：** ✅ 已完成（2026-04-26）

---

### Phase 3.5f — 所有权拓扑 ✅

**工具：** 交互图引擎（Phase 3.5e）

**核心设计理念：** **所有权 = 持有 = 控制边 qty>0，LLM 从图拓扑推导身份**

- `持有` 边的目标如果是物品 → 表示"拥有 X 数量的物品"
- `持有` 边的目标如果是物体 → 表示"拥有/控制此物体"
- 物体有 `可持有` 接口，与 NPC 的 `持有` 接口通过同一种边连接
- LLM 推导身份：如 NPC 有到吧台的 `持有` 边 → LLM 推断其为酒馆老板
- 无需 `bar_owner`、`coin_owner` 等角色枚举

**拓扑规则：**
| 实体对 | 连接规则 |
|---|---|
| NPC → Zone | 所有 NPC → 所有 Zone（移动）
| NPC → 物品 | 所有 NPC → 所有物品类型实体（库存）
| NPC → NPC（同 Zone） | 同 Zone NPC 互相连接（社交/经济）
| NPC → 有主物体（是所有者） | 连接（qty=1 的持有边）
| NPC → 有主物体（非所有者） | **不连接** → 通过 NPC-to-NPC 间接交互
| NPC → 无主物体（公有） | 连接所有同 Zone NPC
| Zone → 物体 | Zone 到区域内物体（LLM 推理用）

**所有权初始化（npc_defaults.py）：**
- 老陈（merchant）→ tavern_bar_counter（酒吧吧台）
- 匹配机制：按 NPC 名称匹配，而非 Region 内第一个 NPC

**状态：** ✅ 已完成（2026-04-27）

---

### Phase 4 — 外部 Agent 接入协议 🔄

**工具：** 注册协议 / `API Key` 认证 / REST 接口

**功能：**
- 外部 AI Agent 可通过 API 加入世界
- 外部 Agent 与 NPC 交互
- 世界状态对外部 Agent 可见

**产出文件：**
- `src/agent_world/api/agent.py`（注册/认证/act）
- 外部 Agent 可接入

**状态：** 🔄 基础已完成

---

### Phase 5 — 持久化 + NPC 演进 ○

**工具：** SQLite / `schedule` / NPC 记忆系统

**功能：**
- 服务重启后世界状态保留
- NPC 根据历史发展
- 世界时间独立推进（日夜循环）

**状态：** ○ 未开始

---

## 设计原则

1. **每阶段独立验证** — 不影响上一步
2. **可视化从第一天就集成** — 不事后补救
3. **NPC Engine 和 API 层分离** — 便于单独测试
4. **新增不覆盖** — 已完成代码不被改写
5. **LLM 推导一切，Engine 纯执行** — 图拓扑 + 原子接口 + LLM，无硬编码行为
6. **所有权从拓扑推断** — 无需角色枚举，持有边 qty>0 = 拥有

---

## 当前阶段

**✅ Phase 1** — 数据层 + 可视化骨架
**✅ Phase 2** — API 层 + 实时推送
**✅ Phase 3** — NPC Engine（行为树版，已退役）
**✅ Phase 3.5a** — Cognition Module（已完成）
**✅ Phase 3.5b** — Entities Module（已完成）
**✅ Phase 3.5c** — NPC Engine 重构（已退役）
**✅ Phase 3.5d** — NPC 属性/记忆系统（已完成）
**✅ Phase 3.5e** — 交互图引擎（已完成）
**✅ Phase 3.5f** — 所有权拓扑（已完成）
**🔄 Phase 4** — 外部 Agent 接入协议（基础已就绪）
**○ Phase 5** — 持久化 + NPC 演进

---

## 已知问题 / 技术债务

### 🔴 需立刻处理
- [ ] 删除旧文件：`services/npc_engine.py`、`cognition/fallbacks.py`、`cognition/llm_decider.py`、`cognition/plan_parser.py`
- [ ] 清理旧测试：`tests/test_npc_module.py`、`tests/test_plan_parser.py`
- [ ] 创建 `tests/test_graph_engine.py`

### 🟡 待优化
- [ ] LLM edge_qty_changes 双向生效（从源扣除 + 目标增加）
- [ ] tick 输出显示 LLM result_text
- [ ] NPC memory 系统连接到 graph engine
- [ ] 所有权扩展到所有 Zone（农田→农民、摊位→商人）
- [ ] 旧 `entities/base.py` 与 `entities/base_entity.py` 合并

### ⚪ 长期
- [ ] LLM 调用频率自适应（非固定 60 tick）
- [ ] 多模型回退链（MiniMax → OpenAI → 本地模型）
- [ ] 玩家/外部 Agent 接入交互图
- [ ] 世界持久化（Phase 5）

---

## 服务器信息

- **URL：** `http://localhost:8765`
- **引擎：** `GraphNPCEngine` + fallback
- **LLM 调用：** 每 60 tick（~5 分钟），MiniMax M2.7
- **LLM 超时：** 90 秒，超时自动 fallback
- **NPC 数量：** 6（老张、王老板、赵铁柱、李夫子、孙大夫、老陈）
- **日志：** `/tmp/aw_owner.log`

---

_最后更新：2026-04-27_
