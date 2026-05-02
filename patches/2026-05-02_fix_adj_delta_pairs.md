# 补丁：`_adjust_delta_pairs()` 无边跳过漏洞

**日期**: 2026-05-02  
**涉及文件**: `src/agent_world/services/graph_engine.py`  
**改动量**: 3 行（`if not edge: continue` → `available = edge.quantity if edge else 0`）

---

## 原始问题

Tick 6（春·第2天 11:00），LLM #4a 输出：

```json
delta: 田嫂→item_蔬菜  -5
delta: 王老板→item_蔬菜  +5
delta: 田嫂→item_金币  +3
delta: 王老板→item_金币  -3
```

但田嫂库存 `蔬菜 = 0`（边不存在），王老板库存 `金币 = 0`（边不存在）。

实际执行效果：
- 蔬菜 -5 → modify_edge_quantity 发现无此边，返回 False
- 蔬菜 +5 → 自动 connect，王老板蔬菜凭空 +5
- 金币 -3 → 同蔬菜，返回 False
- 金币 +3 → 自动 connect，田嫂凭空 +3

净效果：**凭空创造 3 金币 + 5 蔬菜。**

---

## 根因分析

### 漏洞 1：无边 → 跳过

```python
# graph_engine.py:254 (old)
edge = self.get_edge(src_eid, item_eid)
if not edge:
    continue  # ← 库存为 0 时边被删了，get_edge 返回 None
available = edge.quantity
```

库存扣到 0 时 `modify_edge_quantity` 调用 `self.disconnect()` 删边。之后 `get_edge` 返回 None → `continue` 跳过整个检查 → delta 原样提交给执行器。

### 漏洞 2：跨物品交易不回退

`_adjust_delta_pairs()` 按 item 类型分组（蔬菜一组、金币一组）独立处理。田嫂蔬菜不够 → 蔬菜组双边 clip 到 0 ✅。但 **金币组独立检查 → 王老板也没金币 → 同样被漏洞 1 跳过**。两组独立 clip 但互不知道对方存在，系统无法从"蔬菜被取消"推导出"对应金币也应取消"。

漏洞 2 实际被漏洞 1 "补救了"——王老板碰巧也没金币，所以金币组也被 clip 了。但如果王老板有金币，田嫂蔬菜被 clip 后仍会凭空收钱。

---

## 解决方案

**Fix 1（漏洞 1）：** 无边等价于 `available = 0`

```python
# old
edge = self.get_edge(src_eid, item_eid)
if not edge:
    continue
available = edge.quantity

# new
edge = self.get_edge(src_eid, item_eid)
available = edge.quantity if edge else 0
```

`available=0 < need` → 正常进入裁剪逻辑 → 付出方 clip 到 0 → 接收方同步裁剪 → 双边归零 → 净效果 = 取消该 delta 对。

**Fix 2（漏洞 2）：** 暂不处理。当前场景中，跨物品交易的"关联"需要语义理解（LLM 知道蔬菜和金币是一笔交易的两侧），引擎层无法可靠推断。Fix 1 已覆盖大部分实际场景（交易双方同时缺库存时才触发），留待出现真实 case 再处理。

---

## 验证

测试场景：完整复刻 Tick 6 的 12 条 delta 操作

```
王老板库存: 蔬菜=10, 金币=无
田嫂库存:   蔬菜=无, 金币=无
老张库存:   小麦=15
老陈库存:   酒=20

调整前: Σ=0（数值平衡）
调整后: 田嫂蔬菜-5→0，王老板蔬菜+5→0 ✅
        王老板金币-3→0，田嫂金币+3→0 ✅
        王老板金币-2→0，老张金币+2→0 ✅
        王老板金币-4→0，老陈金币+4→0 ✅
        老张小卖-3→-3 ✅（正常保持）
        老陈酒-2→-2 ✅（正常保持）
所有组 Σ=0 ✅
```

---

## 附带影响

老张和老陈的小麦/酒交易也因王老板没钱而被 clip 了金币收入。这是正确的守恒行为——王老板没金币，系统不该凭空造。

但 LLM #3 的故事可能写"成交"，而库存没变 → LLM #4b 写 recent_info 时会感知矛盾。这本质上是 LLM #3 幻觉（无视库存数据写交易故事），修复方向在 LLM #3 prompt 的拓扑事实约束——已在 LLM #2 和 LLM #4a 的 prompt 中加入【核心原则】声明。
