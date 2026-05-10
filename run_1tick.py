"""
Run 1 tick on the existing 7-zone 12-NPC world.
All LLM IO archived per tick. Timing breakdown. Story highlights.
Usage: python3 run_1tick.py [tick_label]
"""
import sys, os, json, shutil, asyncio, logging, time, glob, contextvars
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "src"))


def _report_extract_ops(raw: str) -> list[dict]:
    """
    报告专用的 LLM 响应解析 — 独立于引擎解析器。
    只负责从 LLM 原始输出中提取操作列表用于显示，不参与落地。
    引擎的解析方式是引擎自己的事。
    """
    import re as _re
    text = _re.sub(r'^```(?:json)?\s*', '', raw.strip())
    text = _re.sub(r'\s*```$', '', text)
    start = -1
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            start = i
            break
    if start == -1:
        return []
    text = text[start:]
    stack = []
    end = 0
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{':
            stack.pop()
            if not stack:
                end = i + 1
                break
        elif ch == ']' and stack and stack[-1] == '[':
            stack.pop()
            if not stack:
                end = i + 1
                break
    if not end:
        return []
    try:
        parsed = json.loads(text[:end])
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return parsed.get("operations", [])
    return []

# Clean module cache for fresh config reload
for mod in list(sys.modules.keys()):
    if "agent_world.config" in mod:
        del sys.modules[mod]

import agent_world.services.interaction_resolver as ir
from agent_world.services.graph_npc_engine import GraphNPCEngine
from agent_world.services.interaction_layer import InteractionLayer
from agent_world.db.db import init_db
from agent_world.services.post_processor import PostProcessor
from agent_world.services.verification_layer import VerificationLayer

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S")
logger = logging.getLogger("run_1tick")

tick_label = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("tick_%H%M%S")
IO_DIR = f"/tmp/full_tick/{tick_label}"
os.makedirs(IO_DIR, exist_ok=True)

# ─── Install ALL-call tracer ───
_call_log = []          # list of {stage, prompt, response, time_s}
_stage_var = contextvars.ContextVar('stage', default='')
_comp_var = contextvars.ContextVar('comp', default=-1)
_component_timings = {}  # comp.id → {stage: {t0, t1}} + total
# Progressive IO counter: write each LLM call to disk immediately
_io_counters = {}

def _write_io_progressive(stage, prompt, response, time_s):
    """Write IO to disk immediately after each LLM call, for crash safety."""
    n = _io_counters.get(stage, 0)
    suffix = f"_{n}" if n > 0 else ""
    pfile = os.path.join(IO_DIR, f"{stage}{suffix}_prompt.txt")
    rfile = os.path.join(IO_DIR, f"{stage}{suffix}_response.txt")
    with open(pfile, "w", encoding="utf-8") as f:
        f.write(prompt)
    with open(rfile, "w", encoding="utf-8") as f:
        f.write(response)
    _io_counters[stage] = n + 1

orig_call_llm = ir.InteractionResolver._call_llm

def _wrapped_call_llm(self, prompt, system_prompt=None, temperature=None):
    t0 = time.time()
    s = _stage_var.get() or "UNKNOWN"
    cid = _comp_var.get()
    sys_info = f" sp={system_prompt[:30] if system_prompt else 'default'} temp={temperature if temperature else 'default'}"
    print(f"  [TRACE] _wrapped_call_llm: stage={s!r} comp={cid}{sys_info} prompt_len={len(prompt)}", flush=True)
    raw = orig_call_llm(self, prompt, system_prompt, temperature)
    t1 = time.time()
    elapsed = t1 - t0
    call_entry = {"stage": s, "comp": cid, "prompt": prompt, "response": raw, "time_s": round(elapsed, 1), "t0": t0, "t1": t1}
    _call_log.append(call_entry)
    _write_io_progressive(s, prompt, raw, round(elapsed, 1))
    return raw

ir.InteractionResolver._call_llm = _wrapped_call_llm

# Patch each pipeline stage to set _current_stage
# LLM #1 — migrated to PipelineOrchestrator._run_stage_plan
from agent_world.services.pipeline_orchestrator import PipelineOrchestrator, PipelineContext
from agent_world.services.pipeline_engine import PipelineEngine

# LLM #1 — _run_stage_plan_for_component sets _current_stage
orig_llm1 = PipelineOrchestrator._run_stage_plan_for_component
async def _patched_llm1(self, comp, npc_list, ctx):
    _stage_var.set("LLM1_plans")
    print(f"  [TRACE] _patched_llm1 called, stage={_stage_var.get()!r}", flush=True)
    return await orig_llm1(self, comp, npc_list, ctx)
PipelineOrchestrator._run_stage_plan_for_component = _patched_llm1

# LLM #2 removed — replaced by inline exec_results

# LLM #3 — now goes through call_llm_async (via engine); _patched_call_llm_async handles timing
orig_llm3 = InteractionLayer.process
async def _patched_llm3(self, *a, **kw):
    _stage_var.set("LLM3_story")
    return await orig_llm3(self, *a, **kw)
InteractionLayer.process = _patched_llm3

# LLM #4a / #4b — async calls go through call_llm_async (not run_stage)
# Map stage_key to display names for _current_stage, record comp ID via contextvars
orig_call_llm_async = PipelineEngine.call_llm_async
async def _patched_call_llm_async(self, prompt, stage_key, suffix=""):
    llm4_map = {"topo_delta": "LLM4a_topo_delta", "content_update": "LLM5_projection"}
    if stage_key in llm4_map:
        _stage_var.set(llm4_map[stage_key])
    cid = _comp_var.get()
    cs = _stage_var.get() or stage_key
    t0 = time.time()
    result = await orig_call_llm_async(self, prompt, stage_key, suffix)
    t1 = time.time()
    # Record component-level timing (runs in event loop thread, _comp_var is correct)
    if cid >= 0:
        ct = _component_timings.setdefault(cid, {"stages": {}, "total": 0.0})
        if cs not in ct["stages"]:
            ct["stages"][cs] = {"t0": t0, "t1": t1}
        else:
            if t0 < ct["stages"][cs]["t0"]: ct["stages"][cs]["t0"] = t0
            if t1 > ct["stages"][cs]["t1"]: ct["stages"][cs]["t1"] = t1
    return result
PipelineEngine.call_llm_async = _patched_call_llm_async

# Component-level timing: wrap _run_component_full to set _comp_var + track total
orig_run_comp_full = PipelineOrchestrator._run_component_full
async def _patched_run_comp_full(self, ctx, comp, npcs):
    tok = _comp_var.set(comp.id)
    t0 = time.time()
    try:
        result = await orig_run_comp_full(self, ctx, comp, npcs)
        return result
    finally:
        t1 = time.time()
        _comp_var.reset(tok)
        ct = _component_timings.setdefault(comp.id, {"stages": {}, "total": 0.0})
        ct["total"] = round(t1 - t0, 1)
        ct["stages"] = {k: round(v["t1"] - v["t0"], 1) for k, v in ct["stages"].items()}
PipelineOrchestrator._run_component_full = _patched_run_comp_full

# LLM #5 verification — no LLM calls, pure code checks, but log for completeness
orig_llm5 = VerificationLayer.check_all
def _patched_llm5(self, *a, **kw):
    _stage_var.set("LLM5_verify")
    return orig_llm5(self, *a, **kw)
VerificationLayer.check_all = _patched_llm5

# ─── World snapshots ───
def snapshot_world(engine, tag=""):
    """Return structured dict of NPC states."""
    snap = {"npcs": []}
    ge = engine.graph_engine if hasattr(engine, 'graph_engine') else None
    if not ge:
        return snap
    from agent_world.config.config_loader import has_role
    for ent in ge.all_entities():
        if hasattr(ent, 'type_id') and has_role(ent.type_id, "actor"):
            name = getattr(ent, 'name', ent.entity_id)
            zone_id = "?"
            zone_name = "?"
            for conn in ent.connected_entity_ids:
                ce = ge.get_entity(conn)
                if ce and hasattr(ce, 'type_id') and has_role(ce.type_id, "region"):
                    zone_id = conn
                    zone_name = getattr(ce, 'name', conn)
                    break
            inv = ge.get_inventory_view(ent.entity_id) if hasattr(ge, 'get_inventory_view') else []
            inv_detail = [{"item": i.get('item_name', i.get('name','?')), "qty": i.get('quantity', 1)} for i in inv]
            attrs = {}
            if hasattr(ent, 'attributes'):
                attrs = {k: getattr(ent, k, v) for k, v in ent.attributes.items()}
            snap["npcs"].append({
                "id": ent.entity_id,
                "name": name,
                "zone_id": zone_id,
                "zone_name": zone_name,
                "inventory": inv_detail,
                "attributes": attrs,
            })
    return snap

async def main():
    logger.info(f"🚀 Running 1 tick: {tick_label}")
    logger.info(f"📁 IO → {IO_DIR}/")

    # Initialize DB (create tables if first run, or open existing)
    init_db()

    t_total = time.time()
    engine = GraphNPCEngine(
        llm_available=True,
        llm_model="minimax/MiniMax-M2.7",
        llm_temperature=0.7,
        small=True,
        llm_callback=None,
    )

    # Snapshot BEFORE
    snap_before = snapshot_world(engine, "before")

    results = await engine.tick()
    total_time = round(time.time() - t_total, 1)

    # Snapshot AFTER
    snap_after = snapshot_world(engine, "after")

    # ─── Save all IO ───
    seq_by_stage = {}
    for c in _call_log:
        seq_by_stage.setdefault(c["stage"], []).append(c)

    for stage, calls in sorted(seq_by_stage.items()):
        for i, c in enumerate(calls):
            suffix = f"_{i}" if len(calls) > 1 else ""
            pfile = os.path.join(IO_DIR, f"{stage}{suffix}_prompt.txt")
            rfile = os.path.join(IO_DIR, f"{stage}{suffix}_response.txt")
            with open(pfile, "w", encoding="utf-8") as f:
                f.write(c["prompt"])
            with open(rfile, "w", encoding="utf-8") as f:
                f.write(c["response"])

    # ─── Timing breakdown (wall-clock intervals per stage) ───
    timing = {}
    for s, calls in sorted(seq_by_stage.items()):
        # Check if calls have absolute timestamps (new parallel-aware format)
        has_ts = any("t0" in c and "t1" in c for c in calls)
        if has_ts:
            # Wall-clock interval: max(t1) - min(t0) — correct for parallel stages
            t_starts = [c["t0"] for c in calls]
            t_ends = [c["t1"] for c in calls]
            wall = round(max(t_ends) - min(t_starts), 1)
        else:
            # Legacy: sum of individual call durations
            wall = round(sum(c.get("time_s", 0) for c in calls), 1)
        timing[s] = wall

    # ─── Build report ───
    lines = []
    lines.append(f"# Tick Report: {tick_label}")
    lines.append(f"**Total**: {total_time}s | **LLM calls**: {len(_call_log)}")
    lines.append("")

    # Timing
    lines.append("## ⏱ Timing Breakdown")
    accounted = 0
    for k, v in sorted(timing.items()):
        pct = round(v / total_time * 100, 1) if total_time else 0
        accounted += v
        lines.append(f"- {k:25s} {v:>6.1f}s ({pct}%)")
    overhead = max(0, total_time - accounted)
    overhead_pct = round(overhead / total_time * 100, 1) if total_time else 0
    lines.append(f"- {'OVERHEAD':25s} {overhead:>6.1f}s ({overhead_pct}%)")
    # Note for parallel runs: stage times are wall-clock intervals (overlapping stages may sum > total)
    accounted_pct = round(accounted / total_time * 100, 1) if total_time else 0
    if accounted_pct > 100:
        lines.append(f"  *并行模式：阶段耗时取 wall-clock 区间，重叠阶段合计 {accounted_pct}% > 100%")
        lines.append(f"  总耗时 {total_time}s，关键瓶颈为最慢阶段")
    lines.append("")

    if _component_timings:
        lines.append("### 📦 Per-Component Timing")
        lines.append("")
        lines.append("| Comp | LLM #3 | LLM #4a | LLM #4b | LLM #5 | Total |")
        lines.append("|------|--------|---------|---------|--------|-------|")
        for comp_id in sorted(_component_timings.keys()):
            ct = _component_timings[comp_id]
            def _sd(stg):
                d = ct["stages"].get(stg)
                return f"{d:.1f}s" if d is not None else "-"
            lines.append(f"| {comp_id} | {_sd('LLM3_story')} | {_sd('LLM4a_topo_delta')} | {_sd('LLM4b_attr')} | {_sd('LLM5_verify')} | {ct['total']:.1f}s |")
        lines.append("")

    # Save timing as JSON too
    with open(os.path.join(IO_DIR, "timing.json"), "w") as f:
        json.dump({"total": total_time, "stages": timing, "tick_label": tick_label,
                    "llm_calls": len(_call_log), "note": "wall-clock intervals per stage"}, f, indent=2)

    # ─── World diff ───
    lines.append("## 🌍 NPC States (Before → After)")
    before_map = {n["name"]: n for n in snap_before.get("npcs", [])}
    after_map = {n["name"]: n for n in snap_after.get("npcs", [])}
    all_names = sorted(set(list(before_map.keys()) + list(after_map.keys())))

    for name in all_names:
        b = before_map.get(name, {})
        a = after_map.get(name, {})
        b_zone = b.get("zone_name", "?")
        a_zone = a.get("zone_name", "?")
        zone_arrow = f"→ {a_zone}" if b_zone != a_zone else "@"
        zone_str = f"{b_zone:12s} {zone_arrow} {a_zone}" if b_zone != a_zone else f"{b_zone}"

        b_inv = {i["item"]: i["qty"] for i in b.get("inventory", [])}
        a_inv = {i["item"]: i["qty"] for i in a.get("inventory", [])}
        inv_changes = []
        all_items = sorted(set(list(b_inv.keys()) + list(a_inv.keys())))
        for item in all_items:
            bq = b_inv.get(item, 0)
            aq = a_inv.get(item, 0)
            if bq != aq:
                delta = aq - bq
                inv_changes.append(f"{item}{'+' if delta>0 else ''}{delta}")
        inv_str = ", ".join(inv_changes) if inv_changes else "—"
        lines.append(f"  {name:8s} | {zone_str:25s} | [{inv_str}]")

    lines.append("")

    # ─── LLM #2 topo ops ───
    if "LLM2_topo_struct" in seq_by_stage:
        lines.append("## 🔗 LLM #2 Topo Ops")
        for c in seq_by_stage["LLM2_topo_struct"]:
            ops_raw = _report_extract_ops(c["response"])
            lines.append(f"- {len(ops_raw)} ops:")
            if ops_raw:
                for op in ops_raw[:20]:
                    lines.append(f"  - {json.dumps(op, ensure_ascii=False)}")
                if len(ops_raw) > 20:
                    lines.append(f"  - ... and {len(ops_raw)-20} more")
        lines.append("")

    # ─── LLM #4a delta ops ───
    if "LLM4a_topo_delta" in seq_by_stage:
        lines.append("## 💰 LLM #4a Delta (Trade) Ops")
        for i, c in enumerate(seq_by_stage["LLM4a_topo_delta"]):
            ops_raw = _report_extract_ops(c["response"])
            lines.append(f"- Round {i+1} ({len(ops_raw)} ops):")
            if ops_raw:
                groups = {}
                for op in ops_raw:
                    g = op.get("group", "ungrouped")
                    groups.setdefault(g, []).append(op)
                for g, gops in sorted(groups.items()):
                    lines.append(f"  {g}: {' ↔ '.join(set(op.get('src', op.get('tgt', '?')) for op in gops))}")
                    for op in gops:
                        src = op.get("src", "?")
                        tgt = op.get("tgt", "")
                        delta = op.get("delta", 0)
                        if tgt:
                            lines.append(f"    {src} → {tgt} {delta:+d}")
                        elif "consumes" in op:
                            c = op["consumes"]
                            p = op.get("produces", {})
                            lines.append(f"    {src}: consumes {c}, produces {p}")
                        else:
                            lines.append(f"    {src}: {op}")
        lines.append("")

    # ─── LLM #4b attr + recent ───
    if "LLM4b_attr" in seq_by_stage:
        lines.append("## 🧬 LLM #4b Attribute Changes")
        for i, c in enumerate(seq_by_stage["LLM4b_attr"]):
            ops_raw = _report_extract_ops(c["response"])
            lines.append(f"- Round {i+1} ({len(ops_raw)} ops):")
            if ops_raw:
                shown = 0
                for op in ops_raw:
                    delta = op.get("delta", 0)
                    if abs(delta) >= 5 or shown < 3:
                        tgt = op.get('target', op.get('tgt', ''))
                        attr_name = op.get('attr', op.get('attribute', ''))
                        lines.append(f"  {tgt:8s} → {attr_name:10s} {delta:+d} | {op.get('description','')[:40]}")
                        shown += 1
                if len(ops_raw) > shown:
                    lines.append(f"  ... and {len(ops_raw)-shown} more")
        lines.append("")

    # ─── Stories ───
    if "LLM3_story" in seq_by_stage:
        lines.append("## 📖 Stories")
        for i, c in enumerate(seq_by_stage["LLM3_story"]):
            resp = c["response"]
            lines.append(f"")
            lines.append(f"### Story {i+1} ({len(resp)} chars)")
            lines.append(f"{resp}")
        lines.append("")

    # ─── LLM #5 verification ───
    if "LLM5_verify" in seq_by_stage:
        lines.append("## ✅ LLM #5 Verification")
        for i, c in enumerate(seq_by_stage["LLM5_verify"]):
            lines.append(f"- Round {i+1}: {c['response'][:300]}")
        lines.append("")

    # ─── IO file list ───
    lines.append("## 📁 IO Archive")
    io_files = sorted(glob.glob(os.path.join(IO_DIR, "*_prompt.txt")) +
                      glob.glob(os.path.join(IO_DIR, "*_response.txt")) +
                      glob.glob(os.path.join(IO_DIR, "*.json")))
    for f in io_files:
        lines.append(f"- {os.path.basename(f)}")
    lines.append("")

    report_text = "\n".join(lines)

    # Save report
    report_path = os.path.join(IO_DIR, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Print summary for Telegram
    print("\n" + "="*70)
    print(f"  TICK: {tick_label} — {total_time}s total")
    print("="*70)
    print(f"\n⏱  Timing (wall-clock intervals):")
    for k, v in sorted(timing.items()):
        pct = round(v / total_time * 100, 1)
        print(f"  {k:25s} {v:>6.1f}s ({pct:5.1f}%)")
    accounted = sum(timing.values())
    overhead = max(0, total_time - accounted)
    overhead_pct = round(overhead / total_time * 100, 1) if total_time else 0
    print(f"  {'OVERHEAD':25s} {overhead:>6.1f}s ({overhead_pct:5.1f}%)")
    apct = round(accounted / total_time * 100, 1) if total_time else 0
    if apct > 100:
        print(f"  (合计 {apct}% > 100%，阶段在并行模式下重叠)")

    # Story highlights
    if "LLM3_story" in seq_by_stage:
        print(f"\n📖 {len(seq_by_stage['LLM3_story'])} stories generated")

    # Per-component timing (stdout)
    if _component_timings:
        print(f"\n📦 Per-Component Timing:")
        header = f"  {'Comp':>4} | {'#3':>7} | {'#4a':>7} | {'#4b':>7} | {'#5':>7} | {'Total':>7} |"
        print(header)
        print("  " + "-" * len(header))
        for comp_id in sorted(_component_timings.keys()):
            ct = _component_timings[comp_id]
            def _sd(stg):
                d = ct["stages"].get(stg)
                return f"{d:>5.1f}s" if d is not None else "    -  "
            print(f"  {comp_id:>4} | {_sd('LLM3_story')} | {_sd('LLM4a_topo_delta')} | {_sd('LLM4b_attr')} | {_sd('LLM5_verify')} | {ct['total']:>5.1f}s |")

    # Save snapshots
    with open(os.path.join(IO_DIR, "snapshot_before.json"), "w", encoding="utf-8") as f:
        json.dump(snap_before, f, ensure_ascii=False, indent=2)
    with open(os.path.join(IO_DIR, "snapshot_after.json"), "w", encoding="utf-8") as f:
        json.dump(snap_after, f, ensure_ascii=False, indent=2)

    print(f"\n📊 Report: {report_path}")
    print(f"📁 IO:     {IO_DIR}/")
    print("="*70)

    return seq_by_stage, total_time, timing

if __name__ == "__main__":
    asyncio.run(main())
