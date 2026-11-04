#!/usr/bin/env python3
"""Build a realistic synthetic raw-material → output extract into webapp/data/materials/.

Simulates 12 months of a currency-style print plant day by day (production runs,
FIFO lot consumption, reorder-point procurement, monthly cycle counts) and writes
two separate source-system extracts that must be joined:

  ERP (Oracle EBS-style):  mtl_system_items, ap_suppliers, po_asl, bom_components,
                           po_headers, po_lines, mtl_lot_numbers, wip_discrete_jobs,
                           mtl_material_transactions, mtl_onhand_quantities,
                           mrp_schedule_dates
  MES (generic):           mes_production_runs, mes_spoilage_reasons,
                           mes_material_loads, mes_press_events, mes_environment

plus answer_key.json (planted scenarios with true quantities), data_dictionary.csv
and manifest.json.

Everything is synthetic: neutral product/vendor codes, plausible volumes, no real
denominations or agency data. Deterministic for a given seed:

    python3 tools/build_materials.py            # → data/materials/
    python3 tools/build_materials.py --seed 7   # a different but equivalent plant

Stdlib only.
"""
import argparse
import csv
import json
import math
import os
import random
from collections import defaultdict, deque
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "materials")

START = date(2025, 11, 4)
END = date(2026, 11, 3)          # last production day in the extract
EXTRACT = date(2026, 11, 4)      # "today"
HOLIDAYS = {date(2025, 11, 11), date(2025, 11, 27), date(2025, 12, 25), date(2026, 1, 1),
            date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25), date(2026, 6, 19),
            date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12)}

PRESSES = {"P01": "H1", "P02": "H1", "P03": "H1", "P04": "H2", "P05": "H2", "P06": "H2"}
OVI_PRESSES = {"P04", "P05", "P06"}          # optically-variable ink units fitted in Hall 2
SHIFTS = {"A": (6, 14), "B": (14, 22), "C": (22, 30)}
SHEETS_PER_SHIFT = 24000

# ---------------------------------------------------------------- vendors
VENDORS = [
    ("VND-A", "Substrate supplier A"), ("VND-B", "Substrate supplier B"),
    ("VND-C", "Intaglio ink supplier C"), ("VND-D", "Offset & numbering ink supplier D"),
    ("VND-E", "Press consumables supplier E"), ("VND-F", "Optically variable ink supplier F"),
    ("VND-G", "Coatings & chemicals supplier G"), ("VND-H", "Packaging supplier H"),
]
VENDOR_ID = {code: 5001 + i for i, (code, _) in enumerate(VENDORS)}

# ---------------------------------------------------------------- items
# code: (description, uom, class, std_cost, lead_days, moq, multiple, supply, vendors)
#   supply PUSH = issued from a measured/counted quantity; PULL = backflushed at BOM standard
ITEMS = {}


def item(code, desc, uom, cls, cost, lead, moq, mult, supply, vendors, shelf=0, status="Active"):
    ITEMS[code] = dict(code=code, desc=desc, uom=uom, cls=cls, cost=cost, lead=lead, moq=moq,
                       mult=mult, supply=supply, vendors=vendors, shelf=shelf, status=status)


for c, cost in (("A", 0.088), ("B", 0.092), ("C", 0.105), ("D", 0.112)):
    item(f"SUB-{c}", f"Security substrate, grade {c} (sheets)", "SHT", "SUBSTRATE", cost, 70, 200000, 10000, "PUSH", ["VND-A", "VND-B"])
item("INK-IB-01", "Intaglio ink, black", "KG", "INK", 48.0, 42, 200, 50, "PUSH", ["VND-C"], 365)
item("INK-IG-01", "Intaglio ink, green", "KG", "INK", 62.0, 42, 200, 50, "PUSH", ["VND-C"], 365)
for n in range(2, 8):
    item(f"INK-IC-{n:02d}", f"Intaglio ink, face color {n:02d}", "KG", "INK", 66.0 + n, 42, 100, 25, "PUSH", ["VND-C"], 365)
for n in range(1, 19):
    item(f"INK-OF-{n:02d}", f"Offset ink, background color {n:02d}", "KG", "INK", 40.0 + (n * 7) % 35, 42, 50, 25, "PUSH", ["VND-D"], 365)
for n in range(1, 4):
    item(f"INK-OV-{n:02d}", f"Optically variable ink {n:02d}", "KG", "INK", 395.0 + 15 * n, 84, 50, 10, "PUSH", ["VND-F"], 270)
item("INK-NB-01", "Numbering ink, black", "KG", "INK", 38.0, 42, 50, 25, "PUSH", ["VND-D"], 365)
item("INK-NG-01", "Numbering ink, green", "KG", "INK", 41.0, 42, 50, 25, "PUSH", ["VND-D"], 365)
item("VRN-01", "Overprint varnish, standard", "KG", "COATING", 31.0, 35, 500, 100, "PULL", ["VND-G"], 365)
item("VRN-02", "Overprint varnish, high-durability", "KG", "COATING", 36.0, 35, 500, 100, "PULL", ["VND-G"], 365)
item("WIP-SOL-01", "Intaglio wiping solution", "L", "CHEMICAL", 4.2, 28, 2000, 200, "PULL", ["VND-G"])
item("WIP-PAP-01", "Wiping paper", "M", "CONSUMABLE", 0.9, 28, 5000, 500, "PULL", ["VND-E"])
item("CLN-SOL-01", "Press cleaning solvent", "L", "CHEMICAL", 6.5, 28, 1000, 200, "PULL", ["VND-G"])
item("FNT-SOL-01", "Offset fountain solution", "L", "CHEMICAL", 2.1, 28, 1000, 200, "PULL", ["VND-G"])
item("ADD-01", "Ink drier additive", "KG", "CHEMICAL", 28.0, 28, 50, 10, "PULL", ["VND-G"], 365)
item("ADD-02", "OVI flow additive", "KG", "CHEMICAL", 44.0, 28, 25, 5, "PULL", ["VND-G"], 365)
item("PKG-01", "Banding strap", "M", "PACKAGING", 0.12, 21, 5000, 1000, "PULL", ["VND-H"])
item("PKG-02", "Shrink film", "KG", "PACKAGING", 5.5, 21, 200, 50, "PULL", ["VND-H"])
for n in range(1, 8):
    item(f"PLT-{n:02d}", f"Intaglio plate, product {n:02d}", "EA", "PLATE", 2400.0, 56, 2, 1, "PUSH", ["VND-E"])
item("OFP-01", "Offset printing plate", "EA", "PLATE", 35.0, 21, 200, 50, "PUSH", ["VND-E"])
item("BLK-01", "Offset blanket", "EA", "CONSUMABLE", 260.0, 28, 20, 10, "PUSH", ["VND-E"])
# legacy items still in the master (no activity) — realistic clutter
item("SUB-X", "Security substrate, legacy grade X", "SHT", "SUBSTRATE", 0.081, 70, 200000, 10000, "PUSH", ["VND-A"], status="Inactive")
item("INK-IC-08", "Intaglio ink, face color 08 (retired)", "KG", "INK", 74.0, 42, 100, 25, "PUSH", ["VND-C"], 365, "Inactive")
item("INK-OF-19", "Offset ink, background color 19 (retired)", "KG", "INK", 52.0, 42, 50, 25, "PUSH", ["VND-D"], 365, "Inactive")
item("INK-OF-20", "Offset ink, background color 20 (retired)", "KG", "INK", 49.0, 42, 50, 25, "PUSH", ["VND-D"], 365, "Inactive")
item("ADD-04", "Anti-setoff powder (superseded)", "KG", "CHEMICAL", 12.0, 28, 50, 10, "PULL", ["VND-G"], status="Inactive")
item("PLT-08", "Intaglio plate, product 08 (retired)", "EA", "PLATE", 2400.0, 56, 2, 1, "PUSH", ["VND-E"], status="Inactive")

# ---------------------------------------------------------------- products + BOMs
# Rates are per 1,000 good sheets (stored in the BOM per single sheet, as Oracle would).
RATE = {"INK-IB-01": 1.10, "INK-IG-01": 2.40, "IC": 0.55, "OF": 0.22, "OV": 0.28, "INK-NB-01": 0.12, "INK-NG-01": 0.10,
        "VRN": 0.90, "WIP-SOL-01": 3.5, "WIP-PAP-01": 8.0, "CLN-SOL-01": 0.40, "FNT-SOL-01": 1.20, "ADD-01": 0.05,
        "ADD-02": 0.03, "PKG-01": 0.6, "PKG-02": 0.08}
PRODUCTS = {
    "PRD-01": dict(subjects=50, allow=0.040, weight=0.30, sub="SUB-A", ic=None, of=[1, 2, 3], ov=None, vrn="VRN-01"),
    "PRD-02": dict(subjects=50, allow=0.042, weight=0.08, sub="SUB-A", ic=2, of=[4, 5, 6], ov=None, vrn="VRN-01"),
    "PRD-03": dict(subjects=32, allow=0.045, weight=0.06, sub="SUB-B", ic=3, of=[7, 8], ov=None, vrn="VRN-01"),
    "PRD-04": dict(subjects=50, allow=0.040, weight=0.14, sub="SUB-B", ic=4, of=[9, 10, 11], ov=None, vrn="VRN-01"),
    "PRD-05": dict(subjects=50, allow=0.038, weight=0.20, sub="SUB-C", ic=5, of=[12, 13, 14], ov=1, vrn="VRN-02"),
    "PRD-06": dict(subjects=32, allow=0.045, weight=0.12, sub="SUB-D", ic=6, of=[15, 16], ov=2, vrn="VRN-02"),
    "PRD-07": dict(subjects=32, allow=0.048, weight=0.10, sub="SUB-D", ic=7, of=[17, 18], ov=3, vrn="VRN-02"),
}


def bom(prod):
    """[(component, qty_per, basis)] — basis 'ITEM' = per good sheet, 'LOT' = per job/campaign."""
    p = PRODUCTS[prod]
    rows = [(p["sub"], 1.0, "ITEM"), ("INK-IB-01", RATE["INK-IB-01"], "ITEM"), ("INK-IG-01", RATE["INK-IG-01"], "ITEM")]
    if p["ic"]:
        rows.append((f"INK-IC-{p['ic']:02d}", RATE["IC"], "ITEM"))
    rows += [(f"INK-OF-{n:02d}", RATE["OF"], "ITEM") for n in p["of"]]
    if p["ov"]:
        rows += [(f"INK-OV-{p['ov']:02d}", RATE["OV"], "ITEM"), ("ADD-02", RATE["ADD-02"], "ITEM")]
    rows += [("INK-NB-01", RATE["INK-NB-01"], "ITEM"), ("INK-NG-01", RATE["INK-NG-01"], "ITEM"), (p["vrn"], RATE["VRN"], "ITEM")]
    rows += [(c, RATE[c], "ITEM") for c in ("WIP-SOL-01", "WIP-PAP-01", "CLN-SOL-01", "FNT-SOL-01", "ADD-01", "PKG-01", "PKG-02")]
    rows += [(f"PLT-{int(prod[-2:]):02d}", 1, "LOT"), ("OFP-01", 4, "LOT")]
    # ITEM-basis rates are per 1,000 except substrate (1 sheet per sheet)
    return [(c, q if c.startswith("SUB-") or b == "LOT" else q / 1000.0, b) for c, q, b in rows]


BOMS = {p: bom(p) for p in PRODUCTS}

# ---------------------------------------------------------------- planted scenarios
S1 = dict(item="INK-IG-01", press="P02", week=date(2026, 10, 26), factor=1.12)
S2 = dict(item="SUB-A", window=(date(2026, 9, 24), date(2026, 10, 7)), vendor="VND-B", spoil=0.055)
S3 = dict(press="P03", shift="C", spoil=0.028)
S4 = dict(item="INK-IB-01", product="PRD-05", since=date(2026, 5, 4), factor=0.86)
S5 = dict(hall="H2", rh_threshold=56.0, per_pt=0.0012)
F1 = dict(vendor="VND-C", since=date(2026, 6, 15), slip_days=35)
F2 = dict(product="PRD-06", from_week=4, factor=1.60)
D1 = dict(item="WIP-SOL-01", hall="H2", since=date(2026, 8, 3), factor=1.09)
D2 = dict(press="P05", run_dates=(date(2026, 9, 28), date(2026, 9, 30)), posted=date(2026, 10, 5))
D3_MISSING = 6       # runs present in ERP, absent in MES
D3_DUPES = 3         # runs keyed twice in MES (and completed twice in ERP)
D3_TYPOS = 2         # manual entries with a transposed printed count
MANUAL_RATE = 0.012  # share of runs keyed by hand (counts rounded to the nearest 100)


# ---------------------------------------------------------------- helpers
def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def is_workday(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def next_workday(d):
    while not is_workday(d):
        d += timedelta(days=1)
    return d


def monday(d):
    return d - timedelta(days=d.weekday())


def ymd(d):
    return d.strftime("%y%m%d")


def round_up(q, moq, mult):
    q = max(q, moq)
    return int(math.ceil(q / mult) * mult)


def prec(it):
    return 0 if it["uom"] in ("SHT", "EA") else 2


def write_csv(name, rows, cols):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# ================================================================ build
def build(seed):
    R = random.Random(seed)
    gauss = R.gauss
    workdays = [d for d in daterange(START, END) if is_workday(d)]

    # ---------------- environment (daily, both halls)
    env = {}
    for d in daterange(START, END):
        doy = (d - date(2026, 7, 20)).days
        summer = math.exp(-(doy / 34.0) ** 2)
        for hall in ("H1", "H2"):
            if hall == "H1":
                rh = 47 + 2.5 * summer + gauss(0, 1.4)
                t = 21.5 + 1.0 * summer + gauss(0, 0.4)
            else:  # Hall 2 HVAC can't hold humidity in summer
                rh = 49 + 15 * summer + gauss(0, 2.0)
                t = 22.0 + 2.5 * summer + gauss(0, 0.5)
            env[(hall, d)] = (round(rh, 1), round(t, 1))

    # ---------------- press schedule: campaigns of 2–4 weeks, products by demand deficit
    weeks = sorted({monday(d) for d in workdays})
    assigned = defaultdict(float)
    press_product = {}                      # (press, week) -> product
    campaigns = []                          # (press, product, first_week)
    left = {p: 0 for p in PRESSES}
    current = {}
    for wi, wk in enumerate(weeks):
        total = sum(assigned.values()) + len(PRESSES)
        for p in PRESSES:
            if left[p] <= 0:
                allowed = [x for x in PRODUCTS if PRODUCTS[x]["ov"] is None or p in OVI_PRESSES]
                deficit = {x: PRODUCTS[x]["weight"] * total - assigned[x] for x in allowed}
                # Hall 2 presses prefer OVI products (only they can run them)
                if p in OVI_PRESSES:
                    for x in deficit:
                        if PRODUCTS[x]["ov"]:
                            deficit[x] += 1.5
                choice = max(sorted(deficit), key=lambda x: deficit[x])
                current[p] = choice
                left[p] = R.choice((2, 3, 3, 4))
                campaigns.append((p, choice, wk))
            press_product[(p, wk)] = current[p]
            assigned[current[p]] += 1
            left[p] -= 1

    # ---------------- press events known up front (downtime, PMs, blanket changes, calibrations)
    events = []
    downtime = set()
    for p in PRESSES:
        next_blanket = R.randint(5, 10)
        for i, d in enumerate(workdays):
            for s in SHIFTS:
                if R.random() < 0.012:
                    downtime.add((p, d, s))
                    events.append(dict(press=p, ts=f"{d}T{SHIFTS[s][0]:02d}:15", type="DOWNTIME", shift=s,
                                       note=R.choice(["Feeder fault", "Ink unit fault", "Power dip", "Sheet jam, long clear", "Awaiting QA release"]) + " — shift lost"))
            if i == next_blanket:
                events.append(dict(press=p, ts=f"{d}T06:30", type="BLANKET_CHANGE", shift="A", note="Offset blankets replaced (2)"))
                next_blanket += R.randint(8, 12)
            if d.day <= 7 and d.weekday() == 2:
                events.append(dict(press=p, ts=f"{d}T06:00", type="MAINTENANCE", shift="A", note="Monthly preventive maintenance"))
            if d.month in (1, 4, 7, 10) and d.day <= 7 and d.weekday() == 3:
                events.append(dict(press=p, ts=f"{d}T06:00", type="CALIBRATION", shift="A", note="Quarterly ink-meter calibration — within tolerance"))
    events.append(dict(press=S1["press"], ts="2026-11-02T06:10", type="CALIBRATION", shift="A",
                       note=f"{S1['item']} dispenser recalibrated after operator report — drift +11.6% found"))
    for d in daterange(START, END):
        if env[("H2", d)][0] >= 63 and is_workday(d):
            events.append(dict(press="P04", ts=f"{d}T13:00", type="HVAC_ALARM", shift="A", note=f"Hall 2 RH high ({env[('H2', d)][0]}%)"))
    blanket_days = {(e["press"], e["ts"][:10]) for e in events if e["type"] == "BLANKET_CHANGE"}

    # ---------------- runs (planned), WIP jobs (press × product × week)
    runs = []
    jobs = {}
    campaign_start = {(p, wk) for p, _, wk in campaigns}
    for d in workdays:
        wk = monday(d)
        for s in SHIFTS:
            for p in PRESSES:
                if (p, d, s) in downtime:
                    continue
                prod = press_product[(p, wk)]
                jname = f"WJ-{p}-{ymd(wk)}-{prod}"
                if jname not in jobs:
                    jobs[jname] = dict(name=jname, id=700000 + len(jobs), press=p, product=prod, week=wk, start_qty=0,
                                       completed=0, new_campaign=(p, wk) in campaign_start, first_run=None)
                planned = int(round(SHEETS_PER_SHIFT + gauss(0, 800)))
                run = dict(id=f"R{ymd(d)}{p}{s}", date=d, week=wk, shift=s, press=p, hall=PRESSES[p], product=prod,
                           job=jname, planned=planned, printed=int(round(planned * (0.975 + R.random() * 0.035))))
                if jobs[jname]["first_run"] is None:
                    jobs[jname]["first_run"] = run["id"]
                jobs[jname]["start_qty"] += planned
                runs.append(run)
    by_day = defaultdict(list)
    for r in runs:
        by_day[r["date"]].append(r)

    # expected daily usage from the plan (for opening stock / early reorder points)
    exp_total = defaultdict(float)
    for r in runs:
        for c, q, b in BOMS[r["product"]]:
            if b == "ITEM":
                exp_total[c] += q * r["printed"]
    for j in jobs.values():
        if j["new_campaign"]:
            for c, q, b in BOMS[j["product"]]:
                if b == "LOT":
                    exp_total[c] += q
    exp_total["BLK-01"] += 2 * len(blanket_days)
    exp_daily = {c: v / len(workdays) for c, v in exp_total.items()}

    # forward plan baseline (MPS before the new plan increase) — weekly good sheets per product
    hist_weeks = len(weeks)
    base_weekly = defaultdict(float)
    for r in runs:
        base_weekly[r["product"]] += r["printed"] / (1 + PRODUCTS[r["product"]]["allow"]) / hist_weeks
    plan_start = monday(EXTRACT) + timedelta(days=7)
    mps_base = {(p, w): int(round(base_weekly[p] * (1 + gauss(0, 0.02)), -3)) for w in range(1, 13) for p in PRODUCTS}

    # day-by-day demand the buyer sees (MRP-style): the press schedule for history,
    # then the baseline MPS (flat) beyond the extract
    horizon = [d for d in daterange(START, END + timedelta(days=260)) if is_workday(d)]
    widx = {d: i for i, d in enumerate(horizon)}
    demand = defaultdict(lambda: [0.0] * len(horizon))
    for r in runs:
        for c, q, b in BOMS[r["product"]]:
            if b == "ITEM":
                demand[c][widx[r["date"]]] += q * r["printed"]
    for j in jobs.values():
        if j["new_campaign"]:
            for c, q, b in BOMS[j["product"]]:
                if b == "LOT":
                    demand[c][widx[max(j["week"], START)] if max(j["week"], START) in widx else widx[next_workday(max(j["week"], START))]] += q
    for p_, d_ in blanket_days:
        demand["BLK-01"][widx[date.fromisoformat(d_)]] += 2
    fwd = defaultdict(float)
    for p in PRODUCTS:
        for c, q, b in BOMS[p]:
            if b == "ITEM":
                fwd[c] += q * base_weekly[p] * (1 + PRODUCTS[p]["allow"]) / 5
    for c in exp_daily:
        rate = fwd.get(c, exp_daily[c])
        for i in range(widx[END] + 1, len(horizon)):
            demand[c][i] = rate

    # ---------------- inventory state
    SAFETY = {"SUBSTRATE": 12, "INK": 18, "COATING": 15, "CHEMICAL": 15, "CONSUMABLE": 15, "PACKAGING": 15, "PLATE": 20}
    COVER = {"SUBSTRATE": 6, "INK": 20, "COATING": 20, "CHEMICAL": 20, "CONSUMABLE": 25, "PACKAGING": 30, "PLATE": 40}
    active = [c for c, it in ITEMS.items() if it["status"] == "Active" and exp_daily.get(c)]
    book = {}; phys = {}; lots = {}; on_order = []
    lot_rows, txns, po_heads, po_lines, mes_runs, mes_reasons, mes_loads = [], [], [], [], [], [], []
    truth = defaultdict(float)
    truth_runs = defaultdict(list)
    seq = {"txn": 9000001, "po": 261000, "poline": 880001, "mes": 1, "lot": 1}
    s2_lot = {"lot": None, "po": None}
    vendor_alt = defaultdict(int)

    def safety(c):
        it = ITEMS[c]
        return 10 if c.startswith("INK-OV") else SAFETY[it["cls"]]

    def lead_wd(c):
        return int(round(ITEMS[c]["lead"] * 5 / 7))

    def need(c, d, a, b):
        i = widx[d]
        return sum(demand[c][i + a:i + b])

    def add_txn(d, ttype, c, qty, src_type, src, lot="", sub="RAW-STORES", ref="", created=None, by="INTERFACE"):
        it = ITEMS.get(c) or {"uom": "SHT"}
        txns.append(dict(TRANSACTION_ID=seq["txn"], TRANSACTION_DATE=d.isoformat(), CREATION_DATE=(created or d).isoformat(),
                         TRANSACTION_TYPE_ID={"PO Receipt": 18, "WIP Issue": 35, "WIP Return": 43, "WIP Completion": 44, "Cycle Count Adjust": 4}[ttype],
                         TRANSACTION_TYPE_NAME=ttype, ITEM=c, INVENTORY_ITEM_ID=ITEM_ID.get(c, 0), SUBINVENTORY_CODE=sub,
                         TRANSACTION_QUANTITY=qty, TRANSACTION_UOM=it["uom"], LOT_NUMBER=lot,
                         TRANSACTION_SOURCE_TYPE={"PO Receipt": "Purchase order", "Cycle Count Adjust": "Cycle Count"}.get(ttype, "Job or Schedule"),
                         TRANSACTION_SOURCE_NAME=src, TRANSACTION_REFERENCE=ref, CREATED_BY=by))
        seq["txn"] += 1

    def new_lot(c, d, qty, vendor, po, opening=False):
        lot = f"{'OPN' if opening else 'L'}{ymd(d)}-{c}-{seq['lot']:04d}"
        seq["lot"] += 1
        it = ITEMS[c]
        lot_rows.append(dict(LOT_NUMBER=lot, ITEM=c, ORIGINATION_DATE=d.isoformat(),
                             EXPIRATION_DATE=(d + timedelta(days=it["shelf"])).isoformat() if it["shelf"] else "",
                             VENDOR_ID=VENDOR_ID[vendor], SUPPLIER_LOT_NUMBER=f"{vendor[-1]}{R.randint(100000, 999999)}", PO_NUMBER=po,
                             RECEIVED_QTY=qty))
        lots[c].append([lot, qty])
        return lot

    def consume(c, qty):
        """FIFO draw on book lots; returns [(lot, qty)]."""
        out, need, q = [], qty, lots[c]
        while need > 1e-9:
            if not q:  # never expected — book would go negative; draw on a phantom last lot
                out.append((out[-1][0] if out else "NO-LOT", need)); break
            lot, have = q[0]
            take = min(have, need)
            out.append((lot, take))
            need -= take
            if have - take <= 1e-9:
                q.popleft()
            else:
                q[0][1] = have - take
        return out

    ITEM_ID = {c: 100001 + i for i, c in enumerate(ITEMS)}
    for i, p in enumerate(PRODUCTS):
        ITEM_ID[p] = 200001 + i

    # opening balances (migrated as OPN lots dated the day before the window)
    for c in active:
        lots[c] = deque()
        it = ITEMS[c]
        qty = need(c, START, 0, safety(c) + lead_wd(c) + int(COVER[it["cls"]] * 0.6))
        qty = round(qty, prec(it)) if prec(it) else int(math.ceil(qty))
        book[c] = phys[c] = qty
        parts = 3 if c.startswith("SUB-") else 1
        for k in range(parts):
            new_lot(c, START - timedelta(days=parts - k), round(qty / parts, prec(it)) if prec(it) else qty // parts + (qty % parts if k == parts - 1 else 0),
                    it["vendors"][0], "OPENING", opening=True)

    def place_po(c, d, expedite=False):
        it = ITEMS[c]
        if expedite:
            qty = need(c, d, 1, 11)
        else:  # cover the window after arrival and top the position back up to safety stock
            position = book[c] + sum(o["qty"] for o in on_order if o["item"] == c)
            qty = need(c, d, 1, 1 + lead_wd(c) + COVER[it["cls"]]) + exp_daily[c] * safety(c) - position
        qty = round_up(qty, it["mult"] if expedite else it["moq"], it["mult"])
        vendors = it["vendors"]
        vendor = vendors[0]
        if len(vendors) > 1:
            vendor_alt[c] += 1
            vendor = vendors[1] if vendor_alt[c] % 3 == 0 else vendors[0]
        lead = it["lead"] * (1 + gauss(0, 0.05))
        slipped = it["vendors"][0] == F1["vendor"] and d >= F1["since"]
        if slipped:
            lead += F1["slip_days"] + gauss(0, 3)
        if expedite:
            lead = 4
        arrive = next_workday(d + timedelta(days=int(round(lead))))
        bad = False
        if not expedite and c == S2["item"] and s2_lot["po"] is None and S2["window"][0] <= arrive <= S2["window"][1]:
            vendor, bad = S2["vendor"], True
        po = f"PO-{seq['po']}"
        seq["po"] += 1
        promised = d + timedelta(days=4 if expedite else it["lead"] + R.randint(0, 3))
        head = dict(PO_HEADER_ID=seq["po"] + 400000, SEGMENT1=po, VENDOR_ID=VENDOR_ID[vendor], VENDOR=vendor,
                    CREATION_DATE=d.isoformat(), AUTHORIZATION_STATUS="APPROVED", CLOSED_CODE="OPEN", CURRENCY_CODE="USD")
        line = dict(PO_LINE_ID=seq["poline"], PO_HEADER_ID=head["PO_HEADER_ID"], PO_NUMBER=po, LINE_NUM=1, ITEM=c,
                    INVENTORY_ITEM_ID=ITEM_ID[c], ITEM_DESCRIPTION=it["desc"], QUANTITY=qty, UOM_CODE=it["uom"],
                    UNIT_PRICE=round(it["cost"] * (1.15 if expedite else 1) * (1 + gauss(0, 0.02)), 4 if it["cost"] < 1 else 2),
                    NEED_BY_DATE=(d + timedelta(days=4 if expedite else it["lead"])).isoformat(), PROMISED_DATE=promised.isoformat(),
                    QUANTITY_RECEIVED=0, CLOSED_CODE="OPEN", NOTE_TO_VENDOR="EXPEDITE - premium freight" if expedite else "")
        seq["poline"] += 1
        po_heads.append(head); po_lines.append(line)
        split = not expedite and R.random() < 0.08 and qty >= 2 * it["mult"]
        first = round_up(qty * 0.6, it["mult"], it["mult"]) if split else qty
        on_order.append(dict(item=c, arrive=arrive, qty=first, line=line, head=head, vendor=vendor, bad=bad, slipped=slipped))
        if split:
            on_order.append(dict(item=c, arrive=next_workday(arrive + timedelta(days=R.randint(3, 8))), qty=qty - first,
                                 line=line, head=head, vendor=vendor, bad=False, slipped=slipped))
        if bad:
            s2_lot["po"] = po

    def receive(o, d):
        c = o["item"]
        lot = new_lot(c, d, o["qty"], o["vendor"], o["line"]["PO_NUMBER"])
        if o["bad"]:
            s2_lot["lot"] = lot
        add_txn(d, "PO Receipt", c, o["qty"], "Purchase order", o["line"]["PO_NUMBER"], lot)
        book[c] += o["qty"]; phys[c] += o["qty"]
        o["line"]["QUANTITY_RECEIVED"] += o["qty"]
        if o["line"]["QUANTITY_RECEIVED"] >= o["line"]["QUANTITY"]:
            o["line"]["CLOSED_CODE"] = "CLOSED FOR RECEIVING"; o["head"]["CLOSED_CODE"] = "CLOSED"
        o["line"].setdefault("_receipts", []).append(d)

    def issue(run, c, qty, actual, posted=None, ref=""):
        """Record an issue (book) and the true physical consumption."""
        it = ITEMS[c]
        qty = round(qty, prec(it)) if prec(it) else int(round(qty))
        if qty > 0:
            for lot, q in consume(c, qty):
                add_txn(posted or run["date"], "WIP Issue", c, -round(q, prec(it)) if prec(it) else -int(q), "Job or Schedule", run["job"], lot,
                        ref=ref or run["id"], created=posted, by="WIP_BACKFLUSH" if it["supply"] == "PULL" else "MES_INTERFACE")
            book[c] -= qty
            day_issue[c] += qty
        phys[c] -= actual

    # D3 selections (deterministic)
    run_ids = [r["id"] for r in runs]
    pick = random.Random(seed + 3)
    d3_missing = set(pick.sample(run_ids[200:-200], D3_MISSING))
    rest = [x for x in run_ids[200:-200] if x not in d3_missing]
    d3_dupes = set(pick.sample(rest, D3_DUPES))
    rest = [x for x in rest if x not in d3_dupes]
    manual = set(x for x in rest if pick.random() < MANUAL_RATE) | d3_dupes
    d3_typos = set(pick.sample(sorted(manual - d3_dupes), D3_TYPOS))

    ls_count = defaultdict(int)
    cc_days = set()
    for y, m in sorted({(d.year, d.month) for d in workdays}):
        cc_days.add(next_workday(date(y, m, 15)))

    # ================================================================ day loop
    for d in daterange(START, END):
        for o in [o for o in on_order if o["arrive"] == d]:
            receive(o, d); on_order.remove(o)
        if not is_workday(d):
            continue
        day_issue = defaultdict(float)
        for run in sorted(by_day[d], key=lambda r: ("ABC".index(r["shift"]), r["press"])):
            prod, P = run["product"], PRODUCTS[run["product"]]
            job = jobs[run["job"]]
            first = job["first_run"] == run["id"]
            # ---- spoilage
            rate = P["allow"] - 0.002 + gauss(0, 0.006)
            reasons = defaultdict(float)
            if first and job["new_campaign"]:
                rate += 0.008; reasons["MKR"] += 0.008
            if run["press"] == S3["press"] and run["shift"] == S3["shift"]:
                rate += S3["spoil"]; reasons["REG"] += S3["spoil"] * 0.6; reasons["WIP"] += S3["spoil"] * 0.4
            rh = env[(run["hall"], d)][0]
            hum = max(0.0, rh - S5["rh_threshold"]) * S5["per_pt"] if run["hall"] == S5["hall"] else 0.0
            if hum:
                rate += hum; reasons["SUB"] += hum * 0.5; reasons["REG"] += hum * 0.5
            printed = run["printed"]
            sub_draw = consume(P["sub"], printed)
            bad_frac = sum(q for lot, q in sub_draw if lot == s2_lot["lot"]) / printed if s2_lot["lot"] else 0.0
            if bad_frac:
                rate += S2["spoil"] * bad_frac; reasons["SUB"] += S2["spoil"] * bad_frac * 0.8; reasons["JAM"] += S2["spoil"] * bad_frac * 0.2
            rate = max(0.012, rate)
            spoiled = int(round(printed * rate)); good = printed - spoiled
            run.update(spoiled=spoiled, good=good, rh=rh, bad_frac=bad_frac)
            # truth for the answer key (excess sheets vs the run's no-scenario spoilage)
            if run["press"] == S3["press"] and run["shift"] == S3["shift"]:
                truth["S3_sheets"] += printed * S3["spoil"]; truth_runs["S3"].append(run["id"])
            if bad_frac:
                truth["S2_sheets"] += printed * S2["spoil"] * bad_frac; truth_runs["S2"].append(run["id"])
            if hum:
                truth["S5_sheets"] += printed * hum; truth["S5_cost"] += printed * hum * sheet_cost(prod)
                truth_runs["S5"].append(run["id"])
            # ---- substrate: counted sheets issued per lot (book == physical)
            for lot, q in sub_draw:
                add_txn(d, "WIP Issue", P["sub"], -int(q), "Job or Schedule", run["job"], lot, ref=run["id"], by="MES_INTERFACE")
                mes_loads.append(dict(RUN_ID=run["id"], MATERIAL_CODE=P["sub"], LOT_NUMBER=lot, QTY_LOADED=int(q), UOM="SHT", SOURCE="Sheet counter"))
            book[P["sub"]] -= printed; phys[P["sub"]] -= printed; day_issue[P["sub"]] += printed
            # ---- lot-basis items at campaign start
            if first and job["new_campaign"]:
                for c, q, b in BOMS[prod]:
                    if b == "LOT":
                        issue(run, c, q, q)
            if first and (run["press"], d.isoformat()) in blanket_days:
                issue(run, "BLK-01", 2, 2)
            # ---- per-sheet materials
            for c, q, b in BOMS[prod]:
                it = ITEMS[c]
                if b != "ITEM" or c.startswith("SUB-") or it["supply"] == "PULL":
                    continue
                f = 1 + gauss(0, 0.005)
                if c == S1["item"] and run["press"] == S1["press"] and run["week"] == S1["week"]:
                    truth["S1_qty"] += q * printed * f * (S1["factor"] - 1); f *= S1["factor"]; truth_runs["S1"].append(run["id"])
                if c == S4["item"] and prod == S4["product"] and d >= S4["since"]:
                    truth["S4_qty"] += q * printed * f * (1 - S4["factor"]); f *= S4["factor"]; truth_runs["S4"].append(run["id"])
                actual = q * printed * f
                # metered: book follows the meter (tiny meter error)
                meter = round(actual * (1 + gauss(0, 0.002)), 2)
                posted = None
                if run["press"] == D2["press"] and D2["run_dates"][0] <= d <= D2["run_dates"][1]:
                    posted = D2["posted"]; truth["D2_qty_cost"] += meter * it["cost"]; truth["D2_txns"] += 1
                ret = round(meter * (0.03 + R.random() * 0.05), 2) if R.random() < 0.04 else 0
                if ret:
                    issue(run, c, meter + ret, actual + ret, posted)
                    lot = txns[-1]["LOT_NUMBER"]
                    add_txn(posted or d, "WIP Return", c, ret, "Job or Schedule", run["job"], lot, ref=run["id"], created=posted, by="MES_INTERFACE")
                    book[c] += ret; phys[c] += ret; day_issue[c] -= ret
                    if lots[c] and lots[c][0][0] == lot:
                        lots[c][0][1] += ret
                    else:
                        lots[c].appendleft([lot, ret])
                else:
                    issue(run, c, meter, actual, posted)
                mes_loads.append(dict(RUN_ID=run["id"], MATERIAL_CODE=c, LOT_NUMBER=txns[-1]["LOT_NUMBER"], QTY_LOADED=meter, UOM=it["uom"], SOURCE="Ink dispenser meter"))
                if posted:
                    truth_runs["D2"].append(run["id"])
            # ---- MES record (possibly manual / missing / duplicated)
            entry = "MANUAL" if run["id"] in manual else "AUTO"
            m_printed, m_good = printed, good
            if entry == "MANUAL":
                m_printed, m_good = int(round(printed, -2)), int(round(good, -2))
                if run["id"] in d3_typos:
                    s = str(m_printed); m_printed = int(s[0] + s[2] + s[1] + s[3:]) if s[1] != s[2] else m_printed + 900
                    m_good = m_printed - (printed - good)
            completed = m_good
            # backflush at completion: BOM standard × reported good × (1 + allowance)
            for c, q, b in BOMS[prod]:
                if b == "ITEM" and ITEMS[c]["supply"] == "PULL":
                    std = q * completed * (1 + P["allow"])
                    f = 1 + gauss(0, 0.005)
                    if c == D1["item"] and run["hall"] == D1["hall"] and d >= D1["since"]:
                        truth["D1_qty"] += q * printed * f * (D1["factor"] - 1); f *= D1["factor"]
                    issue(run, c, std, q * printed * f)
            add_txn(d, "WIP Completion", prod, completed, "Job or Schedule", run["job"], "", sub="FG-VAULT", ref=run["id"], by="MES_INTERFACE")
            job["completed"] += completed
            if run["id"] not in d3_missing:
                rec = dict(RECORD_ID=f"MES-{seq['mes']:07d}", RUN_ID=run["id"], PRESS_ID=run["press"], PRESS_HALL=run["hall"],
                           SHIFT=run["shift"], CREW_ID=f"CR-{run['press']}-{run['shift']}", RUN_DATE=d.isoformat(),
                           START_TS=f"{d}T{SHIFTS[run['shift']][0]:02d}:00",
                           END_TS=(f"{d}T{SHIFTS[run['shift']][1]:02d}:00" if SHIFTS[run["shift"]][1] < 24 else f"{d + timedelta(days=1)}T{SHIFTS[run['shift']][1] - 24:02d}:00"),
                           WIP_JOB=run["job"], PRODUCT_CODE=prod, SHEETS_PLANNED=run["planned"], SHEETS_PRINTED=m_printed,
                           SHEETS_GOOD=m_good, SHEETS_SPOILED=m_printed - m_good, ENTRY_METHOD=entry)
                seq["mes"] += 1
                mes_runs.append(rec)
                # spoilage reasons: baseline mix + scenario excess
                base = max(0.0, rate - sum(reasons.values()))
                mix = {"REG": .30, "INK": .20, "WIP": .15, "SUB": .10, "JAM": .10, "MKR": .05, "OTH": .10}
                share = {k: base * v + reasons.get(k, 0) for k, v in mix.items()}
                tot = sum(share.values()) or 1
                alloc = {k: int(round(spoiled * v / tot)) for k, v in share.items()}
                alloc["OTH"] += spoiled - sum(alloc.values())
                for k, v in alloc.items():
                    if v > 0:
                        mes_reasons.append(dict(RUN_ID=run["id"], RECORD_ID=rec["RECORD_ID"], REASON_CODE=k, SHEETS=v))
                if run["id"] in d3_dupes:
                    dup = dict(rec, RECORD_ID=f"MES-{seq['mes']:07d}", ENTRY_METHOD="MANUAL")
                    seq["mes"] += 1
                    mes_runs.append(dup)
                    add_txn(d + timedelta(days=1), "WIP Completion", prod, completed, "Job or Schedule", run["job"], "", sub="FG-VAULT",
                            ref=run["id"], created=d + timedelta(days=1), by="OPS_CLERK")
                    for c, q, b in BOMS[prod]:
                        if b == "ITEM" and ITEMS[c]["supply"] == "PULL":
                            issue(run, c, q * completed * (1 + P["allow"]), 0, d + timedelta(days=1))
                    job["completed"] += completed
                    truth["D3_dupe_sheets"] += completed
        # ---- cycle counts (mid-month, all active raw items)
        if d in cc_days:
            for c in active:
                it = ITEMS[c]
                noise = 0 if it["uom"] in ("SHT", "EA") else gauss(0, 0.0015)
                count = phys[c] * (1 + noise)
                count = round(count, prec(it)) if prec(it) else int(round(count))
                adj = round(count - book[c], prec(it)) if prec(it) else int(round(count - book[c]))
                if abs(adj) >= (1 if prec(it) == 0 else 0.05):
                    lot = lots[c][0][0] if lots[c] else ""
                    add_txn(d, "Cycle Count Adjust", c, adj, "Cycle Count", f"CC-{d.strftime('%Y-%m')}", lot, by="INV_CONTROL")
                    if adj > 0:
                        if lots[c]: lots[c][0][1] += adj
                    else:
                        consume(c, -adj)
                    if c == D1["item"]:
                        truth["D1_cc_qty"] += adj
                    book[c] += adj
        # ---- reorder check
        for c in active:
            position = book[c] + sum(o["qty"] for o in on_order if o["item"] == c)
            if position < need(c, d, 1, 1 + lead_wd(c)) + exp_daily[c] * safety(c):
                place_po(c, d)
            # expedite when the next 3 days can't be covered and nothing lands in time
            soon = sum(o["qty"] for o in on_order if o["item"] == c and o["arrive"] <= d + timedelta(days=4))
            if book[c] + soon < need(c, d, 1, 6) * 1.2:
                place_po(c, d, expedite=True)

    # ================================================================ forward plan (MPS) — 12 weeks
    mps = []
    for w in range(1, 13):
        for p in PRODUCTS:
            f = F2["factor"] if p == F2["product"] and w >= F2["from_week"] else 1.0
            mps.append(dict(SCHEDULE_DESIGNATOR="MPS-FY27", ITEM=p, INVENTORY_ITEM_ID=ITEM_ID[p], WEEK_NUM=w,
                            SCHEDULE_DATE=(plan_start + timedelta(days=7 * (w - 1))).isoformat(),
                            SCHEDULE_QUANTITY=int(round(mps_base[(p, w)] * f, -3)), UOM_CODE="SHT", _base=mps_base[(p, w)]))

    return dict(R=R, runs=runs, jobs=jobs, events=events, env=env, txns=txns, lot_rows=lot_rows, po_heads=po_heads,
                po_lines=po_lines, on_order=on_order, mes_runs=mes_runs, mes_reasons=mes_reasons, mes_loads=mes_loads,
                book=book, phys=phys, lots=lots, mps=mps, truth=truth, truth_runs=truth_runs, s2=s2_lot, ITEM_ID=ITEM_ID,
                d3=dict(missing=sorted(d3_missing), dupes=sorted(d3_dupes), typos=sorted(d3_typos), manual=len(manual)),
                active=active, exp_daily=exp_daily, safety={c: (10 if c.startswith("INK-OV") else SAFETY[ITEMS[c]["cls"]]) for c in active})


def sheet_cost(prod):
    """$ of material consumed per printed sheet at standard (ITEM-basis components)."""
    return sum(q * ITEMS[c]["cost"] for c, q, b in BOMS[prod] if b == "ITEM")


# ================================================================ forecast check (truth for F1/F2)
def project(sim, use_plan, use_observed_lead, qty_key="SCHEDULE_QUANTITY"):
    """Weekly projected book stock for each item over the 12-week plan.  Returns {item: first week below safety stock within lead time, or None}."""
    lines = {l["PO_LINE_ID"]: l for l in sim["po_lines"]}
    # observed slip per vendor: median(actual receipt − promised) over receipts in the last 60 days
    slip = defaultdict(list)
    for l in sim["po_lines"]:
        for rd in l.get("_receipts", []):
            if rd >= END - timedelta(days=60):
                slip[ITEMS[l["ITEM"]]["vendors"][0]].append((rd - date.fromisoformat(l["PROMISED_DATE"])).days)
    slip = {v: sorted(x)[len(x) // 2] for v, x in slip.items()}  # median of recent receipts
    hist = defaultdict(float)
    cutoff = END - timedelta(days=56)
    for t in sim["txns"]:
        if t["TRANSACTION_TYPE_NAME"] in ("WIP Issue", "WIP Return") and t["TRANSACTION_DATE"] > cutoff.isoformat():
            hist[t["ITEM"]] -= t["TRANSACTION_QUANTITY"]
    plan_start = monday(EXTRACT) + timedelta(days=7)
    out = {}
    for c in sim["active"]:
        stock = sim["book"][c]
        weekly_hist = hist[c] / 8
        floor = sim["exp_daily"][c] * sim["safety"][c]   # safety stock
        first = None
        for w in range(1, 13):
            wk0 = plan_start + timedelta(days=7 * (w - 1))
            wk1 = wk0 + timedelta(days=7)
            if use_plan:
                dem = 0.0
                for m in sim["mps"]:
                    if m["WEEK_NUM"] == w:
                        for comp, q, b in BOMS[m["ITEM"]]:
                            if comp == c and b == "ITEM":
                                dem += q * m[qty_key] * (1 + PRODUCTS[m["ITEM"]]["allow"])
                if dem == 0:
                    dem = weekly_hist
            else:
                dem = weekly_hist
            sup = 0
            for o in sim["on_order"]:
                if o["item"] != c:
                    continue
                due = date.fromisoformat(o["line"]["PROMISED_DATE"])
                if use_observed_lead:
                    due += timedelta(days=round(slip.get(ITEMS[c]["vendors"][0], 0)))
                if (w == 1 and due < wk1) or wk0 <= due < wk1:
                    sup += o["qty"]
            stock = stock + sup - dem
            if first is None and stock < floor:
                first = w
        # only a shortfall inside the item's lead time is actionable (beyond it, a PO placed today fixes it)
        out[c] = first if first and first <= math.ceil(ITEMS[c]["lead"] / 7) else None
    return out, slip


# ================================================================ write
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261104)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sim = build(args.seed)
    ITEM_ID = sim["ITEM_ID"]
    counts = {}

    items_rows = []
    for c, it in ITEMS.items():
        items_rows.append(dict(INVENTORY_ITEM_ID=ITEM_ID[c], SEGMENT1=c, DESCRIPTION=it["desc"], PRIMARY_UOM_CODE=it["uom"],
                               ITEM_TYPE="RAW" if it["cls"] in ("SUBSTRATE", "INK", "COATING") else "CONSUMABLE",
                               ITEM_CLASS=it["cls"], INVENTORY_ITEM_STATUS_CODE=it["status"], STANDARD_COST=it["cost"],
                               FULL_LEAD_TIME=it["lead"], MINIMUM_ORDER_QUANTITY=it["moq"], FIXED_LOT_MULTIPLIER=it["mult"],
                               WIP_SUPPLY_TYPE=1 if it["supply"] == "PUSH" else 3,
                               WIP_SUPPLY_TYPE_NAME="Push" if it["supply"] == "PUSH" else "Operation Pull",
                               LOT_CONTROL_CODE=2, SHELF_LIFE_DAYS=it["shelf"] or "", SHRINKAGE_RATE="",
                               SAFETY_STOCK_DAYS=sim["safety"].get(c, "")))
    for p, P in PRODUCTS.items():
        items_rows.append(dict(INVENTORY_ITEM_ID=ITEM_ID[p], SEGMENT1=p, DESCRIPTION=f"Note product {p[-2:]}, {P['subjects']}-subject printed sheet",
                               PRIMARY_UOM_CODE="SHT", ITEM_TYPE="FG", ITEM_CLASS="PRODUCT", INVENTORY_ITEM_STATUS_CODE="Active",
                               STANDARD_COST=round(sheet_cost(p) * (1 + P["allow"]), 4), FULL_LEAD_TIME="", MINIMUM_ORDER_QUANTITY="",
                               FIXED_LOT_MULTIPLIER="", WIP_SUPPLY_TYPE="", WIP_SUPPLY_TYPE_NAME="", LOT_CONTROL_CODE=1,
                               SHELF_LIFE_DAYS="", SHRINKAGE_RATE=P["allow"], SAFETY_STOCK_DAYS="", SUBJECTS_PER_SHEET=P["subjects"]))
    item_cols = ["INVENTORY_ITEM_ID", "SEGMENT1", "DESCRIPTION", "PRIMARY_UOM_CODE", "ITEM_TYPE", "ITEM_CLASS", "INVENTORY_ITEM_STATUS_CODE",
                 "STANDARD_COST", "FULL_LEAD_TIME", "MINIMUM_ORDER_QUANTITY", "FIXED_LOT_MULTIPLIER", "WIP_SUPPLY_TYPE", "WIP_SUPPLY_TYPE_NAME",
                 "LOT_CONTROL_CODE", "SHELF_LIFE_DAYS", "SHRINKAGE_RATE", "SAFETY_STOCK_DAYS", "SUBJECTS_PER_SHEET"]
    counts["mtl_system_items.csv"] = write_csv("mtl_system_items.csv", items_rows, item_cols)
    counts["ap_suppliers.csv"] = write_csv("ap_suppliers.csv", [dict(VENDOR_ID=VENDOR_ID[c], SEGMENT1=c, VENDOR_NAME=n, ENABLED_FLAG="Y") for c, n in VENDORS],
                                           ["VENDOR_ID", "SEGMENT1", "VENDOR_NAME", "ENABLED_FLAG"])
    asl = [dict(ITEM=c, INVENTORY_ITEM_ID=ITEM_ID[c], VENDOR_ID=VENDOR_ID[v], VENDOR=v, ASL_STATUS="Approved", PRIMARY_FLAG="Y" if i == 0 else "N")
           for c, it in ITEMS.items() if it["status"] == "Active" for i, v in enumerate(it["vendors"])]
    counts["po_asl.csv"] = write_csv("po_asl.csv", asl, ["ITEM", "INVENTORY_ITEM_ID", "VENDOR_ID", "VENDOR", "ASL_STATUS", "PRIMARY_FLAG"])

    bom_rows, bseq = [], 10001
    for p in PRODUCTS:
        for op, (c, q, b) in enumerate(BOMS[p], start=1):
            bom_rows.append(dict(BILL_SEQUENCE_ID=bseq, ASSEMBLY_ITEM=p, COMPONENT_SEQUENCE_ID=bseq * 100 + op, ITEM_NUM=op * 10,
                                 COMPONENT_ITEM=c, COMPONENT_QUANTITY=round(q, 8), BASIS_TYPE="Lot" if b == "LOT" else "Item",
                                 COMPONENT_UOM=ITEMS[c]["uom"], COMPONENT_YIELD_FACTOR=1,
                                 WIP_SUPPLY_TYPE_NAME="Push" if ITEMS[c]["supply"] == "PUSH" else "Operation Pull",
                                 EFFECTIVITY_DATE="2024-07-01", DISABLE_DATE=""))
        bseq += 1
    counts["bom_components.csv"] = write_csv("bom_components.csv", bom_rows, list(bom_rows[0].keys()))

    counts["po_headers.csv"] = write_csv("po_headers.csv", sim["po_heads"], ["PO_HEADER_ID", "SEGMENT1", "VENDOR_ID", "VENDOR", "CREATION_DATE",
                                                                             "AUTHORIZATION_STATUS", "CLOSED_CODE", "CURRENCY_CODE"])
    counts["po_lines.csv"] = write_csv("po_lines.csv", sim["po_lines"], ["PO_LINE_ID", "PO_HEADER_ID", "PO_NUMBER", "LINE_NUM", "ITEM", "INVENTORY_ITEM_ID",
                                                                         "ITEM_DESCRIPTION", "QUANTITY", "UOM_CODE", "UNIT_PRICE", "NEED_BY_DATE",
                                                                         "PROMISED_DATE", "QUANTITY_RECEIVED", "CLOSED_CODE", "NOTE_TO_VENDOR"])
    counts["mtl_lot_numbers.csv"] = write_csv("mtl_lot_numbers.csv", sim["lot_rows"], ["LOT_NUMBER", "ITEM", "ORIGINATION_DATE", "EXPIRATION_DATE",
                                                                                        "VENDOR_ID", "SUPPLIER_LOT_NUMBER", "PO_NUMBER", "RECEIVED_QTY"])
    job_rows = [dict(WIP_ENTITY_ID=j["id"], WIP_ENTITY_NAME=j["name"], PRIMARY_ITEM=j["product"], INVENTORY_ITEM_ID=ITEM_ID[j["product"]],
                     CLASS_CODE="PRINT", ATTRIBUTE1_PRESS=j["press"], SCHEDULED_START_DATE=j["week"].isoformat(),
                     SCHEDULED_COMPLETION_DATE=(j["week"] + timedelta(days=4)).isoformat(), START_QUANTITY=j["start_qty"],
                     QUANTITY_COMPLETED=j["completed"], STATUS_TYPE_NAME="Closed" if j["week"] < monday(END) else "Released")
                for j in sim["jobs"].values()]
    counts["wip_discrete_jobs.csv"] = write_csv("wip_discrete_jobs.csv", job_rows, list(job_rows[0].keys()))
    txns = sorted(sim["txns"], key=lambda t: (t["TRANSACTION_DATE"], t["TRANSACTION_ID"]))
    counts["mtl_material_transactions.csv"] = write_csv("mtl_material_transactions.csv", txns, list(txns[0].keys()))
    onhand = []
    for c in sim["active"]:
        for lot, q in sim["lots"][c]:
            if q > 1e-6:
                onhand.append(dict(ITEM=c, INVENTORY_ITEM_ID=ITEM_ID[c], SUBINVENTORY_CODE="RAW-STORES", LOT_NUMBER=lot,
                                   PRIMARY_TRANSACTION_QUANTITY=round(q, prec(ITEMS[c])) if prec(ITEMS[c]) else int(round(q)),
                                   UOM_CODE=ITEMS[c]["uom"], AS_OF_DATE=END.isoformat()))
    counts["mtl_onhand_quantities.csv"] = write_csv("mtl_onhand_quantities.csv", onhand, list(onhand[0].keys()))
    counts["mrp_schedule_dates.csv"] = write_csv("mrp_schedule_dates.csv", sim["mps"], [k for k in sim["mps"][0] if not k.startswith("_")])

    counts["mes_production_runs.csv"] = write_csv("mes_production_runs.csv", sim["mes_runs"], list(sim["mes_runs"][0].keys()))
    counts["mes_spoilage_reasons.csv"] = write_csv("mes_spoilage_reasons.csv", sim["mes_reasons"], ["RUN_ID", "RECORD_ID", "REASON_CODE", "SHEETS"])
    counts["mes_material_loads.csv"] = write_csv("mes_material_loads.csv", sim["mes_loads"], ["RUN_ID", "MATERIAL_CODE", "LOT_NUMBER", "QTY_LOADED", "UOM", "SOURCE"])
    ev = sorted(sim["events"], key=lambda e: (e["ts"], e["press"]))
    counts["mes_press_events.csv"] = write_csv("mes_press_events.csv",
                                               [dict(EVENT_ID=f"EV-{i:06d}", PRESS_ID=e["press"], EVENT_TS=e["ts"], SHIFT=e["shift"],
                                                     EVENT_TYPE=e["type"], NOTE=e["note"]) for i, e in enumerate(ev, start=1)],
                                               ["EVENT_ID", "PRESS_ID", "EVENT_TS", "SHIFT", "EVENT_TYPE", "NOTE"])
    env_rows = [dict(PRESS_HALL=h, READING_DATE=d.isoformat(), AVG_RH_PCT=v[0], AVG_TEMP_C=v[1]) for (h, d), v in sorted(sim["env"].items(), key=lambda x: (x[0][1], x[0][0]))]
    counts["mes_environment.csv"] = write_csv("mes_environment.csv", env_rows, ["PRESS_HALL", "READING_DATE", "AVG_RH_PCT", "AVG_TEMP_C"])

    write_answer_key(sim)
    write_dictionary()
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(dict(generator="tools/build_materials.py", seed=args.seed, period=[START.isoformat(), END.isoformat()],
                       extract_date=EXTRACT.isoformat(), synthetic=True, files=counts), f, indent=2)
    total = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    for n, c in counts.items():
        print(f"  {n:34s} {c:>8,d} rows")
    print(f"  → {os.path.relpath(OUT)}  ({total / 1e6:.1f} MB)")


def write_answer_key(sim):
    t, tr = sim["truth"], sim["truth_runs"]
    runs = {r["id"]: r for r in sim["runs"]}
    cost = lambda c: ITEMS[c]["cost"]
    s2_runs = tr["S2"]
    prods_s2 = {runs[x]["product"] for x in s2_runs}
    s2_cost = sum(runs[x]["printed"] * S2["spoil"] * runs[x]["bad_frac"] * sheet_cost(runs[x]["product"]) for x in s2_runs)
    s3_cost = sum(runs[x]["printed"] * S3["spoil"] * sheet_cost(runs[x]["product"]) for x in tr["S3"])
    fc_naive, slip = project(sim, use_plan=False, use_observed_lead=False)
    fc_base, _ = project(sim, use_plan=True, use_observed_lead=False, qty_key="_base")
    fc_plan, _ = project(sim, use_plan=True, use_observed_lead=False)
    fc_both, _ = project(sim, use_plan=True, use_observed_lead=True)
    sim["fc"] = dict(naive=fc_naive, base=fc_base, plan=fc_plan, both=fc_both)
    f1_items = sorted(c for c in sim["active"] if fc_both[c] and not fc_plan[c])
    f2_items = sorted(c for c in sim["active"] if fc_plan[c] and not fc_base[c])
    s5_months = defaultdict(float)
    for x in tr["S5"]:
        r = runs[x]
        s5_months[r["date"].strftime("%Y-%m")] += r["printed"] * max(0, r["rh"] - S5["rh_threshold"]) * S5["per_pt"]
    d1_cc = t["D1_cc_qty"]
    key = dict(
        note="Planted ground truth. Everything not listed here is ordinary noise. Quantities are the generator's true effects.",
        scenarios=[
            dict(id="S1", category="root-cause", title="Ink over-consumption on one press",
                 where=f"{S1['press']} · {S1['item']} · week of {S1['week']}", driver="usage-rate", item=S1["item"], press=S1["press"],
                 truth=f"Usage rate +{(S1['factor'] - 1) * 100:.0f}% over standard for one week (dispenser drift; see the {S1['press']} calibration event on 2026-11-02).",
                 excess_qty=round(t["S1_qty"], 1), uom="KG", excess_cost=round(t["S1_qty"] * cost(S1["item"])), runs=len(tr["S1"])),
            dict(id="S2", category="root-cause", title="Bad substrate lot",
                 where=f"{sim['s2']['lot']} ({S2['vendor']}, PO {sim['s2']['po']})", driver="lot", lot=sim["s2"]["lot"], item=S2["item"],
                 vendor=S2["vendor"], truth=f"+{S2['spoil'] * 100:.1f} pp spoilage on every run that drew from the lot, across presses ({', '.join(sorted({runs[x]['press'] for x in s2_runs}))}); MES reason codes skew to SUB/JAM.",
                 products=sorted(prods_s2), excess_sheets=round(t["S2_sheets"]), excess_cost=round(s2_cost), runs=len(s2_runs),
                 first_run=min(s2_runs) if s2_runs else None, last_run=max(s2_runs) if s2_runs else None),
            dict(id="S3", category="root-cause", title="High-spoilage shift", where=f"{S3['press']} · shift {S3['shift']} · whole year",
                 driver="press-shift", press=S3["press"], shift=S3["shift"],
                 truth=f"+{S3['spoil'] * 100:.1f} pp spoilage on every {S3['press']} shift-{S3['shift']} run; reason codes skew to REG/WIP.",
                 excess_sheets=round(t["S3_sheets"]), excess_cost=round(s3_cost), runs=len(tr["S3"])),
            dict(id="S4", category="root-cause", title="Out-of-date usage standard",
                 where=f"{S4['product']} · {S4['item']} · since {S4['since']}", driver="standard", item=S4["item"], product=S4["product"],
                 truth=f"Actual usage fell {(1 - S4['factor']) * 100:.0f}% (process change) but the BOM still carries the old rate, so the variance is favorable and the standard overstates need — a standards problem, not a floor problem.",
                 favorable_qty=round(t["S4_qty"], 1), uom="KG", favorable_cost=round(t["S4_qty"] * cost(S4["item"])), runs=len(tr["S4"]),
                 bom_rate_per_1000=RATE["INK-IB-01"], actual_rate_per_1000=round(RATE["INK-IB-01"] * S4["factor"], 3)),
            dict(id="S5", category="root-cause", title="Seasonal humidity spoilage", where=f"Hall {S5['hall'][-1]} (P04–P06) · summer",
                 driver="environment", hall=S5["hall"],
                 truth=f"Spoilage rises {S5['per_pt'] * 100:.2f} pp per RH point above {S5['rh_threshold']:.0f}% in Hall 2 (mes_environment); peaks Jul–Aug; reason codes SUB/REG.",
                 excess_sheets=round(t["S5_sheets"]), excess_cost=round(t["S5_cost"]), by_month={k: round(v) for k, v in sorted(s5_months.items())}),
            dict(id="F1", category="forecast", title="Vendor lead-time slip", where=f"{F1['vendor']} (intaglio inks) · POs placed since {F1['since']}",
                 driver="lead-time", vendor=F1["vendor"],
                 truth=f"Actual lead time ≈ {ITEMS['INK-IG-01']['lead'] + F1['slip_days']} days vs {ITEMS['INK-IG-01']['lead']} in the item master; vendor still promises {ITEMS['INK-IG-01']['lead']}. Safety stock has quietly eroded.",
                 observed_slip_days=round(slip.get(F1["vendor"], 0), 1), items_short_only_with_observed_lead=f1_items,
                 first_short_week={c: fc_both[c] for c in f1_items},
                 note="Short = projected book stock below safety stock within the item's lead time, counting open POs only."),
            dict(id="F2", category="forecast", title="Production-plan increase", where=f"{F2['product']} · +{(F2['factor'] - 1) * 100:.0f}% from plan week {F2['from_week']}",
                 driver="plan", product=F2["product"],
                 truth="A plan-driven forecast shows the shortfall; a historical-average forecast does not.",
                 items_short_only_with_plan=f2_items, first_short_week={c: fc_plan[c] for c in f2_items}),
            dict(id="D1", category="data-quality", title="Hidden over-use of a backflushed material",
                 where=f"{D1['item']} · Hall {D1['hall'][-1]} · since {D1['since']}", driver="backflush", item=D1["item"],
                 truth=f"Actual use +{(D1['factor'] - 1) * 100:.0f}% (recirculation leak). The ERP backflushes at standard, so run-level variance is invisible; it only surfaces as negative cycle-count adjustments.",
                 hidden_excess_qty=round(t["D1_qty"]), uom="L", hidden_excess_cost=round(t["D1_qty"] * cost(D1["item"])),
                 cycle_count_adjust_total=round(d1_cc), unobservable_items=sorted(c for c, it in ITEMS.items() if it["supply"] == "PULL" and it["status"] == "Active")),
            dict(id="D2", category="data-quality", title="Issues posted in the wrong period",
                 where=f"{D2['press']} · ink issues for runs {D2['run_dates'][0]}…{D2['run_dates'][1]} · posted {D2['posted']}", driver="late-posting",
                 truth="Issue transactions carry the posting date, not the run date, so September ERP consumption is understated and October overstated vs MES.",
                 transactions=int(t["D2_txns"]), cost_shifted=round(t["D2_qty_cost"]), runs=sorted(set(tr["D2"]))),
            dict(id="D3", category="data-quality", title="Missing, duplicate and mis-keyed MES records", driver="mes-records",
                 truth="ERP completions reference runs that MES lacks; some runs were keyed twice (ERP completed twice, backflush doubled); two manual entries have transposed printed counts that disagree with the ERP substrate issue.",
                 missing_runs=sim["d3"]["missing"], duplicate_runs=sim["d3"]["dupes"], duplicate_sheets=round(t["D3_dupe_sheets"]),
                 mistyped_runs=sim["d3"]["typos"], manual_entries=sim["d3"]["manual"]),
        ],
    )
    with open(os.path.join(OUT, "answer_key.json"), "w") as f:
        json.dump(key, f, indent=2)


DICTIONARY = [
    ("mtl_system_items", "*", "ERP item master (raw materials, consumables and finished-sheet products)."),
    ("mtl_system_items", "WIP_SUPPLY_TYPE", "1 = Push (issued from a measured/counted quantity); 3 = Operation Pull (backflushed at BOM standard on completion — actual use is not recorded)."),
    ("mtl_system_items", "SHRINKAGE_RATE", "Products only: spoilage allowance (spoiled ÷ good) built into the standard."),
    ("mtl_system_items", "FULL_LEAD_TIME", "Quoted lead time in calendar days (as maintained in the master — may be stale)."),
    ("bom_components", "COMPONENT_QUANTITY", "Per one good sheet for BASIS_TYPE=Item; per job/campaign for BASIS_TYPE=Lot. Standard = qty × good × (1 + SHRINKAGE_RATE)."),
    ("po_lines", "PROMISED_DATE", "Vendor-promised delivery date. Actual receipt dates are the PO Receipt rows in mtl_material_transactions."),
    ("mtl_material_transactions", "TRANSACTION_QUANTITY", "Signed: receipts/returns/completions positive, issues negative (Oracle convention)."),
    ("mtl_material_transactions", "TRANSACTION_DATE", "Posting date. Normally the run date; late postings carry the later date (CREATION_DATE shows when entered)."),
    ("mtl_material_transactions", "TRANSACTION_REFERENCE", "MES RUN_ID for issues/completions — the join key between ERP and MES."),
    ("mtl_material_transactions", "CREATED_BY", "MES_INTERFACE (metered/counted), WIP_BACKFLUSH (pull at standard), OPS_CLERK (manual), INV_CONTROL (cycle counts), INTERFACE (receipts)."),
    ("mtl_onhand_quantities", "*", "Book on-hand by lot at the end of the extract."),
    ("mrp_schedule_dates", "*", "Master production schedule: planned good sheets per product per week, 12 weeks forward."),
    ("wip_discrete_jobs", "*", "One job per press × product × week. ATTRIBUTE1_PRESS is the press."),
    ("mes_production_runs", "*", "One record per press × shift run. ENTRY_METHOD = AUTO (sheet counters) or MANUAL (keyed). RUN_ID should be unique — duplicates are data-quality issues."),
    ("mes_spoilage_reasons", "REASON_CODE", "REG registration · INK ink density/void · WIP wiping · SUB substrate defect/cockle · JAM feeder/sheet jam · MKR makeready · OTH other."),
    ("mes_material_loads", "*", "Lots loaded per run: substrate from sheet counters, inks from dispenser meters. Backflushed materials are not recorded."),
    ("mes_press_events", "*", "Downtime, maintenance, calibration, blanket changes and HVAC alarms."),
    ("mes_environment", "*", "Daily average relative humidity and temperature per press hall."),
    ("answer_key.json", "*", "Planted scenarios and their true quantities, for checking the platform's findings. Not part of a real extract."),
]


def write_dictionary():
    write_csv("data_dictionary.csv", [dict(table=t, column=c, description=d) for t, c, d in DICTIONARY], ["table", "column", "description"])


if __name__ == "__main__":
    main()
