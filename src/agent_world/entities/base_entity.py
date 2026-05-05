"""
基础实体 —— 节点（无接口，只有属性、连接、自我描述）

Entity 是图中的一个节点，携带：
- 拓扑身份：type_id（数字，引擎只读这个）
- 自我描述：type/role/attrs/traits/desc（内容层，LLM 消费）
- 连接的节点列表
- 属性字典

设计原则：
  拓扑引擎对语义完全透明，所有遍历行为由 node_ontology 配置驱动。
  节点自我描述，边只提供结构（谁连谁 + 数量）。
"""

from __future__ import annotations
import logging
from typing import Any

from agent_world.config.node_ontology import (
    prefix_to_type_id_wrap as _prefix_to_tid,
    is_terminal,
    is_same_type_blocked as _same_type_blocked,
)
from agent_world.config.config_loader import get_type_def as _get_type_def

logger = logging.getLogger(__name__)


# ─── 自描述节点 ───

class Entity:
    """
    世界中的一个自描述节点。

    节点自己携带完整的语义描述（角色、属性、性格、记忆）。
    LLM 从节点的信息推断边的语义。

    拓扑与内容解耦：
      - type_id（数字）：拓扑引擎用于 BFS 遍历
      - entity_type（字符串）：内容层，向 LLM 描述类型
      - name（字符串）：内容层，LLM 可读名称
    """

    def __init__(self, entity_id: str, name: str, entity_type: str = ""):
        self.entity_id = entity_id
        self.name = name

        # 拓扑身份：从 ID 前缀推断 type_id，引擎只读这个
        self.type_id: int = _prefix_to_tid(entity_id)

        # entity_type 仅供 LLM 提示词显示，引擎不依赖此字段
        # 如果未传入，从 config 自动补全
        if not entity_type and self.type_id:
            tdef = _get_type_def(self.type_id)
            entity_type = tdef.get("id", "") if tdef else ""
        self.entity_type = entity_type

        self.attributes: dict[str, Any] = {}  # 基础属性（vitality, satiety, mood...）
        self.connected_entity_ids: set[str] = set()  # 连接的节点 ID 集合
        self.recent_info: str = ""  # 近况投影（LLM #4b 写入，类型无关）
        self.conserved: bool = False  # 是否为守恒量（度守恒校验用）

        # 半结构化自我描述
        self.role: str = ""       # 角色（用于 NPC/Entity）
        self.traits: list[str] = []  # 性格特质列表
        self.desc: str = ""       # 自由格式描述

    @property
    def is_leaf(self) -> bool:
        """拓扑叶子节点标志：BFS 到此停止。由 node_ontology 配置决定。"""
        return is_terminal(self.type_id)

    @property
    def no_same_type(self) -> bool:
        """同类型阻断标志：BFS 不跨同类型节点。由 node_ontology 配置决定。"""
        return _same_type_blocked(self.type_id)

    def connect_to(self, entity_id: str):
        """添加有向连接"""
        self.connected_entity_ids.add(entity_id)

    def disconnect_from(self, entity_id: str):
        """移除有向连接"""
        self.connected_entity_ids.discard(entity_id)

    def is_connected_to(self, entity_id: str) -> bool:
        return entity_id in self.connected_entity_ids

    def get_subgraph(self) -> set[str]:
        """获取 1-hop 邻居集合（包括自己）"""
        result = {self.entity_id}
        result.update(self.connected_entity_ids)
        return result

    # ─── 自我描述（供 LLM 使用） ───

    def to_prompt_block(self) -> str:
        """
        生成 LLM 可读的自我描述块。

        格式：
          {name}（{type}） | 角色:{role}
          描述：{desc}
          属性：{vitality}/100, {satiety}/100...
          性格：{traits}
          持有物品：{items}
        """
        lines = [f"{self.name}（{self.entity_type}）"]

        if self.role:
            lines.append(f"  角色：{self.role}")

        if self.desc:
            lines.append(f"  描述：{self.desc}")

        if self.traits:
            lines.append(f"  性格：{'、'.join(self.traits)}")

        # 属性摘要
        attr_parts = []
        for key, val in self.attributes.items():
            if val is not None:
                attr_parts.append(f"{key}={val}")
        if attr_parts:
            lines.append(f"  属性：{' '.join(attr_parts)}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type,
            "role": self.role,
            "traits": list(self.traits),
            "desc": self.desc,
            "attributes": dict(self.attributes),
            "connected_entity_ids": sorted(self.connected_entity_ids),
            "conserved": self.conserved,
        }

    def __repr__(self) -> str:
        return f"Entity({self.entity_id}, {self.name}, {self.entity_type})"
