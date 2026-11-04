#!/usr/bin/env python3
"""Offline evaluation of retrieval and routing on a time-based holdout.

Train = earliest 85% of tickets by reported date, test = latest 15%. Results are also
reported on the "unseen phrasing" subset (test tickets whose text variant never appears
in train), which guards against near-duplicate leakage from the synthetic text pool.

    python eval/evaluate.py                    # retrieval + classifier
    python eval/evaluate.py --llm gpt-oss-20b  # + hybrid routing with LLM fallback (slower)
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, loaders  # noqa: E402
from app.index import Encoder, Index, query_text  # noqa: E402
from app.router import Router, labels_of, route  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=400, help="test tickets used for retrieval eval")
    ap.add_argument("--llm", default=None, help="also evaluate hybrid routing with this LLM")
    ap.add_argument("--llm-queries", type=int, default=150)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results.json"))
    a = ap.parse_args()

    corpus = loaders.load(config.DATA_LOADER, config.DATA_SOURCE)
    T = corpus.tickets
    enc = Encoder()
    t0 = time.time()
    idx = Index(T, enc)
    print(f"indexed {len(T)} tickets in {time.time() - t0:.0f}s")
    n = len(T)
    cut = int(n * 0.85)
    train = np.zeros(n, bool)
    train[:cut] = True
    truth = corpus.truth
    train_variants = {truth.get(t["id"], {}).get("text_variant") for t in T[:cut]} - {"", None}
    test_ids = [i for i in range(cut, n) if T[i]["status"] in ("Closed", "Resolved") and T[i].get("resolution")
                and truth.get(T[i]["id"], {}).get("text_author") != "system"]
    unseen = {i for i in test_ids if truth.get(T[i]["id"], {}).get("text_variant") not in train_variants}
    print(f"train {cut}, test {len(test_ids)} (unseen phrasing {len(unseen)})")

    # ---------------- routing: classifier only
    router = Router(idx.emb, T, train_mask=train)
    res = {"n_test": len(test_ids), "n_unseen": len(unseen)}

    def acc(pairs):
        pairs = [p for p in pairs if p[1]]
        return round(float(np.mean([a_ == b_ for a_, b_ in pairs])), 3) if pairs else None

    preds = {i: router.predict(idx.emb[i]) for i in test_ids}
    for f in ("group", "category", "priority"):
        rec = [(preds[i][f][0][0], labels_of(T[i])[f]) for i in test_ids]
        res[f"clf_{f}_acc_recorded"] = acc(rec)
        res[f"clf_{f}_acc_recorded_unseen"] = acc([r for r, i in zip(rec, test_ids) if i in unseen])
    gt_group = [(preds[i]["group"][0][0], truth[T[i]["id"]]["true_owner_group"]) for i in test_ids]
    res["clf_group_acc_truth"] = acc(gt_group)
    gt_cat = [(preds[i]["category"][0][0], " › ".join(x for x in [truth[T[i]["id"]]["true_Categorization_Tier_1"],
                                                                  truth[T[i]["id"]]["true_Categorization_Tier_2"],
                                                                  truth[T[i]["id"]]["true_Categorization_Tier_3"]] if x))
              for i in test_ids]
    res["clf_category_acc_truth"] = acc(gt_cat)
    conf = np.array([preds[i]["group"][0][1] for i in test_ids])
    ok = np.array([a_ == b_ for a_, b_ in gt_group])
    for th in (0.4, 0.55, 0.7):
        m = conf >= th
        res[f"clf_group_truth_acc_conf>={th}"] = round(float(ok[m].mean()), 3) if m.any() else None
        res[f"clf_group_coverage_conf>={th}"] = round(float(m.mean()), 3)

    # ---------------- retrieval
    rng = np.random.default_rng(7)
    q_ids = list(rng.choice(test_ids, size=min(a.queries, len(test_ids)), replace=False))
    rr, r1, r5, lat = [], [], [], []
    for i in q_ids:
        t = T[i]
        q = {"summary": t["summary"], "details": t["details"]}
        s0 = time.time()
        hits = idx.search(q, k=10, allowed=train, exclude_id=t["id"])
        lat.append(time.time() - s0)
        want = t.get("scenario")
        ranks = [r for r, h in enumerate(hits) if h["ticket"].get("scenario") == want]
        r1.append(bool(ranks) and ranks[0] == 0)
        r5.append(bool(ranks) and ranks[0] < 5)
        rr.append(1 / (ranks[0] + 1) if ranks else 0)
    res.update(retrieval_queries=len(q_ids), recall_at_1=round(float(np.mean(r1)), 3),
               recall_at_5=round(float(np.mean(r5)), 3), mrr_at_10=round(float(np.mean(rr)), 3),
               retrieval_p50_ms=int(np.median(lat) * 1000), retrieval_p95_ms=int(np.percentile(lat, 95) * 1000))
    # dense-only baseline for comparison (no BM25 fusion effect isolated, no rerank)
    rr0 = []
    for i in q_ids[:200]:
        t = T[i]
        hits = idx.search({"summary": t["summary"], "details": t["details"]}, k=10, allowed=train,
                          exclude_id=t["id"], rerank=False)
        ranks = [r for r, h in enumerate(hits) if h["ticket"].get("scenario") == t.get("scenario")]
        rr0.append(1 / (ranks[0] + 1) if ranks else 0)
    res["mrr_at_10_no_rerank"] = round(float(np.mean(rr0)), 3)

    # ---------------- hybrid routing with LLM fallback
    if a.llm:
        sub = q_ids[:a.llm_queries]
        okg, okc, used, lat = [], [], 0, []
        for i in sub:
            t = T[i]
            qtext = query_text({"summary": t["summary"], "details": t["details"]})
            hits = idx.search({"summary": t["summary"], "details": t["details"]}, k=6, allowed=train,
                              exclude_id=t["id"], qvec=idx.emb[i])
            s0 = time.time()
            r, meta = route(router, qtext, idx.emb[i], hits, model=a.llm)
            lat.append(time.time() - s0)
            used += any(v["method"] == "llm" for v in r.values())
            g = truth[t["id"]]
            okg.append(r["group"]["value"] == g["true_owner_group"])
            okc.append(r["category"]["value"] == " › ".join(x for x in [g["true_Categorization_Tier_1"],
                                                                        g["true_Categorization_Tier_2"],
                                                                        g["true_Categorization_Tier_3"]] if x))
        clf_g = [preds[i]["group"][0][0] == truth[T[i]["id"]]["true_owner_group"] for i in sub]
        res[f"hybrid_{a.llm}"] = dict(queries=len(sub), group_acc_truth=round(float(np.mean(okg)), 3),
                                     category_acc_truth=round(float(np.mean(okc)), 3),
                                     classifier_only_group_acc_same_subset=round(float(np.mean(clf_g)), 3),
                                     llm_used_share=round(used / len(sub), 3),
                                     route_p50_ms=int(np.median(lat) * 1000))
    print(json.dumps(res, indent=1))
    prev = json.load(open(a.out)) if os.path.exists(a.out) else {}
    prev.update(res)
    json.dump(prev, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
