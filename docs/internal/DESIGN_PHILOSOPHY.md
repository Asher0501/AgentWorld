# RISC-like Entity Minimalization Axiom

## Principle

In AgentWorld's interactive simulation, when the LLM deduces the existence of a new
entity during its reasoning (e.g., referencing an item not yet registered in the
world), the engine follows a **3-step minimalization axiom**, inspired by RISC
philosophy: introduce no entity unless truly irreducible.

## The 3-Step Algorithm

### Step 1: Derive via Process Chain

Try to find an existing **recipe / process chain** that produces the target entity
from entities that already exist in the world.

- Example: "面包" (bread) is referenced but does not exist.
  RecipeRegistry has `烘焙面包: 小麦x2 → 面包x1`.
  "小麦" exists in the world → register "面包" by deriving from the chain.

If all inputs to the chain already exist, **no new basic entities are created**.

### Step 2: Recursive Decomposition

If the inputs to the chain themselves do not exist, recurse on each input.

- Example: "面粉" is referenced, chain `碾磨面粉: 小麦x3 → 面粉x2` exists.
  Recursively check "小麦" → it exists. Register "面粉" via the chain.
- Example: "衣物" is referenced, chain `加工皮毛: 皮毛x2 → 衣物x1` exists.
  Recursively check "皮毛" → does "皮毛" exist? If not, recurse further.

The recursion bottoms out at **basic entities** (Step 3) or existing entities.

### Step 3: Register as Irreducible Basic

If no process chain can produce the entity (or its inputs cannot be recursively
resolved), register it as a **basic entity**. Basic entities are the world's
irreducible primitives — they cannot be manufactured via any known process.

**Default basic entities:**
- 水 (water)
- 石头 (stone)
- 木材 (wood)
- 铁矿石 (iron ore)
- 砂 (sand)
- 陶土 (clay)
- 种子 (seeds)
- 野果 (wild fruit)

These entities exist from the beginning and are not produced by any recipe.

### Step 0 (Implicit): Already Exists?

If the entity already exists in the world graph, do nothing.

## Design Rationale

### Why "RISC-like"?

RISC (Reduced Instruction Set Computer) minimizes the instruction set to the
bare essentials — everything else is composed from those primitives through
well-defined sequences. Similarly, the entity model reduces the world's entity
set to:

1. **Basic primitives** — irreducible materials/items that cannot be made.
2. **Derived entities** — everything else produced by process chains from basics.

This eliminates redundancy (no two ways to represent the same "product entity"),
forces explicit modeling of production chains, and makes the world self-evident:
given the primitives and recipes, the full entity space is determined.

### Traceability

Every entity created through this axiom carries a derivation log:
- Direct registration (already existed)
- Chain-derived (which chain, what inputs)
- Basic-registered (irreducible)

This traceability is essential for debugging LLM behavior and understanding how
the world evolved during simulation.

---

# Energy Conservation Principle (First Principle)

## Statement

AgentWorld is a **closed material system** with open environmental boundaries.
Every inventory quantity change must be explainable by one of four interaction
types. If none applies, the change is a system error.

This is not a numeric zero-sum check — it's a **type-based validation**:
wheat −2 and beer +1 is not a numeric match, but if a recipe says
"2 wheat + 1 vegetable → 2 beer", the change is **explainable** and
thus valid.

## The Four Valid Interaction Types

### Type 1: Transfer (NPC ↔ NPC, same item)

An item moves from one NPC to another without transformation:

```
老张: 小麦 −5
王老板: 小麦 +5
```

**Validation rule:** For the same `item_name`, Σ(delta) across all NPCs = 0.
**Marking in updates:** `"type": "transfer"` or automatically detected when
same item has both + and - entries.

### Type 2: Craft (Recipe Execution)

Items are consumed as recipe inputs; other items are produced as outputs:

```
田嫂: 小麦 −2, 蔬菜 −1, 酒 +2
```

**Validation rule:** The ratio must match a known recipe in RecipeRegistry:
- Inputs: {小麦: 2, 蔬菜: 1}
- Outputs: {酒: 2}
- Match found → ✅ valid transformation
**Marking in updates:** `"type": "craft"` with `"recipe": "酿酒"`.

If no matching recipe exists, the validator issues a **soft warning** —
the NPC might have discovered a new recipe that hasn't been registered yet.

### Type 3: Consume (Item → Attribute)

An item is consumed to affect NPC's own attributes (vitality, hunger, mood):

```
老张: 酒 −1
老张: mood +20, vitality +5
```

**Validation rule:** The item disappears from the system (no compensation).
The attribute changes are recorded separately — validator only checks that
the item's disappearance is marked as `"type": "consume"`.
**Marking in updates:** `"type": "consume"`.

### Type 4: Gather (Open Environmental Boundary)

An item enters the system from the environment without compensation:

```
猎户: 肉 +5
```

**Validation rule:** The item appears from nowhere — only allowed if marked
as `"type": "gather"` and the NPC is in an appropriate zone (forest, mine).
**Marking in updates:** `"type": "gather"` with optional `"source": "环境"`.

### Catch-All: Unknown Change

```
田嫂: 蔬菜 −2, 王老板: 金币 +10  (no recipe, no consume tag)
```

**Validation rule:** No known type matches → **hard violation.** The change
is rolled back and a warning is logged. This catches LLM hallucination where
it invents quantity changes that have no physical or environmental basis.

## Validator Architecture

The ConservationValidator checks inventory changes by interaction type:

```
PostProcessor output → updates[]
    ↓
Validator(type="transfer"): CHECK Σ(delta) per item_name = 0
Validator(type="craft"):    CHECK matches known recipe ratios
Validator(type="consume"):  CHECK marked as consume (always passes)
Validator(type="gather"):   CHECK marked as gather (always passes)
Validator(type="unknown"):  HARD VIOLATION → rollback + alarm
```

**Why `type` must be explicit in PostProcessor output:**
The LLM #3 PostProcessor already knows what type of interaction it's
processing — it saw the LLM #1 decision and the execution result. It should
simply tag each `inventory_changes` entry with its interaction type.
The validator doesn't guess; it checks based on what the LLM declared.

### Transfer Validation

```
def validate_transfers(updates):
    items = {}
    for u in updates:
        for ic in u.get("inventory_changes", []):
            if ic.get("type") == "transfer":
                delta = ic["quantity"]
                delta = -delta if ic["action"] == "remove" else delta
                items[ic["item_name"]] = items.get(ic["item_name"], 0) + delta
    return all(abs(v) < 0.001 for v in items.values())
```

### Craft Validation

```
def validate_craft(updates):
    inputs, outputs = {}, {}
    recipe_name = ""
    for u in updates:
        for ic in u.get("inventory_changes", []):
            if ic.get("type") == "craft":
                recipe_name = ic.get("recipe", recipe_name)
                if ic["action"] == "remove":
                    inputs[ic["item_name"]] = inputs.get(ic["item_name"], 0) + ic["quantity"]
                else:
                    outputs[ic["item_name"]] = outputs.get(ic["item_name"], 0) + ic["quantity"]
    recipe = registry.get_by_name(recipe_name)
    if not recipe: return ("SOFT", "未知配方")
    return ("PASS", "") if recipe.inputs == inputs and recipe.outputs == outputs \
           else ("HARD", f"配方 {recipe_name} 比例不匹配")
```

## Architectural Implications

### What Each Layer Knows About Conservation

| Layer | Knows | Doesn't Know |
|---|---|---|
| **GraphEngine** | edge topology + qty | Conservation entirely |
| **IntentExecutor** | Topology changes only | Quantity, conservation — zero |
| **PostProcessor** | Full NPC states + decision | Must output `type` tags |
| **ConservationValidator** | Validation rules per type | Game mechanics, economy |

### The Three-LLM Pipeline

```
LLM #1: "我想把小麦卖给王老板换金币"  → 自然语言
LLM #2: {interact_with: [...]}       → 只关心交互对象
IntentExecutor: connect(老张↔王老板)  → 拓扑操作
LLM #3: [...] + type="transfer"     → 数据层 + 类型标记
Validator: transfer→Σ小麦=0 ✅        → 纯校验
```

## Design Decisions

### 1. PostProcessor Must Tag Types

**Validator does not infer types.** PostProcessor declares them.
This keeps the validator deterministic and pushes reasoning to LLM #3.

### 2. `allow_npcs` Dedup Policy

One PostProcessor generates BOTH sides of a transfer. `allow_npcs` allows
both halves to be applied.

### 3. Topology vs. Quantity vs. Validation

Three separate concerns, three separate components.

### 4. Market Value: Future Module

Energy = physical truth. Market = economic truth. Validator never
references market prices.

## Integration Points

| Component | Role |
|---|---|
| `DerivationRegistry` | Basic entities + process chains |
| `RecipeRegistry` | Recipes as process chains |
| `EntityDerivationEngine` | 3-step derivation bridge |
| `GraphEngine._auto_register_missing` | Entry: LLM deduces unknown entity |
| `ConservationValidator` | Validates PostProcessor output by type |

## Example Derivation Flows

```
[LLM references "面包"]
  1. Already exists? No
  2. Chain? 烘焙面包: 小麦x2 → 面包x1
  3. Input "小麦" exists? Yes
  4. → Register entity "item_面包", register chain "烘焙面包"
```

```
[LLM references "绸缎"]
  1. Already exists? No
  2. Chain? None found
  3. → Register "item_绸缎" as new basic entity
```

---

# Topology–Content Decoupling Axiom

## Principle

The engine must not contain any code that branches on semantic entity types.

```python
# ❌ Forbidden pattern — engine knows about "NPC" and "zone":
if entity_type == "zone": ...
if e.edge_type == "npc_npc": ...

# ✅ Allowed — engine consults a declarative config by type_id:
if NODE_ONTOLOGY[ent.type_id].get("terminal"): ...
```

**Topology layer** (traversal, grouping, connectivity) does not know the
semantic meaning of any node type. It only reads numeric type IDs and a
configuration table that describes how each type behaves.

**Content layer** (names, attributes, memories, descriptions) carries all
semantic information and is consumed exclusively by LLMs and prompts.

The boundary is absolute: no `if entity_type == "npc"` anywhere in the
engine. Any domain-specific behavior must be declared as configuration,
not coded as branching logic.

## Why This Exists

The original architecture had typed edges (interface-based), semantic
string comparisons, and hardcoded social rules. Every new feature —
zones, items, objects, NPC interactions — required adding new `if type ==`
branches in traversal code, prompt builders, and validators.

This violated the separation between "how the graph works" and "what
the graph means." The solution was to push all semantic knowledge into
the content layer (node self-description) and the configuration layer
(traversal rules), leaving the engine semantically blind.

## The Metaphor: CPU vs. Operating System

| Role | Component | Knows |
|------|-----------|-------|
| CPU (execution) | GraphEngine / BFS / Validator | Identifies by number only |
| OS (configuration) | config/node_ontology.py | Maps numbers to behavior |
| Application (content) | Entity attributes / LLM prompts | All semantic meaning |

Just as a CPU doesn't know whether a register holds an integer, a pointer,
or a string — it only executes instructions — the topology engine doesn't
know whether type_id 3 represents a zone, a region, or a planet. It only
knows: "type_id 3 has same_type_block = true → stop when encountering
another type_id 3."

## Node Identity

```
identity = type_id (numeric) + instance_id (unique)
  → t1_a7b3c2d1  (type 1, instance a7b3c2d1)
  → t3_market    (type 3, instance "market")
```

- `type_id` is a pure number with no string label in engine scope.
- `instance_id` is a unique identifier for the specific node.
- The name (e.g., "market", "老陈", "小麦") is a **content-layer field**
  carried by the node for LLM consumption. The topology engine never
  inspects it.

## Traversal Configuration (Ontology Table)

The single source of truth for how node types behave during traversal:

```python
# config/node_ontology.py
NODE_ONTOLOGY = {
    1: {"terminal": True},              # traversal stops here (was: item)
    2: {"terminal": True},              # (was: object)
    3: {"same_type_block": True},       # don't cross type-3↔type-3 edges (was: zone)
    4: {},                               # default — full traversal (was: npc)
}
```

| Switch | Effect |
|--------|--------|
| `terminal: true` | BFS records this node but does not expand its neighbors |
| `same_type_block: true` | BFS does not cross edges to nodes of the same type_id |
| (both false) | BFS traverses freely — this is the default |

**Switches are all off by default.** A type not listed in the table gets
no special behavior. This ensures extensibility: new types automatically
behave like "full traversal" without code changes.

## Application Beyond Traversal

The ontology table is extensible and should eventually grow beyond
pure traversal switches. Any layer that currently branches on semantic
strings should be refactored to read configuration:

```python
NODE_ONTOLOGY = {
    1: {
        "terminal": True,
        "prompt": {"category": "物品", "order": 3, "fields": ["name"]},
    },
    3: {
        "same_type_block": True,
        "prompt": {"category": "场所", "order": 1, "fields": ["name", "description"]},
        "properties": {"has_description": True},
    },
    4: {
        "prompt": {"category": "角色", "order": 2,
                    "fields": ["name", "role", "mood", "satiety", "memories", "traits", "intent"]},
        "properties": {"has_attributes": True, "has_inventory": True, "has_memory": True},
    },
}
```

## Implications for Existing Code

Patterns that must be eliminated:

| File | Current Pattern | Target |
|------|----------------|--------|
| `interaction_layer.py` | `if e.edge_type == "npc_npc"` | Read from ontology by type_id |
| `graph_adapter.py` | String-based `type` checks | Use type_id throughout |
| `post_processor.py` | `op.get("type") == "craft"` | Operation types as numbers |
| `graph_npc_engine.py` | Entity ID prefix inference (`npc_`/`zone_`) | Replace with type_id lookup |
| `base_entity.py` | `self.is_leaf` hardcoded by entity_type string | Read from ontology |
| `graph_engine.py` | `_infer_type` uses string prefixes | Remove, replace with type_id |

## The Full Vision: Configuration-Driven World

The ontology table is the seed of a broader architecture where the engine
is a pure, domain-agnostic rule engine, and the entire game world is
described by configuration:

```
agent_world/
  core/                  ← Domain-agnostic engine
    traversal.py          (reads NODE_ONTOLOGY for traversal rules)
    prompt_builder.py     (reads NODE_ONTOLOGY for presentation)
    validator.py          (reads VALIDATION_RULES for conservation)

  config/                ← Declarative world definition
    node_ontology.py      (type IDs + traversal + prompt rules)
    world_layout.py       (initial topology, items, NPCs, zones)
    recipes.py            (process chains)

  content/               ← LLM-facing semantic layer
    npc_descriptions.py
    zone_descriptions.py
    item_definitions.py
```

This design allows a different game world to be created by swapping
configuration files — no engine code changes required.




---

# Prompt Topology–Content Decoupling

## Principle

The same topology–content decoupling that governs the engine also governs
prompts. A prompt's topological constraints and its content instructions
must be strictly separated, with zero cross-reference between them.

The engine does not know what a node semantically represents — it only
sees type_ids and configuration tags. The prompt's topological section
follows the same rule: it references only tags and structural rules,
never entity semantics. The content section has the opposite boundary:
it carries all semantic information but never references topological rules.

Separation must be strict. A topological constraint that mentions a
specific entity name or type is a violation — no different from writing
`if type == "npc"` in the engine.

## Why This Matters

This is the same architectural boundary, projected onto the prompt layer.

## Determining Which LLM Gets Topology

The decision of whether a given LLM sees topology information follows one
criterion: **does the LLM's output directly operate on the graph?**

| Layer | Output | Operates on graph? | Needs topology? |
|-------|--------|--------------------|-----------------|
| LLM #1 | Natural language plan (text) | No — plan is read by next layer | No |
| LLM #2 | `connect` / `disconnect` | **Yes** — changes graph structure | **Yes** |
| LLM #3 | Story (text) | No — story is read by next layer | No |
| LLM #4 | `delta` / `attr` (quantities) | **Yes** — changes edge quantities | **Yes** |

Abstracted to any domain:

- **LLMs that output text** (plans, stories, explanations, analyses) do not
  need topology information. Their output is consumed by another component
  (human, LLM, or processor), not by the graph engine. They only need
  content: names, states, attributes, relationships described in natural
  language.

- **LLMs that output graph operations** (structure changes, quantity
  adjustments, edge mutations) need topology information. Their output is
  consumed directly by the graph engine. They need to know the graph
  structure to produce valid operations.

In a protein network:

- An LLM deciding which proteins should form binding sites → operates on
  graph structure → **needs topology**.
- An LLM deciding signal molecule flow quantities along existing edges →
  operates on graph quantities → **needs topology**.
- An LLM describing a protein's own conformational change → outputs text →
  **needs only content**.
- An LLM narrating the entire signaling cascade → outputs text →
  **needs only content**.

**One rule: ignore what the LLM reads; look at where its output lands.**
If the output lands on the graph (edge structure or edge quantity), the
LLM needs topology. If the output lands outside the graph (text), it
does not.


## Prompt Ordering: Guard Clause Pattern

### Principle

Instructions in LLM prompts must be ordered like guard clauses in code:
the most specific, least probable case first; the most generic, most
probable case last.

```python
# ❌ Wrong — common case first, LLM short-circuits:
if has_counterpart: bilateral_delta()
elif from_system: system_delta()
elif is_recipe: recipe()

# ✅ Right — guard clauses first, generic fallback last:
if is_recipe: recipe()
elif from_system: system_delta()
# elif from_counterpart: bilateral_delta()  ← this is the implicit default
```

### Why This Matters

LLMs process prompts sequentially and exhibit a **recency bias toward
matching** — they tend to match the first instruction that fits the
situation, especially if it's a common pattern. If "use delta for
bilateral trades" appears first, the LLM will write delta for
everything, never reaching the system_delta or recipe cases.

By reversing the order — exceptions first, common case last — each
instruction acts as a gate: the LLM checks "is this a recipe?" before
falling through to "is this a bilateral trade?" This mirrors the
**happy path** pattern: exceptions are handled early, and the common
path is the implicit default.

### Rule of Thumb

Order prompt sections from:

```
1. Most constrained / smallest intersection  (finest granularity)
2. More constrained / medium intersection
3. Least constrained / default match         (coarsest granularity)
```

The last instruction should always be the general-purpose fallback
that covers the majority of cases. If the LLM reaches it, it has
already ruled out all the special cases.

### In This Project

The LLM #4 operation type guide follows this pattern:

```
1. recipe         — most specific (crafting, internal balance)
2. system_delta   — medium (item enters/leaves simulation boundary)
3. delta          — most common (bilateral trade, Σ=0)
```

This ensures the LLM considers "is this a recipe?" first, before
falling through to "is this a regular trade?"
