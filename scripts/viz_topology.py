#!/usr/bin/env python3
"""AgentWorld topology v7 — HIGH DENSITY + BIG FONTS."""
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
CJK = lambda s=7: fm.FontProperties(family='Noto Sans CJK SC', size=s)

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

# ── Layout ──
G_layout = nx.Graph()
for u,v,d in G.edges(data=True):
    w = d.get('weight',1)
    if G_layout.has_edge(u,v):
        G_layout[u][v]['weight'] = max(G_layout[u][v].get('weight',1), w)
    else:
        G_layout.add_edge(u,v,weight=w)
z_nodes = set(zone_list)
g_z = nx.Graph(); g_z.add_nodes_from(z_nodes)
for u,v,_ in G_layout.edges(data=True):
    if u in z_nodes and v in z_nodes: g_z.add_edge(u,v)
for i in range(len(zone_list)):
    for j in range(i+1, len(zone_list)):
        if not nx.has_path(g_z, zone_list[i], zone_list[j]):
            G_layout.add_edge(zone_list[i], zone_list[j], weight=0.1)
            g_z.add_edge(zone_list[i], zone_list[j])
np.random.seed(42)
pos = nx.spring_layout(G_layout, k=0.2, iterations=200, scale=0.5, seed=42)

# ── Figure ──
fig = plt.figure(figsize=(14, 9), facecolor='#0a0a16')
fig.suptitle("The Witcher World — Global Topology  tick 008", fontsize=14,
             color='#e8dcc5', fontproperties=CJK(13), y=0.976)

# Main plot
ax_m = fig.add_axes([0.02, 0.02, 0.74, 0.92])
ax_m.set_facecolor('#0a0a16')
xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
pad = 0.06
ax_m.set_xlim(min(xs)-pad, max(xs)+pad)
ax_m.set_ylim(min(ys)-pad, max(ys)+pad)

# Background dots (in axes coords)
for xi in np.linspace(0.02, 0.98, 20):
    for yi in np.linspace(0.02, 0.98, 16):
        ax_m.text(xi, yi, "·", ha='center', va='center', color='#3a3a6a',
                 fontsize=3, alpha=0.2, zorder=0, transform=ax_m.transAxes)
ax_m.axis('off')

# ── Edges ──
for u,v,d in G.edges(data=True):
    et = d.get('etype')
    if et == 'connects':
        ax_m.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='-', color='#5a5a8a', lw=3, alpha=0.45, connectionstyle='arc3,rad=0.08'))
    elif et == 'in_zone':
        ax_m.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color='#6a9a7a', lw=3.5, alpha=0.30))
    elif et == 'holds':
        qty = d.get('qty',1)
        is_d = any(di in v for di in delta_items)
        col = '#e8b84a' if is_d else '#6a8abb'
        lw = 5.5 if is_d else 3 + qty*0.8
        ax_m.annotate("", xy=pos[v], xytext=pos[u],
            arrowprops=dict(arrowstyle='->', color=col, lw=lw, alpha=0.95 if is_d else 0.5))
        mx, my = (pos[u][0]+pos[v][0])/2, (pos[u][1]+pos[v][1])/2
        ax_m.text(mx, my, f"×{qty}", ha='center', va='bottom', color=col,
                 fontproperties=CJK(6), alpha=0.9, fontweight='bold')

# Same-zone NPC links
z_ids = set(npc_nodes.values())
for z in zone_list:
    zns = [npc_nodes[n] for n in zones.get(z,[]) if n in npc_nodes]
    for i in range(len(zns)):
        for j in range(i+1, len(zns)):
            ax_m.annotate("", xy=pos[zns[j]], xytext=pos[zns[i]],
                arrowprops=dict(arrowstyle='-', color='#5a6a4a', lw=1.5, alpha=0.18))

# ── Zone nodes ──
for z in zone_list:
    x,y = pos[z]; nc = len(zones.get(z,[]))
    r = 0.15 + nc*0.015
    for gr in [r+0.03, r+0.015]:
        ax_m.add_patch(plt.Circle((x,y), gr, fc='none', ec='#5a8abb', lw=0.5, alpha=0.10))
    ax_m.add_patch(plt.Circle((x,y), r, fc='#1a2a4a', ec='#6a9abb', lw=2.5, zorder=4, alpha=0.95))
    ax_m.text(x, y-0.04, f"{z}", ha='center', va='center', color='#ece4d0',
             fontproperties=CJK(7.5), zorder=5)
    ax_m.text(x, y+0.04, ZONE_NAMES[z], ha='center', va='center', color='#8aabbb',
             fontproperties=CJK(5.5), zorder=5, style='italic')
    if nc:
        ax_m.text(x+r*0.45, y+r*0.45, f"{nc}N", ha='center', va='center',
                 color='#e8b84a', fontproperties=CJK(6),
                 bbox=dict(boxstyle='circle,pad=0.12', fc='#0a0a16', ec='#e8b84a', lw=1.5), zorder=6)

# ── NPC nodes ──
for name, nid in npc_nodes.items():
    if nid not in pos: continue
    x,y = pos[nid]; a = npc_map[name]["attrs"]
    v = a.get("vitality",0); s = a.get("satiety",0); m = a.get("mood",0)
    goal = a.get("primary_goal","")
    mood_col = '#2a8a4e' if m>=80 else '#b8860b' if m>=50 else '#8b3030'
    bar = "█"*max(1, int(m/20)) + "░"*(5-max(1,int(m/20)))
    stars = "★"*max(1, min(3, int(v/33)+1))
    lines = [f"{name}  {stars}", f"V{v:.0f}  S{s:.0f}  M{m:.0f}", f"{bar}"]
    if goal: lines.append(f"🎯{goal[:12]}")
    txt = "\n".join(lines)
    ax_m.text(x, y, txt, ha='center', va='center', color='#ece4d0',
             fontproperties=CJK(6),
             bbox=dict(boxstyle='round,pad=0.25', fc=mood_col, ec='#0a0a16', lw=1.5, alpha=0.92), zorder=6)

# ── Item nodes ──
for iid, iname in item_nodes.items():
    if iid not in pos: continue
    x,y = pos[iid]; col = item_colors.get(iid, '#3a5a7a')
    is_d = iname in delta_items
    ec = '#e8b84a' if is_d else '#4a5a7a'
    ax_m.text(x, y, f"◈ {iname}", ha='center', va='center', color='#ece4d0',
             fontproperties=CJK(6.5),
             bbox=dict(boxstyle='round,pad=0.18', fc=col, ec=ec, lw=2.5 if is_d else 1.5, alpha=0.92), zorder=5)
    owners = item_owners.get(iid, {})
    if owners:
        ow = "/".join(owners)
        ax_m.text(x, y-0.04, f"◀ {ow}", ha='center', va='top', color='#9aab9a',
                 fontproperties=CJK(4.5), zorder=7)

# ── RIGHT PANEL ──
ax_r = fig.add_axes([0.79, 0.01, 0.20, 0.93]); ax_r.axis('off'); ax_r.set_facecolor('#0a0a16')

def pt(ax, y, left, right="", color='#dcd8d0', sz=8, rc=None):
    fp = CJK(sz)
    if right:
        ax.text(0.04, y, left, ha='left', va='top', color=color, fontproperties=fp)
        ax.text(0.96, y, right, ha='right', va='top', color=rc or color, fontproperties=fp)
    else:
        ax.text(0.04, y, left, ha='left', va='top', color=color, fontproperties=fp)

def ps(ax, y, title):
    ax.text(0.04, y-0.003, f"── {title}", ha='left', va='top', color='#5a6a8a', fontproperties=CJK(6))
    return y - 0.026

y = 0.95
pt(ax_r, y, "TICK 008", "", '#c8a84b', 10); y -= 0.042
for l, r in [("Duration","396.9s"),("LLM Calls","25"),("Topo Ops","8"),("Stories","7")]:
    pt(ax_r, y, l, r, '#9a8a7a', 8); y -= 0.030

y = ps(ax_r, y, "DELTAS")
for title, ops in [
    ("Ore Trade (Novigrad)",
     [("卓尔坦→矿石",-2),("哈托里→矿石",+2),("哈托里→金币",-2),("卓尔坦→金币",+2)]),
    ("Food Trade (Tavern)",
     [("杰洛特→金币",-2),("莎拉→金币",+2),("莎拉→食物",-1),("杰洛特→食物",+1)]),
]:
    pt(ax_r, y, f"▸{title}", "", '#8a7a6a', 7); y -= 0.032
    for desc, delta in ops:
        arr = "+" if delta>0 else ""; cl = '#3a9a5e' if delta>0 else '#9a5a5a'
        pt(ax_r, y, f" {desc}", f"{arr}{delta:+d}", cl, 7); y -= 0.030
    y -= 0.014

y = ps(ax_r, y, "CHAIN EFFECTS")
for note in ["哈托里 矿石x2 → 传奇剑","卓尔坦 金币+2 → 武资",
             "杰洛特 食物+1 → 补给","莎拉 金币+2 → 卖粮"]:
    pt(ax_r, y, f"→{note}", "", '#7a8a7a', 6.5); y -= 0.028

y = ps(ax_r, y, "STATS")
for l, r in [("Nodes",37),("Edges",63),("Zones",len(zone_list)),("NPCs",len(NPC_NAMES)),("Items",len(item_nodes))]:
    pt(ax_r, y, l, str(r), '#8a8a7a', 8); y -= 0.030

y = ps(ax_r, y, "MOOD")
for lbl, cnt, cl in [("≥80 High",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)>=80),'#3a9a5e'),
                     ("50-79 Mid",sum(1 for _,nid in npc_nodes.items() if 50<=npc_map[_]["attrs"].get("mood",0)<80),'#b8860b'),
                     ("<50 Low",sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)<50),'#8b3030')]:
    pt(ax_r, y, lbl, str(cnt), cl, 8); y -= 0.030

y = ps(ax_r, y, "WORST VITALITY")
worst = sorted([(n, npc_map[n]["attrs"].get("vitality",100)) for n in NPC_NAMES if n in npc_map], key=lambda x: x[1])[:4]
for n, v in worst:
    pt(ax_r, y, n, f"V{v:.0f}", '#8b3030' if v<30 else '#b8860b' if v<50 else '#3a8a5e', 8); y -= 0.030

y = ps(ax_r, y, "NPC PER ZONE")
for z, c in sorted([(z, len(zones.get(z,[]))) for z in zone_list], key=lambda x:-x[1]):
    pt(ax_r, y, z, f"{c}N", '#7a8a9a', 7.5); y -= 0.028

ax_r.set_xlim(0,1); ax_r.set_ylim(0,1)

fig.text(0.38, 0.002, "AgentWorld GraphEngine — 2026-05-07", ha='center',
         color='#3a3a5a', fontproperties=CJK(5.5))

outpath = os.path.expanduser("~/Documents/01_Projects/05_AgentWorld/agentworld_topo_tick008.png")
plt.savefig(outpath, dpi=200, bbox_inches='tight', facecolor='#0a0a16')
print(f"OK: {outpath}  ({len(G.nodes)} nodes, {len(G.edges)} edges)")
