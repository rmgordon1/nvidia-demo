#!/usr/bin/env python3
"""Stage 2 (fast): build a pool of LLM-written phrasing variants, then apply them to every ticket.

Instead of one LLM call per ticket, one call per (field x scenario x author/persona) writes several
variants containing {placeholders}. Each ticket draws a variant round-robin and the placeholders are
re-filled with that ticket's details, then persona noise is applied. The pool is cached and resumable.

    python llm_pool.py --out out/full                       # build pool + apply
    python llm_pool.py --out out/full --scale 1.5           # more variants (more LLM time, less reuse)
    python llm_pool.py --out out/full --apply-only          # re-apply cached pool
    python llm_pool.py --out out/full --estimate            # print call/token estimate only

ground_truth.csv gets text_source=pool and text_variant (the description's variant id) so train/test
splits can be grouped by variant to avoid near-duplicate leakage.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

from helixgen import textgen as tg
from helixgen.export import AGENCY_BLURB, PERSONA_DESC, pick_model, write_csv
from helixgen.org import PERSONAS
from helixgen.scenarios import ALERT, MI_EVENTS, SCENARIOS

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODELS = "gpt-oss:20b=3,gemma4:26b=3,granite4.2:latest=2,nemotron-3.5-lightning:30b=1"
SLOTS = {"name", "ci", "line", "site", "product", "err", "host", "dept", "to_group", "from_group"}
SLOT_RE = re.compile(r"\{(\w+)\}")
ID_RE = re.compile(r"\b(?:INC|CRQ|PBI|WLG)\d{12}\b")

SYSTEM = f"""You write realistic text for IT service desk tickets in a BMC Helix ITSM system.
Context: {AGENCY_BLURB}
You produce several DIFFERENT variants of one kind of ticket text. Variants must differ in wording, length,
detail and structure - as if written by different people on different days. Write like real tickets:
specific, uneven, sometimes terse, never marketing-like. Do not mention the data is synthetic.
Use these placeholders (with braces) instead of concrete values wherever such a detail appears:
{{name}} customer first name, {{ci}} affected device/host/CI name, {{line}} production line (e.g. a press),
{{site}} facility name, {{product}} product/application, {{err}} exact error message text, {{dept}} customer's department.
Use only those placeholders; never invent other {{...}} tokens. Do not invent ticket numbers or people's names.
Return only JSON matching the schema."""

SCHEMA_DESC = {"type": "object", "properties": {"variants": {"type": "array", "items": {
    "type": "object", "properties": {"summary": {"type": "string"}, "description": {"type": "string"}},
    "required": ["summary", "description"]}}}, "required": ["variants"]}
SCHEMA_TEXT = {"type": "object", "properties": {"variants": {"type": "array", "items": {
    "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}, "required": ["variants"]}


def scen_brief(sc):
    T = sc["text"]
    return dict(product=sc["product"], manufacturer=sc["mfr"], category=" > ".join(sc["op"]),
                environment="production plant floor (OT)" if sc["domain"] == "OT" else "office IT",
                example_summaries=T["summ"], example_descriptions=T["desc"], possible_errors=T["err"],
                typical_fixes=T["res"], typical_tech_notes=T["notes"])


def build_keys(scale):
    """key -> dict(kind, k, prompt, schema)"""
    keys = {}
    n = lambda base: max(2, int(round(base * scale)))  # noqa: E731
    for sc in SCENARIOS:
        sid, brief = sc["id"], scen_brief(sc)
        alert_only = sc["src"] == ALERT
        if not alert_only:
            for persona, _, pdesc in PERSONAS:
                keys[f"cust|{sid}|{persona}"] = dict(kind="desc", k=n(6), schema=SCHEMA_DESC, prompt=(
                    f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of the SUMMARY (short description as typed "
                    f"at submission, max 90 chars, describes the problem not the outcome) and DESCRIPTION written by the "
                    f"customer themself via email or the self-service portal. Customer writing style: {pdesc}. No greeting "
                    f"and no signature. 1-6 sentences; some variants include {{err}}, some mention {{ci}} or {{line}} "
                    f"where relevant, some mention business impact or what they already tried."))
            keys[f"agent|{sid}"] = dict(kind="desc", k=n(14), schema=SCHEMA_DESC, prompt=(
                f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of the SUMMARY (max 90 chars, the problem as "
                f"reported) and DESCRIPTION written by the service desk agent or on-site technician who took the "
                f"phone call or walk-in, in agent notes style (e.g. 'Caller states...', 'User reports...', fragments). "
                f"Vary which details are captured; some include {{err}}, {{ci}} or {{line}}."))
        keys[f"res|{sid}"] = dict(kind="text", k=n(10), schema=SCHEMA_TEXT, prompt=(
            f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of the RESOLUTION field, written by the resolving "
            f"technician, 1-3 sentences, each describing a plausible fix for this kind of issue (vary the root cause)."))
        keys[f"work|{sid}"] = dict(kind="text", k=n(12), schema=SCHEMA_TEXT, prompt=(
            f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of an internal technician WORK NOTE logged while "
            f"troubleshooting: short, jargon-y, often fragments (e.g. 'rdp'd in, cleared cache, pending user test')."))
        if not alert_only:
            keys[f"triage|{sid}"] = dict(kind="text", k=n(8), schema=SCHEMA_TEXT, prompt=(
                f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of a Tier-1 service desk TRIAGE NOTE written "
                f"just before escalating the ticket: what was checked/tried at first contact and why it can't be "
                f"fixed at Tier 1. 1-2 short sentences. Do not name the destination group."))
            keys[f"reopen|{sid}"] = dict(kind="text", k=n(5), schema=SCHEMA_TEXT, prompt=(
                f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of a short email REPLY from the customer saying "
                f"the problem came back or is not actually fixed after the ticket was resolved. Mix of tones."))
        ask = sc["text"].get("ask")
        if ask and not alert_only:
            keys[f"ask|{sid}"] = dict(kind="text", k=n(5), schema=SCHEMA_TEXT, prompt=(
                f"Ticket type: {json.dumps(brief)}\n\nWrite {{k}} variants of a brief public message from the technician "
                f"to the customer ({{name}}) asking them to: {ask}. Polite, short, varied."))
            keys[f"reply|{sid}"] = dict(kind="text", k=n(6), schema=SCHEMA_TEXT, prompt=(
                f"Ticket type: {json.dumps(brief)}\n\nThe technician asked the customer to: {ask}. Write {{k}} variants of "
                f"the customer's short email reply providing it (or partially providing it). Varied tones, some terse."))
    for ev in MI_EVENTS:
        keys[f"mi|{ev['id']}"] = dict(kind="text", k=n(10), schema=SCHEMA_TEXT, prompt=(
            f"Major incident: {ev['title']}. Supporting group: {ev['group']}. Eventual cause: {ev['cause']}.\n\nWrite "
            f"{{k}} variants of a major-incident STATUS UPDATE posted on the ticket at different stages (early "
            f"triage, investigating, workaround, fix in progress, monitoring). 1-3 sentences each. No update numbers."))
    keys["misroute|generic"] = dict(kind="text", k=n(24), schema=SCHEMA_TEXT, prompt=(
        "Write {k} variants of a terse internal note from a support group that received a ticket by mistake and is "
        "reassigning it. Use the placeholders {from_group} (their group) and {to_group} (where it's going) in each "
        "variant; these two placeholders are allowed here. Tones: curt, helpful, slightly annoyed, matter-of-fact."))
    return keys


def call(model, key, spec, timeout=900):
    prompt = spec["prompt"].replace("{k}", str(spec["k"]))
    body = {"model": model, "stream": False, "format": spec["schema"],
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.95, "top_p": 0.95, "num_ctx": 8192, "num_predict": 4000},
            "keep_alive": "30m", "think": "low" if model.startswith("gpt-oss") else False}
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    return json.loads(js["message"]["content"]).get("variants") or [], js.get("eval_count", 0)


def clean_variants(spec, vs, key):
    allowed = SLOTS if key.startswith("misroute") else SLOTS - {"to_group", "from_group"}
    out, seen = [], set()
    for v in vs:
        if not isinstance(v, dict):
            continue
        v = {k: (v.get(k) or "").strip().strip('"') for k in (("summary", "description") if spec["kind"] == "desc" else ("text",))}
        txt = " ".join(v.values())
        if any(len(x) < 3 for x in v.values()) or ID_RE.search(txt):
            continue
        if any(s not in allowed for s in SLOT_RE.findall(txt)) or "{" in SLOT_RE.sub("", txt):
            continue
        if key.startswith("misroute") and "{to_group}" not in txt:
            continue
        if "summary" in v:
            v["summary"] = v["summary"][:100]
        sig = txt.lower()[:80]
        if sig in seen:
            continue
        seen.add(sig)
        out.append(v)
    return out


def build_pool(out, models, scale, concurrency):
    keys = build_keys(scale)
    path = os.path.join(out, "_work", "pool_cache.jsonl")
    done = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                c = json.loads(line)
                done[c["key"]] = c
            except json.JSONDecodeError:
                pass
    todo = [k for k in keys if k not in done]
    print(f"pool keys: {len(keys)}, cached: {len(done)}, to run: {len(todo)}, "
          f"variants requested: {sum(keys[k]['k'] for k in todo)}", flush=True)
    by_model = collections.defaultdict(list)
    for k in todo:
        by_model[pick_model(k, models)].append(k)
    lock = threading.Lock()
    t_all = time.time()
    with open(path, "a") as fh:
        for model, ks in by_model.items():
            t0, toks, nv = time.time(), 0, 0

            def work(key):
                err = None
                for attempt in range(3):
                    try:
                        vs, n = call(model, key, keys[key])
                        vs = clean_variants(keys[key], vs, key)
                        if len(vs) >= max(1, keys[key]["k"] // 3):
                            return key, vs, n, None
                        err = f"only {len(vs)} usable variants"
                    except Exception as e:  # noqa: BLE001 - retry any model/HTTP/JSON failure
                        err = e
                    time.sleep(1 + attempt)
                return key, None, 0, err

            with ThreadPoolExecutor(concurrency) as ex:
                futs = [ex.submit(work, k) for k in ks]
                for i, fu in enumerate(as_completed(futs), 1):
                    key, vs, n, err = fu.result()
                    with lock:
                        if vs:
                            fh.write(json.dumps(dict(key=key, model=model, variants=vs)) + "\n")
                            fh.flush()
                            toks += n
                            nv += len(vs)
                        else:
                            print(f"  ! {key} failed on {model}: {err}", flush=True)
                    if i % 20 == 0 or i == len(ks):
                        el = time.time() - t0
                        print(f"  [{model}] {i}/{len(ks)} keys, {nv} variants, {toks / max(el, 1):.0f} tok/s, "
                              f"model eta {(len(ks) - i) * el / i / 60:.0f} min, total elapsed {(time.time() - t_all) / 60:.0f} min",
                              flush=True)


def apply_pool(out, seed):
    work = os.path.join(out, "_work")
    style = json.load(open(os.path.join(work, "meta.json")))["style"]
    pool = {}
    for line in open(os.path.join(work, "pool_cache.jsonl")):
        c = json.loads(line)
        pool[c["key"]] = c
    jobs = {}
    for line in open(os.path.join(work, "llm_jobs.jsonl")):
        j = json.loads(line)
        jobs[j["num"]] = j

    def load(t):
        with gzip.open(os.path.join(work, f"{t}.rows.jsonl.gz"), "rt") as fh:
            return [json.loads(l) for l in fh]

    hd, wl = load("HPD_Help_Desk"), load("HPD_WorkLog")
    order = {}   # per-key shuffled round-robin
    counters = collections.Counter()

    def draw(key, ctx=None):
        """Round-robin draw; skips variants needing {err}/{line} when the ticket has no such detail."""
        c = pool.get(key)
        if not c:
            return None, None
        n = len(c["variants"])
        if key not in order:
            h = int(hashlib.md5(f"{seed}:{key}".encode()).hexdigest()[:8], 16)
            order[key] = list(np.random.default_rng(h).permutation(n))
        missing = [k for k in ("err", "line") if ctx is not None and not ctx.get(k)]
        for _ in range(n):
            i = order[key][counters[key] % n]
            counters[key] += 1
            if not any("{%s}" % k in " ".join(c["variants"][i].values()) for k in missing):
                return c["variants"][i], f"{key}#{i}"
        return c["variants"][i], f"{key}#{i}"

    def fill(t, ctx):
        t = SLOT_RE.sub(lambda m: str(ctx.get(m.group(1)) or {"err": "an error", "ci": "my computer",
                                                               "line": "the line"}.get(m.group(1), "")), t)
        return re.sub(r"([.!?])\.(?!\.)", r"\1", t)

    wl_by_id = {w["Work_Log_ID"]: w for w in wl}
    variant_of, n_inc, n_wl = {}, 0, 0
    for r in hd:
        j = jobs.get(r["Incident_Number"])
        if not j:
            continue
        f, ctx = j["facts"], dict(j["ctx"])
        ctx["product"] = f["product"]
        rng = np.random.default_rng(int(r["Entry_ID"]))
        author = f["description_author"]
        key = f"cust|{j['scenario']}|{j['persona']}" if author == "customer" else \
            (f"agent|{j['scenario']}" if author == "agent" else None)
        if key and j.get("mi_role") != "parent":
            v, vid = draw(key, ctx)
            if v:
                summ, desc = fill(v["summary"], ctx), fill(v["description"], ctx)
                if author == "customer":
                    summ = tg.persona_style(rng, summ, j["persona"], "summ")
                    desc = tg.persona_style(rng, desc, j["persona"], "desc")
                if j.get("email_wrap"):
                    hdr, greet, sig = j["email_wrap"]
                    desc = hdr + greet + desc + sig
                    if hdr:
                        summ = "FW: " + summ
                if not j["keep_summary"]:
                    r["Description"] = summ[:100]
                r["Detailed_Decription"] = desc
                variant_of[r["Incident_Number"]] = vid
                n_inc += 1
        seed_res = f.get("resolution_seed")
        if r.get("Resolution") and seed_res and len(seed_res) > 15 and not ID_RE.search(seed_res) \
                and not seed_res.startswith(("Duplicate", "User resolved", "Customer no longer", "Ticket opened")) \
                and j.get("mi_role") != "parent":
            v, _ = draw(f"res|{j['scenario']}", ctx)
            if v:
                res = fill(v["text"], ctx)
                if "UPDATE:" in r["Resolution"]:
                    v2, _ = draw(f"res|{j['scenario']}")
                    res += " UPDATE: " + fill((v2 or v)["text"], ctx)
                if j.get("mi_role") == "child":
                    res = r["Resolution"].split(". ", 1)[0] + ". " + res
                r["Resolution"] = res
        k_upd = 0
        for w in j["worklogs"]:
            row = wl_by_id.get(w["id"])
            if row is None:
                continue
            kind, wctx = w["kind"], dict(ctx, **{k: v for k, v in (w.get("ctx") or {}).items() if isinstance(v, str)})
            text = None
            if kind == "triage":
                v, _ = draw(f"triage|{j['scenario']}", wctx)
                if v:
                    text = fill(v["text"], wctx).rstrip() + f" Transferring to {wctx.get('to_group', '')}."
            elif kind == "work":
                v, _ = draw(f"work|{j['scenario']}", wctx)
                text = fill(v["text"], wctx) if v else None
                if text and w["seed"].startswith("Follow-up after reopen"):
                    text = "Follow-up after reopen: " + text
            elif kind in ("ask", "reply", "reopen"):
                v, _ = draw(f"{kind}|{j['scenario']}", wctx)
                if v:
                    text = fill(v["text"], wctx)
                    if kind != "ask":
                        text = tg.persona_style(rng, text, j["persona"], "desc")
            elif kind == "misroute" and "Re-triaged" not in w["seed"] and "Returning to Service Desk" not in w["seed"]:
                v, _ = draw("misroute|generic")
                text = fill(v["text"], wctx) if v else None
            elif kind == "resolution_comm" and j.get("mi_role") != "parent" and r.get("Resolution"):
                m = re.match(r"(Hello \w+, your incident has been resolved: ).*?( If the issue returns.*)$", w["seed"], re.S)
                if m:
                    text = m.group(1) + r["Resolution"].split(" UPDATE: ")[0] + m.group(2)
            elif kind == "status_update" and j.get("mi_event"):
                v, _ = draw(f"mi|{j['mi_event']}")
                k_upd += 1
                text = f"Update {k_upd}: " + fill(v["text"], wctx) if v else None
            if text:
                row["Detailed_Description"] = text
                row["Description"] = text.split("\n")[0].split(". ")[0][:100]
                n_wl += 1
    write_csv(os.path.join(out, "HPD_Help_Desk.csv"), "HPD_Help_Desk", hd, style)
    write_csv(os.path.join(out, "HPD_WorkLog.csv"), "HPD_WorkLog", wl, style)
    gtp = os.path.join(out, "ground_truth.csv")
    rows = list(csv.DictReader(open(gtp, newline="")))
    for g in rows:
        vid = variant_of.get(g["Incident_Number"])
        g["text_source"] = f"pool:{pool[vid.split('#')[0]]['model']}" if vid else "template"
        g["text_variant"] = vid or ""
    with open(gtp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    reuse = collections.Counter(variant_of.values())
    print(f"applied pool: {n_inc} descriptions, {n_wl} work logs; distinct description variants used "
          f"{len(reuse)}, mean reuse {np.mean(list(reuse.values())):.1f}, max {max(reuse.values())}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplier on variants per pool key")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--apply-only", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args()
    if a.estimate:
        keys = build_keys(a.scale)
        nv = sum(k["k"] for k in keys.values())
        print(f"{len(keys)} LLM calls, {nv} variants, ~{nv * 70 / 1000:.0f}k output tokens, "
              f"~{nv * 70 / 130 / 60:.0f} min at 130 tok/s")
        return
    if not a.apply_only:
        build_pool(a.out, a.models.split(","), a.scale, a.concurrency)
    apply_pool(a.out, a.seed)


if __name__ == "__main__":
    main()
