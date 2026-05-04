"""
Reconfigure world to 7 Witcher zones + 12 NPCs, then run 1 tick.
Usage: python3 run_7zone_12npc.py
"""
import sys, os, json, shutil, asyncio, logging, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

for mod in list(sys.modules.keys()):
    if "agent_world.config" in mod:
        del sys.modules[mod]

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S")
logger = logging.getLogger("7zone_run")

BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "src/agent_world/config")
NODE_JSON = os.path.join(CONFIG_DIR, "node_config.json")
DOMAIN_JSON = os.path.join(CONFIG_DIR, "domain.json")
BAK_DIR = os.path.join(BASE_DIR, ".config_bak")
IO_DIR = "/tmp/7zone_io"

# ════════════════════════════════════════════════
# 7-Zone configuration
# ════════════════════════════════════════════════

# Allowed zone_type values from Zone enum:
# village_square, market, tavern, farm, mine, forest, library, temple, barracks,
# outskirts, forge, alchemy_hut, herb_garden, old_temple, oxenfurt, harbor,
# kaer_morhen, white_orchard, market_square, fox_and_goose, the_forge, alchemist_hut, temple_ruins

ZONE_DEFS = [
    {
        "id": "white_orchard",
        "name": "白果园",
        "zone_type": "white_orchard",
        "capacity": 20,
        "connects_to": ["fox_and_goose", "novigrad", "kaer_morhen"],
        "description": "泰莫利亚北境的小村庄，战争留下的弹坑长出了野花，石板路上有马车经过。物产丰富，草药和食材充足，常有猎魔人歇脚。",
        "npcs": ["杰洛特", "托蜜拉"],
        "objects": [{"type": "WELL", "count": 1}, {"type": "TREE", "count": 3}],
    },
    {
        "id": "fox_and_goose",
        "name": "狐狸与鹅酒馆",
        "zone_type": "tavern",
        "capacity": 15,
        "connects_to": ["white_orchard"],
        "description": "白果园最热闹的酒馆，壁炉烧着柴火，空气中混合着麦酒和炖肉的香气。莎拉老板娘守着炉子，吟游诗人在角落里弹唱。",
        "npcs": ["莎拉", "丹德里恩"],
        "objects": [{"type": "STALL", "count": 1}, {"type": "BENCH", "count": 3}],
    },
    {
        "id": "novigrad",
        "name": "诺维格瑞",
        "zone_type": "market_square",
        "capacity": 30,
        "connects_to": ["white_orchard", "oxenfurt", "vizima", "skellige"],
        "description": "北方最大的自由城邦，港口繁忙，商贾云集。神殿区、商业街、矮人铁匠铺和地下黑市并存。金币是这里唯一的通用语言。",
        "npcs": ["卓尔坦", "哈托里", "乞丐王"],
        "objects": [{"type": "STALL", "count": 3}, {"type": "FORGE", "count": 1}, {"type": "FOUNTAIN", "count": 1}],
    },
    {
        "id": "oxenfurt",
        "name": "奥森弗特",
        "zone_type": "library",
        "capacity": 20,
        "connects_to": ["novigrad"],
        "description": "北方最古老的大学城，拥有庞大的图书馆和炼金实验室。女术士在此授课，学者研究古籍，战争虽然波及但知识从未断绝。",
        "npcs": ["特莉丝", "凯拉"],
        "objects": [{"type": "LIBRARY", "count": 1}, {"type": "LAB", "count": 1}],
    },
    {
        "id": "kaer_morhen",
        "name": "凯尔莫罕",
        "zone_type": "kaer_morhen",
        "capacity": 10,
        "connects_to": ["white_orchard"],
        "description": "群山之中的猎魔人要塞，古老的石墙历经数百年风雨。训练场、武器库和炼金工坊一应俱全，只有猎魔人和受训者才知晓前往这里的山路。",
        "npcs": ["维瑟米尔", "希里"],
        "objects": [{"type": "FORGE", "count": 1}, {"type": "TREE", "count": 2}],
    },
    {
        "id": "vizima",
        "name": "维吉玛",
        "zone_type": "temple",
        "capacity": 15,
        "connects_to": ["novigrad"],
        "description": "泰莫利亚王国的首都，宫殿巍峨，神殿区庄严。贵族们在庭院中谈论政治和魔法，皇家术士和外交使节穿梭其间。",
        "npcs": ["叶奈法"],
        "objects": [{"type": "FOUNTAIN", "count": 2}, {"type": "GARDEN", "count": 1}],
    },
    {
        "id": "skellige",
        "name": "史凯利格",
        "zone_type": "harbor",
        "capacity": 10,
        "connects_to": ["novigrad"],
        "description": "北海群岛的港口，战船桅杆如林，鱼市喧闹。史凯利格人以勇武著称，烈酒和武器是这里最受欢迎的商品。",
        "npcs": [],
        "objects": [{"type": "STALL", "count": 1}, {"type": "SHIP", "count": 2}],
    },
]

NPC_ZONE_MAP = {
    "杰洛特": "white_orchard",
    "托蜜拉": "white_orchard",
    "莎拉": "fox_and_goose",
    "丹德里恩": "fox_and_goose",
    "卓尔坦": "novigrad",
    "哈托里": "novigrad",
    "乞丐王": "novigrad",
    "特莉丝": "oxenfurt",
    "凯拉": "oxenfurt",
    "维瑟米尔": "kaer_morhen",
    "希里": "kaer_morhen",
    "叶奈法": "vizima",
}

def backup_configs():
    os.makedirs(BAK_DIR, exist_ok=True)
    shutil.copy2(NODE_JSON, os.path.join(BAK_DIR, "node_config.json.bak"))
    shutil.copy2(DOMAIN_JSON, os.path.join(BAK_DIR, "domain.json.bak"))
    logger.info("✅ Configs backed up")

def restore_configs():
    for fname in ["node_config.json.bak", "domain.json.bak"]:
        src = os.path.join(BAK_DIR, fname)
        dst = os.path.join(CONFIG_DIR, fname.replace(".bak", ""))
        if os.path.exists(src):
            shutil.copy2(src, dst)
    logger.info("✅ Configs restored")

def write_7zone_config():
    """Write node_config.json and domain.json with 7-zone config."""
    keep_npcs = {"杰洛特", "丹德里恩", "希里", "卓尔坦", "哈托里",
                  "特莉丝", "托蜜拉", "莎拉", "叶奈法", "凯拉",
                  "乞丐王", "维瑟米尔"}
    keep_zones = {z["id"] for z in ZONE_DEFS}
    keep_zone_names = {z["name"] for z in ZONE_DEFS}
    needed_items = {"草药", "魔药", "矿石", "武器", "魔法饰品", "法术书", "食物", "酒", "星辉石", "古籍残页", "金币"}

    # ── node_config.json ──
    with open(NODE_JSON, "r", encoding="utf-8") as f:
        nc = json.load(f)

    # Keep only 12 NPCs
    nc["entities"]["npcs"] = [n for n in nc["entities"]["npcs"] if n["name"] in keep_npcs]
    nc["entities"]["npc_sets"]["small"] = list(keep_npcs)
    nc["entities"]["npc_sets"]["default"] = list(keep_npcs)

    # Replace zone list + connections
    zcfgs = []
    for zd in ZONE_DEFS:
        zcfgs.append({
            "id": zd["id"],
            "name": zd["name"],
            "zone_type": zd["zone_type"],
            "capacity": zd["capacity"],
            "connects_to": zd["connects_to"],
            "description": zd["description"],
            "objects": zd.get("objects", []),
        })
    nc["entities"]["zones"] = zcfgs
    nc["world"]["zone_connections"] = [
        {"from": zd["id"], "to": zd["connects_to"]}
        for zd in ZONE_DEFS
    ]

    # Items: all needed
    nc["entities"]["items"] = [i for i in nc["entities"]["items"] if i["name"] in needed_items]

    # Objects: keep only those in the 7 zones
    nc["entities"]["objects"] = [o for o in nc["entities"]["objects"]
                                   if o.get("zone_id", "") in keep_zones
                                   or o.get("zone_name", "") in keep_zone_names]

    with open(NODE_JSON, "w", encoding="utf-8") as f:
        json.dump(nc, f, ensure_ascii=False, indent=2)

    # ── domain.json ──
    with open(DOMAIN_JSON, "r", encoding="utf-8") as f:
        dc = json.load(f)

    # Zone definitions
    zdomains = []
    for zd in ZONE_DEFS:
        zdomains.append({
            "id": zd["id"],
            "name": zd["name"],
            "zone_type": zd["zone_type"],
            "description": zd["description"],
        })
    dc["zones"] = zdomains

    # NPC initial zones
    dc["npc_initial_zones"] = NPC_ZONE_MAP

    with open(DOMAIN_JSON, "w", encoding="utf-8") as f:
        json.dump(dc, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ Config → 7 zones + 12 NPCs")
    for zd in ZONE_DEFS:
        name = zd["name"]
        npc_in_zone = [n for n, z in NPC_ZONE_MAP.items() if z == zd["id"]]
        connects = zd["connects_to"]
        logger.info(f"   🗺️ {name}: {', '.join(npc_in_zone)}  → {', '.join(connects)}")

def clear_db():
    from agent_world.db.db import get_db_path, init_db
    db_path = get_db_path()
    if os.path.exists(db_path):
        os.remove(db_path)
        logger.info(f"✅ DB cleared: {db_path}")
    init_db()

# ─── Trace saver with timing ───
def _install_trace_saver():
    import agent_world.services.interaction_resolver as ir
    _original_call_llm = ir.InteractionResolver._call_llm
    _counter = [0]
    _times = {}

    def _wrapped_call_llm(self, prompt: str) -> str:
        _counter[0] += 1
        idx = _counter[0]
        os.makedirs(IO_DIR, exist_ok=True)
        t0 = time.time()

        # Stage detection
        stage = f"LLM_{idx}"
        if "你的任务：根据每个角色（NPC）的自然语言计划" in prompt:
            stage = "LLM #2 topo_struct"
        elif "你是一个世界模拟引擎的故事叙事层" in prompt:
            stage = "LLM #3 story"
        elif "你是一个世界模拟引擎的**拓扑变化推理模块**" in prompt:
            stage = "LLM #4a topo_delta"
        elif "你是一个世界模拟引擎的**内容变化推理模块**" in prompt:
            stage = "LLM #4b attr"
        elif "你是一个世界模拟引擎的交互推理模块" in prompt and "每个 NPC 输出一条" in prompt:
            stage = "LLM #1 plans"
        elif "校验反馈" in prompt or "修正" in prompt:
            stage = f"LLM #x retry({idx})"
        else:
            stage = f"LLM #{idx}"

        with open(os.path.join(IO_DIR, f"{idx:02d}_{stage.replace(' ', '_')}_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)

        result = _original_call_llm(self, prompt)
        elapsed = time.time() - t0
        _times[idx] = elapsed

        with open(os.path.join(IO_DIR, f"{idx:02d}_{stage.replace(' ', '_')}_response.txt"), "w", encoding="utf-8") as f:
            f.write(result)

        logger.info(f"[{stage}] {elapsed:.0f}s  ({len(prompt)}c → {len(result)}c)")
        return result

    ir.InteractionResolver._call_llm = _wrapped_call_llm
    return _times

async def run_tick():
    from agent_world.services.graph_npc_engine import GraphNPCEngine

    times_collected = _install_trace_saver()
    engine = GraphNPCEngine(
        llm_available=True,
        llm_model="minimax/MiniMax-M2.7",
        llm_temperature=0.7,
        small=True,
        llm_callback=None,
    )

    logger.info(f"🚀 Running 1 tick with 7 zones + 12 NPCs → {IO_DIR}/")
    t_start = time.time()

    try:
        results = await engine.tick()
        total = time.time() - t_start

        # Print timing
        logger.info(f"\n{'='*60}")
        logger.info(f"  ⏱  TIMING REPORT: 7zone_12npc")
        logger.info(f"{'='*60}")
        for idx, elapsed in sorted(times_collected.items()):
            logger.info(f"  [{idx:2d}] {elapsed:6.1f}s")
        logger.info(f"  {'─'*50}")
        logger.info(f"  {'TOTAL':6s} {total:6.1f}s")
        logger.info(f"{'='*60}")

        return results
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return []

async def main():
    backup_configs()
    write_7zone_config()
    clear_db()
    results = await run_tick()
    logger.info(f"\n🎉 Done! IO saved to {IO_DIR}/")

    # Final world state
    from agent_world.db.db import get_session
    with get_session() as conn:
        rows = conn.execute("SELECT data FROM npcs").fetchall()
        rows = c.fetchall()
        logger.info(f"\n📊 World state ({len(rows)} NPCs):")
        for (data_json,) in rows:
            d = json.loads(data_json)
            zone = d.get("position", {}).get("zone_id", "?")
            zone_clean = zone.replace("zone_", "")
            logger.info(f"  {d['name']:10s} v={d['vitality']:.0f}/100 s={d['satiety']:.0f}/100 m={d['mood']:.0f}/100 @{zone_clean}")

    restore_configs()

if __name__ == "__main__":
    asyncio.run(main())
