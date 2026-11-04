#!/usr/bin/env python3
"""Convert a synthetic BMC Helix export (it-tickets/out/<run>/) into webapp/data/tickets.json,
and migrate the tickets still open at cutover into webapp/data/servicenow.json.

The web UI loads this file for ticket lookup, the client-side placeholder retriever
and the MTTR charts. Re-run it when a larger run (e.g. out/full) is ready:

    python3 tools/build_tickets.py ../it-tickets/out/full

Cutover migration: every incident that is still open in Helix (New, Assigned, In Progress,
Pending) is copied to ServiceNow as a new incident (INC0010001…, oldest first) with its
work notes, and its Helix record is closed as "Transferred to ServiceNow". From then on
the Helix data is history only. itsm-rag-server reads servicenow.json too.

Customer contact fields (email, phone, person IDs) are dropped on purpose; the UI
only needs the operational fields.
"""
import csv
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(HERE, "..", "..", "it-tickets", "out", "sample")
OUT = os.path.join(HERE, "..", "data", "tickets.json")
SNOW_OUT = os.path.join(HERE, "..", "data", "servicenow.json")

# Keep in step with CUTOVER in js/ai/itsm.js (midday UTC on the cutover date).
CUTOVER_DATE = "2026-11-02"
CUTOVER_TS = 1793620800  # 2026-11-02T12:00:00Z
FIRST_NUMBER = 10001     # ServiceNow default numbering: INC0010001
OPEN_HELIX = {"New": "New", "Assigned": "In Progress", "In Progress": "In Progress", "Pending": "On Hold"}
HOLD_REASON = {  # Helix Status_Reason -> ServiceNow on_hold_reason
    "Client Action Required": "Awaiting Caller", "Client Hold": "Awaiting Caller", "Customer Follow-Up Required": "Awaiting Caller",
    "Third Party Vendor Action Reqd": "Awaiting Vendor", "Supplier Delivery": "Awaiting Vendor", "Purchase Order Approval": "Awaiting Vendor",
    "Infrastructure Change": "Awaiting Change", "Infrastructure Change Request": "Awaiting Change",
    "Registration Approval": "Awaiting Approval", "Pending Original Incident": "Awaiting Problem",
}

csv.field_size_limit(10**8)


def read(src, name):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def enums(src):
    """field -> {code: label} from data_dictionary.csv."""
    out = {}
    for r in read(src, "data_dictionary.csv"):
        if r.get("type") == "enum" and r.get("enum_values"):
            m = {}
            for part in r["enum_values"].split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    m[k.strip()] = v.strip()
            out[f"{r['table']}.{r['column']}"] = m
    return out


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main():
    src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC)
    en = enums(src)
    e = lambda field, v: en.get(f"HPD_Help_Desk.{field}", {}).get(v, v)

    worklogs = defaultdict(list)
    wl_type = en.get("HPD_WorkLog.Work_Log_Type", {})
    for w in read(src, "HPD_WorkLog.csv"):
        worklogs[w["Incident_Number"]].append({
            "t": num(w["Work_Log_Submit_Date"] or w["Submit_Date"]),
            "type": wl_type.get(w["Work_Log_Type"], w["Work_Log_Type"]),
            "by": w["Work_Log_Submitter"],
            "text": w["Detailed_Description"] or w["Description"],
        })

    assoc = defaultdict(list)
    for a in read(src, "HPD_Associations.csv"):
        if a.get("Form_Name01") == "HPD:Help Desk":
            assoc[a["Request_ID01"]].append(a.get("Request_Description01") or a.get("Request_ID02"))

    truth = {g["Incident_Number"]: g for g in read(src, "ground_truth.csv")}

    tickets = []
    for r in read(src, "HPD_Help_Desk.csv"):
        inc = r["Incident_Number"]
        g = truth.get(inc, {})
        tickets.append({
            "id": inc,
            "summary": r["Description"],
            "details": r["Detailed_Decription"],
            "status": e("Status", r["Status"]),
            "statusReason": e("Status_Reason", r["Status_Reason"]) if r["Status_Reason"] else "",
            "priority": e("Priority", r["Priority"]),
            "impact": e("Impact", r["Impact"]),
            "urgency": e("Urgency", r["Urgency"]),
            "serviceType": e("Service_Type", r["Service_Type"]),
            "source": e("Reported_Source", r["Reported_Source"]),
            "site": r["Site"],
            "dept": r["Department"],
            "group": r["Assigned_Group"],
            "assignee": r["Assignee"],
            "cat": [r["Categorization_Tier_1"], r["Categorization_Tier_2"], r["Categorization_Tier_3"]],
            "prodCat": [r["Product_Categorization_Tier_1"], r["Product_Categorization_Tier_2"],
                        r["Product_Categorization_Tier_3"]],
            "product": r["Product_Name"],
            "manufacturer": r["Manufacturer"],
            "ci": r["HPD_CI"],
            "serviceCI": r["ServiceCI"],
            "reported": num(r["Reported_Date"]),
            "responded": num(r["Responded_Date"]),
            "resolved": num(r["Last_Resolved_Date"]),
            "closed": num(r["Closed_Date"]),
            "reopened": num(r["Re_Opened_Date"]),
            "resolution": r["Resolution"],
            "resCat": [r["Resolution_Category"], r["Resolution_Category_Tier_2"], r["Resolution_Category_Tier_3"]],
            "transfers": num(r["Total_Transfers"]) or 0,
            "major": r["Major_Incident"] == "1",
            "domain": g.get("domain", ""),
            "scenario": g.get("scenario_id", ""),
            "related": assoc.get(inc, []),
            "worklog": sorted(worklogs.get(inc, []), key=lambda w: w["t"] or 0),
        })

    tickets.sort(key=lambda t: t["reported"] or 0)
    incidents = migrate(tickets)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": os.path.relpath(src, os.path.join(HERE, "..", "..")),
                   "platform": "BMC Helix ITSM (synthetic AR System extract)",
                   "count": len(tickets), "tickets": tickets}, f, separators=(",", ":"))
    print(f"wrote {len(tickets)} tickets -> {os.path.relpath(OUT)}")
    with open(SNOW_OUT, "w", encoding="utf-8") as f:
        json.dump({"platform": "ServiceNow", "table": "incident", "cutover": CUTOVER_DATE, "migratedFrom": "BMC Helix",
                   "count": len(incidents), "nextNumber": FIRST_NUMBER + len(incidents), "incidents": incidents},
                  f, separators=(",", ":"))
    print(f"migrated {len(incidents)} open incidents -> {os.path.relpath(SNOW_OUT)}")


def migrate(tickets):
    """Copy open Helix incidents to ServiceNow; close the Helix originals as transferred (in place)."""
    out = []
    for t in (t for t in tickets if t["status"] in OPEN_HELIX and (t["reported"] or 0) < CUTOVER_TS):
        number = f"INC{FIRST_NUMBER + len(out):07d}"
        was = t["status"] + (f" ({t['statusReason']})" if t["statusReason"] else "")
        sn = dict(t, id=number, origin="servicenow", status=OPEN_HELIX[t["status"]],
                  holdReason=HOLD_REASON.get(t["statusReason"], "Awaiting Caller") if t["status"] == "Pending" else "",
                  statusReason="", correlationId=t["id"], migratedAt=CUTOVER_TS, resolution="", resCat=["", "", ""],
                  resolved=None, closed=None,
                  worklog=t["worklog"] + [{"t": CUTOVER_TS, "type": "Work notes", "by": "Helix migration",
                                           "text": f"Migrated from BMC Helix {t['id']} at cutover ({CUTOVER_DATE}). "
                                                   f"Helix status was {was}. Work notes carried over above."}])
        out.append(sn)
        t.update(migratedTo=number, preMigrationStatus=was, status="Closed", statusReason="Transferred to ServiceNow",
                 closed=CUTOVER_TS, resolution="", resCat=["Transferred to ServiceNow", "", ""])
    return out


if __name__ == "__main__":
    main()
