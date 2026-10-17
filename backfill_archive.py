"""One shard of the parallel Internet Archive backfill (press releases of members who left Congress since 2021).
Usage: python backfill_archive.py SHARD SHARDS MINUTES
Reads ./wb (earlier archive output, restored from the wb- cache) and ./social (known releases); writes this shard's
members only into ./wb_out (merged back into ./wb by the archive workflow's last job)."""
import os, sys, json, time, shutil
import congress_signals.engine as E
import congress_signals.social as S
import congress_signals.press_backfill as PB

shard, shards, minutes = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social",
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "TIME_BUDGET_MIN": 0}
deadline = time.time() + minutes * 60
IN, OUT = "wb", "wb_out"
os.makedirs(OUT, exist_ok=True)
# the member list is shared by every shard (made once, from congress-legislators)
if os.path.exists(os.path.join(IN, "members.json")):
    mmap = json.load(open(os.path.join(IN, "members.json")))
elif os.path.exists("social/wb_members.json"):
    mmap = json.load(open("social/wb_members.json"))
else:
    mmap = PB.departed_since()
json.dump(mmap, open(os.path.join(OUT, "members.json"), "w"))
members = sorted(mmap)
mine = {b for i, b in enumerate(members) if i % shards == shard}
# start from what earlier runs did for these members
try:
    allprog = json.load(open(os.path.join(IN, "progress.json")))
except Exception:
    allprog = {}
prog = {b: v for b, v in allprog.items() if b in mine}
for sub in ("queue", os.path.join("posts", "press_wb")):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    src = os.path.join(IN, sub)
    if os.path.isdir(src):
        for fn in os.listdir(src):
            if fn.split(".")[0] in mine:
                shutil.copy(os.path.join(src, fn), os.path.join(OUT, sub, fn))
seed = "social" if not allprog else None          # first parallel run: carry over the old single job's work
tg = S.Tagger(cfg)
try:
    done = PB.backfill_departed(cfg, prog, tg, deadline, per_run=10 ** 9, workers=4, root=OUT, only=mine, seed_from=seed)
finally:
    json.dump(prog, open(os.path.join(OUT, f"progress_{shard}.json"), "w"))
    if os.path.exists(os.path.join(OUT, "progress.json")):
        os.remove(os.path.join(OUT, "progress.json"))     # each shard keeps its own; the merge job combines them
    from congress_signals import runlog
    runlog.report(f"archive_{shard}", OUT, {f"shard {shard} archived releases": runlog.dir_snapshot(
        os.path.join(OUT, "posts", "press_wb")) + (None,)}, E.log)
left = sum(1 for b in mine if not (prog.get(b) or {}).get("done"))
open(os.path.join(OUT, f"left_{shard}.txt"), "w").write(str(left))
print(f"Shard {shard}: {left} of {len(mine)} members left")
