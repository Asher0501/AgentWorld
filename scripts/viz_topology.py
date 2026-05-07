#!/usr/bin/env python3
"""AgentWorld global topology v3 — clean CJK, spring layout, pro style."""
import sys, json, math, os, warnings
warnings.filterwarnings("ignore", category=UserWarning)  # suppress font warnings

sys.path.insert(0, os.path.expanduser("~/Documents/01_Projects/05_AgentWorld/src"))

snap = json.load(open("/tmp/full_tick/tick_008/snapshot_after.json"))
with open("src/agent_world/config/node_config.json") as f:
    cfg = json.load(f)
zone_links = cfg.get("connections", {}).get("zone", {})

zones = {}; npc_map = {}
for n in snap["npcs"]:
    name = n["name"]; zone = n.get("zone_name", "?")
    zones.setdefault(zone, []).append(name)
    inv = {}
    for item in n.get("inventory", []):
        inv[item["item"]] = inv.get(item["item"], 0) + item["qty"]
    attrs = n.get("attributes", {})
    if isinstance(attrs, str):
        try: attrs = json.loads(attrs)
        except: attrs = {}
    npc_map[name] = {"zone": zone, "inv": inv, "attrs": attrs}

NPC_NAMES = ["杰洛特","叶奈法","希里","特莉丝","维瑟米尔","丹德里恩","卓尔坦",
             "哈托里","凯拉","托蜜拉","莎拉","乞丐王","市集商贩"]
ZONE_NAMES = {
    "凯尔莫罕":"Kaer Morhen","诺维格瑞":"Novigrad","白果园":"White Orchard",
    "维吉玛":"Vizima","奥森弗特":"Oxenfurt",
    "狐狸与鹅酒馆":"Fox & Goose","史凯利格":"Skellige",
}

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import networkx as nx
import numpy as np

# ── Reg CJK font ──
fm.fontManager.addfont("/home/asher/.fonts/NotoSansCJKsc-Regular.otf")
FONT = fm.FontProperties(family='Noto Sans CJK SC', size=8)
FONT_BOLD = fm.FontProperties(family='Noto Sans CJK SC', size=10, weight='bold')
FONT_SM = fm.FontProperties(family='Noto Sans CJK SC', size=6)
FONT_XS = fm.FontProperties(family='Noto Sans CJK SC', size=5)
FONT_TITLE = fm.FontProperties(family='Noto Sans CJK SC', size=22, weight='bold')
FONT_MONO = fm.FontProperties(family='monospace', size=7)

# ── Build graph ──
G = nx.DiGraph()
zone_list = list(ZONE_NAMES.keys())
for z in zone_list:
    G.add_node(z, node_type="zone", npc_count=len(zones.get(z, [])))

npc_nodes = {}
for name in NPC_NAMES:
    if name not in npc_map: continue
    nd = npc_map[name]; zone = nd["zone"]
    if name not in zones.get(zone, []): continue
    nid = f"npc_{name}"
    npc_nodes[name] = nid
    G.add_node(nid, node_type="npc", name=name, zone=zone,
               vitality=nd["attrs"].get("vitality",0),
               satiety=nd["attrs"].get("satiety",0),
               mood=nd["attrs"].get("mood",0))
    G.add_edge(nid, zone, etype="in_zone")

item_nodes = {}; item_colors = {}
palette = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#16a085']; ic = 0
for name in NPC_NAMES:
    if name not in npc_map: continue
    nd = npc_map[name]
    for iname, qty in nd["inv"].items():
        if iname == nd["zone"] or qty <= 0: continue
        iid = f"item_{iname}"
        if iid not in item_nodes:
            item_nodes[iid] = iname
            G.add_node(iid, node_type="item", item_name=iname)
            item_colors[iid] = palette[ic % len(palette)]; ic += 1
        G.add_edge(npc_nodes[name], iid, etype="holds", qty=qty)

for u, v in [(min(z,c),max(z,c)) for z,cs in zone_links.items() for c in cs]:
    if u in ZONE_NAMES and v in ZONE_NAMES:
        G.add_edge(u, v, etype="connects"); G.add_edge(v, u, etype="connects")

delta_pairs = {('npc_卓尔坦','item_矿石'),('npc_哈托里','item_矿石'),
               ('npc_哈托里','item_金币'),('npc_卓尔坦','item_金币'),
               ('npc_杰洛特','item_金币'),('npc_莎拉','item_金币'),
               ('npc_莎拉','item_食物'),('npc_杰洛特','item_食物')}

# ── Layout ──
Gz = nx.Graph(); Gz.add_nodes_from(zone_list)
for u,v,_ in G.edges(data=True):
    if _['etype']=='connects': Gz.add_edge(u,v)
np.random.seed(42)
zp = nx.spring_layout(Gz, k=2.5, iterations=80, scale=3.2, seed=42)
zone_pos = dict(zp)

pos = dict(zone_pos)
for name, nid in npc_nodes.items():
    z = npc_map[name]["zone"]
    if z not in zone_pos: continue
    zx, zy = zone_pos[z]; idx = zones[z].index(name)
    angle = 2*math.pi*idx/len(zones[z]) - math.pi/2
    pos[nid] = (zx + 0.65*math.cos(angle), zy + 0.65*math.sin(angle))

center = np.mean(list(zone_pos.values()), axis=0)
for iid in item_nodes:
    holders = [(u, d['qty']) for u,v,d in G.edges(data=True) if v==iid and d.get('etype')=='holds']
    if not holders: continue
    tq = sum(q for _,q in holders) or 1
    ax = sum(pos[u][0]*q for u,q in holders)/tq
    ay = sum(pos[u][1]*q for u,q in holders)/tq
    off = np.array([ax, ay]) - center
    d = np.linalg.norm(off) or 1
    pos[iid] = (ax + off[0]/d*0.5, ay + off[1]/d*0.5)

# ── Render ──
fig = plt.figure(figsize=(22, 18), facecolor='#0d0d1a')
fig.suptitle("The Witcher World  —  Global Topology (tick_008)", fontsize=22,
             color='#e8dcc5', fontproperties=FONT_TITLE, y=0.965)

ax_main = fig.add_axes([0.22, 0.05, 0.56, 0.88])
ax_main.set_facecolor('#0d0d1a')
xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
x_pad = (max(xs)-min(xs))*0.18 or 0.5; y_pad = (max(ys)-min(ys))*0.18 or 0.5
ax_main.set_xlim(min(xs)-x_pad, max(xs)+x_pad)
ax_main.set_ylim(min(ys)-y_pad, max(ys)+y_pad)

# Edges
for u,v,d in G.edges(data=True):
    et = d.get('etype')
    if et == 'connects':
        ax_main.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color='#3a3a6a', lw=0.5, linestyle='dashed', connectionstyle='arc3,rad=0.12'))
    elif et == 'in_zone':
        ax_main.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color='#4a8a6a', lw=1.2, alpha=0.25))
    elif et == 'holds':
        qty = d.get('qty', 1); is_delta = (u,v) in delta_pairs
        col = '#d4a84b' if is_delta else '#4a6a8a'
        lw = 3.0 if is_delta else min(1.0+qty*0.3, 3)
        ax_main.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color=col, lw=lw, alpha=0.9 if is_delta else 0.3))

import matplotlib.patches as mpatches
# Zone nodes
for z in zone_list:
    x,y = pos[z]; nc = len(zones.get(z,[]))
    r = 0.22 + nc*0.02
    ax_main.add_patch(mpatches.Circle((x,y), r, fc='#1a2a4a', ec='#4a6a8a', lw=2, zorder=4, alpha=0.95))
    ax_main.text(x, y, f"{z}\n{ZONE_NAMES[z]}", ha='center', va='center', color='#d0c8b8',
                fontproperties=FONT_SM, zorder=5, linespacing=1.3)
    if nc > 0:
        ax_main.text(x+r*0.7, y+r*0.7, f"{nc}N", ha='center', va='center', color='#d4a84b',
                    fontsize=5, fontproperties=FONT_SM,
                    bbox=dict(boxstyle='circle,pad=0.1', fc='#0a0a1a', ec='#d4a84b', lw=0.8), zorder=6)

# NPC nodes
for name, nid in npc_nodes.items():
    if nid not in pos: continue
    x,y = pos[nid]; a = npc_map[name]["attrs"]
    v = a.get("vitality",0); s = a.get("satiety",0); m = a.get("mood",0)
    goal = a.get("primary_goal","")
    col = '#2d8a4e' if m>=80 else '#b8860b' if m>=50 else '#8b3030'
    tc = '#e8e0d0'
    dm = '*'*max(1, min(3, int(v/33)+1))
    ax_main.text(x, y, f"{name}\n{dm} V{v:.0f} S{s:.0f} M{m:.0f}", ha='center', va='center',
                color=tc, fontproperties=FONT_SM,
                bbox=dict(boxstyle='round,pad=0.25', fc=col, ec='#0d0d1a', lw=1.2, alpha=0.85), zorder=6)
    if goal:
        gs = goal[:10]+'..' if len(goal)>10 else goal
        ax_main.text(x, y-0.07, f"~ {gs}", ha='center', va='top', color='#d4a84b',
                    fontproperties=FONT_XS, zorder=7)

# Item nodes
for iid, iname in item_nodes.items():
    if iid not in pos: continue
    x,y = pos[iid]; col = item_colors.get(iid, '#4a6a8a')
    is_delta = iid in [f"item_{nm}" for nm in ["矿石","金币","食物"]]
    ec = '#d4a84b' if is_delta else '#2a3a5a'
    ax_main.text(x, y, iname, ha='center', va='center', color='#e8e0d0',
                fontproperties=FONT_XS,
                bbox=dict(boxstyle='round,pad=0.15', fc=col, ec=ec, lw=1.5 if is_delta else 0.5, alpha=0.7), zorder=5)
ax_main.axis('off')

# ── Left panel: stats ──
ax_l = fig.add_axes([0.01, 0.05, 0.20, 0.90]); ax_l.axis('off'); ax_l.set_facecolor('#0d0d1a')
ly = 0.92
t_topos = sum(1 for _ in G.edges(data=True) if _[2].get('etype') in ('in_zone','holds'))
t_ze = sum(1 for _ in G.edges(data=True) if _[2].get('etype')=='connects')

ax_l.text(0.05, ly, "WORLD STATISTICS", ha='left', va='top', color='#d4a84b', fontproperties=FONT_BOLD); ly -= 0.07
for label, val in [("Zones",len(zone_list)),("NPCs",len(NPC_NAMES)),("Items",len(item_nodes)),
                   ("Topo Links",t_topos),("Zone Roads",t_ze)]:
    ax_l.text(0.08, ly, f"  {label:12s}  {val:3d}", ha='left', va='top', color='#dcd8d0', fontproperties=FONT_MONO); ly -= 0.06

ly -= 0.03
ax_l.text(0.05, ly, "─ TICK 008 ─", ha='left', va='top', color='#6a7a8a', fontproperties=FONT_SM); ly -= 0.06
for label, val in [("Time","396.9s"),("LLM Calls","25"),("Topo Ops","8"),("Stories","7")]:
    ax_l.text(0.08, ly, f"  {label:12s}  {val:>6s}", ha='left', va='top', color='#b0a898', fontproperties=FONT_MONO); ly -= 0.05

ly -= 0.03
ax_l.text(0.05, ly, "─ MOOD ─", ha='left', va='top', color='#6a7a8a', fontproperties=FONT_SM); ly -= 0.055
for lbl, cnt, col in [("High 80+",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)>=80),'#2d8a4e'),
                       ("Mid 50-79",sum(1 for _,nid in npc_nodes.items() if 50<=npc_map[_]["attrs"].get("mood",0)<80),'#b8860b'),
                       ("Low <50",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)<50),'#8b3030')]:
    ax_l.text(0.08, ly, f"  {lbl:10s}  {cnt:2d}", ha='left', va='top', color=col, fontproperties=FONT_MONO); ly -= 0.05

ly -= 0.04
ax_l.text(0.05, ly, "─ LOWEST VIT ─", ha='left', va='top', color='#6a7a8a', fontproperties=FONT_SM); ly -= 0.055
worst = sorted([(n, npc_map[n]["attrs"].get("vitality",100)) for n in NPC_NAMES if n in npc_map], key=lambda x: x[1])[:6]
for n, v in worst:
    col = '#8b3030' if v<30 else '#b8860b' if v<50 else '#4a8a6a'
    ax_l.text(0.08, ly, f"  {n:8s}  V{v:.0f}", ha='left', va='top', color=col, fontproperties=FONT_MONO); ly -= 0.05

ly -= 0.03
ax_l.text(0.05, ly, "─ LEGEND ─", ha='left', va='top', color='#6a7a8a', fontproperties=FONT_SM); ly -= 0.05
for lbl, col in [("Zone",'#1a2a4a'),("NPC",'#4a5a4a'),("Item",'#4a6a8a'),("N->Zone",'#4a8a6a'),
                 ("Z Road",'#3a3a6a'),("Hold",'#4a6a8a'),("Delta",'#d4a84b')]:
    ax_l.text(0.08, ly, f"  {lbl}", ha='left', va='top', color=col, fontproperties=FONT_MONO); ly -= 0.04
ax_l.set_xlim(0,0.95); ax_l.set_ylim(0,1)

# ── Right panel: deltas ──
ax_r = fig.add_axes([0.79, 0.05, 0.20, 0.90]); ax_r.axis('off'); ax_r.set_facecolor('#0d0d1a')
ry = 0.92
ax_r.text(0.05, ry, "TICK 008 DELTAS", ha='left', va='top', color='#d4a84b', fontproperties=FONT_BOLD); ry -= 0.07

for title, ops in [("Ore Trade (Novigrad)", [("卓尔坦>矿石",-2),("哈托里>矿石",+2),("哈托里>金币",-2),("卓尔坦>金币",+2)]),
                   ("Food Trade (Tavern)", [("杰洛特>金币",-2),("莎拉>金币",+2),("莎拉>食物",-1),("杰洛特>食物",+1)])]:
    ax_r.text(0.05, ry, title, ha='left', va='top', color='#b0a898', fontproperties=FONT_SM); ry -= 0.055
    for desc, delta in ops:
        arr = "+" if delta>0 else ""; col = '#5a9a6a' if delta>0 else '#9a5a5a'
        ax_r.text(0.08, ry, f"  {desc:15s}  {arr}{delta:+d}", ha='left', va='top', color=col,
                fontproperties=FONT_MONO); ry -= 0.045
    ry -= 0.03

ry -= 0.02
ax_r.text(0.05, ry, "IMPACT", ha='left', va='top', color='#d4a84b', fontproperties=FONT_SM); ry -= 0.05
for imp in ["Hattori: ore x2","  Can forge legendary sword",
            "Zoltan: coins +2","  Closer to weapon fund",
            "Geralt: food +1","  Now in tavern zone"]:
    ax_r.text(0.08, ry, imp, ha='left', va='top', color='#8a9a8a', fontproperties=FONT_XS); ry -= 0.04

# Footer
fig.text(0.5, 0.02, "Data: tick_008 snapshot  |  AgentWorld GraphEngine  |  2026-05-07",
         ha='center', color='#4a4a6a', fontproperties=FONT_XS)

outpath = os.path.expanduser("~/Documents/01_Projects/05_AgentWorld/agentworld_topo_tick008.png")
plt.savefig(outpath, dpi=200, bbox_inches='tight', facecolor='#0d0d1a')
print(f"OK: {outpath}  ({len(G.nodes)} nodes, {len(G.edges)} edges)")
