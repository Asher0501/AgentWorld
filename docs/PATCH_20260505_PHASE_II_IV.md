# Patch: Phase II–IV 重构 & 功能变更

**日期:** 2026-05-05  
**范围:** `3bbb142` (Phase II Step A+B) → HEAD (uncommitted changes)  
**统计:** 16 文件，+232/-419 行（Phase II–III）+ 当前未提交改动 +78/-217 行

---

## 目录

1. [Phase II: PipelineOrchestrator 框架](#phase-ii-pipelineorchestrator-框架)
2. [Phase III: NPC 模型数据驱动 + 前缀通用化](#phase-iii-npc-模型数据驱动--前缀通用化)
3. [Phase IV: survival_needs 槽位 & dead code 清理](#phase-iv-survival_needs-槽位--dead-code-清理)
4. [Recent Info 滚动窗口](#recent-info-滚动窗口)
5. [文件变更索引](#文件变更索引)

---

## Phase II: PipelineOrchestrator 框架

**Commits:** `3bbb142` → `5b8f2ea`  
**目标:** 将 pipeline_engine.py 的 LLM 阶段编排拆入 PipelineOrchestrator

### 新增

| 文件 | 结构 | 作用 |
|------|------|------|
| `services/pipeline_orchestrator.py` | `class PipelineOrchestrator` (~200 行) | 顶层编排器，管理 LLM #1–#5 全流程 |
| `services/pipeline_orchestrator.py` | `class TickContext` | tick 上下文容器（plan_map, stories, attr_ops, recent_info_map, retry 存档等）|
| `services/pipeline_engine.py` | `_build_pipeline()` → 调用 `Orchestrator.run()` | 原来 ~400 行内联逻辑归入编排器 |

### 删除

| 函数 | 文件 | 行数 | 原因 |
|------|------|------|------|
| `_run_plans()` | `pipeline_engine.py` | ~60 行 | 归入 `Orchestrator._run_stage_plan()` |
| `_run_topology()` | `pipeline_engine.py` | ~50 行 | 归入 LLM #2/#3 阶段 |
| `_run_stories()` | `pipeline_engine.py` | ~40 行 | 归入 LLM #3 阶段 |
| `_run_delta()` | `pipeline_engine.py` | ~50 行 | 归入 LLM #4a 阶段 |
| `_run_attr()` | `pipeline_engine.py` | ~40 行 | 归入 LLM #4b 阶段 |
| `_run_validate()` | `pipeline_engine.py` | ~30 行 | 归入 LLM #5 阶段 |
| `_run_execute()` | `pipeline_engine.py` | ~40 行 | 归入执行器 |
| StageOutputType 旧实现 | pipeline_engine.py | 移至 orchestrator | 类型系统独立 |

### 修改

- `pipeline_engine.py` — 主流程从 `run_llm_round()` 扁平化改为 `Orchestrator.run()` 调用
- `graph_npc_engine.py` — 暴露 `get_entity()`、`ent.recent_info` 等图引擎接口供编排器使用

---

## Phase III: NPC 模型数据驱动 + 前缀通用化

**Commits:** `5734e75` → `37b99e7` (Steps A–G)  
**目标:** 移除 `npc_defaults.py` 中的硬编码、EID 前缀走 config 驱动、NPCRole 枚举改为数据驱动

### 新增

| 文件 | 结构 | 行数 | 作用 |
|------|------|------|------|
| `config/config_loader.py` | `resolve_eid()`, `has_role()`, `prefix_to_type_id()` | ~30 行 | EID 解析通用化，不再依赖 `startswith('npc_')` |

### 删除

| 函数 | 文件 | 行数 | 原因 |
|------|------|------|------|
| NPCRole 枚举 (MERCHANT, FARMER 等) | `models/npc.py` | ~30 行 | 数据驱动取代枚举 |
| `_is_item_type()` 内 `startswith()` 兜底 | `graph_adapter.py` | ~5 行 | 统一走 `has_type_prefix()` |
| `startswith('npc_')`/`startswith('item_')`/`startswith('zone_')` | 多处 (translation.py, validation.py, api/*.py) | ~20 行 | `resolve_eid()` 替代 |

### 修改

| 文件 | 关键改动 |
|------|---------|
| `config/config_loader.py` | 新增 `resolve_eid(entity_id: str) -> EntityId`、`get_all_prefixes()`、`has_role(type_id, role)`、`has_recent_info(type_id)` |
| `graph_adapter.py` | `create_node()` / `ensure_node()` + `get_entity()` 通用化 EID 前缀查找 |
| `graph_engine.py` | 边模型优化，`_is_item_type()`, `_is_npc_type()` 移除 |
| `graph_npc_engine.py` | `prepare_npc_context()` 从 280 行精简为单文件加载 + BFS 子图 |
| `models/npc_defaults.py` | `NPCRole` 枚举删除，`_make_npc_from_dict()` 简化为纯 dict 读取 |
| `models/npc.py` | `NPC.__init__` 中角色定义从枚举改为 `role: str` |
| `domain/npc_world/adapter.py` | slot 函数改为 `_adapter_data.get()` 统一读取 |
| `services/post_processor.py` | `resolve_attr_and_recent()` 参数简化 |
| `services/pipeline_orchestrator.py` | LLM #4a prompt 构建 + 拓扑验证重试逻辑精简 |
| `services/world_updater.py` | `_is_item_type()` 替换为 `has_type_prefix()` |
| `api/agent.py`, `api/npc.py` | import 路径清理（移除废弃模块引用）|

---

## Phase IV: survival_needs 槽位 & dead code 清理

**日期:** 2026-05-05（当前 session，未提交）  
**范围:** 7 个文件，+78/-217 行

### 修改

#### 1. survival_needs 槽位内容重写

| 文件 | 行 | 改动 |
|------|----|------|
| `config/domain.json` | ~182 | `adapter.survival_needs` 内容替换 |

**旧内容：** 存在模糊描述的旧版生存需求提示。  
**新内容：** 三属性=0 的明确致命后果：

```
任何一项降到 0：
  体力 = 0 → 虚脱倒下。你撑不住了，身体彻底垮了。
  饱食 = 0 → 饿死。没有东西下肚，就是这么简单。
  心情 = 0 → 心死。人还活着，但已经什么都不在乎了。
哪一个先到 0，你就完了——字面意思的完蛋。
```

**设计决策：** 0 的后果写狠，但"什么时候该补"交给 NPC 性格判断，不做硬编码阈值。保留世界观调味句"猎魔人的世界不相信眼泪"。

#### 2. 死代码清理

| 文件 | 改动 |
|------|------|
| `cognition/npc_prompt_builder.py` | 整个文件替换为注释（`build_one_npc_prompt()` 和 `build_one_fallback_prompt()` 是死代码，slot 系统已完全取代）|
| `cognition/__init__.py` | 移除 `from .npc_prompt_builder import build_one_npc_prompt, build_one_fallback_prompt` 和对应的 `__all__` 条目 |

**删除函数：**
- `build_one_npc_prompt()` — 旧计划生成函数，被 slot 系统 (`prompt_assembler.assemble("llm1_plan", ...)`) 取代
- `build_one_fallback_prompt()` — 旧兜底函数

---

## Recent Info 滚动窗口

**日期:** 2026-05-05（当前 session，最新改动）

### 动机

旧机制：每次 tick 覆盖式写入 `ent.recent_info`，NPC 只有上一 tick 的一句话记忆，没有时间连续性。

新机制：保留最近 3 tick 的 recent_info，带时间戳，形成滚动窗口。

### 改动

#### 1. 写路径 — `pipeline_orchestrator.py`

**改前：** `ent.recent_info = txt`（每次覆盖）  
**改后：** 解析现有 JSON 列表，新条目 prepend，最多留 3 条：

```python
history = []
if ent.recent_info:
    try: history = json.loads(ent.recent_info)
    except: history = []
history.insert(0, {"t": ctx.world_time_str, "text": txt})
history = history[:3]
ent.recent_info = json.dumps(history, ensure_ascii=False)
```

#### 2. 读路径 — `domain/npc_world/adapter.py` → `slot_recent_info()`

**改前：** `template.format(info=entity.recent_info)`（直接塞字符串）  
**改后：** 解析 JSON 数组，逐条加时间戳前缀，拼接多行文本

```python
lines = []
for entry in history:
    t = entry.get("t", "")
    txt = entry.get("text", "")
    lines.append(f"  [{t}] {txt}")
info = "\n".join(lines)
```

#### 3. 模板 — `config/domain.json` → `recent_info_default`

**改前：** `"### 近况\n  {info}\n\n"`  
**改后：** `"### 近况（过去 3 个时间段）\n{info}\n\n"`

#### 4. 初始值 — `models/npc_defaults.py`

**改前：** `"_recent_info": d.get("recent_info", "")`  
**改后：** `"_recent_info": d.get("recent_info", "[]")`（确保新 NPC 初始化时是合法 JSON）

### 效果

LLM #1 在下一 tick 看到的近况（示例）：

```
### 近况（过去 3 个时间段）
  [11:30] 在瞭望台蹲守水鬼守了半小时，剑柄握得发亮
  [11:00] 吃了干粮继续警戒，体力开始下降
  [10:30] 刚到瞭望台接班，警惕水鬼出没
```

---

## 文件变更索引

### Phase II + III（committed, 16 文件 +232/-419 行）

| 文件 | 类型 | 关键改动 |
|------|------|---------|
| `refactor/STATE.md` | 文档 | 同步状态 |
| `api/agent.py` | 导入清理 | 移除废弃模块引用 |
| `api/npc.py` | 导入清理 | 移除废弃模块引用 |
| `cognition/npc_prompt_builder.py` | 精简 | 函数移除 + 注释化 |
| `config/config_loader.py` | **新增** | resolve_eid(), has_role(), has_recent_info() |
| `db/schemas.py` | 适配 | EID 前缀处理更新 |
| `domain/npc_world/adapter.py` | slot 化 | slot 函数统一走 _adapter_data.get() |
| `models/npc.py` | 数据驱动 | NPCRole 枚举 → role: str |
| `models/npc_defaults.py` | 精简 | 移除枚举，简化工厂函数 |
| `services/graph_adapter.py` | 通用化 | create_node() / ensure_node() |
| `services/graph_engine.py` | 精简 | _is_item_type() 移除 |
| `services/graph_npc_engine.py` | **大幅精简** | 280→1 行，BFS 子图 |
| `services/pipeline_orchestrator.py` | **核心新增** | Orchestrator 容器 + TickContext |
| `services/post_processor.py` | 精简 | 参数简化 |
| `services/verification_registry.py` | 适配 | EID 前缀治理 |
| `services/world_updater.py` | 适配 | has_type_prefix() 替换 |

### Phase IV + Recent Info（uncommitted, 7 文件 +78/-217 行）

| 文件 | 改动 |
|------|------|
| `config/domain.json` | survival_needs 槽位 + recent_info_default 模板 |
| `cognition/__init__.py` | 移除死代码 import |
| `cognition/npc_prompt_builder.py` | 整文件注释化 |
| `domain/npc_world/adapter.py` | slot_recent_info() 滚动窗口读取 |
| `models/npc_defaults.py` | _recent_info 初始值 "[]" |
| `services/pipeline_orchestrator.py` | recent_info 写入改为滚动队列 |
| `refactor/STATE.md` | 同步状态 |
