"""Pluggable ticket loaders. Each returns a Corpus of canonical tickets.

The canonical ticket shape matches webapp/data/tickets.json (tools/build_tickets.py),
so hits can be rendered by the UI unchanged. Customer identity is kept server-side
in `_customer` and stripped before tickets are returned to clients.

Add a loader for real data (ServiceNow Table API, Helix REST, a DB export...) by
writing a function that returns Corpus and registering it in LOADERS.
"""
import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field

csv.field_size_limit(10 ** 8)


@dataclass
class Corpus:
    tickets: list
    truth: dict = field(default_factory=dict)      # Incident_Number -> ground-truth row (synthetic data only)
    people: dict = field(default_factory=dict)     # person_id -> {name, dept, site, title}
    source: str = ""


def _read(src, name):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _enums(src):
    out = {}
    for r in _read(src, "data_dictionary.csv"):
        if r.get("type") == "enum" and r.get("enum_values"):
            m = {}
            for part in r["enum_values"].split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    m[k.strip()] = v.strip()
            out[f"{r['table']}.{r['column']}"] = m
    return out


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def helix_csv(src):
    """Synthetic (or real) BMC Helix AR-style CSV export, as written by it-tickets/generate.py."""
    en = _enums(src)
    e = lambda f, v: en.get(f"HPD_Help_Desk.{f}", {}).get(v, v)  # noqa: E731
    wl_type = en.get("HPD_WorkLog.Work_Log_Type", {})
    worklogs = defaultdict(list)
    for w in _read(src, "HPD_WorkLog.csv"):
        worklogs[w["Incident_Number"]].append({
            "t": _num(w["Work_Log_Submit_Date"] or w["Submit_Date"]),
            "type": wl_type.get(w["Work_Log_Type"], w["Work_Log_Type"]),
            "by": w["Work_Log_Submitter"],
            "text": w["Detailed_Description"] or w["Description"],
        })
    assoc = defaultdict(list)
    for a in _read(src, "HPD_Associations.csv"):
        if a.get("Form_Name01") == "HPD:Help Desk":
            assoc[a["Request_ID01"]].append(a.get("Request_Description01") or a.get("Request_ID02"))
    truth = {g["Incident_Number"]: g for g in _read(src, "ground_truth.csv")}
    people = {p["Person_ID"]: dict(name=p["Full_Name"], dept=p["Department"], site=p["Site"], title=p["Job_Title"],
                                   support=p["Support_Staff"] == "1")
              for p in _read(src, "CTM_People.csv")}
    tickets = []
    for r in _read(src, "HPD_Help_Desk.csv"):
        inc = r["Incident_Number"]
        g = truth.get(inc, {})
        tickets.append({
            "id": inc, "summary": r["Description"], "details": r["Detailed_Decription"],
            "status": e("Status", r["Status"]), "priority": e("Priority", r["Priority"]),
            "impact": e("Impact", r["Impact"]), "urgency": e("Urgency", r["Urgency"]),
            "serviceType": e("Service_Type", r["Service_Type"]), "source": e("Reported_Source", r["Reported_Source"]),
            "site": r["Site"], "dept": r["Department"], "group": r["Assigned_Group"], "assignee": r["Assignee"],
            "cat": [r["Categorization_Tier_1"], r["Categorization_Tier_2"], r["Categorization_Tier_3"]],
            "prodCat": [r["Product_Categorization_Tier_1"], r["Product_Categorization_Tier_2"],
                        r["Product_Categorization_Tier_3"]],
            "product": r["Product_Name"], "manufacturer": r["Manufacturer"], "ci": r["HPD_CI"],
            "serviceCI": r["ServiceCI"], "reported": _num(r["Reported_Date"]), "responded": _num(r["Responded_Date"]),
            "resolved": _num(r["Last_Resolved_Date"]), "closed": _num(r["Closed_Date"]),
            "reopened": _num(r["Re_Opened_Date"]), "resolution": r["Resolution"],
            "resCat": [r["Resolution_Category"], r["Resolution_Category_Tier_2"], r["Resolution_Category_Tier_3"]],
            "transfers": _num(r["Total_Transfers"]) or 0, "major": r["Major_Incident"] == "1",
            "domain": g.get("domain", ""), "scenario": g.get("scenario_id", ""),
            "related": assoc.get(inc, []),
            "worklog": sorted(worklogs.get(inc, []), key=lambda w: w["t"] or 0),
            "origin": "helix",
            "_customer": dict(person_id=r["Person_ID"], name=f"{r['First_Name']} {r['Last_Name']}".strip()),
            "_group_transfers": _num(r.get("Group_Transfers")) or 0,
        })
    tickets.sort(key=lambda t: t["reported"] or 0)
    return Corpus(tickets=tickets, truth=truth, people=people, source=os.path.abspath(src))


LOADERS = {"helix_csv": helix_csv}


def load(kind, src):
    if kind not in LOADERS:
        raise ValueError(f"unknown loader {kind!r}; available: {', '.join(LOADERS)}")
    return LOADERS[kind](src)


def public(t):
    """Ticket as returned to agent/manager clients (no customer identity)."""
    return {k: v for k, v in t.items() if not k.startswith("_")}
