# AgentWorld

<p align="center">
  <img src="agentworld_banner.png" alt="AgentWorld Banner" width="100%">
</p>

> 基于 LLM 驱动的多 NPC 世界模拟引擎

一个由 **4 层 LLM 管线**驱动的多智能体世界模拟系统。NPC 在村庄、森林、集市、酒馆等区域中自主生活、交易、社交，所有行为由大模型实时决策，代码仅负责拓扑约束和守恒验证。

## 核心架构

### 4 层 LLM 管线（每 Tick）

```mermaid
flowchart TD
    DB[("DB / 图引擎")]
    
    LLM1["🤖 LLM #1 决策层"]
    LLM2["🤖 LLM #2 拓扑层"]
    LLM3["🤖 LLM #3 叙事层"]
    LLM4["🤖 LLM #4 执行层"]

    VAL["✅ ConservationValidator\n守恒校验"]
    WRITE["💾 回写 DB + 图引擎"]

    DB --> LLM1
    LLM1 -->|"每个NPC输出\n自然语言计划"| LLM2
    LLM2 -->|"connect/disconnect\n只改拓扑不改数值"| LLM3
    LLM3 -->|"为每个子图\n生成故事"| LLM4
    LLM4 -->|"属性/库存/关系\ndelta 批量更新"| VAL
    VAL --> WRITE
    WRITE -.->|"下一 tick"| DB

    style LLM1 fill:#e1f5fe
    style LLM2 fill:#fff3e0
    style LLM3 fill:#f3e5f5
    style LLM4 fill:#e8f5e9
    style VAL fill:#ffebee
```

### 完整生命周期

```mermaid
sequenceDiagram
    participant DB as 数据库
    participant GE as 图引擎
    participant L1 as LLM #1 决策
    participant L2 as LLM #2 拓扑
    participant L3 as LLM #3 叙事
    participant L4 as LLM #4 执行
    participant CV as 守恒校验

    DB->>GE: 加载 NPC / Zone / Item
    GE->>L1: 每个 NPC 的决策提示词
    L1->>L1: 输出自然语言计划（例："卖 2 袋小麦给王老板"）
    L1->>L2: 所有 NPC 的计划汇总
    L2->>L2: 输出 connect / disconnect
    L2->>GE: 执行拓扑变更
    GE->>L3: 按子图分组（BFS 遍历）
    L3->>L3: 每个连通子图生成一段故事
    L3->>L4: 故事 + 全局状态
    L4->>L4: 输出属性/库存 delta + recent_info
    L4->>CV: 校验 Σgold=0, Σitem=0
    CV->>GE: 通过后写入图引擎
    GE->>DB: 持久化所有变更
```

### 数据流

| 层级 | 输入 | 输出 | 一句话 |
|------|------|------|--------|
| **#1 决策** | NPC 状态 + 库存 + 位置 + 最近信息 | 自然语言计划 | 决定做什么 |
| **#2 拓扑** | 所有 NPC 计划 | `connect/disconnect` | 空间/社交移动 |
| **#3 叙事** | 拓扑变更后的子图结构 | 自然语言故事 | 发生了什么 |
| **#4 执行** | 故事 + 全局状态 | 属性/库存 delta | 落实变化 |

## 设计原则

- **LLM 是大脑，代码是骨架**：代码提供上下文和约束，LLM 做判断
- **自然语言 > 硬编码阈值**：属性状态注入 prompt，不写 `if vitality < 30`
- **纯拓扑边**：边只表示连通/断连，不携带接口语义
- **守恒校验**：ConservationValidator 保证经济系统不出错

## 项目结构

```
src/agent_world/
├── api/                  # HTTP API
├── cognition/            # LLM prompt 构建
├── config/               # 节点本体配置
├── db/                   # SQLite 持久化
├── entities/             # 实体模型（Entity / Zone / Item）
├── models/               # Pydantic 数据模型（NPC / World）
└── services/             # 核心管线
    ├── graph_npc_engine.py      # 主编排引擎
    ├── graph_engine.py          # 图拓扑引擎
    ├── graph_adapter.py         # DB → 图适配
    ├── intent_executor.py       # LLM #2 执行
    ├── interaction_layer.py     # LLM #3 故事生成
    ├── post_processor.py        # LLM #4 批量更新
    ├── conservation_validator.py # 守恒校验
    └── interaction_resolver.py  # LLM 调用封装

bin/
├── run_tick_report.py     # 单 tick 报告
└── run_20ticks.py         # 批量运行

docs/                      # 架构决策记录
```

## 世界设定

### 时间系统

- 春夏秋冬四季，起始于春·第 1 天 08:00
- 白天（06:00–21:00）：每 tick **30 分钟**
- 夜间（21:00–06:00）：每 tick **6 小时**

```mermaid
gantt
    title 昼夜推进规则
    dateFormat HH:mm
    axisFormat %H:%M

    section 白天
    活动期        :active, d1, 06:00, 15h
    section 夜间  
    加速睡眠期    :night, n1, 21:00, 9h
```

### 区域

`farm` · `market` · `tavern` · `barracks` · `library` · `temple` · `forest` · `village_square`

### NPC 属性

`vitality(体力)` · `satiety(饱腹)` · `mood(心情)` — 每 tick 自然衰减，交互/交易可提升

## 快速开始

```bash
pip install -r requirements.txt
python3 -c "from agent_world.db.db import init_db; init_db()"
python3 bin/run_tick_report.py           # 跑 1 tick
python3 bin/run_tick_report.py --save    # 保存 JSON trace
python3 bin/run_20ticks.py               # 批量跑 20 tick
```

## 技术栈

**Python 3.12+** · **Pydantic v2** · **MiniMax / Anthropic API** · **SQLite** · **自定义图引擎**

## License

MIT
