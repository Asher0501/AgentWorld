#!/usr/bin/env python3
"""AgentWorld topology v4 — CJK everywhere, cleaner layout, professional theme."""
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

# ── CJK font for ALL text ──
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
    nid = f"npc_{name}"
    npc_nodes[name] = nid
    G.add_node(nid, node_type="npc", name=name, zone=zone,
               vitality=nd["attrs"].get("vitality",0),
               satiety=nd["attrs"].get("satiety",0),
               mood=nd["attrs"].get("mood",0))
    G.add_edge(nid, zone, etype="in_zone")

item_nodes = {}; item_colors = {}
palette = ['#ff6b6b','#3498db','#2ecc71','#f39c12','#a855f7','#14b8a6','#f97316','#06b6d4']; ic = 0
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

delta_items = {"矿石","金币","食物"}
for u,v,d in G.edges(data=True):
    if d.get('etype')=='holds' and any(di in v for di in delta_items):
        pass  # mark for gold highlighting later

for u, v in [(min(z,c),max(z,c)) for z,cs in zone_links.items() for c in cs]:
    if u in ZONE_NAMES and v in ZONE_NAMES:
        G.add_edge(u, v, etype="connects"); G.add_edge(v, u, etype="connects")

# ── Layout ──
Gz = nx.Graph(); Gz.add_nodes_from(zone_list)
for u,v,_ in G.edges(data=True):
    if _['etype']=='connects': Gz.add_edge(u,v)
np.random.seed(42)
zp = nx.spring_layout(Gz, k=2.8, iterations=100, scale=3.5, seed=42)
zone_pos = dict(zp)

pos = dict(zone_pos)
for name, nid in npc_nodes.items():
    z = npc_map[name]["zone"]
    if z not in zone_pos: continue
    zx, zy = zone_pos[z]; idx = zones[z].index(name)
    angle = 2*math.pi*idx/len(zones[z]) - math.pi/2
    pos[nid] = (zx + 0.70*math.cos(angle), zy + 0.70*math.sin(angle))

center = np.mean(list(zone_pos.values()), axis=0)
for iid in item_nodes:
    holders = [(u, d['qty']) for u,v,d in G.edges(data=True) if v==iid and d.get('etype')=='holds']
    if not holders: continue
    tq = sum(q for _,q in holders) or 1
    ax = sum(pos[u][0]*q for u,q in holders)/tq
    ay = sum(pos[u][1]*q for u,q in holders)/tq
    off = np.array([ax, ay]) - center
    d = np.linalg.norm(off) or 1
    pos[iid] = (ax + off[0]/d*0.55, ay + off[1]/d*0.55)

# ── Figure ──
fig = plt.figure(figsize=(24, 18), facecolor='#0a0a16')
fig.suptitle("⚔ THE WITCHER WORLD  ·  GLOBAL TOPOLOGY  ·  tick 008", fontsize=18,
             color='#e8dcc5', fontproperties=F(16), y=0.972)

ax_main = fig.add_axes([0.22, 0.04, 0.56, 0.90])
ax_main.set_facecolor('#0a0a16')
xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
x_pad = (max(xs)-min(xs))*0.22 or 0.5; y_pad = (max(ys)-min(ys))*0.22 or 0.5
ax_main.set_xlim(min(xs)-x_pad, max(xs)+x_pad)
ax_main.set_ylim(min(ys)-y_pad, max(ys)+y_pad)

# ── Edges ──
for u,v,d in G.edges(data=True):
    et = d.get('etype')
    if et == 'connects':
        ax_main.annotate("", xy=pos[v], xytext=pos[u], zorder=1,
            arrowprops=dict(arrowstyle='-', color='#3a3a6a', lw=0.8, alpha=0.35,
                           connectionstyle='arc3,rad=0.10'))
    elif et == 'in_zone':
        ax_main.annotate("", xy=pos[v], xytext=pos[u], zorder=1,
            arrowprops=dict(arrowstyle='->', color='#4a8a6a', lw=1.4, alpha=0.20))
    elif et == 'holds':
        qty = d.get('qty', 1)
        is_delta = any(di in v for di in delta_items)
        col = '#d4a84b' if is_delta else '#3a6a8a'
        lw = 2.5 if is_delta else min(1.0+qty*0.3, 2.5)
        ax_main.annotate("", xy=pos[v], xytext=pos[u], zorder=1,
            arrowprops=dict(arrowstyle='->', color=col, lw=lw, alpha=0.85 if is_delta else 0.30))

# ── Zone nodes ──
for z in zone_list:
    x,y = pos[z]; nc = len(zones.get(z,[]))
    r = 0.28 + nc*0.025
    for gr in [r+0.06, r+0.03]:
        ax_main.add_patch(plt.Circle((x,y), gr, fc='none', ec='#4a6a8a', lw=0.3, alpha=0.12, zorder=2))
    ax_main.add_patch(plt.Circle((x,y), r, fc='#121e3a', ec='#4a7aaa', lw=2.5, zorder=4, alpha=0.95))
    ax_main.text(x, y-0.10, z, ha='center', va='center', color='#ece4d0',
                 fontproperties=F(7.5), zorder=5)
    ax_main.text(x, y+0.05, ZONE_NAMES[z], ha='center', va='center', color='#7a8aaa',
                 fontproperties=F(5.5), zorder=5)
    if nc > 0:
        ax_main.text(x+r*0.65, y+r*0.65, str(nc), ha='center', va='center',
                     color='#d4a84b', fontsize=5, fontproperties=F(5),
                     bbox=dict(boxstyle='circle,pad=0.12', fc='#0a0a16', ec='#d4a84b', lw=1), zorder=6)

# ── NPC nodes ──
for name, nid in npc_nodes.items():
    if nid not in pos: continue
    x,y = pos[nid]; a = npc_map[name]["attrs"]
    v = a.get("vitality",0); s = a.get("satiety",0); m = a.get("mood",0)
    goal = a.get("primary_goal","")
    col = '#2a8a4e' if m>=80 else '#b8860b' if m>=50 else '#8b3030'
    tc = '#ece4d0'
    dm = '*'*max(1, min(3, int(v/33)+1))
    txt = f"{name}  {dm}\nV{v:.0f}  S{s:.0f}  M{m:.0f}"
    ax_main.text(x, y, txt, ha='center', va='center', color=tc, fontproperties=F(5.5),
                bbox=dict(boxstyle='round,pad=0.20', fc=col, ec='#0a0a16', lw=1.5, alpha=0.88), zorder=6)
    if goal:
        gs = goal[:12]+'…' if len(goal)>12 else goal
        ax_main.text(x, y-0.08, f"「{gs}」", ha='center', va='top', color='#d4a84b',
                     fontproperties=F(4.5), zorder=7, style='italic')

# ── Item nodes ──
for iid, iname in item_nodes.items():
    if iid not in pos: continue
    x,y = pos[iid]; col = item_colors.get(iid, '#3a5a7a')
    is_d = iname in delta_items
    ec = '#d4a84b' if is_d else '#233a5a'
    ax_main.text(x, y, iname, ha='center', va='center',
                 color='#ece4d0', fontproperties=F(5),
                 bbox=dict(boxstyle='round,pad=0.12', fc=col, ec=ec, lw=2.0 if is_d else 0.6, alpha=0.75), zorder=5)

ax_main.axis('off')

# ── Panel helper ──
def ptext(ax, yy, left, right="", color='#dcd8d0', sz=6.5, right_color=None, bold=False):
    fp = F(sz+1) if bold else F(sz)
    if right:
        ax.text(0.04, yy, left, ha='left', va='top', color=color, fontproperties=fp)
        ax.text(0.98, yy, right, ha='right', va='top', color=right_color or color, fontproperties=fp)
    else:
        ax.text(0.04, yy, left, ha='left', va='top', color=color, fontproperties=fp)

def psec(ax, yy, title):
    yy -= 0.015
    ax.text(0.04, yy, f"━━━  {title}", ha='left', va='top', color='#5a6a8a', fontproperties=F(5))
    return yy - 0.035

# ── LEFT PANEL ──
ax_l = fig.add_axes([0.012, 0.04, 0.205, 0.91]); ax_l.axis('off'); ax_l.set_facecolor('#0a0a16')
y = 0.95
ptext(ax_l, y, "WORLD  STATISTICS", "", '#c8a84b', 10, bold=True); y -= 0.065

total_edges = sum(1 for _ in G.edges(data=True) if _[2].get('etype') in ('in_zone','holds'))
total_ze = sum(1 for _ in G.edges(data=True) if _[2].get('etype')=='connects')

stats = [("Zones",str(len(zone_list))),("NPCs",str(len(NPC_NAMES))),("Items",str(len(item_nodes))),
         ("Topo Links",str(total_edges)),("Zone Roads",str(total_ze))]
for l, r in stats:
    ptext(ax_l, y, l, r, '#b8b0a0'); y -= 0.048

y = psec(ax_l, y, "TICK  008")
for l, r in [("Time","396.9s"),("LLM Calls","25"),("Topo Ops","8"),("Stories","7")]:
    ptext(ax_l, y, l, r, '#9a8a7a'); y -= 0.045

y = psec(ax_l, y, "MOOD  DISTRIBUTION")
moods = [("≥ 80  (High)", sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)>=80), '#3a9a5e'),
         ("50–79  (Mid)",  sum(1 for _,nid in npc_nodes.items() if 50<=npc_map[_]["attrs"].get("mood",0)<80), '#b8860b'),
         ("< 50  (Low)",   sum(1 for _,nid in npc_nodes.items() if npc_map[_]["attrs"].get("mood",0)<50), '#8b3030')]
for l, c, cl in moods:
    ptext(ax_l, y, l, f"  {c}", cl); y -= 0.045

y = psec(ax_l, y, "LOWEST  VITALITY")
worst = sorted([(n, npc_map[n]["attrs"].get("vitality",100)) for n in NPC_NAMES if n in npc_map], key=lambda x: x[1])[:6]
for n, v in worst:
    cl = '#8b3030' if v<30 else '#b8860b' if v<50 else '#3a8a5e'
    ptext(ax_l, y, n, f"Vitality  {v:.0f}", cl, right_color=cl); y -= 0.045

y = psec(ax_l, y, "LEGEND")
for l, cl in [("Zone  Node",'#1a2a4a'),("NPC  Node",'#2a4a3a'),("Item  Node",'#2a4a6a'),
              ("In−Zone","#4a8a6a"),("Holds","#3a6a8a"),("Zone Road","#3a3a6a"),("Topo Delta","#d4a84b")]:
    ptext(ax_l, y, l, '', cl); y -= 0.035

ax_l.set_xlim(0,1); ax_l.set_ylim(0,1)

# ── RIGHT PANEL ──
ax_r = fig.add_axes([0.79, 0.04, 0.20, 0.91]); ax_r.axis('off'); ax_r.set_facecolor('#0a0a16')
y = 0.95
ptext(ax_r, y, "TICK  008  DELTAS", "", '#c8a84b', 10, bold=True); y -= 0.065

for title, ops in [
    ("Ore Trade — Novigrad",
     [("卓尔坦 → 矿石", -2), ("哈托里 → 矿石", +2),
      ("哈托里 → 金币", -2), ("卓尔坦 → 金币", +2)]),
    ("Food Trade — Tavern",
     [("杰洛特 → 金币", -2), ("莎拉 → 金币", +2),
      ("莎拉 → 食物", -1), ("杰洛特 → 食物", +1)]),
]:
    ptext(ax_r, y, f"▸ {title}", "", '#9a8a7a', 6.5); y -= 0.055
    for desc, delta in ops:
        arr = "+" if delta>0 else ""; cl = '#3a9a5e' if delta>0 else '#9a5a5a'
        ptext(ax_r, y, f"    {desc}", f"{arr}{delta:+d}", cl); y -= 0.042
    y -= 0.02

y = psec(ax_r, y, "CHAIN  EFFECTS")
for note in ["哈托里 矿石x2 → 锻造传奇剑","卓尔坦 金币+2 → 购武资金",
             "杰洛特 食物+1 → 酒馆补给","莎拉 金币+2 → 卖粮获利"]:
    ptext(ax_r, y, f"  →  {note}", "", '#6a8a6a' if '金币' in note or '粮' in note else '#7a7a8a', 5.5); y -= 0.038

y = psec(ax_r, y, "TOPOLOGY  STATS")
for l, r in [("Total Nodes",37),("Total Edges",63),("Zone−Zone Roads",total_ze),
             ("In−Zone Links",sum(1 for _ in G.edges(data=True) if _[2].get('etype')=='in_zone')),
             ("Item Holdings",sum(1 for _ in G.edges(data=True) if _[2].get('etype')=='holds'))]:
    ptext(ax_r, y, l, str(r), '#8a8a7a'); y -= 0.042

y = psec(ax_r, y, "MOOD  vs  VITALITY")
comp = sorted([(n, npc_map[n]["attrs"].get("vitality",0)+npc_map[n]["attrs"].get("mood",0))
               for n in NPC_NAMES if n in npc_map], key=lambda x: x[1], reverse=True)
for l, n in [("Best",comp[0][0] if comp else ""),("Worst", comp[-1][0] if comp else "")]:
    ptext(ax_r, y, f"  {l}:  {n}", "", '#8a9a8a'); y -= 0.038

ax_r.set_xlim(0,1); ax_r.set_ylim(0,1)

# Footer
fig.text(0.5, 0.010, "AgentWorld  GraphEngine  ·  tick 008  ·  2026−05−07",
         ha='center', color='#3a3a5a', fontproperties=F(5.5))

outpath = os.path.expanduser("~/Documents/01_Projects/05_AgentWorld/agentworld_topo_tick008.png")
plt.savefig(outpath, dpi=200, bbox_inches='tight', facecolor='#0a0a16')
print(f"OK: {outpath}  ({len(G.nodes)} nodes, {len(G.edges)} edges)")
