"""
DomainAdapter — 通用域适配器抽象接口。

设计原则：
- 抽象层只提供极简的通用骨肉（节点分类位集、键值配置、阶段声明、校验链注册）
- 域在实现时填充自己的语义
- 换域只需写一个新 Adapter 类

NodeClassification 用布尔旗替代枚举，让每个域自由组合：
  is_actor      = 能主动产生操作？（NPC 域：是；蛋白域：否）
  is_container  = 能装东西？（NPC 域：NPC+区域可以；蛋白域：细胞器可以）
  is_consumable = 能被消耗？（NPC 域：物品可以；蛋白域：底物可以）
  is_location   = 是空间位置？（NPC 域：区域可以；蛋白域：细胞器可以）
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

# ═══════════════════════════════════════════════
# 跨域通用类型
# ═══════════════════════════════════════════════

GraphOp = dict[str, Any]
StateChange = dict[str, Any]


class StageOutputType(Enum):
    """管线阶段输出类型——告诉引擎如何处理 LLM 返回结果。"""
    RAW_TEXT = "raw"              # 原始文本，不解析
    GRAPH_OPS = "graph_ops"        # list[GraphOp]（拓扑操作）
    PLANS_MAP = "plans_map"        # dict[str, str]（NPC 计划映射）
    NARRATIVES = "narratives"      # list[str]（故事文本）
    ATTR_UPDATE = "attr_update"    # StateChange + recent_info 复合
    VERIFY_RESULT = "verify"       # 校验结果
    INTENT_EXEC = "intent_exec"     # 非 LLM 意图执行阶段


class NodeRole(Enum):
    """已废弃，仅用于桥接兼容。新代码用 NodeClassification。"""
    ACTOR = "actor"
    LOCATION = "location"
    RESOURCE = "resource"


@dataclass
class NodeClassification:
    """图引擎需要的粗分类——全部可选布尔旗。"""
    is_actor: bool = False
    is_container: bool = False
    is_consumable: bool = False
    is_location: bool = False


@dataclass
class NodeDescriptor:
    """节点语义描述，供 LLM prompt 使用。"""
    display_name: str = ""
    type_label: str = ""
    attrs: dict = field(default_factory=dict)
    role: NodeRole = NodeRole.ACTOR
    entity_id: str = ""


@dataclass
class SlotDef:
    """Prompt 模板中的槽定义。
    
    name: slot 名称，对应 adapter.render_slot() 的 slot_name
    provider: 值提供者
      - "content"  → adapter.render_slot()
      - "topology" → 引擎 _render_topo_slot()
      - "runtime"  → 运行时渲染
    """
    name: str
    provider: str = "content"  # "content" | "topology" | "runtime"


@dataclass
class RetryPolicy:
    """阶段重试策略。"""
    max_attempts: int = 1           # 最多尝试次数（含首次）
    adaptive: bool = False           # 超时是否自动降 max_tokens
    degrade_on_fail: bool = True     # 失败后是否跳过卡死的操作


@dataclass
class PipelineStage:
    """一个管线阶段的自声明。
    
    可以是 LLM 阶段或非 LLM 阶段（INTENT_EXEC）。
    非 LLM 阶段不需要 prompt_template 和 parser。
    """
    key: str
    label: str
    output_type: StageOutputType = StageOutputType.GRAPH_OPS
    prompt_template: str = ""       # LLM 阶段对应 get_prompt_template(name)
    parser: Callable = lambda r, g: []  # (raw_text, graph) → 按 output_type 返回

    # 新增：adapter 提供执行逻辑
    execute: Callable = lambda ctx, comp, graph, resolver, **kw: None
    validate: Callable | None = None    # (result, comp) → list[Failure]
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass
class StageResult:
    """阶段执行结果的通用容器。管线不读 data 内部。"""
    key: str
    data: Any = None
    raw_llm: str = ""


@dataclass
class GraphValidator:
    """操作校验器。"""
    name: str
    check: Callable  # (list[GraphOp], GraphEngine) → list[GraphOp]


# ═══════════════════════════════════════════════
# 抽象接口
# ═══════════════════════════════════════════════

class DomainAdapter(ABC):
    """
    通用域适配器——抽象接口只包含极简通用骨肉。

    换域步骤：
      1. 继承此类实现所有抽象方法
      2. 注册你的类型系统（classify_node）
      3. 声明你的管线（get_pipeline_stages）
      4. 提供你的模板（get_prompt_template, render_slot）
      5. 注册你的校验器（get_validators）
    """

    @property
    @abstractmethod
    def domain_name(self) -> str:
        """域标识，如 \"npc_world\", \"protein_network\"."""
        ...

    # ─── 实体系统 ───

    @abstractmethod
    def classify_node(self, entity_id: str, graph) -> NodeClassification:
        """返回节点的分类位集。GraphEngine 用此判断连接/流转合法性。"""
        ...

    @abstractmethod
    def describe_node(self, entity_id: str, graph) -> NodeDescriptor:
        """返回节点的完整语义描述。LLM prompt 用此格式化节点信息。"""
        ...

    # ─── 域配置 ───

    @abstractmethod
    def get_config(self, key: str, default=None) -> Any:
        """
        取域专属配置。每个域定义自己的 key 空间。

        NPC 域：key="zones" → [zone_dict, ...],
                key="recipes" → [recipe_dict, ...]
        蛋白域：key="enzymes" → [enzyme_dict, ...],
                key="pathways" → [pathway_dict, ...]
        """
        ...

    # ─── LLM 管线自声明 ───

    @abstractmethod
    def get_pipeline_stages(self) -> list[PipelineStage]:
        """
        域定义自己的 LLM 管线阶段。
        管线引擎按此列表顺序执行。
        stage.execute / validate / retry_policy 由域填充。
        """
        ...

    @abstractmethod
    def get_prompt_template(self, name: str) -> list[SlotDef]:
        """
        返回命名模板的 slot 定义列表。
        PipelineStage.prompt_template 引用此处的 name。
        """
        ...

    # ─── Slot 渲染 ───

    @abstractmethod
    def render_slot(self, slot_name: str, **kw) -> str:
        """给定 slot 名和上下文，返回渲染文本。"""
        ...

    # ─── 校验器 ───

    @abstractmethod
    def get_validators(self) -> list[GraphValidator]:
        """返回域的所有校验器。管线引擎按需调用。"""
        ...

    # ═══════════════════════════════════════════════
    # 新增抽象方法（域净化重构 v2）
    # ═══════════════════════════════════════════════

    @abstractmethod
    def build_entity_context(self, entity_id: str, graph, **extras) -> dict:
        """
        为实体构建执行上下文（opaque dict）。
        管线只读 _ 前缀的隐式契约 key，不读域私有 key。
        
        隐式契约（必须包含）:
          _entity_name, _entity_id, _location, _location_changed,
          _edge_type, _interacted_entities
        """
        ...

    @abstractmethod
    def extract_location(self, entity_id: str, graph) -> str:
        """返回实体的位置名称。域定义如何找位置。"""
        ...

    @abstractmethod
    def resolve_entity_id(self, name: str, type_hint: str = "") -> str:
        """将名称解析为实体 ID。域定义前缀/哈希规则。"""
        ...

    @abstractmethod
    def format_attribute(self, key: str, value: Any) -> str:
        """格式化单个属性为可读文本。域定义数值→文本映射。"""
        ...

    @abstractmethod
    def parse_llm_output(self, stage_key: str, raw: str,
                          label_map: dict | None,
                          graph) -> Any:
        """解析 LLM 原始输出为阶段对应类型。"""
        ...

    @abstractmethod
    def normalize_name(self, raw: str) -> str:
        """规范化实体名称（去前缀、去花括号等）。"""
        ...

    @abstractmethod
    def extract_op_references(self, op: dict) -> list[str]:
        """从操作中提取所有引用的实体名称（用于校验白名单检查）。"""
        ...

    @abstractmethod
    def get_entity_tags(self, eid: str, graph) -> list[str]:
        """返回实体标签列表（如 conserved / terminal），用于 LLM 标注。"""
        ...

    @abstractmethod
    def get_names_by_classification(self, classification: str, graph) -> set[str]:
        """
        按分类名返回实体名称集合。
        classification 值由域定义：NPC 域有 "region"/"thing"/"actor"。
        """
        ...

    @abstractmethod
    def merge_results(self, component_results: list) -> dict:
        """归并多个分量结果为单个上下文。"""
        ...

    # ═══════════════════════════════════════════════
    # Bridge 方法（已废弃，仅用于 Step 1-2 兼容）
    # 新代码不要调用。Phase II 删除。
    # ═══════════════════════════════════════════════

    def get_node_role(self, entity_id: str, graph) -> NodeRole:
        """废弃。用 classify_node() 替代。"""
        nc = self.classify_node(entity_id, graph)
        if nc.is_location:
            return NodeRole.LOCATION
        if nc.is_consumable:
            return NodeRole.RESOURCE
        return NodeRole.ACTOR

    def get_node_descriptor(self, entity_id: str, graph) -> NodeDescriptor:
        """废弃。用 describe_node() 替代。"""
        return self.describe_node(entity_id, graph)

    def get_zones(self) -> list[dict]:
        """废弃。用 get_config(\"zones\") 替代。"""
        return self.get_config("zones", [])

    def get_recipes(self) -> list[dict]:
        """废弃。用 get_config(\"recipes\") 替代。"""
        return self.get_config("recipes", [])

    def get_npc_initial_zones(self) -> dict[str, str]:
        """废弃。用 get_config(\"initial_zones\") 替代。"""
        return self.get_config("initial_zones", {})

    def get_all_entity_names(self) -> set[str]:
        """废弃。用 get_config(\"entity_names\") 替代。"""
        return self.get_config("entity_names", set())
