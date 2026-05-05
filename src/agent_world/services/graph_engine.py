"""
纯拓扑图引擎 —— 唯一的数据写入路径

职责：
  1. 管理实体（Entity）注册和连接
  2. 管理边（InteractionEdge）创建和数量修改
  3. 提供拓扑视图（1-hop 子图、库存视图）
  4. 【唯一】执行 LLM #2 的结构变更指令（connect/disconnect/set_qty）
  5. 【唯一】执行 LLM #4 的数值变更指令（{src, tgt, delta}）

设计原则：任何数据修改必须经过此引擎。LLM 通过结构化指令间接写入。
"""

from __future__ import annotations
import logging
import uuid
from copy import deepcopy
from typing import Any

from ..entities.base_entity import Entity
from ..config.node_ontology import prefix_to_type_id
from ..config.config_loader import has_role, get_type_def
from ..models.interaction import InteractionGraph, InteractionEdge

logger = logging.getLogger(__name__)

EdgeOperation = dict[str, Any]
"""{op: "connect"|"disconnect"|"set_qty"|"delta", src: str, tgt: str, qty: int}"""


class GraphEngine:
    """纯拓扑图引擎"""

    def __init__(self):
        self._entities: dict[str, Entity] = {}
        self._graph: InteractionGraph = InteractionGraph()
        # 边索引加速：{(src, tgt): InteractionEdge}
        self._edge_by_pair: dict[tuple[str, str], InteractionEdge] = {}

    # ═══════════════════════════════════════════
    # 实体管理
    # ═══════════════════════════════════════════

    def register_entity(self, entity: Entity):
        """注册实体（替换已存在的同名实体）"""
        if entity.entity_id in self._entities:
            # 保留连接信息
            entity.connected_entity_ids = self._entities[entity.entity_id].connected_entity_ids
        # 物品类实体自动标记守恒（兜底：即使 item_to_entity 漏设）
        if _is_item_type(entity.entity_id):
            entity.conserved = True
        self._entities[entity.entity_id] = entity
        logger.debug(f"[Graph] 注册实体: {entity.entity_id} ({entity.name})")

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def resolve_eid(self, raw: str) -> str | None:
        """将原始引用解析为实体 ID。
        优先精确匹配，fallback 按名称匹配或按前缀匹配。
        LLM 可能输出物品名（如 '小麦'/'item_小麦'）而非实体 ID。
        """
        # 1. 精确匹配
        if raw in self._entities:
            return raw
        # 2. 按名称匹配
        for eid, ent in self._entities.items():
            if ent.name == raw:
                return eid
        # 3. 去掉已注册前缀后按名称匹配
        from ..config.config_loader import get_all_prefixes
        for pfx in get_all_prefixes():
            if raw.startswith(pfx):
                name = raw[len(pfx):]
                for eid, ent in self._entities.items():
                    if ent.name == name:
                        return eid
        # 4. 按 eid 后缀匹配（LLM 输出含前缀 + 哈希等情况）
        for pfx in get_all_prefixes():
            if raw.startswith(pfx):
                suffix = raw[len(pfx):]
                for eid in self._entities:
                    if eid.endswith(suffix):
                        return eid
        return None

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def is_conserved(self, entity_id: str) -> bool:
        """查询实体是否为守恒量"""
        ent = self._entities.get(entity_id)
        if ent:
            return ent.conserved
        # 未注册实体：根据 eid 前缀判断
        return _is_item_type(entity_id)

    def is_terminal(self, entity_id: str) -> bool:
        """查询实体是否终止 BFS 扩展"""
        ent = self._entities.get(entity_id)
        if ent:
            return ent.is_leaf
        return False

    def find_entity_by_name(self, name: str) -> Entity | None:
        """按名称查找实体"""
        for ent in self._entities.values():
            if ent.name == name:
                return ent
        return None

    # ═══════════════════════════════════════════
    # 边管理（纯拓扑）
    # ═══════════════════════════════════════════

    def connect(self, src_eid: str, tgt_eid: str, qty: int = 0) -> InteractionEdge:
        """
        创建一条边：src → tgt。

        如果边已存在，更新 quantity 并激活。
        如果实体不存在，自动注册占位实体。
        自动建立 Entity 级别的连接。
        """
        # 自动注册占位
        for eid in (src_eid, tgt_eid):
            if eid not in self._entities:
                name = _extract_name_from_eid(eid)
                ent = Entity(entity_id=eid, name=name, entity_type=_infer_type(eid))
                # 物品类实体默认标记为守恒量
                ent.conserved = _is_item_type(eid)
                self._entities[eid] = ent
                logger.info(f"[Graph] 自动注册占位实体: {eid} ({name}) conserved={ent.conserved}")

        # 已有边 → 更新 qty
        existing = self._edge_by_pair.get((src_eid, tgt_eid))
        if existing:
            existing.quantity = qty
            existing.is_active = True
            return existing

        # 新建边
        from ..models.interaction import InteractionEdge
        edge = InteractionEdge(
            edge_id=f"e_{uuid.uuid4().hex[:12]}",
            source_entity_id=src_eid,
            target_entity_id=tgt_eid,
            quantity=qty,
            is_active=True,
        )
        self._graph.add_edge(edge)
        self._edge_by_pair[(src_eid, tgt_eid)] = edge

        # 实体间连接（双向）
        self._entities[src_eid].connect_to(tgt_eid)
        self._entities[tgt_eid].connect_to(src_eid)

        logger.debug(f"[Graph] 连接: {src_eid} ──▸ {tgt_eid} (qty={qty})")
        return edge

    def disconnect(self, src_eid: str, tgt_eid: str) -> bool:
        """移除边。返回是否成功。"""
        removed = self._graph.remove_edge(src_eid, tgt_eid)
        if (src_eid, tgt_eid) in self._edge_by_pair:
            del self._edge_by_pair[(src_eid, tgt_eid)]
        if src_eid in self._entities:
            self._entities[src_eid].disconnect_from(tgt_eid)
        if tgt_eid in self._entities:
            self._entities[tgt_eid].disconnect_from(src_eid)
        if removed:
            logger.debug(f"[Graph] 断开连接: {src_eid} ─/─ {tgt_eid}")
        return removed

    def get_edge_by_name(self, src_name: str, tgt_name: str) -> InteractionEdge | None:
        """按名称查询边（自动映射到实体 ID），不存在返回 None。
        使用 resolve_eid() 兼容 item_/npc_ 前缀，与 apply_edge_operations 保持一致。
        """
        src_eid = self.resolve_eid(src_name)
        tgt_eid = self.resolve_eid(tgt_name)
        if not src_eid or not tgt_eid:
            return None
        return self.get_edge(src_eid, tgt_eid)

    def get_edge(self, src_eid: str, tgt_eid: str) -> InteractionEdge | None:
        """获取边（双向查询）"""
        edge = self._edge_by_pair.get((src_eid, tgt_eid))
        if edge:
            return edge
        edge = self._edge_by_pair.get((tgt_eid, src_eid))
        return edge

    def get_outgoing_edges(self, entity_id: str) -> list[InteractionEdge]:
        """获取实体的所有出边"""
        return [
            e for e in self._graph.edges
            if e.source_entity_id == entity_id
        ]

    def get_incoming_edges(self, entity_id: str) -> list[InteractionEdge]:
        """获取实体的所有入边"""
        return [
            e for e in self._graph.edges
            if e.target_entity_id == entity_id
        ]

    def get_edges_between(self, eid_a: str, eid_b: str) -> list[InteractionEdge]:
        """获取两个实体之间的所有边（双向）"""
        result = []
        for e in self._graph.edges:
            if (e.source_entity_id == eid_a and e.target_entity_id == eid_b) \
               or (e.source_entity_id == eid_b and e.target_entity_id == eid_a):
                result.append(e)
        return result

    # ═══════════════════════════════════════════
    # 数量操作（LLM #4 输出执行）
    # ═══════════════════════════════════════════

    def set_edge_quantity(self, src_eid: str, tgt_eid: str, qty: int) -> bool:
        """设置边的数量"""
        edge = self.get_edge(src_eid, tgt_eid)
        if not edge:
            logger.warning(f"[Graph] set_qty: 边不存在 {src_eid}→{tgt_eid}")
            return False
        edge.quantity = qty
        edge.is_active = True
        logger.debug(f"[Graph] set_qty: {src_eid}→{tgt_eid} = {qty}")
        return True

    def modify_edge_quantity(self, src_eid: str, tgt_eid: str, delta: int) -> bool:
        """修改边的数量（delta 可为正负）"""
        edge = self.get_edge(src_eid, tgt_eid)
        if not edge:
            # 自动创建边（用于 0→正值 的首次转移）
            if delta > 0:
                self.connect(src_eid, tgt_eid, delta)
                return True
            logger.warning(f"[Graph] modify_qty: 边不存在 {src_eid}→{tgt_eid}, delta={delta}")
            return False
        new_qty = edge.quantity + delta
        if new_qty < 0:
            logger.warning(
                f"[Graph] modify_qty: {src_eid}→{tgt_eid} {delta}"
                f" 会将 qty 从 {edge.quantity} 变为 {new_qty}（截断为 0）"
            )
            new_qty = 0
        edge.quantity = new_qty
        if new_qty == 0:
            self.disconnect(src_eid, tgt_eid)
        logger.debug(f"[Graph] modify_qty: {src_eid}→{tgt_eid} {delta:+d} → {new_qty}")
        return True

    def modify_entity_attr(self, entity_id: str, attr: str, delta: float, clamp: bool = True) -> bool:
        """修改实体的属性值（delta 可为正负）"""
        ent = self.get_entity(entity_id)
        if not ent:
            logger.warning(f"[Graph] modify_attr: 实体不存在 {entity_id}")
            return False
        current = ent.attributes.get(attr, 0.0) or 0.0
        new_val = current + delta
        if clamp:
            new_val = max(0.0, min(100.0, new_val))
        ent.attributes[attr] = new_val
        logger.debug(f"[Graph] modify_attr: {entity_id}.{attr} {delta:+} → {new_val:.0f}")
        return True

    def _adjust_delta_pairs(self, ops: list[EdgeOperation]) -> list[EdgeOperation]:
        """
        预校验 delta 操作对，防止 clip 破坏守恒。

        当 A 付出 N 但库存不够时：
          1. 将 A 的 delta 裁剪到实际可付的量
          2. 将对应 B 的 delta 同比缩小，维持 Σ=0

        LLM 输出语义：
          delta: src=-N, tgt=item  → src 向系统付出 N 个 item
          delta: src=B, tgt=item, delta=+M  → B 从系统接收 M 个 item
        """
        # 按 item (tgt) 分组所有 delta
        item_groups: dict[str, list[dict]] = {}
        for op in ops:
            if op.get("op") != "delta":
                continue
            tgt_raw = op.get("tgt", "")
            tgt_eid = self.resolve_eid(tgt_raw) or tgt_raw
            item_groups.setdefault(tgt_eid, []).append(op)

        adjusted = list(ops)
        for item_eid, group in item_groups.items():
            # 收集付出方（delta < 0）和接收方（delta > 0）
            givers = [o for o in group if o.get("delta", 0) < 0]
            receivers = [o for o in group if o.get("delta", 0) > 0]

            # 先统计每个 src→tgt 的总付出，防止同付出方多次扣除
            src_total_demand: dict[str, int] = {}
            for giver in givers:
                src_eid = self.resolve_eid(giver.get("src", "")) or giver.get("src", "")
                src_total_demand[src_eid] = src_total_demand.get(src_eid, 0) + abs(giver["delta"])

            for giver in givers:
                src_raw = giver.get("src", "")
                src_eid = self.resolve_eid(src_raw) or src_raw
                need = abs(giver["delta"])
                # 检查库存（考虑同 src→tgt 多次扣减的累积）
                total_demand = src_total_demand.get(src_eid, need)
                edge = self.get_edge(src_eid, item_eid)
                available = edge.quantity if edge else 0
                if available >= total_demand:
                    continue  # 够付，不用调整

                # 不够付！按比例同比缩减本笔 delta
                need_clipped = int(-need * available / max(1, total_demand))
                excess = abs(giver["delta"]) - abs(need_clipped)
                giver["delta"] = need_clipped
                logger.warning(
                    f"[Graph] conserve: {src_raw} 只有 {available} 个 {item_eid}，"
                    f"总需求 {total_demand}（本笔 {need}），"
                    f"delta {need * (-1)} 裁剪为 {need_clipped}（差量 {excess}）"
                )

                if not receivers:
                    continue
                # 从接收方扣除差额，维持守恒
                remain = excess
                for receiver in receivers:
                    if remain <= 0:
                        break
                    recv_delta = receiver.get("delta", 0)
                    if recv_delta <= 0:
                        continue
                    cut = min(remain, recv_delta)
                    receiver["delta"] = recv_delta - cut
                    remain -= cut
                    logger.warning(
                        f"[Graph] conserve: 同步裁剪 {receiver.get('src','?')} 的 "
                        f"{item_eid} delta +{recv_delta} → +{receiver['delta']}"
                    )

        return adjusted

    def _validate_op_entities(self, ops: list[EdgeOperation]) -> list[EdgeOperation]:
        """
        校验所有操作的 src/tgt 是否引用已知实体。
        过滤掉引用未知实体的操作（默认阻止，config 开关控制）。

        场景：LLM 幻觉出「买家」「脚夫」等不存在的实体名。
        """
        from ..config.config_loader import get_world_config
        allow_unreg = get_world_config("allow_unregistered_entity", False)
        if allow_unreg:
            return ops  # 开关打开→不校验

        valid = []
        for op in ops:
            op_type = op.get("op", "")
            # recipe 的 src 必须已知
            if op_type == "recipe":
                src_raw = op.get("src", "")
                src = self.resolve_eid(src_raw) or src_raw
                if src not in self._entities:
                    logger.warning(f"[Graph] 拒绝: recipe src={src_raw} 不在图中")
                    continue
                valid.append(op)
                continue

            # system_delta 的 tgt 必须已知
            if op_type == "system_delta":
                tgt_raw = op.get("tgt", "")
                tgt = self.resolve_eid(tgt_raw) or tgt_raw
                if tgt not in self._entities:
                    logger.warning(f"[Graph] 拒绝: system_delta tgt={tgt_raw} 不在图中")
                    continue
                valid.append(op)
                continue

            # delta 的 src 必须已知（tgt 是物品，通常可自动注册）
            if op_type == "delta":
                src_raw = op.get("src", "")
                src = self.resolve_eid(src_raw) or src_raw
                if src not in self._entities:
                    logger.warning(f"[Graph] 拒绝: delta src={src_raw} 不在图中")
                    continue
                valid.append(op)
                continue

            # attr / set_qty 等也检查 src/tgt
            if op_type == "attr":
                tgt_raw = op.get("target", "")
                tgt = self.resolve_eid(tgt_raw) or tgt_raw
                if tgt not in self._entities:
                    logger.warning(f"[Graph] 拒绝: attr target={tgt_raw} 不在图中")
                    continue
                valid.append(op)
                continue

            if op_type == "set_qty":
                src_raw = op.get("src", "")
                tgt_raw = op.get("tgt", "")
                src = self.resolve_eid(src_raw) or src_raw
                tgt = self.resolve_eid(tgt_raw) or tgt_raw
                if src not in self._entities or tgt not in self._entities:
                    logger.warning(f"[Graph] 拒绝: set_qty src={src_raw} tgt={tgt_raw} 不在图中")
                    continue
                valid.append(op)
                continue

            # 其他 op 类型通过
            valid.append(op)

        return valid

    def apply_edge_operations(self, ops: list[EdgeOperation]) -> dict[str, Any]:
        """
        批量执行边操作。
        这是 LLM #2 输出（结构变更）和 LLM #4 输出（数值变更）的统一执行入口。

        支持的 op:
          "connect"    — 创建边 ({src, tgt, qty})  [, qty 可选]
          "disconnect" — 移除边 ({src, tgt})
          "set_qty"    — 设置数量 ({src, tgt, qty})
          "delta"      — 增减数量 ({src, tgt, delta})
          "system_delta"  — 系统间物品转移 ({tgt, item, delta})
          "recipe"        — 配方转换 ({src, consumes, produces})

        返回: {status: "ok"|"partial"|"failed", results: [...]}
        """
        results = []
        status = "ok"

        # Pre-pass: 调整 delta pair 防止 clip 破坏守恒
        ops = self._validate_op_entities(ops)
        ops = self._adjust_delta_pairs(ops)

        for op in ops:
            op_type = op.get("op", "")
            src = op.get("src", "")
            tgt = op.get("tgt", "")

            # attr 操作使用 target 字段，不要求 src/tgt
            if op_type != "attr" and (not src or not tgt):
                results.append({"op": op_type, "status": "skipped", "reason": "缺少 src 或 tgt"})
                continue

            # 解析 src/tgt (LLM 可能输出物品名而非实体 ID)
            src = self.resolve_eid(src) or src
            tgt = self.resolve_eid(tgt) or tgt

            try:
                if op_type == "connect":
                    qty = op.get("qty", 0)
                    self.connect(src, tgt, qty)
                    results.append({"op": "connect", "src": src, "tgt": tgt, "qty": qty, "status": "ok"})

                elif op_type == "disconnect":
                    ok = self.disconnect(src, tgt)
                    results.append({"op": "disconnect", "src": src, "tgt": tgt, "status": "ok" if ok else "not_found"})
                    if not ok:
                        status = "partial"

                elif op_type == "set_qty":
                    qty = op.get("qty", 0)
                    self.set_edge_quantity(src, tgt, qty)
                    results.append({"op": "set_qty", "src": src, "tgt": tgt, "qty": qty, "status": "ok"})

                elif op_type == "delta":
                    delta = op.get("delta", 0)
                    ok = self.modify_edge_quantity(src, tgt, delta)
                    results.append({"op": "delta", "src": src, "tgt": tgt, "delta": delta, "status": "ok" if ok else "skipped"})
                    if not ok:
                        status = "partial"

                elif op_type == "system_delta":
                    item = op.get("item", "")
                    delta = op.get("delta", 0)
                    item_eid = self.resolve_eid(item) or item
                    tgt_eid = self.resolve_eid(tgt) or tgt
                    if tgt_eid and item_eid and delta != 0:
                        self.modify_edge_quantity(tgt_eid, item_eid, delta)
                        results.append({"op": "system_delta", "tgt": tgt_eid, "item": item_eid, "delta": delta, "status": "ok"})
                    else:
                        results.append({"op": "system_delta", "status": "skipped", "reason": "缺少 tgt/item/delta"})
                        status = "partial"

                elif op_type == "recipe":
                    src = self.resolve_eid(src) or src
                    consumes = op.get("consumes", {})
                    produces = op.get("produces", {})
                    if src and consumes and produces:
                        recipe_ok = True
                        for item_name, qty in consumes.items():
                            item_eid = self.resolve_eid(item_name) or item_name
                            if not self.modify_edge_quantity(src, item_eid, -qty):
                                recipe_ok = False
                        for item_name, qty in produces.items():
                            item_eid = self.resolve_eid(item_name) or item_name
                            self.modify_edge_quantity(src, item_eid, +qty)
                        results.append({"op": "recipe", "src": src, "consumes": consumes, "produces": produces, "status": "ok" if recipe_ok else "partial"})
                        if not recipe_ok:
                            status = "partial"
                    else:
                        results.append({"op": "recipe", "status": "skipped", "reason": "缺少 src/consumes/produces"})
                        status = "partial"

                elif op_type == "attr":
                    target = self.resolve_eid(op.get("target", "")) or op.get("target", "")
                    attr = op.get("attr", "")
                    delta = op.get("delta", 0)
                    if target and attr and delta != 0:
                        self.modify_entity_attr(target, attr, delta)
                        results.append({"op": "attr", "target": target, "attr": attr, "delta": delta, "status": "ok"})
                    else:
                        results.append({"op": "attr", "status": "skipped", "reason": "缺少 target/attr/delta"})

                else:
                    results.append({"op": op_type, "status": "skipped", "reason": f"未知操作类型: {op_type}"})
                    status = "partial"

            except Exception as e:
                logger.error(f"[Graph] 执行操作失败: {op} → {e}")
                results.append({"op": op_type, "src": src, "tgt": tgt, "status": "error", "error": str(e)})
                status = "partial"

        return {"status": status, "results": results}

    # ═══════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════

    def get_held_quantity(self, npc_eid: str, item_eid: str) -> int:
        """获取 NPC 持有某个物品的数量"""
        edge = self.get_edge(npc_eid, item_eid)
        return edge.quantity if edge else 0

    def get_inventory_view(self, npc_eid: str) -> list[dict]:
        """获取 NPC 的库存视图：{item_name, quantity, item_id}"""
        result = []
        for e in self.get_outgoing_edges(npc_eid):
            if e.quantity > 0:
                item_ent = self.get_entity(e.target_entity_id)
                name = item_ent.name if item_ent else e.target_entity_id
                result.append({
                    "item_name": name,
                    "quantity": e.quantity,
                    "item_id": e.target_entity_id,
                })
        return result

    def get_subgraph(self, entity_id: str, hops: int = 1) -> dict[str, Any]:
        """
        获取实体的 n-hop 子图。

        返回：
        {
            "center": {entity块},
            "entities": {eid: entity块},
            "edges": [edge行],
        }

        其中 entity块 = {
            "entity_id": str,
            "name": str,
            "type": str,
            "role": str,
            "desc": str,
            "traits": [...],
            "attrs": {...},
        }
        """
        visited: set[str] = set()
        current_ring: set[str] = {entity_id}

        for _ in range(hops):
            next_ring: set[str] = set()
            for eid in current_ring:
                if eid in visited:
                    continue
                visited.add(eid)
                ent = self.get_entity(eid)
                if ent:
                    next_ring.update(ent.connected_entity_ids)
            current_ring = next_ring

        # 包含自身
        if entity_id not in visited:
            visited.add(entity_id)

        # 构建返回
        entities = {}
        for eid in visited:
            ent = self.get_entity(eid)
            if ent:
                entities[eid] = self._entity_block(ent)
            else:
                entities[eid] = {"entity_id": eid, "name": _extract_name_from_eid(eid),
                                 "type": _infer_type(eid), "role": "", "desc": "",
                                 "traits": [], "attrs": {}}

        edges = [
            {"src": e.source_entity_id, "tgt": e.target_entity_id, "qty": e.quantity}
            for e in self._graph.edges
            if e.source_entity_id in visited or e.target_entity_id in visited
        ]

        return {"center": entity_id, "entities": entities, "edges": edges}

    def get_1hop_subgraph_text(self, entity_id: str) -> str:
        """
        获取 1-hop 子图的纯文本描述（用于 LLM prompt）。
        """
        sub = self.get_subgraph(entity_id, hops=1)
        parts = ["## 当前拓扑视图"]

        for eid, info in sub["entities"].items():
            parts.append(f"\n### {info.get('name', eid)} ({info.get('type', '?')})")
            if info.get("role"):
                parts.append(f"  角色：{info['role']}")
            if info.get("desc"):
                parts.append(f"  描述：{info['desc']}")
            if info.get("traits"):
                parts.append(f"  性格：{'、'.join(info['traits'])}")
            attrs = info.get("attrs", {})
            if attrs:
                attr_str = " | ".join(f"{k}={v}" for k, v in attrs.items() if v is not None)
                if attr_str:
                    parts.append(f"  属性：{attr_str}")

        # 边视图
        my_edges = [e for e in sub["edges"] if e["src"] == entity_id or e["tgt"] == entity_id]
        if my_edges:
            parts.append(f"\n### 连接")
            for e in my_edges:
                from_name = sub["entities"].get(e["src"], {}).get("name", e["src"])
                to_name = sub["entities"].get(e["tgt"], {}).get("name", e["tgt"])
                qty = f" x{e['qty']}" if e.get("qty", 0) != 0 else ""
                parts.append(f"  {from_name} ──▸ {to_name}{qty}")

        return "\n".join(parts)

    def build_zone_subgraph_text(self) -> str:
        """获取全区域视图文本（用于初始化等）"""
        parts = ["## 区域世界"]

        for ent in self._entities.values():
            if has_role(ent.type_id, "region"):
                # 该区域的 NPC
                npcs = []
                for other in self._entities.values():
                    if has_role(other.type_id, "actor") and other.is_connected_to(ent.entity_id):
                        npcs.append(other.name)
                # 该区域的物体
                objects = []
                for other in self._entities.values():
                    if has_role(other.type_id, "fixture") and other.is_connected_to(ent.entity_id):
                        objects.append(other.name)
                # 相连的区域
                zone_conns = []
                for conn in ent.connected_entity_ids:
                    e = self.get_entity(conn)
                    if e and has_role(e.type_id, "region"):
                        zone_conns.append(e.name)

                lines = [f"\n### {ent.name}"]
                if ent.desc:
                    lines.append(f"  {ent.desc}")
                if npcs:
                    lines.append(f"  人物：{' '.join(npcs)}")
                if objects:
                    lines.append(f"  物体：{' '.join(objects)}")
                if zone_conns:
                    lines.append(f"  连接：{' '.join(zone_conns)}")
                parts.append("\n".join(lines))

        return "\n".join(parts)

    # ═══════════════════════════════════════════
    # 拓扑-内容分离视图
    # ═══════════════════════════════════════════

    def build_tagged_topology(
        self, eid_list: list[str],
        global_label_map: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        """
        构建纯拓扑视角（无内容信息）。
        返回抽象标签（标签）+ 连接的描述，以及 标签→entity_id 映射。

        如果提供了 global_label_map（eid → label），则使用全局一致的标签
        而非为每个调用单独分配。
        """
        if not eid_list:
            return "", {}

        label_to_eid: dict[str, str] = {}
        eid_to_label: dict[str, str] = {}

        if global_label_map is not None:
            # 使用全局标签映射（由调用方构建，全图一致）
            reverse = {v: k for k, v in global_label_map.items()}
            for eid in eid_list:
                label = reverse.get(eid)
                if label:
                    label_to_eid[label] = eid
                    eid_to_label[eid] = label
        else:
            # 旧行为：为每个调用单独分配标签
            from ..config.config_loader import get_all_label_mappings
            config_labels = get_all_label_mappings()  # {name → label}

            used_labels: set[str] = set()
            for eid in eid_list:
                ent = self._entities.get(eid)
                name = ent.name if ent else ""
                label = config_labels.get(name) if name else None
                if label and label not in used_labels:
                    label_to_eid[label] = eid
                    eid_to_label[eid] = label
                    used_labels.add(label)

            # 未配置映射的实体动态分配新标签
            for eid in eid_list:
                if eid in eid_to_label:
                    continue
                i = 0
                while True:
                    label = chr(65 + i)
                    if label not in used_labels:
                        label_to_eid[label] = eid
                        eid_to_label[eid] = label
                        used_labels.add(label)
                        break
                    i += 1

        lines = []
        # 连接描述
        conns = []
        for eid in eid_list:
            ent = self._entities.get(eid)
            if not ent:
                continue
            src_label = eid_to_label.get(eid, "?")
            for conn_eid in ent.connected_entity_ids:
                if conn_eid in eid_to_label:
                    dst_label = eid_to_label[conn_eid]
                    edge = self._edge_by_pair.get((eid, conn_eid))
                    qty = edge.quantity if edge else 0
                    if qty == -1:
                        conns.append(f"  {{{src_label}}} → {{{dst_label}}}")
                    elif qty > 0:
                        conns.append(f"  {{{src_label}}} → {{{dst_label}}}  qty:{qty}")
                    else:
                        conns.append(f"  {{{src_label}}} ↔ {{{dst_label}}}")

        if conns:
            lines.append("连接：")
            lines.extend(conns)

        # 标签
        tag_lines = []
        for label, eid in sorted(label_to_eid.items()):
            ent = self._entities.get(eid)
            if not ent:
                continue
            tags = []
            if ent.conserved:
                tags.append("conserved")
            if hasattr(ent, 'type_id'):
                from ..config.config_loader import is_terminal, is_same_type_blocked
                if is_terminal(ent.type_id):
                    tags.append("terminal")
                if is_same_type_blocked(ent.type_id):
                    tags.append("same_type_block")
            if tags:
                tag_lines.append(f"  {{{label}}}: [{', '.join(tags)}]")

        if tag_lines:
            lines.append("\n标签：")
            lines.extend(tag_lines)

        return "\n".join(lines), label_to_eid

    # ═══════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════

    def _entity_block(self, ent: Entity) -> dict:
        return {
            "entity_id": ent.entity_id,
            "name": ent.name,
            "type": ent.entity_type,
            "role": ent.role,
            "desc": ent.desc,
            "traits": list(ent.traits),
            "attrs": dict(ent.attributes),
        }

    def get_graph_for_prompt(self, entity_id: str | None = None) -> str:
        """构建全局或局部拓扑的 prompt 块"""
        if entity_id:
            return self.get_1hop_subgraph_text(entity_id)
        return self.build_zone_subgraph_text()

    def to_dict(self) -> dict:
        """序列化快照（用于保存/备份）"""
        return {
            "entities": {eid: ent.to_dict() for eid, ent in self._entities.items()},
            "edges": [
                {
                    "src": e.source_entity_id,
                    "tgt": e.target_entity_id,
                    "qty": e.quantity,
                    "active": e.is_active,
                }
                for e in self._graph.edges
            ],
        }

    def from_dict(self, data: dict):
        """反序列化加载"""
        self._entities.clear()
        self._graph = InteractionGraph()
        self._edge_by_pair.clear()

        for eid, edata in data.get("entities", {}).items():
            ent = Entity(
                entity_id=eid,
                name=edata.get("name", eid),
                entity_type=edata.get("entity_type", ""),
            )
            ent.role = edata.get("role", "")
            ent.traits = list(edata.get("traits", []))
            ent.desc = edata.get("desc", "")
            ent.attributes = dict(edata.get("attributes", {}))
            ent.connected_entity_ids = set(edata.get("connected_entity_ids", []))
            ent.conserved = edata.get("conserved", False)
            self._entities[eid] = ent

        for edata in data.get("edges", []):
            edge = InteractionEdge(
                edge_id=f"e_{uuid.uuid4().hex[:12]}",
                source_entity_id=edata["src"],
                target_entity_id=edata["tgt"],
                quantity=edata.get("qty", 0),
                is_active=edata.get("active", True),
            )
            self._graph.add_edge(edge)
            self._edge_by_pair[(edata["src"], edata["tgt"])] = edge

    # ═══════════════════════════════════════════
    # 连通分量分割
    # ═══════════════════════════════════════════

    def build_components(self) -> list["TopoComponent"]:
        """
        BFS 遍历全图，按 same_type_block 切割连通分量。

        从每个 NPC（actor）出发 BFS，zone 节点设 same_type_block=true →
        跨 zone 不穿透，每个 zone 自成一个分量。
        无 zone 的 NPC 归为独立分量。

        Returns:
            list[TopoComponent]: 按 zone 顺序排列
        """
        from collections import deque

        # 收集所有 NPC
        npc_eids = set()
        for eid, ent in self._entities.items():
            if has_role(ent.type_id, "actor"):
                npc_eids.add(eid)

        all_npcs = set(npc_eids)
        components: list[TopoComponent] = []

        while all_npcs:
            start = next(iter(all_npcs))
            queue: deque[str] = deque([start])
            visited: set[str] = set()
            comp_eids: set[str] = set()

            while queue:
                cur = queue.popleft()
                if cur in visited:
                    continue
                visited.add(cur)
                comp_eids.add(cur)
                cur_ent = self._entities.get(cur)
                if not cur_ent:
                    continue
                for conn_eid in cur_ent.connected_entity_ids:
                    if conn_eid in visited:
                        continue
                    conn_ent = self._entities.get(conn_eid)
                    if not conn_ent:
                        continue
                    # Leaf（terminal）：加入但不扩展
                    if conn_ent.is_leaf:
                        comp_eids.add(conn_eid)
                        continue
                    # 同类型阻断：加入但不穿透（zone→zone）
                    if conn_ent.no_same_type and conn_ent.type_id == cur_ent.type_id:
                        comp_eids.add(conn_eid)
                        continue
                    queue.append(conn_eid)

            # 从 visited 中提取 NPC
            comp_npcs = {e for e in comp_eids if e in npc_eids}

            # 找 zone
            zone_eid = None
            for eid in comp_eids:
                ent = self._entities.get(eid)
                if ent and has_role(ent.type_id, "region"):
                    zone_eid = eid
                    break

            # 构建 label_map
            _, label_map = self.build_tagged_topology(list(comp_eids))

            comp = TopoComponent(
                id=len(components),
                eids=comp_eids,
                npc_eids=comp_npcs,
                zone_eid=zone_eid,
                label_map=label_map,
            )
            components.append(comp)
            all_npcs -= comp_npcs

        return components


# ─── 数据类 ───

from dataclasses import field
from typing import Optional


class TopoComponent:
    """连通分量，表示一个独立子图。"""

    def __init__(
        self,
        id: int = 0,
        eids: set[str] | None = None,
        npc_eids: set[str] | None = None,
        zone_eid: str | None = None,
        label_map: dict[str, str] | None = None,
    ):
        self.id = id
        self.eids: set[str] = eids or set()
        self.npc_eids: set[str] = npc_eids or set()
        self.zone_eid: str | None = zone_eid
        self.label_map: dict[str, str] = label_map or {}

        # 运行时填充（由 orchestrator 写入）
        self.exec_results: list[dict] = []
        self.stories: list[str] = []
        self.topo_ops: list[dict] = []
        self.attr_ops: list[dict] = []
        self.recent_info: dict[str, str] = field(default_factory=dict)
        self.failures: list = []


# ─── 辅助函数 ───

def _extract_name_from_eid(eid: str) -> str:
    """从 entity_id 提取可读名称"""
    if "_" in eid:
        parts = eid.split("_", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
    return eid


def _infer_type(eid: str) -> str:
    """从 entity_id 推断实体类型字符串（内容层），仅供显示"""
    tid = prefix_to_type_id(eid)
    if tid:
        tdef = get_type_def(tid)  # type: ignore
        return tdef.get("id", "") if tdef else ""
    return ""


def _is_item_type(eid: str) -> bool:
    """判断实体 ID 是否为物品类型（守恒量候选）"""
    tid = prefix_to_type_id(eid)
    if not tid:
        return False
    return has_role(tid, "thing")


def build_label_mapping_text(
    label_map: dict[str, str],
    graph_engine=None,
    *,
    include_tags: bool = False,
    include_type: bool = False,
) -> str:
    """
    从 label_map 渲染标准化的标签映射文本。

    输出格式：{A} = 可读名称 → entity_id [type] (tag)
    include_tags=True 时追加 conserved/terminal 标记
    include_type=True 时追加 type 信息

    所有 LLM prompt 的映射表由此一处生成，换映射格式只改这里。
    标签按 node_config.json 中 label_mappings.labels[] 的顺序排序。
    """
    from ..config.config_loader import get_all_label_mappings
    config_order = get_all_label_mappings()  # {name → label}

    # 按 config 顺序排序，不在 config 中的按字母序
    def sort_key(item):
        label, eid = item
        ent = graph_engine.get_entity(eid) if graph_engine else None
        name = ent.name if ent else ""
        order = list(config_order.keys()).index(name) if name in config_order else 999
        return (order, label)

    lines = []
    for label, eid in sorted(label_map.items(), key=sort_key):
        name = "?"
        if graph_engine:
            ent = graph_engine.get_entity(eid)
            if ent:
                name = ent.name
        else:
            name = _extract_name_from_eid(eid)

        parts = [f"  {{{label}}} = {name}"]
        parts.append(f"  → {eid}")

        if include_type and graph_engine:
            ent = graph_engine.get_entity(eid)
            if ent and ent.entity_type:
                parts.append(f"  [{ent.entity_type}]")

        if include_tags and graph_engine:
            tags = []
            if graph_engine.is_conserved(eid):
                tags.append("conserved")
            if graph_engine.is_terminal(eid):
                tags.append("terminal")
            if tags:
                parts.append(f"  ({', '.join(tags)})")

        lines.append("".join(parts))

    return "\n".join(lines)

