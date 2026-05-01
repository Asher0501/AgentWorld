"""
Recipe Engine —— 执行配方转换，通过物体交互完成。

核心流程：
  1. LLM 选择配方或系统检测到配方意图
  2. RecipeEngine.can_execute() 验证：
     - NPC 有指向该物体的边
     - NPC 持有足够输入材料
     - NPC 体力够
  3. RecipeEngine.execute() 生成完整的交互指令：
     - 通过物体边交互（设置 edge_id）
     - 扣减输入物品（edge_qty_changes 负值）
     - 增加输出物品（edge_qty_changes 正值）
     - 消耗体力（effects）
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from ..entities.recipe import Recipe, RecipeRegistry
from ..config.config_loader import has_role

if TYPE_CHECKING:
    from ..services.graph_engine import GraphEngine

logger = logging.getLogger("recipe_engine")


class RecipeEngine:
    """
    配方执行引擎。
    被动为 GraphEngine 提供配方验证和执行服务。
    """

    def __init__(self, graph: GraphEngine | None = None):
        self._graph = graph

    def set_graph(self, graph: GraphEngine):
        self._graph = graph

    # ─── 物体连接检测 ───

    def find_interaction_edge(self, npc_eid: str,
                              required_obj_type: str,
                              zone_id: str | None = None) -> tuple[str | None, str | None]:
        """
        查找 NPC 到所需物体类型的交互边。
        先找精确类型匹配，没有则用区域隐式工具。

        Returns:
            (edge_id, object_entity_id) 或 (None, None)
        """
        if not self._graph:
            return None, None

        npc_ent = self._graph.get_entity(npc_eid)
        if not npc_ent:
            return None, None

        # 遍历 NPC 的出边，找目标类型匹配的物体
        for edge in self._graph.graph.edges:
            if edge.source_entity_id != npc_eid:
                continue
            tgt = self._graph.get_entity(edge.target_entity_id)
            if not tgt or not has_role(tgt.type_id, "fixture"):
                continue
            # 检查物体类型：通过实体名称或属性
            obj_type = tgt.get_attr("object_type") or tgt.name
            if required_obj_type in obj_type or required_obj_type == obj_type:
                return edge.edge_id, tgt.entity_id

        # 没有精确边匹配，用区域隐式工具(有边链接到该区域任何物体即视为达标)
        if zone_id:
            zone_tools = self._map_zone_to_object_types(zone_id)
            if required_obj_type in zone_tools:
                # 找 NPC 到本区域任意物体的第一条边
                for edge in self._graph.graph.edges:
                    if edge.source_entity_id != npc_eid:
                        continue
                    tgt = self._graph.get_entity(edge.target_entity_id)
                    if tgt and has_role(tgt.type_id, "fixture"):
                        return edge.edge_id, tgt.entity_id

        return None, None

    @staticmethod
    def _map_zone_to_object_types(zone_id: str) -> list[str]:
        """区域 → 可用的工具类型映射（当没有精确物体边时使用）"""
        zone_tools = {
            "farm": ["烤炉", "磨具", "农具"],
            "market": ["铁砧", "工作台", "摊位"],
            "tavern": ["酿酒桶", "吧台"],
            "temple": ["药臼", "祭坛"],
            "forest": ["缝纫台", "木工台"],
            "library": ["书桌", "书架"],
            "barracks": ["训练器材", "武器架"],
            "village_square": [],
        }
        return zone_tools.get(zone_id, [])

    def get_npc_connected_object_types(self, npc_eid: str, zone_id: str | None = None) -> list[str]:
        """
        返回 NPC 可用的物体类型。
        从图的实际连接 + 区域隐式工具两处获取。
        """
        types = []
        if self._graph:
            for edge in self._graph.graph.edges:
                if edge.source_entity_id != npc_eid:
                    continue
                tgt = self._graph.get_entity(edge.target_entity_id)
                if tgt and has_role(tgt.type_id, "fixture"):
                    obj_type = tgt.get_attr("object_type") or tgt.name
                    types.append(obj_type)

        # 补充区域隐式工具（没有精确物体边时，区域自带一些基础设施）
        if zone_id:
            for t in self._map_zone_to_object_types(zone_id):
                if t not in types:
                    types.append(t)

        return types

    # ─── 配方意图检测 ───

    def detect_recipe_from_instruction(self, instruction: dict,
                                       inventory: dict[str, int],
                                       zone_id: str,
                                       npc_eid: str) -> Recipe | None:
        """
        从 LLM 指令中检测是否有配方意图。

        匹配策略（按优先级）：
          1. action 精准匹配配方名（如 "烘焙面包"）
          2. action 包含输出物品名
          3. edge_qty_changes 的模式匹配
        """
        action = instruction.get("action", "") or ""
        qty_changes = instruction.get("edge_qty_changes", [])
        result_text = instruction.get("result_text", "")

        # 策略1：精准匹配
        recipe = RecipeRegistry.get_by_name(action)
        if recipe:
            return recipe

        # 策略2：从 action + result_text 匹配输出物品
        text = action + result_text
        candidates = RecipeRegistry.get_all()
        matched = []
        for r in candidates:
            for out_item in r.outputs:
                if out_item in text:
                    matched.append(r)
                    break

        if matched:
            # 优先选综合匹配度最高的（物体 + 库存）
            connected = self.get_npc_connected_object_types(npc_eid)
            for r in matched:
                if r.required_object_type and r.required_object_type in connected:
                    return r
            # 没有物体匹配，返回第一个匹配
            return matched[0]

        # 策略3：qty_changes 模式
        inputs_found = {}
        outputs_found = {}
        for qc in qty_changes:
            delta = qc.get("delta", 0)
            tgt = qc.get("target_entity_id", "")
            if delta < 0:
                inputs_found[tgt] = abs(delta)
            elif delta > 0:
                outputs_found[tgt] = delta

        for r in candidates:
            if any(i in inputs_found for i in r.inputs) or any(o in outputs_found for o in r.outputs):
                return r

        return None

    # ─── 配方执行 ───

    def can_execute(self, recipe: Recipe, npc_eid: str, zone_id: str) -> tuple[bool, str]:
        """
        检查 NPC 能否执行配方。

        Returns:
            (True, reason_if_false)
        """
        if not self._graph:
            return False, "图引擎未初始化"

        inv = self._graph.get_inventory_view(npc_eid)
        npc_ent = self._graph.get_entity(npc_eid)

        # 检查物体连接
        if recipe.required_object_type:
            edge_id, _ = self.find_interaction_edge(npc_eid, recipe.required_object_type, zone_id)
            if not edge_id:
                conns = self.get_npc_connected_object_types(npc_eid)
                return False, f"找不到 [{recipe.required_object_type}] 的连接（已连接: {conns}）"

        # 检查库存
        for item_name, need_qty in recipe.inputs.items():
            if inv.get(item_name, 0) < need_qty:
                return False, f"库存不足：需要 {item_name}x{need_qty}，持有 {inv.get(item_name, 0)}"

        # 检查区域
        if recipe.zone_id and zone_id != recipe.zone_id:
            return False, f"区域不符：{recipe.name} 需在 {recipe.zone_id}，当前在 {zone_id}"

        # 检查体力
        if npc_ent:
            vit = npc_ent.get_attr("vitality") or 0
            if vit < recipe.vitality_cost:
                return False, f"体力不足：需要 {recipe.vitality_cost}，当前 {vit}"

        return True, ""

    def execute(self, recipe: Recipe, npc_eid: str,
                npc_name: str, zone_id: str) -> dict:
        """
        执行配方转换——生成完整的交互指令。

        Returns:
            instruction dict（和 LLM 输出格式一致，可直接喂给 execute_npc_instruction）
        """
        if not self._graph:
            return {"result_text": f"{npc_name} 配方执行失败：图引擎未初始化"}

        # 1. 找交互边
        edge_id, obj_eid = self.find_interaction_edge(npc_eid, recipe.required_object_type, zone_id) \
            if recipe.required_object_type else (None, None)

        effect_list = []
        qty_list = []

        # 2. 扣减输入
        for item_name, qty in recipe.inputs.items():
            item_eid = self._find_item_entity(item_name)
            if not item_eid:
                item_eid = self._graph.ensure_entity_exists(
                    entity_id=f"item_{item_name}",
                    name=item_name, entity_type="item",
                )
            qty_list.append({
                "source_entity_id": npc_eid,
                "target_entity_id": item_eid,
                "delta": -qty,
            })

        # 3. 增加输出（如果输出物品不存在，auto-register）
        for item_name, qty in recipe.outputs.items():
            item_eid = self._find_item_entity(item_name)
            if not item_eid:
                item_eid = self._graph.ensure_entity_exists(
                    entity_id=f"item_{item_name}",
                    name=item_name, entity_type="item",
                )
                logger.info(f"[AutoReg] 配方产出新物品: {item_name} (eid={item_eid})")
            qty_list.append({
                "source_entity_id": npc_eid,
                "target_entity_id": item_eid,
                "delta": qty,
            })

        # 4. 消耗体力
        effect_list.append({
            "target_entity_id": npc_eid,
            "attribute_name": "vitality",
            "operation": "sub",
            "value": recipe.vitality_cost,
            "description": f"{recipe.name}消耗体力",
        })

        # 5. 如果 LLM 发现的配方不在注册表中，登记
        if recipe.source == "llm" and RecipeRegistry.get_by_name(recipe.name) is None:
            RecipeRegistry.register_llm_recipe(recipe)

        # 6. 构建 result_text（含物体交互信息）
        obj_part = f"通过 [{recipe.required_object_type}]" if recipe.required_object_type else ""
        inp_part = " + ".join(f"{k}x{v}" for k, v in recipe.inputs.items())
        out_part = " + ".join(f"{k}x{v}" for k, v in recipe.outputs.items())
        result_text = f"{npc_name} 在{zone_id} {obj_part} {recipe.name}，消耗{inp_part}，获得{out_part}"

        instruction = {
            "edge_id": edge_id or "",
            "action": recipe.name,
            "effects": effect_list,
            "edge_qty_changes": qty_list,
            "result_text": result_text,
        }

        logger.info(f"[Recipe执行] {npc_name} @ {zone_id} | "
                     f"{recipe} | "
                     f"边={edge_id or '无'}")

        return instruction

    def _find_item_entity(self, item_name: str) -> str | None:
        if not self._graph:
            return None
        for ent in self._graph.all_entities():
            if ent.name == item_name and has_role(ent.type_id, "thing"):
                return ent.entity_id
        return None
