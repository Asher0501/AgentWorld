#!/usr/bin/env python3
"""AgentWorld topology v5 — dense, minimal crossing, compact layout."""
import sys, json, math, os, warnings
warnings.filterwarnings("ignore")

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

fm.fontManager.addfont("/home/asher/.fonts/NotoSansCJKsc-Regular.otf")
F = lambda s=7: fm.FontProperties(family='Noto Sans CJK SC', size=s)

G = nx.DiGraph()
zone_list = list(ZONE_NAMES.keys())
for z in zone_list:
    G.add_node(z, node_type="zone", npc_count=len(zones.get(z, [])))

npc_nodes = {}
for name in NPC_NAMES:
    if name not in npc_map: continue
    nd = npc_map[name]; zone = nd["zone"]
    if name not in zones.get(zone, []): continue
    nid = f"N_{name}"
    npc_nodes[name] = nid
    G.add_node(nid, node_type="npc", name=name, zone=zone,
               vitality=nd["attrs"].get("vitality",0),
               satiety=nd["attrs"].get("satiety",0),
               mood=nd["attrs"].get("mood",0))
    G.add_edge(nid, zone, etype="in_zone", weight=3)

item_nodes = {}; item_colors = {}
palette = ['#ff6b6b','#3498db','#2ecc71','#f39c12','#a855f7','#14b8a6','#f97316','#06b6d4']; ic = 0
# Track owner per item for labeling
item_owners = {}
for name in NPC_NAMES:
    if name not in npc_map: continue
    nd = npc_map[name]
    for iname, qty in nd["inv"].items():
        if iname == nd["zone"] or qty <= 0: continue
        iid = f"I_{iname}"
        if iid not in item_nodes:
            item_nodes[iid] = iname
            G.add_node(iid, node_type="item", item_name=iname)
            item_colors[iid] = palette[ic % len(palette)]; ic += 1
        G.add_edge(npc_nodes[name], iid, etype="holds", qty=qty, weight=1+qty*0.5)
        item_owners.setdefault(iid, {})[name] = qty

delta_items = {"矿石","金币","食物"}

for u, v in [(min(z,c),max(z,c)) for z,cs in zone_links.items() for c in cs]:
    if u in ZONE_NAMES and v in ZONE_NAMES:
        G.add_edge(u, v, etype="connects", weight=1)

# ── Layout: Kamada–Kawai (minimizes crossings for planar-like graphs) ──
G_layout = nx.Graph()
# Build a graph for layout — connect zones to all reachable nodes, NPCs to zones, items to NPCs
for u,v,d in G.edges(data=True):
    w = d.get('weight',1)
    if G_layout.has_edge(u,v):
        G_layout[u][v]['weight'] = max(G_layout[u][v].get('weight',1), w)
    else:
        G_layout.add_edge(u,v,weight=w)

# Connect any disconnected zone nodes to nearest other zone by adding weak edges
# (Kamada-Kawai fails on disconnected graphs)
z_nodes = set(zone_list)
g_z = nx.Graph()
g_z.add_nodes_from(z_nodes)
for u,v,_ in G_layout.edges(data=True):
    if u in z_nodes and v in z_nodes:
        g_z.add_edge(u,v)
# Add weak edges between disconnected zone components
for i in range(len(zone_list)):
    for j in range(i+1, len(zone_list)):
        if not nx.has_path(g_z, zone_list[i], zone_list[j]):
            G_layout.add_edge(zone_list[i], zone_list[j], weight=0.05)
            g_z.add_edge(zone_list[i], zone_list[j])

np.random.seed(42)
pos_all = nx.spring_layout(G_layout, k=0.7, iterations=150, scale=1.2, seed=42)
pos = dict(pos_all)

# ── Figure ──
fig = plt.figure(figsize=(18, 12), facecolor='#0a0a16')
fig.suptitle("The Witcher World — Global Topology  tick 008", fontsize=16,
             color='#e8dcc5', fontproperties=F(14), y=0.977)

# Main graph area (takes most of the space)
ax_main = fig.add_axes([0.02, 0.01, 0.73, 0.93])
ax_main.set_facecolor('#0a0a16')
ax_main.axis('off')

xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
pad = 0.15
ax_main.set_xlim(min(xs)-pad, max(xs)+pad)
ax_main.set_ylim(min(ys)-pad, max(ys)+pad)

# ── Edges (draw zone zone roads first, then in-zone, then holds) ──
# First draw zone connections (background)
for u,v,d in G.edges(data=True):
    et = d.get('etype')
    if et == 'connects':
        ax_main.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='-', color='#4a4a8a', lw=0.8, alpha=0.4, connectionstyle='arc3,rad=0.08'))

# Then in_zone edges
for u,v,d in G.edges(data=True):
    if d.get('etype') == 'in_zone':
        ax_main.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color='#5a9a7a', lw=2.0, alpha=0.25))

# Then holds edges  (with delta highlight)
for u,v,d in G.edges(data=True):
    if d.get('etype') != 'holds': continue
    qty = d.get('qty',1)
    is_delta = any(di in v for di in delta_items)
    col = '#e8b84a' if is_delta else '#5a7aaa'
    lw = 4.0 if is_delta else 1.5 + qty*0.5
    ax_main.annotate("", xy=pos[v], xytext=pos[u],
        arrowprops=dict(arrowstyle='->', color=col, lw=lw, alpha=0.9 if is_delta else 0.5))
    # Edge label for quantity
    mx, my = (pos[u][0]+pos[v][0])/2, (pos[u][1]+pos[v][1])/2
    ax_main.text(mx, my, f"x{qty}", ha='center', va='bottom', color=col,
                 fontproperties=F(5), alpha=0.8, style='italic',
                 bbox=dict(boxstyle='round,pad=0.05', fc='#0a0a16', ec='none', alpha=0.7))

# Draw NPC-to-NPC same-zone edges (subtle)
npc_ids = {nid for nid in npc_nodes.values()}
for z in zone_list:
    z_npcs = [npc_nodes[n] for n in zones.get(z,[]) if n in npc_nodes]
    for i in range(len(z_npcs)):
        for j in range(i+1, len(z_npcs)):
            u, v = z_npcs[i], z_npcs[j]
            ax_main.annotate("", xy=pos[v], xytext=pos[u],
                arrowprops=dict(arrowstyle='-', color='#5a6a4a', lw=0.5, alpha=0.15, connectionstyle='arc3,rad=0.1'))

# ── Zone nodes ──
for z in zone_list:
    x,y = pos[z]; nc = len(zones.get(z,[]))
    r = 0.09 + nc*0.015
    # Glow rings
    for gr in [r+0.03, r+0.015]:
        ax_main.add_patch(plt.Circle((x,y), gr, fc='none', ec='#4a7aaa', lw=0.3, alpha=0.1))
    ax_main.add_patch(plt.Circle((x,y), r, fc='#14203a', ec='#5a8abb', lw=2.0, zorder=4, alpha=0.95))
    ax_main.text(x, y-0.02, f"{z}", ha='center', va='center', color='#ece4d0',
                 fontproperties=F(7), zorder=5)
    ax_main.text(x, y+0.02, ZONE_NAMES[z], ha='center', va='center', color='#7a9abb',
                 fontproperties=F(4.5), zorder=5, style='italic')
    if nc:
        ax_main.text(x+r*0.6, y+r*0.6, str(nc), ha='center', va='center',
                     color='#e8b84a', fontsize=4.5, fontproperties=F(4.5),
                     bbox=dict(boxstyle='circle,pad=0.08', fc='#0a0a16', ec='#e8b84a', lw=0.8), zorder=6)

# ── NPC nodes ──
for name, nid in npc_nodes.items():
    if nid not in pos: continue
    x,y = pos[nid]; a = npc_map[name]["attrs"]
    v = a.get("vitality",0); s = a.get("satiety",0); m = a.get("mood",0)
    goal = a.get("primary_goal","")
    col = '#2a8a4e' if m>=80 else '#b8860b' if m>=50 else '#8b3030'
    stars = '*'*max(1, min(3, int(v/33)+1))
    lines = [f"{name}  {stars}", f"V{v:.0f} S{s:.0f} M{m:.0f}"]
    if goal: lines.append(f"「{goal[:12]}」")
    txt = "\n".join(lines)
    ax_main.text(x, y, txt, ha='center', va='center', color='#ece4d0', fontproperties=F(5.5),
                bbox=dict(boxstyle='round,pad=0.2', fc=col, ec='#0a0a16', lw=1.5, alpha=0.9), zorder=6)

# ── Item nodes ──
for iid, iname in item_nodes.items():
    if iid not in pos: continue
    x,y = pos[iid]; col = item_colors.get(iid, '#3a5a7a')
    is_d = iname in delta_items
    ec = '#e8b84a' if is_d else '#3a5a7a'
    ax_main.text(x, y, iname, ha='center', va='center', color='#ece4d0', fontproperties=F(5.5),
                bbox=dict(boxstyle='round,pad=0.15', fc=col, ec=ec, lw=2.5 if is_d else 1.0, alpha=0.85), zorder=5)
    # Owner annotation
    owners = item_owners.get(iid, {})
    if owners:
        owner_str = "/".join(owners.keys())
        ax_main.text(x, y-0.035, f"← {owner_str}", ha='center', va='top', color='#7a8a8a',
                    fontproperties=F(4.5), zorder=7)

# ── RIGHT INFO PANEL ──
ax_r = fig.add_axes([0.77, 0.02, 0.22, 0.93]); ax_r.axis('off'); ax_r.set_facecolor('#0a0a16')

def pt(ax, yy, left, right="", color='#dcd8d0', sz=6.5, rc=None):
    fp = F(sz+1) if 'BOLD' in left else F(sz)
    lbl = left.replace("|BOLD|","")
    if right:
        ax.text(0.05, yy, lbl, ha='left', va='top', color=color, fontproperties=fp)
        ax.text(0.97, yy, right, ha='right', va='top', color=rc or color, fontproperties=F(sz))
    else:
        ax.text(0.05, yy, lbl if not left.startswith("|SEC|") else f"──  {left.replace('|SEC|','')}", 
                ha='left', va='top', color='#6a7a9a' if left.startswith("|SEC|") else color, fontproperties=F(sz-0.5 if left.startswith("|SEC|") else sz))

def sec(ax, yy, title):
    pt(ax, yy*0.998, f"|SEC|{title}", "", '#5a6a8a', 5.5)
    return yy - 0.028

y = 0.96
pt(ax_r, y, "|BOLD|TICK 008", "", '#c8a84b', 9); y -= 0.045
for l, r in [("Duration","396.9s"),("LLM Calls","25"),("Topo Ops","8"),("Stories","7")]:
    pt(ax_r, y, l, r, '#9a8a7a'); y -= 0.035

y = sec(ax_r, y, "DELTAS")
for title, ops in [
    ("Ore Trade",
     [("卓尔坦→矿石",-2),("哈托里→矿石",+2),("哈托里→金币",-2),("卓尔坦→金币",+2)]),
    ("Food Trade",
     [("杰洛特→金币",-2),("莎拉→金币",+2),("莎拉→食物",-1),("杰洛特→食物",+1)]),
]:
    pt(ax_r, y, f"▸{title}", "", '#8a7a6a', 5.5); y -= 0.035
    for desc, delta in ops:
        arr = "+" if delta>0 else ""; cl = '#3a9a5e' if delta>0 else '#9a5a5a'
        pt(ax_r, y, f"  {desc}", f"{arr}{delta:+d}", cl); y -= 0.032
    y -= 0.015

y = sec(ax_r, y, "CHAIN EFFECTS")
for note in ["哈托里 矿石x2 → 传奇剑","卓尔坦 金币+2 → 武资",
             "杰洛特 食物+1 → 补给","莎拉 金币+2 → 卖粮获利"]:
    pt(ax_r, y, f"→ {note}", "", '#7a8a7a', 5); y -= 0.032

y = sec(ax_r, y, "STATS")
for l, r in [("Nodes",37),("Edges",63),("Zones",len(zone_list)),("NPCs",len(NPC_NAMES)),("Items",len(item_nodes))]:
    pt(ax_r, y, l, str(r), '#8a8a7a'); y -= 0.032

y = sec(ax_r, y, "MOOD")
for lbl, cnt, cl in [("≥80",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)>=80),'#3a9a5e'),
                     ("50-79",sum(1 for _,nid in npc_nodes.items() if 50<=npc_map[_]["attrs"].get("mood",0)<80),'#b8860b'),
                     ("<50",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)<50),'#8b3030')]:
    pt(ax_r, y, f"  {lbl}", str(cnt), cl); y -= 0.030

y = sec(ax_r, y, "WORST VIT")
worst = sorted([(n, npc_map[n]["attrs"].get("vitality",100)) for n in NPC_NAMES if n in npc_map], key=lambda x: x[1])[:4]
for n, v in worst:
    pt(ax_r, y, n, f"V{v:.0f}", '#8b3030' if v<30 else '#b8860b' if v<50 else '#3a8a5e'); y -= 0.030

y = sec(ax_r, y, "LEGEND")
for l, cl in [("●Zone",'#5a8abb'),("■NPC",'#2a4a3a'),("▲Item",'#3a5a7a'),
              ("Edge types:",""),("→In Zone","#5a9a7a"),("→Holds","#5a7aaa"),("→Delta","#e8b84a")]:
    pt(ax_r, y, l, '', cl if cl else '#4a4a6a'); y -= 0.025

ax_r.set_xlim(0,1); ax_r.set_ylim(0,1)

fig.text(0.36, 0.005, "AgentWorld GraphEngine — 2026-05-07", ha='center',
         color='#3a3a5a', fontproperties=F(5))

outpath = os.path.expanduser("~/Documents/01_Projects/05_AgentWorld/agentworld_topo_tick008.png")
plt.savefig(outpath, dpi=240, bbox_inches='tight', facecolor='#0a0a16')
print(f"OK: {outpath}  ({len(G.nodes)} nodes, {len(G.edges)} edges)")
