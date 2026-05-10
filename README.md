<div align="center">

# AgentWorld

**Graph-first · LLM-driven · Domain-agnostic**  
**图优先 · LLM 驱动 · 域无关**

[![Python](https://img.shields.io/badge/Python-3.12+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![LLM](https://img.shields.io/badge/LLM-MiniMax_M2.7-purple?style=flat-square)](#)
[![Status](https://img.shields.io/badge/Status-Active-success?style=flat-square)](#)

> *"Graph is not a feature. Graph is the system."*  
> 图拓扑不是组件，是整个系统的骨架。

---

</div>

## Table of Contents · 目录

- [Why AgentWorld](#why-agentworld--为什么)
- [Core Concepts](#core-concepts--核心概念)
- [Architecture](#architecture--架构)
- [Configuration](#configuration--配置)
- [Pipeline](#pipeline--管线)
- [Domain Purification](#domain-purification--域净化)
- [Quick Start](#quick-start--快速开始)
- [Project Structure](#project-structure--项目结构)

---

## Why AgentWorld · 为什么

Building a multi-agent simulation typically means hardcoding domain logic into every layer. The engine knows about "NPCs", "zones", "inventory" — switch to a different world (trading market, protein network, IoT grid), and you rewrite everything.

传统的多智能体仿真把域逻辑嵌入每层代码——引擎认识 "NPC"、"区域"、"背包"。换个世界（交易市场、蛋白质网络、物联网）就得重写。

**AgentWorld flips this.** The engine sees only nodes and weighted edges. "What this node is" comes from config files. "What happens this tick" comes from an LLM. The simulation logic is purely structural — and domain is a plugin.

**AgentWorld 反过来。** 引擎只看到节点和加权边。"节点是什么"由配置文件决定。"这一帧发生什么"由 LLM 决定。仿真逻辑纯粹是结构性的——域只是个插件。

---

## Core Concepts · 核心概念

### Graph as Single Source of Truth · 图即真相

Everything in AgentWorld is a **node** with **weighted directed edges**. An NPC? A node. A gold coin? A node. A zone, a recipe, a relationship — all nodes. Every connection (carrying, located-in, owns, produces) is a labeled, weighted edge.

AgentWorld 中一切皆是**节点**加**加权有向边**。角色是节点，金币是节点，区域、配方、关系……都是节点。每个连接（持有、位于、生产）是一条带标签、有权重的边。

| Concept · 概念 | Graph Representation · 图表示 |
|:---------------|:-----------------------------|
| Geralt is in White Orchard | `Geralt → White Orchard` (label: `location`, qty: 1) |
| Geralt has 8 gold coins | `Geralt → GoldCoin` (label: `carries`, qty: 8) |
| Tomira can brew potions | `Tomira → PotionRecipe` (label: `can_use`) |
| White Orchard connects to Inn | `White Orchard → Inn` (label: `connects_to`, qty: 1) |

### Single Table Persistence · 单表持久化

All nodes live in one SQLite table `nodes` — no schema per entity type, no JOINs. Every tick snapshots the entire graph in under 200ms.

所有节点存在一张 SQLite 表 `nodes` 里——没有按类型的多张表，没有 JOIN。每 tick 快照全图不到 200ms。

```
id              type    name     data
──────────────  ──────  ───────  ─────────────────────────────────
npc_97845b74    npc     杰洛特   {"attributes": {"vitality": 77, ...}}
zone_白果园      zone    白果园   {"role": "village", ...}
item_7402599b   item    金币     {"conserved": true}
recipe_魔药      recipe  研磨魔药  {"inputs": [草药×2], ...}
```

---

## Architecture · 架构

<br>

![Architecture Overview](agentworld_arch.svg)

<br>

The system divides into three conceptual "swimlanes" — **Config**, **Engine**, and **Output** — connected by a single graph data structure.

系统分三个概念泳道——**配置层**、**引擎层**、**输出层**——通过单个图数据结构贯通。

### Layer Stack · 层级栈

```
┌──────────────────────────────────────────────────────────┐
│                    Config Layer · 配置层                    │
│  node_config.json  domain.json  adapter.py                │
│  (topology labels, prompts, domain logic)                 │
└────────────────────────┬─────────────────────────────────┘
                         │ loads into
                         ▼
┌──────────────────────────────────────────────────────────┐
│              Graph Engine · 图引擎 (内存)                   │
│  weighted multi-digraph, adjacency lookups in O(1)        │
│  加权有向多重图，邻接查询 O(1)                               │
└────────────────────────┬─────────────────────────────────┘
                         │ orchestrates
                         ▼
┌──────────────────────────────────────────────────────────┐
│              Pipeline · 管线 (per tick)                    │
│  LLM #1 Plan → LLM #3 Story → LLM #4a Topo → LLM #5 Proj│
│  规划 → 叙事 → 拓扑变更 → 属性投影                          │
└────────────────────────┬─────────────────────────────────┘
                         │ writes to
                         ▼
┌──────────────────────────────────────────────────────────┐
│           Persistence · 持久层 (SQLite)                    │
│  single nodes table, tick report to /tmp/                 │
│  单表 nodes，tick 报告输出到 /tmp/                           │
└──────────────────────────────────────────────────────────┘
```

---

## Configuration · 配置

Only two JSON files define the world:

整个世界的定义只需要两个 JSON 文件：

### `node_config.json`

```json
{
  "types": {
    "npc": {
      "name": "角色",
      "bfs_starter": true,       // topology label: BFS starts here
      "max_edges": {"zone": 1}   // each NPC can be in 1 zone
    },
    "zone": {
      "name": "区域",
      "component_anchor": true,  // topology label: component boundary
      "disconnect_on_zero_edge": true
    },
    "item": {
      "name": "物品",
      "is_leaf": true,           // BFS stops here
      "disconnect_on_zero_edge": true
    }
  }
}
```

### `domain.json`

```json
{
  "prompts": {
    "LLM1_plans": "你是一个世界模拟引擎的规划层...",
    "LLM3_story": "你是世界模拟引擎的故事叙事层...",
    "LLM4a_topo_delta": "根据故事推理出拓扑变更...",
    "LLM5_projection": "推理出属性变化和近况摘要..."
  },
  "attributes": {
    "vitality": {"min": 0, "max": 100, "default": 70},
    "satiety": {"min": 0, "max": 100, "default": 60},
    "mood": {"min": 0, "max": 100, "default": 65}
  }
}
```

> **To switch to a new domain:** Rewrite these two JSON files + implement one adapter class. The engine code never changes.  
> **换一个世界：** 重写这两个 JSON 文件 + 实现一个适配器类。引擎代码不用动。

---

## Pipeline · 管线

<br>

![Pipeline Flow](agentworld_pipeline.svg)

<br>

Each tick = one pipeline run. Components are processed **in parallel** across all stages.

每 tick = 一次管线运行。分量在各阶段**并行**处理。

### Stage Breakdown · 阶段分解

| # | Stage · 阶段 | What it does · 做什么 | LLM |
|:-:|:------------|:---------------------|:---:|
| 1 | **Component Split** · 分量分割 | BFS from NPCs → groups of connected entities | ❌ |
| 2 | **Plan** · 规划 | Each NPC decides what to do this 30-min tick | ✅ #1 |
| 3 | **Exec Context** · 构建执行上下文 | Build entity context, interaction edges, inventory snapshot | ❌ |
| 4 | **Narrative** · 叙事 | Write a vivid story with scene, dialogue, actions | ✅ #3 |
| 5 | **Topology Delta** · 拓扑变更 | Compute edge changes (trade, move, consume, produce) | ✅ #4a |
| 6 | **Attribute Projection** · 属性投影 | Update vitality/satiety/mood + recent_info summaries | ✅ #5 |
| 7 | **Merge** · 归并 | Combine all component results, write to DB | ❌ |

### Verification Checks · 校验检查

Every LLM output is validated before acceptance:

| Check · 检查项 | Detects · 检测目标 |
|:-------------|:------------------|
| `json_format` | Non-JSON output, extra text |
| `entity_existence` | Hallucinated NPCs/items/zones |
| `capacity_upper_bound` | Overspending: spending more than you have |
| `degree_conservation` | Items appearing from nowhere (conserved items) |
| `story_topo_consistency` | Story says "went to forest" but edge says "stayed in inn" |

The system retries up to 3 times with feedback, then applies a **conservation-safe fallback** (clamp negative deltas, skip invalid ops).

系统最多重试 3 次并附上反馈，之后执行**守恒安全降级**（裁剪负 delta，跳过无效操作）。

---

## Domain Purification · 域净化

> *This is the architectural superpower.*  
> *这是架构最核心的设计。*

In a traditional simulation, domain knowledge leaks everywhere:

```
// Traditional: engine knows domain
if entity.type == "npc":              // hardcoded type check
    zone = find_zone(entity)          // hardcoded zone logic
    mood_text = f"{mood}%"            // hardcoded attribute name
```

传统仿真中，域知识随处可见。

**After purification, the kernel has ZERO domain knowledge:**

```
// Purified: engine reads topology labels
if entity.is_starter:                 // config-driven topology label
    ctx = adapter.build_context(entity, graph)  // adapter handles domain
    location = adapter.extract_location(ctx)    // adapter extracts
```

**净化后，内核完全不知域为何物。**

### Before vs After · 净化前后对比

| Component · 组件 | Before (domain leaked) | After (purified) |
|:----------------|:----------------------|:-----------------|
| Pipeline orchestrator | `has_role(entity, "actor")`, `_MOOD` constant | Reads opaque context from adapter |
| Post-processor | `has_role(entity, "region")` for zone filtering | `adapter.get_names_by_classification("region")` |
| Conservation validator | `has_role(entity, "actor")` for item tracking | `entity.is_starter` topology label |
| Graph engine BFS | `has_role(entity, "actor")` for component root | `entity.is_starter` + `entity.is_component_anchor` |
| Interaction layer | `has_role(entity, "actor")` for edge filtering | `entity.is_starter` |
| **Total domain-aware files** | **All of them** | **Only 3: config + adapter + `run_1tick.py`** |

### The Adapter Interface · 适配器接口

The `DomainAdapter` abstract class defines 24 methods — the complete contract between engine and domain.

`DomainAdapter` 抽象类定义了 24 个方法——引擎与域之间的完整契约。

```
Adapter (abstract)          NPCWorldAdapter          StockMarketAdapter
─────────────────           ────────────────         ──────────────────
get_pipeline_stages()  →    [plan, story, topo, proj] [plan, trade, settle]
build_prompt()         →    "You are a Witcher..."    "You are a trader..."
validate_topo()        →    check entity existence    check fund balance
merge_results()        →    update gold/stats         update P&L/portfolio
... 20 more methods    →    NPC-specific logic         market-specific logic
```

Each domain keeps its own `node_config.json`, `domain.json`, and `adapter.py` in a subdirectory under `domain/`. The engine layer reads **only** interfaces and topology labels.

每个域在 `domain/` 下有自己的目录，包含 `node_config.json`、`domain.json` 和 `adapter.py`。引擎层只读取接口和拓扑标签。

### Architecture Diagram · 架构图

<br>

![Config to Database](agentworld_config_db.svg)

<br>

---

## Quick Start · 快速开始

```bash
# Clone & install
git clone https://github.com/your-repo/agent-world.git
cd agent-world
pip install -r requirements.txt

# Reset DB to clean state
rm -f data/agent_world.db

# Run a single tick
PYTHONPATH=src python3 run_1tick.py tick_001

# Run 50 ticks sequentially
PYTHONPATH=src python3 run_50ticks.py
```

### Output · 输出

Each tick produces a full I/O archive in `/tmp/full_tick/{tick_label}/`:

```
/tmp/full_tick/tick_001/
├── LLM1_plans_0_prompt.txt       # Raw prompt → LLM
├── LLM1_plans_0_response.txt     # Raw response ← LLM
├── LLM3_story_0_prompt.txt
├── LLM3_story_0_response.txt
├── LLM4a_topo_delta_0_prompt.txt
├── LLM4a_topo_delta_0_response.txt
├── LLM5_projection_0_prompt.txt
├── LLM5_projection_0_response.txt
├── REPORT.md                     # Timing breakdown, NPC states, stories
└── timing.json                   # Machine-readable timing data
```

### Requirements · 依赖

- Python 3.12+
- MiniMax M2.7 API key (configured in `interaction_resolver.py`)
- SQLite 3.x (built-in)

---

## Project Structure · 项目结构

```
agent-world/
├── src/
│   └── agent_world/
│       ├── config/                 # Config loader + JSON definitions
│       │   ├── node_config.json    #   Entity types, topology labels
│       │   └── domain.json         #   Prompts, rules, recipes
│       ├── db/                     # SQLite persistence (single table)
│       │   ├── db.py               #   NodeDB CRUD
│       │   └── converters.py       #   node ↔ dict conversion
│       ├── domain/                 # Domain adapters (one dir per domain)
│       │   ├── adapter.py          #   Abstract DomainAdapter base class
│       │   └── npc_world/          #   NPCWorld concrete implementation
│       ├── entities/               # Entity (node) data model
│       ├── models/                 # Data models (interactions, NPC defaults)
│       └── services/               # Core engine logic
│           ├── graph_engine.py         # Weighted multi-digraph management
│           ├── graph_npc_engine.py     # High-level orchestrator
│           ├── pipeline_orchestrator.py# Pipeline stage dispatcher
│           ├── pipeline_engine.py      # LLM invocation plumbing
│           ├── interaction_resolver.py # LLM API client + adaptive retry
│           ├── interaction_layer.py    # Edge computation for plan context
│           ├── verification_layer.py   # LLM output verification
│           ├── verification_registry.py# Validation rule registry
│           ├── conservation_validator.py # Item conservation enforcement
│           ├── post_processor.py       # Result post-processing
│           └── prompt_assembler.py     # Prompt construction
├── run_1tick.py                 # Single-tick runner
├── run_50ticks.py               # Sequential batch runner
├── requirements.txt
└── README.md
```

---

<div align="center">

**Built with ❤️ on the principle that *structure is everything, domain is data*.**  
**基于"结构即一切，域即是数据"的理念构建。**

<sub>AgentWorld · Domain-Agnostic Multi-Agent Simulation Engine</sub>

</div>
