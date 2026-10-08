"""Read-only: runs the normal data loading (prepare), captures compute_features' inputs, and profiles it.
Prints only function timings (no account data). Nothing is saved back."""
import sys, os, cProfile, pstats, io, time, pickle
sys.path.insert(0, ".")
import run_daily as R
import congress_signals.engine as E


class Stop(Exception):
    pass


orig = E.compute_features


def captured(tx, px, data, cfg):
    t0 = time.time()
    pr = cProfile.Profile()
    pr.enable()
    out = orig(tx, px, data, cfg)
    pr.disable()
    print(f"compute_features: {time.time() - t0:.0f}s (with profiling), {len(tx)} rows")
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(45)
    print(s.getvalue()[:12000])
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(60)
    print(s.getvalue()[:14000])
    if os.environ.get("SAVE_INPUTS"):
        pickle.dump((tx, px, data, {k: v for k, v in cfg.items() if not k.startswith("_")}), open("/tmp/feat_inputs.pkl", "wb"))
    raise Stop()


E.compute_features = captured
# read-only runs don't OCR scanned reports (tesseract can crash here, and the saved trades are all we need)
_rd = lambda name: (lambda cfg: E.pd.read_pickle(E._p(cfg, "cache", name)) if os.path.exists(E._p(cfg, "cache", name)) else E.pd.DataFrame())
E.collect_house_paper = _rd("house_paper_tx.pkl")
E.collect_senate_paper = _rd("senate_paper_tx.pkl")
try:
    E.prepare(R.cfg)
except Stop:
    pass
