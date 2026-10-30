"""Read-only preview (owner, Oct 29) of what the weekly check will decide about the new bill-based settings, using the
same check functions on the saved scores and prices, plus the bill and vote data:
  - trial signals bill_lead_sponsor, bill_vote_against, bill_committee_passed (each on vs off)
  - weak-sales filter (net-sell filter leaves out sales the bill data marks weak), on vs off
  - hold rule "ahead_daily_bill" against the current hold-longer rule (and the other options)
Every switch needs: +1%/yr and sure enough on the years before the last 3 (bar rises with options tried), then also
better over the last 3 years. Nothing is saved back and no orders are placed; prints aggregate results only."""
import sys, copy, datetime as dt
import pandas as pd

sys.path.insert(0, ".")
import run_daily as R
import congress_signals.engine as E

cfg = dict(R.cfg)
sc = pd.read_pickle(E._p(cfg, "cache", "scored.pkl"))
px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
E.register_opens(cfg, px)
tables, votes = E.collect_bill_stages(cfg)
if E.BILL_DATA["problems"]:
    print("DATA PROBLEMS:", E.BILL_DATA["problems"])
committees = E.load_committees(cfg)
history = E.load_committee_history(cfg, committees)
mems = E.committee_records(sc, history, committees)
sc = sc.reset_index(drop=True)
sc = E.bill_stage_features(sc, {"bill_tables": tables, "vv_votes": votes}, cfg, mems)
a = copy.deepcopy(E.load_adaptive(cfg))
pol = dict(a["policy"])
print(f"Policy the checks start from: price_exit={pol.get('price_exit')}, extend={pol.get('extend')}, "
      f"hold={pol['hold_other']}/{pol['hold_small']}, sizing={pol.get('sizing')}, signals_on={pol.get('signals_on')}")
bw, sw = E.active_weights(cfg, signals_on=pol.get("signals_on", []))
sc = E.apply_hold_policy(E.apply_scores(sc, cfg, buy_w=bw, short_w=sw), px, cfg, pol)
rows, today = [], dt.date.today().isoformat()
new_sigs = ["bill_lead_sponsor", "bill_vote_against", "bill_committee_passed"]
E.check_trial_signals(sc, px, cfg, a, pol, rows, today, only=new_sigs)
E.check_sale_discount(sc, px, cfg, a, pol, rows, today)
E._price_rule_check(sc, px, cfg, a, pol, rows, today, "extend", E.EXTENDS, E.EXTEND_LABEL)

print("\n| Setting | Option | Per year vs S&P | Gain vs current | Earlier-years t (bar) | Last 3 yrs | Verdict |")
print("|---|---|---|---|---|---|---|")
f = lambda v: "" if v is None or v != v else f"{v * 100:+.2f}%"
for r in rows:
    tt = r.get("Sureness (earlier years)")
    print(f"| {r['Setting'][:70]} | {r['Option'][:70]} | {f(r.get('Per year vs S&P'))} | {f(r.get('Gain vs current'))} | "
          f"{'' if tt is None else f'{tt:.2f}'}{'' if r.get('Bar') is None else ' (%.2f)' % r['Bar']} | "
          f"{f(r.get('Gain (last 3 years)'))} | {r['Verdict']} |")
print("\nWould switch:", [h["change"][:60] + f" -> {h['to']}" for h in a["history"][len(E.load_adaptive(cfg)["history"]):]] or "nothing")
