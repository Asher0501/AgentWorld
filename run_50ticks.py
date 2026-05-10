#!/usr/bin/env python3
"""50-tick batch runner with per-tick timeout, retry, and highlight extraction."""
import subprocess, sys, os, time, json, re
from pathlib import Path

BASE = "/tmp/full_tick"
os.makedirs(BASE, exist_ok=True)

LOG = Path(BASE) / "batch_50ticks.log"
log_fh = open(LOG, "a", buffering=1)

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    log_fh.write(line + "\n")

HIGHLIGHTS = []

log(f"{'='*60}")
log(f"BATCH RUN START: 50 ticks (001-050)")
log(f"{'='*60}")

start_tick = 1
end_tick = 50

for i in range(start_tick, end_tick + 1):
    tick = f"tick_{i:03d}"
    tick_dir = Path(BASE) / tick
    report = tick_dir / "REPORT.md"
    
    # Skip if already completed
    if report.exists():
        log(f"⏩ {tick} already done, skipping")
        continue
    
    t0 = time.time()
    log(f"🔄 {tick} starting...")
    
    cmd = [
        sys.executable, "-u",
        str(Path.home() / "Documents/01_Projects/05_AgentWorld/run_1tick.py"),
        tick
    ]
    
    completed = False
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(Path.home() / "Documents/01_Projects/05_AgentWorld"),
                capture_output=True, text=True,
                timeout=1200  # 20 min per tick (含自适应重试)
            )
            elapsed = time.time() - t0
            stdout = proc.stdout
            
            # Extract key stats
            tick_match = re.search(r'TICK:.*?(\d+\.\d+)s', stdout)
            elapsed_s = float(tick_match.group(1)) if tick_match else round(elapsed, 1)
            
            stories_match = re.search(r'(\d+) stories generated', stdout)
            stories = int(stories_match.group(1)) if stories_match else 0
            
            comps_match = re.search(r"'components': (\d+)", stdout)
            comps = int(comps_match.group(1)) if comps_match else 0
            
            calls_match = re.search(r"LLM calls.*?(\d+)", stdout)
            llm_calls = int(calls_match.group(1)) if calls_match else 0
            
            # Timing breakdown
            timing = {}
            PATTERNS = [
                ("llm1", r"LLM1_plans\s+(\d+\.\d+)s"),
                ("llm3", r"LLM3_story\s+(\d+\.\d+)s"),
                ("llm4a", r"LLM4a_topo_delta\s+(\d+\.\d+)s"),
                ("llm5", r"LLM5_projection\s+(\d+\.\d+)s"),
            ]
            for key, pat in PATTERNS:
                m = re.search(pat, stdout)
                if m:
                    timing[key] = float(m.group(1))
            
            # Check for MiniMax errors
            timeout_count = stdout.count("read operation timed out")
            
            # Check for verification failures
            fail_count = stdout.count("拓扑校验失败")
            
            # Save full stdout
            with open(tick_dir / "stdout.log", "w") as f:
                f.write(stdout)
            if proc.stderr:
                with open(tick_dir / "stderr.log", "w") as f:
                    f.write(proc.stderr)
            
            # Get highlight: find stories in report
            highlight = ""
            if report.exists():
                text = report.read_text()
                stories_section = text.split("## 📖 Stories")[-1].split("## ")[0] if "## 📖 Stories" in text else ""
                story_lines = [l.strip() for l in stories_section.split("\n") if l.strip()]
                # Find the longest story (most characters)
                longest_story = ""
                longest_len = 0
                for s in re.finditer(r'### Story \d+ \(.*?\).*?(?=### Story \d+|$)', stories_section, re.DOTALL):
                    content = s.group()
                    char_match = re.search(r'\((\d+) chars\)', content)
                    if char_match and int(char_match.group(1)) > longest_len:
                        longest_len = int(char_match.group(1))
                        longest_story = content[:120].replace('\n', ' ')
                highlight = longest_story

            # NPC state changes
            npc_changes = {}
            if report.exists():
                for line in text.split("\n"):
                    if "|" in line and "→" in line:
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 4 and parts[0].strip():
                            name = parts[0].strip()
                            zone_info = parts[2].strip() if len(parts) > 2 else ""
                            changes = parts[-1].strip() if parts[-1].strip() else ""
                            npc_changes[name] = {"zone": zone_info, "changes": changes}
            
            result = {
                "tick": tick, "elapsed_s": elapsed_s,
                "stories": stories, "components": comps,
                "llm_calls": llm_calls, "status": "ok",
                "timeouts": timeout_count,
                "verification_fails": fail_count,
                "timing": timing,
                "highlight": highlight[:200],
            }
            HIGHLIGHTS.append(result)
            
            timing_str = f" [P{timing.get('llm1',0):.0f}|S{timing.get('llm3',0):.0f}|T{timing.get('llm4a',0):.0f}|A{timing.get('llm5',0):.0f}]"
            timeout_str = f" ⚡{timeout_count}timeout" if timeout_count else ""
            fail_str = f" ❌{fail_count}fail" if fail_count else ""
            
            log(f"  ✅ {tick}: {elapsed_s:.0f}s | {stories} stories | {comps} comps | {llm_calls} LLM{timing_str}{timeout_str}{fail_str}")
            completed = True
            break
            
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            log(f"  ⚠️  {tick} attempt {attempt} TIMEOUT at {elapsed:.0f}s (>900s), retrying...")
        except Exception as e:
            elapsed = time.time() - t0
            log(f"  ❌ {tick} attempt {attempt} ERROR at {elapsed:.0f}s: {e}")
    
    if not completed:
        log(f"  ❌ {tick} FAILED after {max_retries} attempts")
        HIGHLIGHTS.append({
            "tick": tick, "elapsed_s": round(time.time()-t0, 1),
            "stories": 0, "components": 0, "llm_calls": 0,
            "status": "failed", "timeouts": 0, "verification_fails": 0,
            "timing": {}, "highlight": ""
        })

# Summary
ok_count = len([r for r in HIGHLIGHTS if r['status'] == 'ok'])
total_wall = sum(r['elapsed_s'] for r in HIGHLIGHTS)
total_timeouts = sum(r['timeouts'] for r in HIGHLIGHTS)
total_fails = sum(r['verification_fails'] for r in HIGHLIGHTS)

log(f"\n{'='*60}")
log(f"BATCH RUN COMPLETE")
log(f"{'='*60}")
log(f"Completed: {ok_count}/{len(HIGHLIGHTS)}")
log(f"Total wall time: {total_wall:.0f}s ({total_wall/60:.1f} min)")
log(f"Avg per tick: {total_wall/ok_count:.0f}s" if ok_count else "N/A")
log(f"MiniMax timeouts: {total_timeouts}")
log(f"Verification fails: {total_fails}")

# Write summary JSON
summary_path = Path(BASE) / "batch_50ticks_summary.json"
with open(summary_path, "w") as f:
    json.dump(HIGHLIGHTS, f, indent=2, ensure_ascii=False)
log(f"Summary -> {summary_path}")

# Write markdown summary
md_path = Path(BASE) / "batch_50ticks_summary.md"
with open(md_path, "w") as f:
    f.write(f"# Batch 50 Ticks Summary (tick_021~tick_070)\n\n")
    f.write(f"**Completed**: {ok_count}/{len(HIGHLIGHTS)} | ")
    f.write(f"**Total wall**: {total_wall:.0f}s ({total_wall/60:.1f} min) | ")
    f.write(f"**Avg**: {total_wall/ok_count:.0f}s/tick | ")
    f.write(f"**Timeouts**: {total_timeouts} | **V-fails**: {total_fails}\n\n")
    f.write(f"| Tick | Time | LLM | S | C | T | P#1 | S#3 | T#4a | A#5 | Timeouts | Highlights |\n")
    f.write(f"|------|:----:|:---:|:-:|:-:|:-:|:---:|:---:|:----:|:---:|:--------:|:-----------|\n")
    for r in HIGHLIGHTS:
        tm = r['timing']
        h = (r['highlight'][:60] + '...') if len(r['highlight']) > 60 else r['highlight']
        f.write(f"| {r['tick']} | {r['elapsed_s']:.0f}s | {r['llm_calls']} | {r['stories']} | {r['components']} | 0 | ")
        f.write(f"{tm.get('llm1',0):.0f}s | {tm.get('llm3',0):.0f}s | {tm.get('llm4a',0):.0f}s | {tm.get('llm5',0):.0f}s | ")
        f.write(f"{'⚡'+str(r['timeouts'])} | {h} |\n")

log(f"Markdown -> {md_path}")
log_fh.close()
