"""
DomainAdapter — 域适配器接口（抽象层）

将图引擎（通用）与 LLM 管线（通用）之间的域特定知识封装在此协议中。
每个世界观实现一个 DomainAdapter 子类：

    NPCWorldAdapter    → 猎人魔女/奇幻 NPC 世界
    ProteinAdapter     → 蛋白质互作网络
    SocialAdapter      → 社交关系网络

核心原则：
  1. 管线层不知道任何域概念（NPC/Zone/Item），只调用 adapter 方法
  2. 每个 adapter 自己决定 prompt 模板、解析格式、校验规则
  3. 换世界观 = 换 adapter class，零图引擎修改
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


# ═══════════════════════════════════════════════════
# 1. 节点语义角色（域无关）
# ═══════════════════════════════════════════════════

class NodeRole(Enum):
    """节点的域无关语义角色"""
    ACTOR     = auto()  # 能发出意图、采取行动的实体（NPC、蛋白质？）
    RESOURCE  = auto()  # 被持有/消耗的资源（物品、货币、ATP）
    LOCATION  = auto()  # 容纳其他节点的容器（区域、细胞器）
    RELATION  = auto()  # 标记两个节点之间特定关系的标记节点


# ═══════════════════════════════════════════════════
# 2. 统一图操作（域无关）
# ═══════════════════════════════════════════════════

class OpType(Enum):
    CONNECT      = auto()  # 建立边
    DISCONNECT   = auto()  # 移除边
    SET_QTY      = auto()  # 设置边的数量
    DELTA        = auto()  # 边的数量增减（守恒对 Σ=0）
    SYSTEM_DELTA = auto()  # 与系统外部的物品转移（跳过守恒）
    ATTR         = auto()  # 节点属性变更


@dataclass
class GraphOp:
    """域无关的统一图操作"""
    op: OpType                          # 操作类型
    src: str                            # 源节点 entity_id
    tgt: str                            # 目标节点 entity_id
    qty: float                          # 数量/变化量
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata 承载域特定信息，如：
    #   {"group": "g1"}        — NPC 交易分组
    #   {"binding": 0.8}       — 蛋白质结合亲和力
    #   {"reason": "吃饭了"}   — 属性变更原因


@dataclass
class StateChange:
    """状态变更日志（apply_state_updates 的返回值）"""
    entity_id: str
    attr: str
    old_value: Any
    new_value: Any
    reason: str = ""


# ═══════════════════════════════════════════════════
# 3. NodeDescriptor — 节点展示描述
# ═══════════════════════════════════════════════════

@dataclass
class NodeDescriptor:
    """节点在 LLM prompt 中的结构化描述"""
    display_name: str                      # 展示名（如"杰洛特"、"Ras"）
    type_label: str                        # 类型标签（如"猎魔人"、"GTPase"）
    attrs: dict[str, Any] = field(default_factory=dict)   # 属性（活力/饱腹等）
    traits: list[str] = field(default_factory=list)       # 特质标签
    role: NodeRole = NodeRole.ACTOR        # 语义角色
    entity_id: str = ""                    # 图节点 ID


# ═══════════════════════════════════════════════════
# 4. DomainAdapter — 抽象接口
# ═══════════════════════════════════════════════════

class DomainAdapter(ABC):
    """域适配器基类 — 一个世界观的全部领域知识"""

    # ── 元数据 ────────────────────────────────────

    @property
    @abstractmethod
    def domain_name(self) -> str:
        """域名称，用于日志和调试"""
        ...

    # ── 实体系统 ───────────────────────────────────

    @abstractmethod
    def get_node_role(self, entity_id: str, graph: "GraphEngine") -> NodeRole:
        """返回节点的语义角色"""
        ...

    @abstractmethod
    def get_node_descriptor(
        self, entity_id: str, graph: "GraphEngine"
    ) -> NodeDescriptor:
        """返回节点的结构化描述（给 LLM 看）"""
        ...

    # ── Prompt 构建 ───────────────────────────────

    @abstractmethod
    def build_prompt(
        self,
        stage: int,
        context: Any,
        graph: "GraphEngine",
        label_map: Optional[dict[str, str]] = None,
        **kw: Any,
    ) -> str:
        """
        构建指定 LLM 阶段的 prompt。

        Stages:
          1 — 意图生成：为每个 ACTOR 生成"下一步计划"
          2 — 结构变更：计划 → 拓扑连接/断开操作
          3 — 叙事生成：变更后的子图 → 故事文本
          4a— 数量变更：故事+拓扑 → 物品/资源增减
          4b— 属性变更：故事+拓扑 → 节点属性变化
          5 — 校验验证：操作集 → 合规性校验

        Parameters
        ----------
        stage : int
            LLM 阶段编号 (1-5)
        context : Any
            阶段特定的上下文数据
            stage 1 → entity_id  (当前 ACTOR 的 ID)
            stage 2 → plans: dict[str, str]  (所有 ACTOR 的计划文本)
            stage 3 → component: Component  (子图组件)
            stage 4 → stories: list[Story]  (所有故事)
            stage 5 → ops: list[GraphOp]    (待校验的操作集)
        graph : GraphEngine
            当前图状态
        label_map : dict[str, str] | None
            标签 ↔ entity_id 映射（stage 2 必需）
        **kw
            域特定扩展参数

        Returns
        -------
        str
            LLM 输入 prompt
        """
        ...

    # ── LLM 输出解析 ─────────────────────────────

    @abstractmethod
    def parse_llm_output(
        self,
        stage: int,
        raw_text: str,
        label_map: Optional[dict[str, str]],
        graph: "GraphEngine",
    ) -> list[GraphOp]:
        """
        将 LLM 原始输出解析为统一 GraphOp 列表。

        Parameters
        ----------
        stage : int
            LLM 阶段编号
        raw_text : str
            LLM 返回的原始文本
        label_map : dict[str, str] | None
            标签 ↔ entity_id 映射（stage 2 必需）
        graph : GraphEngine
            当前图状态

        Returns
        -------
        list[GraphOp]
            解析后的操作列表

        Notes
        -----
        解析失败时应返回空列表（不抛异常），
        让校验层做最终决定，而非卡在解析层抛错。
        """
        ...

    # ── 操作校验 ───────────────────────────────────

    @abstractmethod
    def validate_ops(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[GraphOp]:
        """
        域特定的操作校验 + 修正。

        只返回通过校验的 ops（失败的静默过滤或修正）。

        NPC 世界示例：
          - 检查 delta 是否守恒（Σ=0）
          - 检查资源是否充足（src 持有量 ≥ 流出量）
          - 限制系统 delta 使用场景

        Parameters
        ----------
        ops : list[GraphOp]
            待校验的操作列表
        graph : GraphEngine
            当前图状态

        Returns
        -------
        list[GraphOp]
            通过校验的操作列表
        """
        ...

    # ── 状态更新 ───────────────────────────────────

    @abstractmethod
    def apply_state_updates(
        self, ops: list[GraphOp], graph: "GraphEngine"
    ) -> list[StateChange]:
        """
        将校验通过的 ops 应用到节点属性上。

        NPC 世界示例：
          OpType.ATTR → 修改 vitality/satiety/mood

        返回变更日志用于叙事生成和校验。

        Parameters
        ----------
        ops : list[GraphOp]
            已通过校验的操作列表
        graph : GraphEngine
            当前图状态

        Returns
        -------
        list[StateChange]
            属性变更日志
        """
        ...

    # ── 领域规则 ───────────────────────────────────

    @property
    @abstractmethod
    def interaction_rules(self) -> dict[str, Any]:
        """
        域特定交互规则配置。

        NPC 世界示例：
        {
            "allowed_actor_actor": True,       # NPC 之间可交互
            "allowed_actor_resource": True,     # NPC 可持有资源
            "allowed_actor_location": True,     # NPC 可位于区域
            "allowed_actor_relation": True,     # NPC 可建立关系
            "chase_distance": 1,               # 追人 BFS 深度
        }

        Returns
        -------
        dict
            交互规则配置
        """
        ...

    @property
    @abstractmethod
    def conservation_rules(self) -> dict[str, Any]:
        """
        守恒规则配置。

        NPC 世界示例：
        {
            "enforce_delta_conservation": True,   # delta 必须 Σ=0
            "system_delta_allowed": True,         # 允许系统边界转移
            "check_resource_sufficiency": True,   # 检查资源是否充足
        }

        Returns
        -------
        dict
            守恒规则配置
        """
        ...


# ═══════════════════════════════════════════════════
# 5. 类型别名（延迟导入避免循环引用）
# ═══════════════════════════════════════════════════

# GraphEngine 在 adapter.py 中仅作为类型标注，
# 实际在子类中通过 `from ..engine.graph_engine import GraphEngine` 导入。
# 这里不 import，避免循环依赖。
