#!/usr/bin/env python3
"""Stage 1: simulate the incident archive and write CSVs with template text.

    python generate.py --n 200 --out out/sample
    python generate.py --n 10000 --out out/full --llm-fraction 0.7
Then run llm_pass.py on the same --out to rewrite text with local models.
"""
import argparse
import collections
import time

import numpy as np

import importlib

from helixgen.export import build, write_all
from helixgen.simulate import Simulator

PROFILES = {  # name -> (org class, scenario catalog module)
    "bcp": ("helixgen.org:Org", "helixgen.scenarios"),
    "dell": ("helixgen.org_dell:DellOrg", "helixgen.scenarios_dell"),
}


def load_profile(name):
    org_ref, cat_ref = PROFILES[name]
    mod, cls = org_ref.split(":")
    return getattr(importlib.import_module(mod), cls), importlib.import_module(cat_ref)

DEFAULT_MODELS = "gpt-oss:20b=3,gemma4:26b=3,granite4.2:latest=2,nemotron-3.5-lightning:30b=1"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10000, help="number of incidents")
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--out", default="out/full")
    ap.add_argument("--columns", choices=["view", "cid"], default="view",
                    help="view = AR SQL view column names (Incident_Number); cid = C<fieldid> (C1000000161)")
    ap.add_argument("--llm-fraction", type=float, default=0.7,
                    help="share of incidents queued for LLM rewriting (rest keep template text)")
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma-separated Ollama models to rotate through, optional =weight")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="bcp",
                    help="bcp = original mixed-vendor estate; dell = all-Dell estate, 40/40/20 client/infra/software")
    a = ap.parse_args()

    t0 = time.time()
    rng = np.random.default_rng(a.seed)
    org_cls, catalog = load_profile(a.profile)
    org = org_cls(rng, a.seed)
    sim = Simulator(org, rng, a.n, catalog=catalog)
    incidents = sim.run()
    rows, gt, jobs = build(org, incidents, a.seed, a.llm_fraction, a.models.split(","))
    write_all(a.out, rows, gt, jobs, a.columns, profile=a.profile)

    st = collections.Counter(r["Status"] for r in rows["HPD_Help_Desk"])
    names = {0: "New", 1: "Assigned", 2: "In Progress", 3: "Pending", 4: "Resolved", 5: "Closed", 6: "Cancelled"}
    print(f"incidents: {len(incidents)}  ({time.time() - t0:.1f}s)")
    print("status:", {names[k]: v for k, v in sorted(st.items())})
    for t, rs in rows.items():
        print(f"  {t}: {len(rs)} rows")
    print(f"LLM jobs queued: {len(jobs)} -> run: python llm_pass.py --out {a.out}")


if __name__ == "__main__":
    main()
