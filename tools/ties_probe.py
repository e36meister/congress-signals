"""Read-only probe (owner, Oct 30): the GS 'Worth a look' spouse-job flag vs the member page's 'Ties to companies'.
Prints public data only (member names, disclosure lines)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
t = (sys.argv[1] if len(sys.argv) > 1 else "GS").upper()
d = json.loads(cloud_store.get("dashboard_data.json") or b"{}")
w = [r for r in d.get("worth") or [] if r.get("t") == t]
print("worth rows:", json.dumps(w, default=str)[:1500])
for p in (w[0].get("who") or []) if w else []:
    idx = json.loads(cloud_store.get("members/index.json") or b"{}")
    ks = [m["k"] for m in idx.get("members", []) if m.get("n") == p.get("n")]
    print("member", p, "page keys:", ks)
    for k in ks:
        doc = json.loads(cloud_store.get(f"members/{k}.json") or b"{}")
        print(" page", k, "b:", doc.get("b"), "ties:", len(doc.get("ties") or []), json.dumps(doc.get("ties"))[:800])
        tr = [x for x in doc.get("trades") or [] if len(x) > 3 and x[3] == t]
        print(" trades in", t, ":", tr[:5])
