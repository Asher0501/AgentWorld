# noqa: D104
"""
Interaction Layer —— 边级交互结果处理器（LLM 驱动）

功能：
  1. 收集所有 NPC 的 ExecutionResult，按边去重
  2. 按连通子图（BFS）分组，每子图 = 一个场景
  3. 按子图调用 LLM 生成自然语言故事
  4. 输出给 PostProcessor 做集中式批处理

设计要点：
  - 拓扑由 graph_engine 提供，BFS 读取 NODE_ONTOLOGY 决定遍历规则
  - 引擎对节点语义完全透明——prompt 只列节点自有属性，不预先分类
  - 每连通子图独立调 LLM，无需解析输出做路由

避免分布式更新不一致：
  - 老张↔王老板的 trade 只作为一条边出现一次
  - PostProcessor 拿到所有边的故事描述后，一次产出全部更新
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config.config_loader import has_role
from .prompt_assembler import assemble

logger = logging.getLogger("interaction_layer")


# ─── 边级结果 ───

@dataclass
class EdgeResult:
    """一条交互边的结果（供 PostProcessor 消费）"""
    source: str
    target: str
    edge_type: str          # "npc_npc" | "npc_object" | "npc_zone"
    zone: str
    description: str        # LLM 生成的自然语言故事描述
    success: bool
    chase: bool = False
    stayed: bool = False        # True=驻足停留（没移动+没交互）
    obj_order: int = 0
    source_importance: float = 0.0
    target_importance: float = 0.0

    @property
    def label(self) -> str:
        return f"【{self.source}↔{self.target}】"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "zone": self.zone,
            "description": self.description,
            "success": self.success,
            "chase": self.chase,
            "stayed": self.stayed,
        }


# ─── 连通子图 ───

@dataclass
class Component:
    """一个连通子图——对应一个场景。

    由 BFS 从 NPC 节点求出，不包含语义分类。
    子图内的所有节点（NPC、zone、item、object）统一存放在 entity_ids 中。
    edges 是归属于该子图的 EdgeResult。
    """
    entity_ids: set[str]           # 子图中所有实体的 ID（所有类型）
    npc_names: set[str]            # 子图中 NPC 的名称（用于匹配 EdgeResult）
    edges: list[EdgeResult]        # 本子图包含的边


# ─── InteractionLayer ───

class InteractionLayer:
    """
    输入：list[ExecutionResult.to_dict()]
    输出：list[EdgeResult]（去重后的边级结果，description 为 LLM 生成的故事）
    """

    def __init__(self, resolver=None, adapter=None, engine=None):
        self._resolver = resolver
        self._adapter = adapter  # VillageDomainAdapter
        self._engine = engine  # PipelineEngine（统一 LLM 入口）

    async def process(self, exec_results: list[dict], graph_engine=None,
                       world_time_str: str | None = None,
                       tick_duration_str: str | None = None) -> list[EdgeResult]:
        """
        输入：list[ExecutionResult.to_dict()]
        输出：list[EdgeResult]（去重后的边级结果，description 为 LLM 生成的故事）
        """
        if not exec_results:
            return []

        edges = self._build_edges(exec_results)
        unique = self._deduplicate(edges)

        if self._resolver and unique:
            # 按连通子图分组——每子图 = 一个场景
            components = self._group_by_subgraph(unique, exec_results, graph_engine)
            # 逐子图调 LLM：每子图生成一段故事，直接分配
            for comp in components:
                prompt = self._build_story_prompt(
                    comp, exec_results, graph_engine,
                    world_time_str=world_time_str,
                    tick_duration_str=tick_duration_str,
                )
                if self._engine:
                    story = await self._engine.call_llm_async(prompt, "narrative") or ""
                else:
                    import asyncio
                    story = await asyncio.to_thread(self._resolver._call_llm, prompt) or ""
                for e in comp.edges:
                    e.description = story
        else:
            # 降级：模板描述
            self._fallback_describe(unique, exec_results)

        return unique

    # ───── 公共方法 ─────

    def _build_edges(self, exec_results: list[dict]) -> list[EdgeResult]:
        """从所有执行结果中提取边"""
        results = []

        # 预扫描：收集所有被其他 NPC 交互的目标 NPC
        # 避免 A 主动找 B 的同时，B 又被生成独自休息的矛盾故事
        targeted_npcs = set()
        for er in exec_results:
            for target_name in er.get("interacted_npcs", []):
                targeted_npcs.add(target_name)

        for er in exec_results:
            src = er["npc_name"]
            zone_after = er.get("zone_after", "?")
            zone_before = er.get("zone_before", "?")
            zone_changed = er.get("zone_changed", False)

            # 区域移动边（或驻足停留边）
            if zone_changed:
                results.append(EdgeResult(
                    source=src,
                    target=zone_after,
                    edge_type="npc_zone",
                    zone=zone_after,
                    description="",
                    success=True,
                ))
            elif not er.get("interacted_npcs") and not er.get("interacted_objects") and not er.get("unreachable_targets"):
                # 没移动+没交互→生成驻足停留边，让 LLM #3 有故事可写
                # 但前提是没人主动来找该 NPC，否则会写出
                # "田嫂来找铁匠王" 同时 "铁匠王独自在market" 的矛盾故事
                if src not in targeted_npcs:
                    results.append(EdgeResult(
                        source=src,
                        target=zone_after,
                        edge_type="npc_zone",
                        zone=zone_after,
                        description="",
                        success=True,
                        stayed=True,
                    ))

            # NPC 交互边
            for target_name in er.get("interacted_npcs", []):
                results.append(EdgeResult(
                    source=src,
                    target=target_name,
                    edge_type="npc_npc",
                    zone=zone_after,
                    description="",
                    success=True,
                ))

            # 物体交互边
            for obj_name in er.get("interacted_objects", []):
                results.append(EdgeResult(
                    source=src,
                    target=obj_name,
                    edge_type="npc_object",
                    zone=zone_after,
                    description="",
                    success=True,
                ))

            # 不可达目标
            for target_name in er.get("unreachable_targets", []):
                chase = zone_changed
                results.append(EdgeResult(
                    source=src,
                    target=target_name,
                    edge_type="npc_npc",
                    zone=zone_after,
                    description="",
                    success=False,
                    chase=chase,
                ))

        return results

    def _deduplicate(self, edges: list[EdgeResult]) -> list[EdgeResult]:
        """
        去重：老张↔王老板 只保留一条。
        双向边（A↔B + B↔A）合并为 A↔B，success 取或。
        """
        sig_map: dict[str, EdgeResult] = {}

        def sig(a: str, b: str) -> str:
            return f"{min(a,b)}↔{max(a,b)}"

        for e in edges:
            if e.edge_type != "npc_npc":
                sig_map[f"{e.edge_type}:{e.source}→{e.target}"] = e
                continue
            key = sig(e.source, e.target)
            existing = sig_map.get(key)
            if not existing:
                sig_map[key] = e
            elif existing.source != e.source:
                # 双向边合并
                existing.success = existing.success or e.success

        return list(sig_map.values())

    def _group_by_subgraph(self, edges: list[EdgeResult], exec_results: list[dict],
                           graph_engine=None) -> list[Component]:
        """按连通子图分组（BFS）。

        从每个 NPC 节点出发 BFS，遇到 is_leaf / no_same_type 节点停止扩展。
        同次 BFS 中的全部节点构成一个 Component。
        """
        if not graph_engine:
            return self._group_by_zone_only(edges, exec_results)

        # 1. 收集 NPC 信息
        npc_eids = [er["npc_eid"] for er in exec_results if "npc_eid" in er]
        npc_name_to_eid = {
            er["npc_name"]: er["npc_eid"]
            for er in exec_results if "npc_name" in er and "npc_eid" in er
        }
        eid_to_name = {v: k for k, v in npc_name_to_eid.items()}

        # 2. BFS 遍历每个 NPC 的连通分量
        visited: set[str] = set()
        components: list[Component] = []

        for start_eid in npc_eids:
            if start_eid in visited:
                continue

            component_entity_ids: set[str] = set()
            component_npc_names: set[str] = set()
            queue = [start_eid]

            while queue:
                eid = queue.pop(0)
                if eid in visited:
                    continue
                visited.add(eid)
                component_entity_ids.add(eid)

                ent = graph_engine.get_entity(eid)
                if not ent:
                    continue

                if has_role(ent.type_id, "actor"):
                    npc_name = eid_to_name.get(eid, ent.name)
                    component_npc_names.add(npc_name)

                # 叶子节点：记录但不扩展
                if ent.is_leaf:
                    continue

                # 非叶子：继续扩展（遇同类型邻居才阻断）
                for conn_id in ent.connected_entity_ids:
                    if conn_id in visited:
                        continue
                    conn_ent = graph_engine.get_entity(conn_id)
                    if conn_ent and conn_ent.no_same_type and conn_ent.type_id == ent.type_id:
                        # 同类型阻断：比如 zone→zone 不穿透
                        continue
                    queue.append(conn_id)

            if component_npc_names:
                # 将 edges 分配到对应组件
                comp_edges = [e for e in edges if e.source in component_npc_names]
                components.append(Component(
                    entity_ids=component_entity_ids,
                    npc_names=component_npc_names,
                    edges=comp_edges,
                ))

        return components

    def _group_by_zone_only(self, edges: list[EdgeResult],
                            exec_results: list[dict]) -> list[Component]:
        """无 graph_engine 兜底：按 zone name 分组构造单组件。"""
        npc_zone: dict[str, str] = {}
        for er in exec_results:
            name = er["npc_name"]
            zone = er.get("zone_after", er.get("zone_before", "?"))
            npc_zone[name] = zone

        # 归入同一个假组件
        all_npcs = set(npc_zone.keys())
        return [Component(
            entity_ids=set(),
            npc_names=all_npcs,
            edges=list(edges),
        )]

    def _build_story_prompt(self, component: Component, exec_results: list[dict],
                            graph_engine=None, world_time_str: str | None = None,
                            tick_duration_str: str | None = None) -> str:
        """为一个连通子图构建故事 prompt。"""
        if not graph_engine:
            return self._legacy_build_prompt(component, exec_results,
                                             world_time_str, tick_duration_str)

        # 使用 Slot 式组装
        if self._adapter:
            # 构建 entity_blocks：list of (entity, exec_result)
            npc_map: dict[str, dict] = {er["npc_name"]: er for er in exec_results}
            entity_blocks = []
            for eid in component.entity_ids:
                ent = graph_engine.get_entity(eid)
                if not ent:
                    continue
                er = npc_map.get(ent.name, {})
                entity_blocks.append((ent, er))

            topo_eids = list(component.entity_ids)
            return assemble(
                "llm3_story", self._adapter, graph_engine,
                _caller="llm3",
                time_str=world_time_str,
                tick_str=tick_duration_str,
                entity_blocks=entity_blocks,
                component=component,
                exec_results=exec_results,
                topo_eids=topo_eids,
            )

        # 旧版回退
        npc_map: dict[str, dict] = {er["npc_name"]: er for er in exec_results}

        parts = [
            "你是世界模拟引擎的故事叙事层。",
            "你的任务：为以下场景写一段生动的故事。",
            "完全自由发挥，不要输出任何 JSON 或结构化格式。只写故事。",
            "",
        ]
        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        parts.append("")
        parts.append("【角色和物体】")
        parts.append("")
        node_blocks = []
        for eid in component.entity_ids:
            ent = graph_engine.get_entity(eid)
            if not ent:
                continue
            block_lines = [f"· {ent.name}"]
            type_str = ent.entity_type if ent.entity_type else "?"
            block_lines.append(f"  类型: {type_str}")
            if ent.desc:
                block_lines.append(f"  描述: {ent.desc}")
            if ent.role:
                block_lines.append(f"  身份: {ent.role}")
            npc_name = ent.name
            er = npc_map.get(npc_name)
            if er:
                for txt_key, label in [("vitality_text","体力"),("satiety_text","饱腹"),("mood_text","心情")]:
                    val = er.get(txt_key,"")
                    if val:
                        block_lines.append(f"  {label}: {val}")
                mems = er.get("memories","")
                if mems:
                    ml = mems.strip().split("\n")[:3]
                    block_lines.append(f"  最近经历: {'；'.join(ml)}")
                traits = er.get("traits",[])
                if traits:
                    block_lines.append(f"  性格: {'、'.join(str(t) for t in traits[:3])}")
                intent = er.get("raw_intent","")
                if intent:
                    block_lines.append(f"  想法: {intent}")
            node_blocks.append("\n".join(block_lines))
        parts.append("\n\n".join(node_blocks) if node_blocks else "(无节点信息)")
        parts.append("")
        if component.npc_names:
            inventory_lines = []
            for npc_name in sorted(component.npc_names):
                er = npc_map.get(npc_name)
                if er and "npc_eid" in er:
                    inv = graph_engine.get_inventory_view(er["npc_eid"])
                    if inv:
                        items = [f"{i['item_name']}x{i['quantity']}" for i in inv]
                        inventory_lines.append(f"{npc_name}带着: {'、'.join(items)}")
            if inventory_lines:
                parts.append("【库存】")
                for line in inventory_lines:
                    parts.append(f"  · {line}")
        topo_eids = list(component.entity_ids)
        topo_text, label_map = graph_engine.build_tagged_topology(topo_eids)
        if topo_text:
            parts.append("")
            parts.append("拓扑信息是客观数据层的唯一事实来源。")
            parts.append(topo_text)
            parts.append("")
            from .graph_engine import build_label_mapping_text
            parts.append(build_label_mapping_text(label_map, graph_engine))
            parts.append("")
            parts.append("拓扑约束：故事内容不得超出上述边的定义范围。")
            parts.append("例如，若 {A} 无 → {Y} 边，则 {A} 对应的角色在故事中不能与 {Y} 发生交互。")
        if component.edges:
            parts.append("")
            for e in component.edges:
                if e.edge_type == "npc_zone" and e.stayed:
                    parts.append(f"· {e.source} 在原地驻足")
                elif e.edge_type == "npc_zone":
                    parts.append(f"· {e.source} 来到此处")
                elif e.edge_type == "npc_npc" and not e.success:
                    parts.append(f"· {e.source} 试图找 {e.target}，但没成功")
                elif e.edge_type == "npc_npc":
                    parts.append(f"· {e.source} 与 {e.target} 有互动")
                elif e.edge_type == "npc_object":
                    parts.append(f"· {e.source} 使用 {e.target}")
        else:
            parts.append("（无交互事件）")
        parts.append("")
        parts.append("请为以上场景写一段生动的故事。一段即可。")
        parts.append("用【场景】开头。")
        return "\n".join(parts)

    def _legacy_build_prompt(self, component: Component, exec_results: list[dict],
                             world_time_str: str | None = None,
                             tick_duration_str: str | None = None) -> str:
        """无 graph_engine 兜底用 prompt"""
        parts = [
            "你是世界模拟引擎的故事叙事层。",
            "完全自由发挥，不要输出任何 JSON 或结构化格式。只写故事。",
            "",
        ]
        if world_time_str:
            parts.append(f"当前时间：{world_time_str}")
        if tick_duration_str:
            parts.append(f"本 tick 时长：{tick_duration_str}")
        parts.append("")
        parts.append("==== 每个 NPC 的想法 ====")
        for er in exec_results:
            name = er["npc_name"]
            parts.append(f"【{name}】{er.get('raw_intent', '无')}")
        parts.append("")
        parts.append("==== 交互 ====")
        for e in component.edges:
            if e.edge_type == "npc_zone" and e.stayed:
                parts.append(f"- {e.source}@{e.zone}驻足")
            elif e.edge_type == "npc_npc":
                parts.append(f"- {e.source}↔{e.target}")
            elif e.edge_type == "npc_object":
                parts.append(f"- {e.source}使用{e.target}")
            else:
                parts.append(f"- {e.source}→{e.target}")
        parts.append("")
        parts.append("为以上场景写一段故事。")
        return "\n".join(parts)

    def _fallback_describe(self, edges: list[EdgeResult], exec_results: list[dict]):
        """降级：不用 LLM，直接用模板生成描述"""
        for e in edges:
            if e.edge_type == "npc_zone":
                if e.stayed:
                    e.description = f"{e.source}在{e.zone}待了一会，没有特别的事情发生。"
                else:
                    e.description = f"{e.source}来到了{e.zone}。"
            elif e.edge_type == "npc_npc":
                if e.success:
                    e.description = f"{e.source}找到了{e.target}，两人聊了一会儿。"
                else:
                    e.description = f"{e.source}想找{e.target}，但{e.target}不在这里。"
            elif e.edge_type == "npc_object":
                e.description = f"{e.source}使用了{e.target}。"
