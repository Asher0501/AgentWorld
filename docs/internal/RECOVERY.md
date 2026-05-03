# Agent World — 项目恢复文档

> 生成时间：2026-04-24 23:33 GMT+8
> 项目路径：`~/Documents/01_Projects/05_AgentWorld/`

---

## 一、项目概述

一个 AI Agent 与 NPC 共存的世界模拟器。NPC 由 LLM 驱动（当前为规则引擎兜底），围绕 Goal-Driven 行为循环运行。

---

## 二、文件结构

```
05_AgentWorld/
├── src/agent_world/
│   ├── __init__.py
│   ├── config.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── npc.py              # NPC 数据模型（含 MemoryEntry）
│   │   └── world.py             # World/Zone/WorldTime 模型
│   ├── db/
│   │   ├── __init__.py
│   │   ├── db.py                # SQLite 封装（WorldDB/NPCDB）
│   │   └── schemas.py           # API 响应模型
│   ├── api/
│   │   ├── __init__.py
│   │   ├── npc.py              # NPC REST API
│   │   └── world.py            # World REST API
│   ├── cognition/              # ✅ Phase 3.5a — 认知模块
│   │   ├── __init__.py
│   │   ├── persona.py          # PersonaTags（角色标签，可扩展）
│   │   ├── memory.py           # MemoryStore（记忆管理）
│   │   ├── context.py         # ContextBuilder（LLM 输入构建）
│   │   ├── reasoner.py         # GoalReasoner（LLM 推理引擎）
│   │   └── fallbacks.py       # FallbackEngine（规则引擎兜底）
│   ├── entities/               # ✅ Phase 3.5b — 实体系统
│   │   ├── __init__.py
│   │   ├── base.py            # WorldObject 基类 + 状态机 + 交互接口
│   │   └── world_objects.py    # 具体实体 + WorldObjectManager
│   └── services/
│       ├── __init__.py
│       └── npc_engine.py       # ✅ Phase 3.5c — NPC Engine v2（Goal-Driven）
├── web/
│   ├── __init__.py
│   ├── world_viewer.py        # FastAPI 入口 + WebSocket + NPC Engine 启动
│   └── index.html             # 可视化界面
├── data/
│   └── agent_world.db         # SQLite 数据库（包含历史数据）
├── requirements.txt
├── README.md
└── PROJECT_PLAN.md
```

---

## 三、当前已完成阶段

```
Phase 1   ✅ 数据层 + 可视化骨架
Phase 2   ✅ API 层 + 实时推送
Phase 3   ✅ NPC Engine（行为树版，已被替换）
Phase 3.5a ✅ Cognition Module — GoalReasoner + Persona + Memory + Context
Phase 3.5b ✅ Entities Module — WorldObject + Stall/FarmPlot/OreVein/BarCounter...
Phase 3.5c ✅ NPC Engine v2 — Goal-Driven（接 cognition + entities）
Phase 4   ○  外部 Agent 接入协议
Phase 5   ○  持久化 + NPC 演进
```

---

## 四、恢复步骤

### 4.1 环境准备

```bash
# 克隆/复制项目目录
cp -r ~/Documents/01_Projects/05_AgentWorld /path/to/restore/

# 安装依赖
pip install openai --break-system-packages
pip install fastapi uvicorn websockets pydantic python-dotenv jinja2

# 或通过 requirements.txt
pip install -r requirements.txt --break-system-packages
```

**注意**：`openai` 包需要单独安装。

### 4.2 数据库恢复

```bash
# 数据库文件直接拷贝即可（SQLite 是文件数据库）
cp ~/Documents/01_Projects/05_AgentWorld/data/agent_world.db /path/to/restore/data/
```

### 4.3 启动服务

```bash
cd ~/Documents/01_Projects/05_AgentWorld
PYTHONUNBUFFERED=1 python3 web/world_viewer.py > /tmp/agent-world.log 2>&1 &
sleep 5
curl http://192.168.10.226:8765/
# 应返回: {"msg":"Agent World API","version":"0.1.0","phase":3}
```

服务端口：**8765**（HTTP + WebSocket）

### 4.4 验证运行状态

```bash
# 检查 NPC 状态
curl http://192.168.10.226:8765/api/npc

# 检查世界状态
curl http://192.168.10.226:8765/api/world
```

---

## 五、关键模块说明

### 5.1 Cognition 模块（Goal 推理核心）

**数据流向：**
```
PersonaTags + MemoryStore + ContextBuilder
        ↓ 构建 prompt
   GoalReasoner (LLM) — 需 OPENAI_API_KEY 环境变量
        ↓ (fallback)
   FallbackEngine (规则兜底)
        ↓
   GoalOutput { goal, reason, plan, urgency }
```

**文件对应：**
- `cognition/persona.py` — 角色标签（cautious/ambitious/social/diligence...）
- `cognition/memory.py` — 记忆存储（add/search/summarize/prune）
- `cognition/context.py` — `ContextBuilder.format_for_llm()` 构建输入 prompt
- `cognition/reasoner.py` — `GoalReasoner.reason()` 调用 LLM
- `cognition/fallbacks.py` — `FallbackEngine.resolve()` 规则兜底

**环境变量：**
```bash
export OPENAI_API_KEY=sk-...   # LLM 推理用，不设置则走规则引擎兜底
```

### 5.2 Entities 模块（世界实体）

**实体类型：**
| 类型 | 类名 | 位置 |
|------|------|------|
| STALL | `Stall` | 商人摊位 |
| FARM_PLOT | `FarmPlot` | 农田（plant/water/harvest）|
| ORE_VEIN | `OreVein` | 矿脉（mine，可枯竭）|
| BAR_COUNTER | `BarCounter` | 酒馆吧台（drink/talk）|
| LIBRARY_DESK | `LibraryDesk` | 图书馆书桌 |
| TEMPLE_ALTAR | `TempleAltar` | 神庙祭坛 |
| BARRACKS_EQUIPMENT | `BarracksEquipment` | 兵营训练设施 |
| FOREST_HUNTING_GROUND | `ForestHuntingGround` | 森林狩猎场 |

**`WorldObjectManager`：**
- `init_default_world(zones)` — 根据世界 Zone 创建实体
- `find_nearest_available(from_zone_id, object_type, zone_connections)` — BFS 找最近可用实体
- `tick()` — 全局 tick（更新农田生长状态等）

### 5.3 NPC Engine v2（行为循环）

**行为循环：**
```python
tick():
  for npc in npcs:
    1. 检查是否需要新 Goal（无 goal / plan 执行完 / cooldown 到期）
    2. GoalReasoner 推理（LLM）或 FallbackEngine 兜底 → GoalOutput
    3. 执行当前 Plan 步骤：
       - 移动到目标 Zone
       - 找可用 Entity → interact()
    4. 记录记忆（npc.add_memory）
    5. 更新 NPC 状态
    6. 广播（WebSocket）
```

**Goal → ObjectType 映射：**
```python
GOAL_TO_OBJECTS = {
    "trade": STALL,
    "farm": FARM_PLOT,
    "mine": ORE_VEIN,
    "rest": BAR_COUNTER,
    "socialize": BAR_COUNTER,
}
```

**运行时状态（内存）：**
```python
npc_state = {
    "current_goal": GoalOutput,
    "current_plan": list[str],  # action 步骤列表
    "plan_index": int,
    "goal_cooldown": int,        # 3 tick 冷却再重新推理
    "last_goal_reason": str,
}
```

---

## 六、数据恢复（数据库）

数据库路径：`data/agent_world.db`

**数据库结构：**
```sql
CREATE TABLE world (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,       -- JSON: World 模型
  updated_at TEXT
);

CREATE TABLE npcs (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,       -- JSON: NPC 模型（含 memory 列表）
  created_at TEXT,
  updated_at TEXT
);
```

**清理旧记忆重新测试（可选）：**
```python
import json
with open("data/agent_world.db") as f: pass  # SQLite 直接操作
# 或用 SQLiteBrowser 直接编辑
```

---

## 七、当前运行状态

- **服务地址**：http://192.168.10.226:8765
- **WebSocket**：ws://192.168.10.226:8765/ws
- **当前 NPC**：老王（merchant）、老张（farmer）
- **实体数量**：8 个（market×2 stall, farm×3 farm_plot, mine×2 ore_vein, tavern×1 bar_counter）
- **历史数据**：老记忆547/551条（建议测试时清理）

---

## 八、待办/改进项

1. **OPENAI_API_KEY 配置**：设置后 LLM 推理启用，行为更智能
2. **清理旧记忆**：NPC 历史记忆过多影响可观测性
3. **Phase 3.5a LLM 提示词优化**：当前 prompt 模板较简单，可迭代改进
4. **Phase 4**：外部 Agent 接入协议
5. **Phase 5**：NPC 持久化 + 演进系统
6. **视觉升级**：从色块升级为像素风格地图（远期目标）

---

_最后更新：2026-04-24 23:33 GMT+8_
