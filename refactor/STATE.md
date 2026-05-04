# State: Step 0/6 ✅ 完成 — 接口文件 + 目录结构

## 输出产物
- ✅ `src/agent_world/domain/adapter.py` — DomainAdapter 接口
  - NodeRole 枚举（ACTOR / RESOURCE / LOCATION / RELATION）
  - OpType 枚举（CONNECT / DISCONNECT / SET_QTY / DELTA / SYSTEM_DELTA / ATTR）
  - GraphOp 数据类 + StateChange / NodeDescriptor
  - DomainAdapter 抽象类（8 个抽象方法 + 2 个 property）
- ✅ `src/agent_world/domain/__init__.py`
- ✅ `refactor/STATE.md`

## 下一步：Step 1 — prompt 模板抽取
将 prompt_assembler.py 中的域特定 prompt 模板移到 NPCWorldAdapter。

目标文件：
- `src/agent_world/domain/npc_world/adapter.py`（新建）
- `src/agent_world/domain/npc_world/prompts.py`（新建）
- `src/agent_world/domain/npc_world/validators.py`（新建）

改造策略：
  prompt_assembler.py 的 LLM #1 和 LLM #3 prompt 模板 → NPCWorldAdapter.build_prompt()
  → 跑 run_1tick.py 验证行为一致

Tag: refactor/step-0-done
