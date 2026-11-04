#!/usr/bin/env python3
"""Stage 2: rewrite ticket text with local Ollama models, then merge into the CSVs.

Resumable: results are appended to <out>/_work/llm_cache.jsonl, and finished
tickets are skipped on re-run. Jobs are processed one model at a time so models
are not swapped in and out of memory.

    python llm_pass.py --out out/sample                  # run + merge
    python llm_pass.py --out out/full --concurrency 4
    python llm_pass.py --out out/full --merge-only       # rebuild CSVs from cache
"""
import argparse
import collections
import csv
import gzip
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

from helixgen import textgen as tg
from helixgen.export import AGENCY_BLURB, write_csv

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
ID_RE = re.compile(r"\b(?:INC|CRQ|PBI|WLG)\d{12}\b")

SYSTEM = f"""You write realistic text for IT service desk tickets in a BMC Helix ITSM system.
Context: {AGENCY_BLURB}
You are given structured facts about one incident and must write its free-text fields so they read like
real tickets typed by real people: specific, uneven, sometimes terse, never marketing-like. Rules:
- Stay consistent with the facts (product, CI/host names, error text, timeline, outcome). Do not invent new
  incident/change/problem numbers; copy any IDs that appear in seeds exactly.
- Customer-written text follows the customer's persona. Do not add a greeting or email signature.
- Technician work notes are short, internal, jargon-y, often fragments ("rdp'd in, cleared cache, pending user test").
- Agent-written descriptions (phone/walk-in) read like a service desk agent's notes ("Caller states ...").
- Resolution text is written by the resolving technician, 1-3 sentences, in their own words.
- Seeds are rough placeholders, NOT text to copy: never reuse more than 5 consecutive words from a seed. Rewrite every field in fresh wording and add plausible specific
  detail (what was tried, times, business impact, what the tech actually checked). Generic seeds such as
  "Customer replied with the requested information" must become the actual reply content. Vary length and tone
  between authors. Templated system emails (resolution_comm) may stay close to the seed.
- The summary describes the problem as reported when the ticket was opened, never the outcome.
- Never mention that the data is synthetic, fictional, or generated. No real-world agency names.
Return only JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "resolution": {"type": "string"},
        "worklogs": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "text": {"type": "string"}}, "required": ["id", "text"]}},
    },
    "required": ["summary", "description", "resolution", "worklogs"],
}

KIND_HINT = {
    "triage": "service desk triage note before transferring",
    "misroute": "note from a group that received the ticket by mistake, reassigning it (can be curt)",
    "work": "technician troubleshooting note",
    "ask": "message to the customer asking for information (public, polite but brief)",
    "reply": "the customer's reply with the requested information (customer persona)",
    "vendor": "note about opening/working a vendor support case",
    "change": "note that the fix needs a change request (keep CRQ id)",
    "resolution_comm": "resolution email to the customer",
    "reopen": "customer saying the problem came back (customer persona)",
    "mi_link": "note linking this ticket to the major incident (keep INC id)",
    "status_update": "major-incident status update for stakeholders",
    "dup": "note cancelling a duplicate ticket (keep INC id)",
    "escalation": "note that urgency was raised after an escalation",
}


def build_prompt(job):
    f = job["facts"]
    wl = [dict(id=w["id"], kind=w["kind"], purpose=KIND_HINT.get(w["kind"], ""), type=w["type"], author=w["author"],
               author_role=w["author_role"], group=w["group"], visibility=w["visibility"], seed=w["seed"])
          for w in job["worklogs"]]
    desc_rule = {
        "customer": "description: written by the customer (persona applies), 1-6 sentences, may include pasted error text.",
        "agent": "description: written by the service desk agent or technician who took the call, notes style.",
        "system": "description: monitoring alert text; keep it machine-like, close to the seed.",
    }[f["description_author"]]
    return (
        "FACTS:\n" + json.dumps(f, indent=1) +
        "\n\nWORK LOG ENTRIES (rewrite each `seed`, same id, same order):\n" + json.dumps(wl, indent=1) +
        "\n\nWrite:\n- summary: the ticket's short description as typed at submission, max 90 characters.\n- "
        + desc_rule +
        "\n- resolution: technician's resolution text consistent with resolution_seed (empty string if resolution_seed is null)."
        "\n- worklogs: one entry per work log above, with the same id."
    )


def call_ollama(model, prompt, timeout=600):
    body = {"model": model, "stream": False, "format": SCHEMA,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.85, "top_p": 0.95, "num_ctx": 8192, "num_predict": 2500},
            "keep_alive": "30m"}
    body["think"] = "low" if model.startswith("gpt-oss") else False
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    if r.status_code == 400 and "think" in r.text:
        body.pop("think")
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    return json.loads(js["message"]["content"]), js.get("eval_count", 0)


def validate(job, out):
    """Return cleaned dict of overrides, keeping template text where output is unusable."""
    f = job["facts"]
    res = {"worklogs": {}}
    s = (out.get("summary") or "").strip().strip('"')
    if s and not job["keep_summary"]:
        res["summary"] = s[:100]
    d = (out.get("description") or "").strip()
    if d and f["description_author"] != "system" and len(d) > 15:
        res["description"] = d
    seed = f.get("resolution_seed")
    rz = (out.get("resolution") or "").strip()
    if seed and rz and len(seed) > 15 and not ID_RE.search(seed):
        res["resolution"] = rz
    by_id = {w.get("id"): (w.get("text") or "").strip() for w in out.get("worklogs") or [] if isinstance(w, dict)}
    for w in job["worklogs"]:
        t = by_id.get(w["id"])
        if not t or len(t) < 3:
            continue
        need = set(ID_RE.findall(w["seed"]))
        if need and not need.issubset(set(ID_RE.findall(t))):
            continue
        res["worklogs"][w["id"]] = t
    return res


def run_jobs(out, jobs, concurrency, limit):
    cache_path = os.path.join(out, "_work", "llm_cache.jsonl")
    done = set()
    if os.path.exists(cache_path):
        with open(cache_path) as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["num"])
                except (json.JSONDecodeError, KeyError):
                    pass
    todo = [j for j in jobs if j["num"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(jobs)} jobs, {len(done)} cached, {len(todo)} to run", flush=True)
    lock = threading.Lock()
    by_model = collections.defaultdict(list)
    for j in todo:
        by_model[j["model"]].append(j)
    with open(cache_path, "a") as cache:
        for model, mj in by_model.items():
            t0, n_ok, n_err, toks = time.time(), 0, 0, 0

            def work(job):
                last = None
                for attempt in range(3):
                    try:
                        o, n = call_ollama(model, build_prompt(job))
                        return job, validate(job, o), n, None
                    except Exception as e:  # noqa: BLE001 - retry any model/HTTP/JSON failure
                        last = e
                        time.sleep(2 * (attempt + 1))
                return job, None, 0, last

            with ThreadPoolExecutor(concurrency) as ex:
                futs = [ex.submit(work, j) for j in mj]
                for k, fu in enumerate(as_completed(futs), 1):
                    job, res, n, err = fu.result()
                    with lock:
                        if res is not None:
                            cache.write(json.dumps(dict(num=job["num"], model=model, res=res)) + "\n")
                            cache.flush()
                            n_ok += 1
                            toks += n
                        else:
                            n_err += 1
                            print(f"  ! {job['num']} failed on {model}: {err}")
                    if k % 10 == 0 or k == len(mj):
                        el = time.time() - t0
                        print(f"  [{model}] {k}/{len(mj)} ok={n_ok} err={n_err} {el / k:.1f}s/ticket "
                              f"{toks / max(el, 1):.0f} tok/s  eta {(len(mj) - k) * el / k / 60:.0f} min", flush=True)


def merge(out):
    work = os.path.join(out, "_work")
    style = json.load(open(os.path.join(work, "meta.json")))["style"]
    jobs = {}
    with open(os.path.join(work, "llm_jobs.jsonl")) as fh:
        for line in fh:
            j = json.loads(line)
            jobs[j["num"]] = j
    cache = {}
    cp = os.path.join(work, "llm_cache.jsonl")
    if os.path.exists(cp):
        with open(cp) as fh:
            for line in fh:
                try:
                    c = json.loads(line)
                    cache[c["num"]] = c
                except json.JSONDecodeError:
                    pass

    def load(t):
        with gzip.open(os.path.join(work, f"{t}.rows.jsonl.gz"), "rt") as fh:
            return [json.loads(l) for l in fh]

    hd, wl = load("HPD_Help_Desk"), load("HPD_WorkLog")
    wl_over, wl_customer, n = {}, {}, 0
    for num, c in cache.items():
        j = jobs.get(num)
        if not j:
            continue
        for w in j["worklogs"]:
            wl_customer[w["id"]] = (w["author_role"] == "customer", j["persona"], num)
        wl_over.update(c["res"]["worklogs"])
    stale = [num for num in cache if num not in jobs]
    if stale:
        print(f"warning: {len(stale)} cached results have no matching job (stage 1 re-run with different "
              f"settings?) - ignoring them")
        for num in stale:
            del cache[num]
    for r in hd:
        c = cache.get(r["Incident_Number"])
        if not c:
            continue
        j, res = jobs[r["Incident_Number"]], c["res"]
        rng = np.random.default_rng(int(r["Entry_ID"]))
        cust = j["facts"]["description_author"] == "customer"
        if "summary" in res:
            r["Description"] = tg.persona_style(rng, res["summary"], j["persona"], "summ") if cust else res["summary"]
        if "description" in res:
            body = tg.persona_style(rng, res["description"], j["persona"], "desc") if cust else res["description"]
            if j.get("email_wrap"):
                hdr, greet, sig = j["email_wrap"]
                body = hdr + greet + body + sig
            r["Detailed_Decription"] = body
        if "resolution" in res and r.get("Resolution"):
            if "UPDATE:" in (r["Resolution"] or ""):
                r["Resolution"] = res["resolution"] + " UPDATE: " + r["Resolution"].split("UPDATE:", 1)[1].strip()
            else:
                r["Resolution"] = res["resolution"]
        n += 1
    for r in wl:
        t = wl_over.get(r["Work_Log_ID"])
        if t:
            is_cust, persona, num = wl_customer.get(r["Work_Log_ID"], (False, "average", ""))
            if is_cust:
                t = tg.persona_style(np.random.default_rng(int(r["Work_Log_ID"][3:])), t, persona, "desc")
            r["Detailed_Description"] = t
            r["Description"] = t.split("\n")[0].split(". ")[0][:100]
    write_csv(os.path.join(out, "HPD_Help_Desk.csv"), "HPD_Help_Desk", hd, style)
    write_csv(os.path.join(out, "HPD_WorkLog.csv"), "HPD_WorkLog", wl, style)
    gtp = os.path.join(out, "ground_truth.csv")
    with open(gtp, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for g in rows:
        c = cache.get(g["Incident_Number"])
        g["text_source"] = f"llm:{c['model']}" if c else "template"
    with open(gtp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"merged LLM text into {n} incidents, {len(wl_over)} work logs")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only run this many new jobs (testing)")
    ap.add_argument("--merge-only", action="store_true")
    a = ap.parse_args()
    if not a.merge_only:
        with open(os.path.join(a.out, "_work", "llm_jobs.jsonl")) as fh:
            jobs = [json.loads(l) for l in fh]
        run_jobs(a.out, jobs, a.concurrency, a.limit)
    merge(a.out)


if __name__ == "__main__":
    main()
