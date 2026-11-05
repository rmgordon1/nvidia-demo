"""Incident lifecycle simulation.

Phase A: create ticket shells (scenario, customer, submit time, relations)
Phase B: sort by submit time and assign Incident/Entry numbers
Phase C: simulate each lifecycle (audit events + work logs), MI parents first
Phase D: apply the archive-extract cutoff and categorization noise
"""
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from . import textgen as tg
from .arschema import PRIORITY_MATRIX
from .org import COMPANY, SITES
from . import scenarios as default_catalog

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
START = datetime(2024, 11, 1, tzinfo=ET)
END = datetime(2026, 10, 31, 23, 59, tzinfo=ET)
EXTRACT = datetime(2026, 11, 3, 6, 0, tzinfo=ET)

HOLIDAYS = {date(*d) for d in [
    (2024, 11, 11), (2024, 11, 28), (2024, 12, 25), (2025, 1, 1), (2025, 1, 20), (2025, 2, 17),
    (2025, 5, 26), (2025, 6, 19), (2025, 7, 4), (2025, 9, 1), (2025, 10, 13), (2025, 11, 11),
    (2025, 11, 27), (2025, 12, 25), (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 5, 25),
    (2026, 6, 19), (2026, 7, 3), (2026, 9, 7), (2026, 10, 12)]}

SYS_EMAIL = "Remedy Application Service"
SYS_MON = "monint_svc"
SYS_ESC = "AR_ESCALATOR"

HOUR_IT = [.02, .02, .02, .02, .03, .08, .3, .8, 1.4, 1.5, 1.4, 1.1, .8, 1.1, 1.2, 1.0, .7, .35, .15, .1, .06, .05, .04, .03]
HOUR_OT = [.6, .55, .5, .5, .55, .7, 1.3, 1.2, 1.0, 1.0, .95, .9, .85, .9, 1.3, 1.2, 1.0, .9, .85, .8, .75, .7, 1.2, .9]
HOUR_INFRA = [1.2, 1.4, 1.5, 1.4, 1.1, .9, .8, .9, 1.0, 1.0, 1.0, 1.0, .9, 1.0, 1.0, 1.0, .9, .8, .7, .7, .7, .8, .9, 1.0]

CONFUSERS = {
    "IT": ["desktop", "Network Operations", "Server & Storage", "Messaging & Collaboration",
           "Identity & Access Management", "Business Applications", "ERP & Financial Systems"],
    "OT": ["ot", "MES Support", "Inspection Systems", "Network Operations", "desktop", "Press Controls Engineering"],
    "INFRA": ["Server & Storage", "Database Administration", "Network Operations", "Business Applications"],
}
LAZY_CATS = [("Failure", "General", ""), ("Request", "Other", ""), ("Failure", "Software", ""),
             ("Inquiry", "General", ""), ("Failure", "Hardware", "")]


# ---------------------------------------------------------------- calendars
def windows(day, cal):
    wd = day.weekday()
    if cal == "bus":
        return [] if wd >= 5 or day in HOLIDAYS else [(7.0, 17.5)]
    if cal == "sd":
        if day in HOLIDAYS or wd == 6:
            return []
        return [(8.0, 16.0)] if wd == 5 else [(6.0, 20.0)]
    if cal == "cust":
        return [] if wd >= 5 or day in HOLIDAYS else [(7.5, 17.0)]
    return [(0.0, 24.0)]


def add_work(t, hours, cal, tz):
    if cal == "24x7" or hours <= 0:
        return t + timedelta(hours=max(hours, 0))
    lt = t.astimezone(tz)
    remaining = hours * 3600.0
    for _ in range(800):
        day = lt.date()
        for s, e in windows(day, cal):
            ws = datetime.combine(day, time(int(s), int((s % 1) * 60)), tz)
            we = datetime.combine(day, time(int(e) % 24, int((e % 1) * 60)), tz) if e < 24 else \
                datetime.combine(day + timedelta(days=1), time(0), tz)
            st = max(lt, ws)
            if st >= we:
                continue
            avail = (we - st).total_seconds()
            if remaining <= avail:
                return (st + timedelta(seconds=remaining)).astimezone(UTC)
            remaining -= avail
            lt = we
        lt = datetime.combine(day + timedelta(days=1), time(0), tz)
    return lt.astimezone(UTC)


def second_tuesday(y, m):
    d = date(y, m, 1)
    first_tue = d + timedelta(days=(1 - d.weekday()) % 7)
    return first_tue + timedelta(days=7)


# ---------------------------------------------------------------- timeline
class Timeline:
    def __init__(self):
        self.events = []   # (t, user, {field: value}, action)
        self.logs = []     # work log dicts
        self.state = {}

    def set(self, t, user, action="Set", **ch):
        diff = {k: v for k, v in ch.items() if self.state.get(k, "__none__") != v}
        if not diff:
            return
        self.state.update(diff)
        self.events.append((t, user, diff, action))

    def log(self, t, kind, author, wl_type, view, text, summary, ctx=None, attach=None, secure=0):
        self.logs.append(dict(t=t, kind=kind, author=author, type=wl_type, view=view, text=text,
                              summary=summary, ctx=ctx or {}, attach=attach, secure=secure))


class Simulator:
    def __init__(self, org, rng, n_target, catalog=None):
        self.org, self.rng, self.N = org, rng, n_target
        cat = catalog or default_catalog
        self.SCENARIOS, self.SCEN_BY_ID, self.MI_EVENTS = cat.SCENARIOS, cat.SCEN_BY_ID, cat.MI_EVENTS
        self.CONFUSERS = getattr(cat, "CONFUSERS", CONFUSERS)
        self.LINE_SHARE = getattr(cat, "LINE_SHARE", None)
        self.FIRMWARE_WAVES = getattr(cat, "FIRMWARE_WAVES", [])
        self.days = []
        d = START.date()
        while d <= END.date():
            self.days.append(d)
            d += timedelta(days=1)
        self.incidents = []
        self.problem_seq = 4100
        self.change_seq = 31200
        self._pools = {}
        # monitoring integration "customer"
        p = org._new_person("HQ", "Office of the CIO", "Office of the CIO", "System Account", False, support=True)
        p.first, p.last, p.login, p.email, p.weight = "Event", "Management", SYS_MON, f"eventmgmt@bcp.example.gov", 0.0
        org.by_site["HQ"].remove(p)
        self.monperson = p

    # ------------------------------------------------------------ helpers
    def r(self):
        return self.rng.random()

    def choice(self, seq, p=None):
        i = self.rng.choice(len(seq), p=p)
        return seq[i]

    def wchoice(self, d):
        keys = list(d)
        w = np.array([d[k] for k in keys], float)
        return keys[int(self.rng.choice(len(keys), p=w / w.sum()))]

    def lognorm(self, median, sigma):
        return float(median * math.exp(self.rng.normal(0, sigma)))

    def pool(self, sites):
        key = str(sites)
        if key in self._pools:
            return self._pools[key]
        people = [p for p in self.org.people if not p.support]
        if sites == "user":
            pool = people
        elif sites == "office":
            pool = [p for p in people if not p.production]
        elif sites == "production":
            pool = [p for p in people if p.production]
        else:
            pool = [p for p in people if p.site_key in sites]
        w = np.array([p.weight * (0.55 if (sites == "user" and p.production) else 1.0) for p in pool])
        self._pools[key] = (pool, w / w.sum())
        return self._pools[key]

    def pick_person(self, sites):
        pool, w = self.pool(sites)
        return pool[int(self.rng.choice(len(pool), p=w))]

    def site_of(self, p):
        return p.site_key if p.site_key != "REM" else "HQ"

    def resolve_group(self, g, site_key):
        sk = site_key if site_key in ("HQ", "CPF", "SPF") else "HQ"
        if g == "desktop":
            return f"Desktop Support - {sk}"
        if g == "ot":
            return f"OT Support - {sk if sk != 'HQ' else 'CPF'}"
        return g

    def group_tz(self, g):
        site = self.org.groups[g]["site"]
        return ZoneInfo(SITES[site]["tz"]) if site else ET

    def tech(self, g):
        return self.choice(self.org.groups[g]["members"])

    def pick_ci(self, spec, cust, site_key):
        if not spec:
            return None
        o = self.org
        if spec == "workstation":
            if cust.workstation:
                return o.ci_by_name[cust.workstation]
            ks = o.find("meskiosk", site_key) if site_key in ("CPF", "SPF") else []
            return self.choice(ks) if ks else None
        if spec.startswith("app:"):
            return o.apps[spec[4:]]
        tag, _, sk = spec.partition(":")
        sk = sk or site_key
        cands = o.find(tag, sk) or o.find(tag)
        return self.choice(cands) if cands else None

    # ------------------------------------------------------------ arrivals
    def day_weights(self, scen):
        dom = scen["domain"]
        w = np.empty(len(self.days))
        for i, d in enumerate(self.days):
            wd = d.weekday()
            if dom == "OT":
                base = [1.0, 1.0, 1.0, 1.0, 1.0, .45, .3][wd] * (.4 if d in HOLIDAYS else 1)
            elif dom == "INFRA":
                base = [1.0, 1.0, 1.0, 1.0, .95, .75, .7][wd]
            else:
                base = [1.15, 1.05, 1.0, .98, .85, .12, .08][wd] * (.12 if d in HOLIDAYS else 1)
                if d.month == 12 and d.day >= 22 or (d.month == 1 and d.day <= 2):
                    base *= .6
                if d.month in (7, 8):
                    base *= .9
            base *= 1 + 0.06 * ((d - START.date()).days / 365)
            for tag in scen["tags"]:
                if tag == "patch":
                    pt = second_tuesday(d.year, d.month)
                    if 0 <= (d - pt).days <= 2:
                        base *= 2.0
                elif tag == "fyend":
                    if d.month == 9:
                        base *= 1.8
                    elif d.month == 10 and d.day <= 15:
                        base *= 1.3
                elif tag == "payroll":
                    if (d - date(2024, 11, 11)).days % 14 in (0, 1):
                        base *= 3.0
                elif tag == "firmware":
                    for wd0 in self.FIRMWARE_WAVES:
                        if 0 <= (d - wd0).days <= 4:
                            base *= 2.2
                elif tag == "openseason":
                    if (d.month == 11 and d.day >= 10) or (d.month == 12 and d.day <= 12):
                        base *= 2.5
            w[i] = base
        return w / w.sum()

    def sample_time(self, scen, cust, day_w):
        d = self.days[int(self.rng.choice(len(self.days), p=day_w))]
        prof = {"OT": HOUR_OT, "INFRA": HOUR_INFRA}.get(scen["domain"], HOUR_IT)
        if 5000 in scen["src"] and scen["src"][5000] == 1.0:
            prof = HOUR_INFRA
        hp = np.array(prof) / sum(prof)
        h = int(self.rng.choice(24, p=hp))
        tz = ZoneInfo(SITES[cust.site_key]["tz"])
        lt = datetime.combine(d, time(h, int(self.rng.integers(60)), int(self.rng.integers(60))), tz)
        return lt.astimezone(UTC)

    def new_shell(self, scen, cust, submit, **kw):
        sk = self.site_of(cust)
        if scen["sites"] == "production" or scen["domain"] == "OT":
            sk = cust.site_key if cust.site_key in ("CPF", "SPF") else "CPF"
        inc = dict(scen=scen, cust=cust, site_key=sk, submit=submit, mi_event=None, mi_parent=None,
                   mi_role=None, dup_of=None, dup_kind=None, linked=False)
        inc.update(kw)
        self.incidents.append(inc)
        return inc

    def build_shells(self):
        N = self.N
        scale = N / 10000.0
        # major incidents
        n_events = max(1, int(round(len(self.MI_EVENTS) * min(1.0, scale * 1.0)))) if N >= 150 else 0
        events = list(self.rng.permutation(len(self.MI_EVENTS))[:n_events]) if n_events else []
        n_mi_tickets = 0
        mi_plan = []
        for ei in events:
            ev = self.MI_EVENTS[ei]
            lo, hi = ev["children"]
            nch = max(3, int(self.rng.integers(lo, hi + 1) * max(scale, 0.15)))
            mi_plan.append((ev, nch))
            n_mi_tickets += nch + 1
        n_dup = int(N * 0.015)
        n_reg = N - n_mi_tickets - n_dup
        weights = np.array([s["w"] for s in self.SCENARIOS], float)
        if self.LINE_SHARE:  # normalise so each line (client / infra / software) gets its target share
            lines = [s["line"] for s in self.SCENARIOS]
            for ln, share in self.LINE_SHARE.items():
                m = np.array([x == ln for x in lines])
                weights[m] *= share / weights[m].sum()
        counts = self.rng.multinomial(n_reg, weights / weights.sum())
        for scen, c in zip(self.SCENARIOS, counts):
            if not c:
                continue
            dw = self.day_weights(scen)
            for _ in range(c):
                cust = self.pick_person(scen["sites"])
                self.new_shell(scen, cust, self.sample_time(scen, cust, dw))
        # duplicates: same customer reports again shortly after through another channel
        regs = [i for i in self.incidents if i["scen"]["src"] != {5000: 1.0}]
        for orig in self.rng.choice(len(regs), size=min(n_dup, len(regs)), replace=False):
            o = regs[int(orig)]
            t = o["submit"] + timedelta(minutes=self.lognorm(40, 0.9))
            self.new_shell(o["scen"], o["cust"], t, dup_of=o, dup_kind="random")
        # major incident events spread across the archive
        span = (END - START).total_seconds()
        for k, (ev, nch) in enumerate(mi_plan):
            frac = (k + self.rng.random()) / max(len(mi_plan), 1)
            t0 = START + timedelta(seconds=span * frac * 0.97)
            d = t0.astimezone(ET).date()
            if ev["id"] == "patch_print":
                d = second_tuesday(d.year, d.month) + timedelta(days=1)
            elif ev["id"] == "erp_fyclose":
                d = date(2025 if d < date(2025, 11, 1) else 2026, 9, 24)
            while d.weekday() >= 5 or d in HOLIDAYS:
                d += timedelta(days=1)
            anytime = ev.get("anytime", ev["child"] in ("press_hmi_frozen", "historian_gap"))
            hour = int(self.rng.integers(0, 24)) if anytime else int(self.rng.integers(7, 15))
            start = datetime.combine(d, time(hour, int(self.rng.integers(60))), ET).astimezone(UTC)
            dur = self.lognorm(math.sqrt(ev["hours"][0] * ev["hours"][1]), 0.4)
            ev = dict(ev, start=start, end=start + timedelta(hours=dur))
            child_scen = self.SCEN_BY_ID[ev["child"]]
            parent_cust = self.monperson
            parent = self.new_shell(child_scen, parent_cust, start + timedelta(minutes=self.lognorm(20, 0.6)),
                                    mi_event=ev, mi_role="parent",
                                    site_key=ev["sites"][0] if isinstance(ev["sites"], list) else
                                    ("CPF" if ev["sites"] == "production" else "HQ"))
            for _ in range(nch):
                cust = self.pick_person(ev["sites"])
                t = start + timedelta(hours=float(self.rng.exponential(dur / 2.5)))
                if t > ev["end"] + timedelta(hours=1):
                    t = start + timedelta(hours=float(self.rng.uniform(0, dur)))
                self.new_shell(child_scen, cust, t, mi_event=ev, mi_role="child", mi_parent=parent,
                               linked=self.rng.random() < 0.65)

    def number(self):
        self.incidents.sort(key=lambda i: i["submit"])
        base = 873412
        for k, inc in enumerate(self.incidents):
            inc["num"] = f"INC{base + k:012d}"
            inc["entry_id"] = f"{base + 1100 + k:015d}"

    # ------------------------------------------------------------ lifecycle
    def run(self):
        self.build_shells()
        self.number()
        order = sorted(self.incidents, key=lambda i: (i["mi_role"] != "parent", i["submit"]))
        for inc in order:
            self.simulate(inc)
        for inc in self.incidents:
            self.finalize(inc)
        return self.incidents

    def ctx_for(self, inc):
        sc, cust = inc["scen"], inc["cust"]
        ci = inc.get("ci")
        sk = inc["site_key"]
        line = ""
        if ci is not None and len(ci.tags) >= 3 and ci.tags[0] in ("plc", "hmi", "insp", "ink"):
            line = ci.tags[2].split(" ", 1)[1]
        elif sc["domain"] == "OT" and sk in self.org.lines:
            line = self.choice(self.org.lines[sk]).name.split(" ", 1)[1]
        host = self.choice(self.org.find("server")).name if sc["id"] in ("backup_fail", "cert_expiring") else ""
        ci_name = ci.name if ci is not None and ci.class_id != "BMC_Application" else (cust.workstation or "my computer")
        if sc["id"] == "account_locked":
            host = cust.workstation or self.choice(self.org.find("meskiosk")).name
        dev = ci if ci is not None and ci.class_id != "BMC_Application" else \
            self.org.ci_by_name.get(cust.workstation) if cust.workstation else None
        if sc.get("product") == "@ci":
            if dev is not None and dev.manufacturer == "Dell" and dev.model:
                inc["product_override"] = (dev.model, "Dell")
            else:  # no Dell asset on record (e.g. shared plant-floor user): generic model for the line
                inc["product_override"] = ("PowerEdge R760", "Dell") if sc.get("line") == "infra" else ("Latitude 7450", "Dell")
        return dict(name=cust.first, ci=ci_name, line=line, tag=getattr(dev, "serial", "") or "",
                    model=getattr(dev, "model", "") or "",
                    site=SITES[sk]["site"], site_short=sk, product=sc["product"],
                    err=self.choice(sc["text"]["err"]) if sc["text"]["err"] else "",
                    room=ci.name if ci else "", n=int(self.rng.integers(100000, 999999)), host=host or (ci.name if ci else ""),
                    dept=cust.dept)

    def write_initial_text(self, inc, source, creator_kind):
        sc, cust, ctx = inc["scen"], inc["cust"], inc["ctx"]
        T = sc["text"]
        r = self.rng
        persona = cust.persona
        inc["text_author"] = creator_kind
        if creator_kind == "system":
            alerts = T.get("alert") or T["desc"]
            desc = tg.fill(self.choice(alerts), ctx)
            summ = tg.fill(self.choice(T["summ"]), ctx)
        elif creator_kind == "agent":
            summ = tg.fill(self.choice(T["summ"]), ctx)
            if r.random() < 0.3:
                summ = summ[0].upper() + summ[1:]
            base = tg.fill(self.choice(T.get("sd") or T["desc"]), ctx)
            if ctx["err"] and r.random() < 0.4:
                base += f" Error: {ctx['err']}"
            desc = base
            if r.random() < 0.25:
                desc = f"{base} Contact: {cust.full} {cust.phone}."
            summ = tg.add_typos(r, summ, 0.01)
            desc = tg.add_typos(r, desc, 0.012)
        else:  # customer-written (email / self service)
            summ = tg.persona_style(r, tg.fill(self.choice(T["summ"]), ctx), persona, "summ")
            body = tg.fill(self.choice(T["desc"]), ctx)
            if ctx["err"] and (persona == "detailed" or r.random() < 0.25):
                body += (f' The error says: "{ctx['err']}"' if r.random() < 0.5 else f"\n\nError: {ctx['err']}")
            body = tg.persona_style(r, body, persona, "desc")
            if source == 2000:
                greet, sig, hdr = tg.greeting(r, persona), tg.signature(r, cust, SITES[cust.site_key]["site"]), ""
                if r.random() < 0.07:
                    hdr = f"-----Original Message-----\nFrom: {cust.full}\nSent: {inc['submit'].astimezone(ET):%A, %B %d, %Y %I:%M %p}\nTo: Service Desk\nSubject: {summ}\n\n"
                    summ = "FW: " + summ
                inc["email_wrap"] = (hdr, greet, sig)
                desc = hdr + greet + body + sig
            else:
                desc = body
            if r.random() < 0.07:
                inc["vague_summary"] = True
                summ = self.choice(tg.VAGUE_SUMMARIES) + ("" if r.random() < 0.5 else " " + summ.split()[0])
        if inc["mi_role"] == "parent":
            ev = inc["mi_event"]
            summ = ("MAJOR INCIDENT: " if r.random() < 0.6 else "MI - ") + ev["title"]
            desc = (f"Major incident declared. {ev['title']}. Multiple users/sites reporting impact. "
                    f"Bridge opened, {ev['group']} engaged. Service: {ev['service']}.")
        inc["summary"] = summ[:100]
        inc["description"] = desc
        inc["slots"] = dict(summary=creator_kind, description=creator_kind)

    def build_path(self, sc, final, site_key, inc):
        """Groups the ticket visits after (optional) Service Desk triage."""
        r = self.rng
        pm = 0.13 + (0.12 if inc.get("vague_summary") else 0) + (0.05 if sc["domain"] == "OT" else 0)
        if inc["mi_role"] or final == "Service Desk":
            pm = 0.03 if inc["mi_role"] == "child" else 0
        if r.random() >= pm:
            return [final], 0
        conf = [self.resolve_group(g, site_key) for g in self.CONFUSERS[sc["domain"]]]
        conf = [g for g in conf if g != final] or ["Service Desk"]
        u = r.random()
        if u < 0.55:
            return [self.choice(conf), final], 1
        if u < 0.75:
            a, b = r.choice(len(conf), 2, replace=len(conf) < 2)
            return [conf[a], conf[b], final], 2
        if u < 0.9:  # ping-pong: final bounces it, wrong group bounces it back
            return [final, self.choice(conf), final], 2
        return [self.choice(conf), "Service Desk", final], 2

    def misroute_text(self, g, nxt, sc):
        if g == "Service Desk":
            return f"Re-triaged after return. Routing to {nxt}."
        if nxt == "Service Desk":
            return f"Not a {g} issue. Returning to Service Desk for re-triage."
        return self.choice([f"Not a {g} issue - {sc['product']} is supported by {nxt}. Reassigning.",
                            f"Wrong queue. Sending to {nxt}.",
                            f"Checked from our side, nothing wrong on the {g} end. Reassigning to {nxt}."])

    def priority(self, impact, urgency):
        return PRIORITY_MATRIX[(impact, urgency)]

    def simulate(self, inc):
        r = self.rng
        sc, cust = inc["scen"], inc["cust"]
        sk = inc["site_key"]
        tl = Timeline()
        inc["tl"] = tl
        ev = inc["mi_event"]
        role = inc["mi_role"]

        # ---- classification
        final = self.resolve_group(ev["group"] if ev else sc["group"], sk)
        inc["final_group"] = final
        ci_spec = ev["ci"] if role == "parent" and ev["ci"] else sc["ci"]
        inc["ci"] = self.pick_ci(ci_spec, cust if role != "parent" else self.pick_person(ev["sites"]), sk)
        svc_name = sc["service"]
        if svc_name == "production":
            svc_name = f"Currency Production - {sk}"
        if ev:
            svc_name = ev["service"]
        inc["service_ci"] = self.org.services.get(svc_name)
        inc["ctx"] = self.ctx_for(inc)

        if role == "parent":
            source = 5000 if r.random() < 0.45 else 6000
            stype = 2 if sc["stype"] != 4 else 4
            impact = 1000 if r.random() < 0.35 else 2000
            urgency = 1000
        else:
            source = self.wchoice(sc["src"])
            if inc["dup_kind"]:
                alt = [s for s in (2000, 6000, 4200) if s != inc["dup_of"].get("source")]
                source = self.choice(alt)
            stype = sc["stype"]
            impact = self.wchoice(sc["impact"])
            urgency = self.wchoice(sc["urgency"])
            if role == "child":
                stype = 0 if sc["stype"] in (0, 1) else sc["stype"]
            if cust.vip and urgency > 1000 and r.random() < 0.5:
                urgency -= 1000
        if source == 5000 and role != "parent" and sc["ci"] != "workstation":
            inc["cust"] = cust = self.monperson
            inc["ctx"]["name"] = cust.first
        inc.update(source=source, stype=stype, impact=impact, urgency=urgency)
        prio = self.priority(impact, urgency)

        # ---- who creates it
        t = inc["submit"]
        reported = t - timedelta(minutes=self.lognorm(6, 0.8)) if source in (6000, 8000, 7000) else t
        if source == 7000:
            reported = t - timedelta(hours=self.lognorm(1.5, 0.8))
        inc["reported"] = reported
        sd_triage = sc["sd_triage"] and source != 5000
        human_created = source in (6000, 8000, 1000, 7000)
        if role == "parent" and source == 6000:
            creator, creator_kind, first = self.tech("Service Desk"), "agent", "Service Desk"
        elif source == 5000:
            creator, creator_kind, first = None, "system", None
        elif human_created and not sd_triage:
            if r.random() < 0.6 or source == 1000:
                creator, first = self.tech(final), final
            else:
                creator, first = self.tech("Service Desk"), "Service Desk"
            creator_kind = "agent"
        elif human_created:
            if source == 8000 and final.startswith("Desktop"):
                creator, first = self.tech(final), final
            else:
                creator, first = self.tech("Service Desk"), "Service Desk"
            creator_kind = "agent"
        else:
            creator, creator_kind, first = None, "customer", "Service Desk"
        if role == "parent" and source == 5000:
            creator_kind = "system"
        self.write_initial_text(inc, source, creator_kind)

        path, misroutes = self.build_path(sc, final, sk, inc)
        if first is None:
            first = path[0]
            path = path[1:]
        elif first == path[0]:
            path = path[1:]
        if first == "Service Desk" and path and path[0] == "Service Desk":
            path = path[1:]
        inc["misroutes"] = misroutes
        groups_seen = [first]

        submitter = creator.login if creator else (SYS_MON if source == 5000 else
                                                   (SYS_EMAIL if source == 2000 else cust.login))
        inc["submitter"] = submitter
        base = dict(Status=None, Assigned_Group=None, Assignee=None)
        tl.set(t, submitter, "Create", Status=1, Assigned_Group=first, Assignee=None, Priority=prio,
               Impact=impact, Urgency=urgency)
        if creator:
            tl.set(t + timedelta(seconds=int(r.integers(20, 240))), creator.login, Status=2, Assignee=creator.full,
                   Responded_Date=t)
            cur = creator
        else:
            cur = None
        state = dict(group=first, t=t, tech=cur, prio=prio)

        def pickup(g):
            cal = self.org.groups[g]["cal"]
            if state["prio"] <= 1:
                cal = "24x7"
            med = {0: .08, 1: .35, 2: 1.5, 3: 3.0}[state["prio"]]
            if role == "child":
                med *= 0.5
            tt = add_work(state["t"], self.lognorm(med, 0.8), cal, self.group_tz(g))
            tech = self.tech(g)
            extra = {} if "Responded_Date" in tl.state else dict(Responded_Date=tt)
            tl.set(tt, tech.login, Status=2, Assignee=tech.full, **extra)
            state.update(t=tt, tech=tech)

        def work(hours, g=None):
            g = g or state["group"]
            cal = "24x7" if state["prio"] <= 1 else self.org.groups[g]["cal"]
            state["t"] = add_work(state["t"], hours, cal, self.group_tz(g))

        def transfer(g, note_kind=None, note=None):
            prev = state["tech"]
            if note:
                self.wlog(inc, state["t"], note_kind, prev, 6000, 0, note,
                          ctx=dict(from_group=state["group"], to_group=g))
            tl.set(state["t"], prev.login if prev else SYS_MON, Status=1, Assigned_Group=g, Assignee=None)
            state["group"] = g
            groups_seen.append(g)
            pickup(g)

        if cur is None:
            pickup(first)

        T = sc["text"]
        ctx = inc["ctx"]

        # ---- duplicates detected at triage
        if inc["dup_kind"] and r.random() < 0.75:
            work(self.lognorm(0.15, 0.6))
            o = inc["dup_of"]
            self.wlog(inc, state["t"], "dup", state["tech"], 3000, 0,
                      f"Duplicate of {o['num']} submitted by same customer. Cancelling this record.",
                      ctx=dict(original=o["num"]))
            tl.set(state["t"], state["tech"].login, Status=6, Status_Reason=None,
                   Resolution=f"Duplicate of {o['num']}.")
            inc["res_cat"] = ("Cancelled", "Duplicate", "")
            inc["assoc"] = [(o["num"], "HPD:Help Desk", 36000)]
            inc["cancelled"] = True
            return

        # ---- MI child linked to parent
        if role == "child" and inc["linked"]:
            p = inc["mi_parent"]
            work(self.lognorm(0.1, 0.5))
            self.wlog(inc, state["t"], "mi_link", state["tech"], 3000, 1,
                      f"Known issue - related to major incident {p['num']}: {ev['title']}. You will be notified when service is restored.",
                      ctx=dict(parent=p["num"], title=ev["title"]))
            tl.set(state["t"], state["tech"].login, Status=3, Status_Reason=12000)
            inc["assoc"] = [(p["num"], "HPD:Help Desk", 35000)]
            rt = max(p["resolved_t"], state["t"]) + timedelta(minutes=self.lognorm(25, 0.7))
            state["t"] = rt
            res = f"Resolved by original incident {p['num']}. {ev['title']} - service restored."
            tl.set(rt, SYS_ESC, Status=4, Status_Reason=18000, Last_Resolved_Date=rt, Resolution=res)
            inc["res_cat"] = ("Fix/Repair", "Resolved by Original Incident", "")
            inc["resolution"] = res
            self.close(inc, state, rt)
            return

        # ---- service desk first touch
        if state["group"] == "Service Desk":
            fcr_p = sc["fcr"] if final != "Service Desk" else 1.0
            if role == "child":
                fcr_p *= 0.3
            if r.random() < fcr_p and not misroutes and role != "parent":
                work(self.lognorm(0.25, 0.6))
                self.resolve(inc, state, T, ctx)
                return
            work(self.lognorm(0.2, 0.6))
            nxt = path[0] if path else final
            self.wlog(inc, state["t"], "triage", state["tech"], 6000, 0,
                      self.choice(tg.TRIAGE_NOTES) + (f" Error: {ctx['err']}." if ctx["err"] and r.random() < 0.3 else "")
                      + f" Transferring to {nxt}.",
                      ctx=dict(to_group=nxt))

        elif state["group"] != final and path:
            work(self.lognorm(1.0, 0.9))
            self.wlog(inc, state["t"], "misroute", state["tech"], 6000, 0,
                      self.misroute_text(state["group"], path[0], sc), ctx=dict(from_group=state["group"], to_group=path[0]))

        # ---- walk the path
        for i, g in enumerate(path):
            is_last = i == len(path) - 1
            if g == state["group"]:
                continue
            transfer(g)
            if not is_last:
                work(self.lognorm(1.2, 0.9))
                nxt = path[i + 1]
                self.wlog(inc, state["t"], "misroute", state["tech"], 6000, 0,
                          self.misroute_text(g, nxt, sc), ctx=dict(from_group=g, to_group=nxt))
        if state["group"] != final:
            transfer(final)

        # ---- priority change mid-flight
        if r.random() < 0.04 and urgency > 1000:
            inc["urgency"] = urgency - 1000
            state["prio"] = self.priority(impact, inc["urgency"])
            tl.set(state["t"] + timedelta(minutes=5), state["tech"].login, Urgency=inc["urgency"], Priority=state["prio"])
            self.wlog(inc, state["t"] + timedelta(minutes=6), "escalation", state["tech"], 3000, 0,
                      "Urgency raised per customer/supervisor escalation.", ctx={})

        # ---- MI parent: status updates until restore
        if role == "parent":
            tt = state["t"]
            k = 0
            while tt < ev["end"] and k < 20:
                tt = tt + timedelta(minutes=self.lognorm(45, 0.4))
                k += 1
                self.wlog(inc, min(tt, ev["end"]), "status_update", state["tech"], 7000, 1,
                          f"Update {k}: {ev['title']}. Investigation ongoing; {ev['group']} working with vendor/engineering. Next update in 60 minutes.",
                          ctx=dict(update=k, cause=ev["cause"]))
            state["t"] = ev["end"]
            pbi = f"PBI{self.problem_seq:012d}"
            self.problem_seq += 1
            inc["assoc"] = [(pbi, "PBM:Problem Investigation", 39000)]
            if r.random() < 0.5:
                inc["assoc"].append((f"CRQ{self.change_seq:012d}", "CHG:Infrastructure Change", 40000))
                self.change_seq += 1
            inc["resolution"] = f"Service restored. Cause: {ev['cause']}. Root cause analysis tracked in {pbi}."
            inc["res_cat"] = ("Fix/Repair", "Infrastructure", "Service Restored")
            tl.set(state["t"], state["tech"].login, Status=4, Status_Reason=None, Last_Resolved_Date=state["t"],
                   Resolution=inc["resolution"])
            inc["resolved_t"] = state["t"]
            self.wlog(inc, state["t"], "resolution_comm", state["tech"], 5000, 1, inc["resolution"], ctx={})
            self.close(inc, state, state["t"], days=self.lognorm(6, 0.4))
            return

        # ---- final group work
        work(self.lognorm(0.3, 0.6))
        self.wlog(inc, state["t"], "work", state["tech"], 6000, 0, tg.fill(self.choice(T["notes"]), ctx), ctx={})
        pend_p, pend_reason = sc["pending"]
        stale = (inc["submit"] < EXTRACT - timedelta(days=60)) and r.random() < 0.004
        if (r.random() < pend_p and pend_reason) or stale:
            reason = pend_reason or 8000
            if T.get("ask") and reason in (8000, 13000, 4000, 3000):
                self.wlog(inc, state["t"], "ask", state["tech"], 2000, 1,
                          f"Hi {cust.first}, to continue working this ticket please {T['ask']}.", ctx=dict(ask=T["ask"]))
            tl.set(state["t"], state["tech"].login, Status=3, Status_Reason=reason)
            if stale:
                inc["stale"] = True
                return
            med = {7000: 48, 5000: 60, 9000: 120, 4000: 30, 3000: 50}.get(reason, 18)
            state["t"] = add_work(state["t"], self.lognorm(med, 0.9), "cust",
                                  ZoneInfo(SITES[cust.site_key]["tz"]))
            if reason in (8000, 13000, 4000):
                self.wlog(inc, state["t"], "reply", cust, 1000, 1, "Customer replied with the requested information.", ctx={})
            tl.set(state["t"], cust.login if reason in (8000, 13000, 4000) else state["tech"].login,
                   Status=2, Status_Reason=None)
        if r.random() < sc["vendor"]:
            mfr = (inc.get("product_override") or (None, sc["mfr"]))[1]
            if mfr == "Dell":
                part = self.choice(sc.get("parts") or ["replacement part"])
                tag = ctx.get("tag") or "unknown"
                self.wlog(inc, state["t"], "vendor", state["tech"], 8000, 0,
                          f"Opened Dell ProSupport case {int(r.integers(10 ** 8, 10 ** 9))} for service tag {tag}. "
                          f"Dispatch {int(r.integers(10 ** 8, 10 ** 9))} created for {part}, next-business-day onsite.",
                          ctx=dict(vendor="Dell ProSupport", tag=tag, part=part))
                tl.set(state["t"], state["tech"].login, Status=3, Status_Reason=5000)
                state["t"] = add_work(state["t"], self.lognorm(14, 0.6), "bus", self.group_tz(state["group"]))
                self.wlog(inc, state["t"], "vendor", state["tech"], 8000, 0,
                          f"Dell onsite technician replaced {part} on {ctx.get('ci')}. Dispatch closed.",
                          ctx=dict(vendor="Dell ProSupport", tag=tag, part=part))
            else:
                self.wlog(inc, state["t"], "vendor", state["tech"], 8000, 0,
                          f"Opened support case {int(r.integers(10 ** 7, 10 ** 8))} with {mfr}.", ctx=dict(vendor=mfr))
                tl.set(state["t"], state["tech"].login, Status=3, Status_Reason=7000)
                state["t"] = add_work(state["t"], self.lognorm(30, 0.8), "bus", self.group_tz(state["group"]))
            tl.set(state["t"], state["tech"].login, Status=2, Status_Reason=None)
        if r.random() < sc["change"]:
            crq = f"CRQ{self.change_seq:012d}"
            self.change_seq += 1
            inc.setdefault("assoc", []).append((crq, "CHG:Infrastructure Change", 40000))
            self.wlog(inc, state["t"], "change", state["tech"], 6000, 0, f"Fix requires change {crq}; scheduled for next maintenance window.",
                      ctx=dict(change=crq))
            tl.set(state["t"], state["tech"].login, Status=3, Status_Reason=1000)
            state["t"] = add_work(state["t"], self.lognorm(40, 0.6), "bus", self.group_tz(state["group"]))
            tl.set(state["t"], state["tech"].login, Status=2, Status_Reason=None)
        ttr = self.lognorm(*sc["ttr"])
        work(ttr)
        if role == "child":
            state["t"] = max(state["t"], ev["end"] + timedelta(minutes=self.lognorm(30, 0.8)))
        self.resolve(inc, state, T, ctx)

    def resolve(self, inc, state, T, ctx):
        r = self.rng
        tl = inc["tl"]
        sc = inc["scen"]
        res = tg.fill(self.choice(T["res"]), ctx)
        if inc["mi_role"] == "child":
            res = f"Service restored following {inc['mi_event']['title'].lower()}. " + res
        cancelled = False
        if r.random() < 0.025:
            res = self.choice(["User resolved the issue on their own; cancelling per customer.",
                               "Customer no longer requires assistance.",
                               "Ticket opened in error."])
            cancelled = True
        if r.random() < 0.06:
            res = self.choice(["fixed", "resolved", "done", "Issue resolved.", "see work notes", "completed"])
        tech = state["tech"]
        if cancelled:
            tl.set(state["t"], tech.login, Status=6, Status_Reason=None, Resolution=res)
            inc["res_cat"] = ("Cancelled", "No Longer Required", "")
            inc["resolution"] = res
            inc["cancelled"] = True
            return
        reason = self.wchoice({None: .8, 17000: .12, 15000: .05, 16000: .03})
        tl.set(state["t"], tech.login, Status=4, Status_Reason=reason, Last_Resolved_Date=state["t"], Resolution=res)
        inc["res_cat"] = sc["res"]
        inc["resolution"] = res
        inc["resolved_t"] = state["t"]
        system_cust = inc["cust"] is self.monperson or inc["source"] == 5000
        if r.random() < 0.55 and not system_cust:
            self.wlog(inc, state["t"], "resolution_comm", tech, 5000, 1,
                      f"Hello {inc['cust'].first}, your incident has been resolved: {res} If the issue returns, reply to this email within 5 days to reopen.",
                      ctx={})
        # reopen
        p_reopen = 0.03 + (0.03 if inc["cust"].persona == "frustrated" else 0) + (0.02 if sc["domain"] == "OT" else 0)
        if r.random() < p_reopen and inc["mi_role"] is None and not system_cust:
            t2 = state["t"] + timedelta(hours=self.lognorm(30, 0.8))
            self.wlog(inc, t2, "reopen", inc["cust"], 1000, 1, "This is happening again / still not fixed.", ctx={})
            tl.set(t2, inc["cust"].login, Status=2, Status_Reason=None, Re_Opened_Date=t2)
            inc["reopened"] = True
            state["t"] = t2
            g = state["group"]
            cal = self.org.groups[g]["cal"] if state["prio"] > 1 else "24x7"
            state["t"] = add_work(state["t"], self.lognorm(sc["ttr"][0] * 0.6, 0.8), cal, self.group_tz(g))
            res2 = tg.fill(self.choice(T["res"]), ctx)
            self.wlog(inc, state["t"], "work", tech, 6000, 0, "Follow-up after reopen: " + tg.fill(self.choice(T["notes"]), ctx), ctx={})
            inc["resolution"] = res + " UPDATE: " + res2
            tl.set(state["t"], tech.login, Status=4, Last_Resolved_Date=state["t"], Resolution=inc["resolution"])
            inc["resolved_t"] = state["t"]
        self.close(inc, state, state["t"])

    def close(self, inc, state, rt, days=5.0):
        r = self.rng
        tl = inc["tl"]
        if inc["mi_role"] is None and r.random() < 0.15 and inc["cust"] is not self.monperson:
            ct = add_work(rt, self.lognorm(6, 0.9), "cust", ZoneInfo(SITES[inc["cust"].site_key]["tz"]))
            tl.set(ct, inc["cust"].login, Status=5, Closed_Date=ct)
            return
        ct = (rt + timedelta(days=days)).astimezone(ET)
        ct = datetime.combine(ct.date() + timedelta(days=1), time(3, int(r.integers(0, 30)), int(r.integers(60))), ET)
        tl.set(ct.astimezone(UTC), SYS_ESC, Status=5, Closed_Date=ct.astimezone(UTC))

    def wlog(self, inc, t, kind, author, wl_type, view, text, ctx=None):
        a = author
        name = a.full if hasattr(a, "full") else str(a)
        login = a.login if hasattr(a, "login") else str(a)
        role = "customer" if (hasattr(a, "support") and not a.support) else "technician"
        grp = inc["tl"].state.get("Assigned_Group")
        attach = None
        if kind in ("reply", "triage") and self.rng.random() < 0.08:
            attach = self.choice(["screenshot.png", "error.jpg", "image001.png", "logs.zip", "IMG_4471.jpg"])
        inc["tl"].log(t, kind, dict(name=name, login=login, role=role, group=grp), wl_type, view, text,
                      text.split(".")[0][:100], ctx=ctx, attach=attach,
                      secure=1 if kind in ("vendor",) and self.rng.random() < 0.3 else 0)

    # ------------------------------------------------------------ cutoff + noise
    def finalize(self, inc):
        tl = inc["tl"]
        ext = EXTRACT.astimezone(UTC)
        tl.events = [e for e in tl.events if e[0] <= ext]
        tl.logs = [l for l in tl.logs if l["t"] <= ext]
        tl.events.sort(key=lambda e: e[0])
        tl.logs.sort(key=lambda l: l["t"])
        st = {}
        for _, _, ch, _ in tl.events:
            st.update(ch)
        inc["final"] = st
        inc["last_mod"] = tl.events[-1][0] if tl.events else inc["submit"]
        inc["last_mod_by"] = tl.events[-1][1] if tl.events else inc["submitter"]
        if st.get("Status") != 4 and st.get("Status") != 5:
            if st.get("Status") != 6:
                inc["resolution"] = None
        self.categorize(inc)

    def categorize(self, inc):
        r = self.rng
        sc = inc["scen"]
        op, prod = list(sc["op"]), list(sc["prod"])
        product, mfr = inc.get("product_override") or (sc["product"], sc["mfr"])
        inc["true_product"] = product
        kind = None
        mult = 1.6 if inc.get("vague_summary") else (0.4 if inc["mi_role"] else 1.0)
        u = r.random()
        if u < 0.07 * mult:
            op, kind = ["", "", ""], "blank"
        elif u < 0.13 * mult:
            op, kind = list(self.choice(LAZY_CATS)), "lazy"
        elif u < 0.19 * mult:
            other = self.choice([s for s in self.SCENARIOS if s["domain"] == sc["domain"] and s["id"] != sc["id"]] or self.SCENARIOS)
            op, prod, product, mfr, kind = list(other["op"]), list(other["prod"]), other["product"], other["mfr"], "wrong"
            if product == "@ci":
                product = self.choice(["Latitude 7450", "PowerEdge R760", "OptiPlex 7020", "PowerStore 1200T"])
        elif u < 0.25 * mult:
            op[2], kind = "", "partial"
        if r.random() < 0.08 * mult and kind != "wrong":
            prod, product, mfr = ["", "", ""], "", ""
            kind = (kind + "+" if kind else "") + "noproduct"
        inc["rec_op"], inc["rec_prod"], inc["rec_product"], inc["rec_mfr"] = op, prod, product, mfr
        inc["miscat"] = kind
        rc = inc.get("res_cat") or ("", "", "")
        if r.random() < 0.12:
            rc = ("", "", "")
        inc["rec_res_cat"] = rc if inc["final"].get("Status") in (4, 5, 6) else ("", "", "")
