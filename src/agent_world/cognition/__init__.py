"""
Cognition Module - NPC 认知与推理引擎

Purpose-driven behavior:
  Persona Tags + Memory + Recent Context + Knowledge → Goal → Action

Files:
  - persona.py    : Persona Tags 管理（可扩展，支持自动生成）
  - memory.py     : 记忆管理（追加/检索/摘要）
  - context.py    : 上下文构建（给 LLM 的输入）
  - reasoner.py   : LLM 推理 + Goal 输出
  - fallbacks.py  : 规则引擎兜底
"""

from .persona import PersonaTags
from .memory import MemoryStore, MemoryEntry
from .context import ContextBuilder
from .reasoner import GoalReasoner, GoalOutput
from .common_knowledge import COMMON_KNOWLEDGE, WORK_SCHEDULE, ROLE_BEHAVIORS, WORLD_PHYSICS, GOAL_TYPES

__all__ = [
    "PersonaTags",
    "MemoryStore",
    "MemoryEntry",
    "ContextBuilder",
    "GoalReasoner",
    "GoalOutput",

    "COMMON_KNOWLEDGE",
    "WORK_SCHEDULE",
    "ROLE_BEHAVIORS",
    "WORLD_PHYSICS",
    "GOAL_TYPES",
]