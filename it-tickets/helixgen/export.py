"""Build AR-style table rows from the simulation and write CSVs + LLM job files."""
import csv
import gzip
import hashlib
import json
import os

from .arschema import ASSOC_TYPE, IMPACT, PRIORITY, REPORTED_SOURCE, SERVICE_TYPE, STATUS, STATUS_REASON, \
    TABLES, URGENCY, WORK_LOG_TYPE
from .org import COMPANY, PERSONAS, SITES

AGENCY_BLURB = ("The Bureau of Engraving and Printing (BEP) is a fictional US federal agency that prints paper currency. "
                "It has a headquarters in Washington DC, the Capital Production Facility (Washington DC) and the Southwest "
                "Production Facility (Fort Worth TX), plus teleworkers. Plants run offset and intaglio presses, overprint "
                "and finishing lines with inline inspection, a MES called PlantTrack, and an OT network. Office IT uses "
                "Microsoft 365, PIV smart-card login, GlobalProtect VPN, the Ledgerline ERP and TimeWeb timekeeping.")
PERSONA_DESC = {n: d for n, _, d in PERSONAS}
DISPLAY = {"Status": STATUS, "Status_Reason": STATUS_REASON, "Priority": PRIORITY, "Impact": IMPACT,
           "Urgency": URGENCY}


def ep(t):
    return int(t.timestamp()) if t is not None else None


def cols(table, style):
    return [(f"C{fid}" if style == "cid" else c) for c, fid, *_ in TABLES[table][1]]


def write_csv(path, table, rows, style="view"):
    names = [c for c, *_ in TABLES[table][1]]
    hdr = cols(table, style)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(hdr)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in names])


def write_dictionary(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "form", "column", "field_id", "label", "type", "enum_values", "field_id_confidence"])
        for t, (form, columns) in TABLES.items():
            for c, fid, label, typ, enum, conf in columns:
                ev = "; ".join(f"{k}={v}" for k, v in enum.items()) if enum else ""
                w.writerow([t, form, c, fid, label, typ, ev, conf])


def llm_selected(num, frac, seed):
    h = int(hashlib.md5(f"{seed}:{num}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < frac


def build(org, incidents, seed, llm_fraction, models):
    name2login = {p.full: p.login for p in org.people if p.support}
    rows = {t: [] for t in TABLES}
    gt, jobs = [], []
    aud_seq = wl_seq = assoc_seq = 0
    all_logs = []

    for inc in incidents:
        st, tl, cust, sc = inc["final"], inc["tl"], inc["cust"], inc["scen"]
        group = st.get("Assigned_Group")
        ci, svc = inc.get("ci"), inc.get("service_ci")
        assignee = st.get("Assignee")
        g_changes = sum(1 for e in tl.events[1:] if "Assigned_Group" in e[2])
        a_changes = sum(1 for e in tl.events if e[2].get("Assignee"))
        row = dict(
            Entry_ID=inc["entry_id"], Submitter=inc["submitter"], Submit_Date=ep(inc["submit"]),
            Assignee_Login_ID=name2login.get(assignee), Last_Modified_By=inc["last_mod_by"],
            Last_Modified_Date=ep(inc["last_mod"]), Status=st.get("Status"), Incident_Number=inc["num"],
            Description=inc["summary"], Detailed_Decription=inc["description"], Service_Type=inc["stype"],
            Reported_Source=inc["source"], Impact=st.get("Impact"), Urgency=st.get("Urgency"),
            Priority=st.get("Priority"), Status_Reason=st.get("Status_Reason"), Person_ID=cust.person_id,
            First_Name=cust.first, Last_Name=cust.last, Internet_E_mail=cust.email, Phone_Number=cust.phone,
            VIP=int(cust.vip), Company=COMPANY, Organization=cust.org, Department=cust.dept,
            Site=SITES[cust.site_key]["site"], Region=SITES[cust.site_key]["region"],
            Assigned_Support_Company=COMPANY, Assigned_Support_Organization=org.groups[group]["org"] if group else None,
            Assigned_Group=group, Assigned_Group_ID=org.groups[group]["id"] if group else None, Assignee=assignee,
            Categorization_Tier_1=inc["rec_op"][0] or None, Categorization_Tier_2=inc["rec_op"][1] or None,
            Categorization_Tier_3=inc["rec_op"][2] or None,
            Product_Categorization_Tier_1=inc["rec_prod"][0] or None,
            Product_Categorization_Tier_2=inc["rec_prod"][1] or None,
            Product_Categorization_Tier_3=inc["rec_prod"][2] or None,
            Product_Name=inc["rec_product"] or None, Manufacturer=inc["rec_mfr"] or None,
            HPD_CI=ci.name if ci else None, HPD_CI_ReconID=ci.recon_id if ci else None,
            ServiceCI=svc.name if svc else None, ServiceCI_ReconID=svc.recon_id if svc else None,
            Reported_Date=ep(inc["reported"]), Responded_Date=ep(st.get("Responded_Date")),
            Last_Resolved_Date=ep(st.get("Last_Resolved_Date")), Closed_Date=ep(st.get("Closed_Date")),
            Re_Opened_Date=ep(st.get("Re_Opened_Date")), Resolution=st.get("Resolution"),
            Resolution_Category=inc["rec_res_cat"][0] or None, Resolution_Category_Tier_2=inc["rec_res_cat"][1] or None,
            Resolution_Category_Tier_3=inc["rec_res_cat"][2] or None, Group_Transfers=g_changes,
            Total_Transfers=a_changes, Major_Incident=1 if inc["mi_role"] == "parent" else 0,
        )
        rows["HPD_Help_Desk"].append(row)

        # audit trail
        for t, user, ch, action in tl.events:
            aud_seq += 1
            lines = []
            for k, v in ch.items():
                if k in ("Responded_Date", "Last_Resolved_Date", "Closed_Date", "Re_Opened_Date"):
                    v = t.strftime("%m/%d/%Y %I:%M:%S %p UTC")
                elif k in DISPLAY:
                    v = DISPLAY[k].get(v, "") if v is not None else ""
                elif k == "Resolution":
                    v = (v or "")[:120]
                lines.append(f"{k.replace('_', ' ')}: {'' if v is None else v}")
            rows["HPD_HelpDesk_AuditLogSystem"].append(dict(
                Request_ID=f"{aud_seq:015d}", Original_Request_ID=inc["entry_id"], Incident_Number=inc["num"],
                Audit_Date=ep(t), User=user, Action=action,
                Fields_Changed=";" + ";".join(k.replace("_", " ") for k in ch) + ";", Log="\n".join(lines)))

        for i, lg in enumerate(tl.logs):
            all_logs.append((lg["t"], inc, i, lg))

        # associations
        for target, form, atype in inc.get("assoc", []):
            assoc_seq += 1
            rows["HPD_Associations"].append(dict(
                Request_ID=f"{assoc_seq:015d}", Submit_Date=ep(inc["submit"]), Request_ID01=inc["num"],
                Form_Name01="HPD:Help Desk", Request_ID02=target, Form_Name02=form, Association_Type01=atype,
                Request_Description01=inc["summary"]))
            if form == "HPD:Help Desk":
                assoc_seq += 1
                rev = {36000: 37000}.get(atype, atype)
                rows["HPD_Associations"].append(dict(
                    Request_ID=f"{assoc_seq:015d}", Submit_Date=ep(inc["submit"]), Request_ID01=target,
                    Form_Name01="HPD:Help Desk", Request_ID02=inc["num"], Form_Name02=form,
                    Association_Type01=rev, Request_Description01=None))
        if ci is not None:
            assoc_seq += 1
            rows["HPD_Associations"].append(dict(
                Request_ID=f"{assoc_seq:015d}", Submit_Date=ep(inc["submit"]), Request_ID01=inc["num"],
                Form_Name01="HPD:Help Desk", Request_ID02=ci.recon_id, Form_Name02="BMC.CORE:BMC_BaseElement",
                Association_Type01=41000, Request_Description01=ci.name))

        # ground truth sidecar
        gt.append(dict(
            Incident_Number=inc["num"], scenario_id=sc["id"], domain=sc["domain"],
            true_Categorization_Tier_1=sc["op"][0], true_Categorization_Tier_2=sc["op"][1],
            true_Categorization_Tier_3=sc["op"][2], true_Product_Categorization_Tier_1=sc["prod"][0],
            true_Product_Categorization_Tier_2=sc["prod"][1], true_Product_Categorization_Tier_3=sc["prod"][2],
            true_Product_Name=inc.get("true_product", sc["product"]), true_owner_group=inc["final_group"],
            line=sc.get("line", ""),
            resolved_by_group=group if st.get("Status") in (4, 5) else "",
            miscategorization=inc["miscat"] or "", vague_summary=int(bool(inc.get("vague_summary"))),
            misroute_hops=inc["misroutes"], reopened=int(bool(inc.get("reopened"))),
            major_incident_event=inc["mi_event"]["id"] if inc["mi_event"] else "",
            major_incident_role=inc["mi_role"] or "",
            major_incident_parent=inc["mi_parent"]["num"] if inc.get("mi_parent") else "",
            duplicate_of=inc["dup_of"]["num"] if inc.get("dup_of") else "",
            stale=int(bool(inc.get("stale"))), customer_persona=cust.persona, text_author=inc["text_author"],
            text_source="template"))

        # LLM job
        if llm_fraction > 0 and llm_selected(inc["num"], llm_fraction, seed):
            m = pick_model(inc["num"], models)
            jobs.append(make_job(inc, m))

    all_logs.sort(key=lambda x: x[0])
    for t, inc, i, lg in all_logs:
        wl_seq += 1
        a = lg["author"]
        lg["id"] = f"WLG{wl_seq + 2200000:012d}"
        rows["HPD_WorkLog"].append(dict(
            Work_Log_ID=lg["id"], Submitter=a["login"], Submit_Date=ep(t), Incident_Number=inc["num"],
            Incident_Entry_ID=inc["entry_id"], Work_Log_Type=lg["type"], Description=lg["summary"],
            Detailed_Description=lg["text"], Work_Log_Submitter=a["name"], Work_Log_Submit_Date=ep(t),
            View_Access=lg["view"], Secure_Work_Log=lg["secure"], Number_of_Attachments=1 if lg["attach"] else 0,
            z2AF_Work_Log01=lg["attach"]))
    for j in jobs:  # attach work log ids now that they exist
        for w in j["worklogs"]:
            w["id"] = w.pop("_lg")["id"]

    # people / groups / CIs
    for p in org.people:
        rows["CTM_People"].append(dict(
            Person_ID=p.person_id, Submit_Date=1704067200, Profile_Status=1, First_Name=p.first, Last_Name=p.last,
            Full_Name=p.full, Remedy_Login_ID=p.login, Internet_E_mail=p.email, Phone_Number_Business=p.phone,
            Company=COMPANY, Organization=p.org, Department=p.dept, Site=SITES[p.site_key]["site"],
            Job_Title=p.title, Support_Staff=int(p.support), VIP=int(p.vip)))
    sga = 0
    for g, info in org.groups.items():
        rows["CTM_Support_Group"].append(dict(
            Support_Group_ID=info["id"], Status=1, Company=COMPANY, Support_Organization=info["org"],
            Support_Group_Name=g, Support_Group_Role=info["role"],
            Business_Hours={"bus": "Mon-Fri 07:00-17:30", "sd": "Mon-Fri 06:00-20:00, Sat 08:00-16:00",
                            "24x7": "24x7"}[info["cal"]]))
        for p in info["members"]:
            sga += 1
            rows["CTM_Support_Group_Association"].append(dict(
                Request_ID=f"SGA{sga:012d}", Person_ID=p.person_id, Login_ID=p.login, Support_Group_ID=info["id"],
                Support_Group_Name=g, Default=int(p.groups[0] == g)))
    for c in org.cis:
        rows["BMC_BaseElement"].append(dict(
            InstanceId=c.instance_id, ReconciliationIdentity=c.recon_id, ClassId=c.class_id.upper(), Name=c.name,
            ShortDescription=c.desc, Category=c.category, Type=c.ctype, Item=c.item, Model=c.model,
            ManufacturerName=c.manufacturer, SerialNumber=getattr(c, "serial", "") or None,
            Site=SITES[c.site_key]["site"], AssetLifecycleStatus=c.status,
            OwnerName=c.owner, Submit_Date=1704067200))
    cls_by_rid = {c.recon_id: c.class_id.upper() for c in org.cis}
    for k, (cls, name, src, dst) in enumerate(org.rels, 1):
        rows["BMC_BaseRelationship"].append(dict(
            InstanceId="OI-" + hashlib.sha1(f"rel{k}".encode()).hexdigest()[:32].upper(), ClassId=cls.upper(),
            Name=name, Source_ReconciliationIdentity=src, Destination_ReconciliationIdentity=dst,
            Source_ClassId=cls_by_rid.get(src), Destination_ClassId=cls_by_rid.get(dst)))
    return rows, gt, jobs


def pick_model(num, models):
    """models: list of "name" or "name=weight"; deterministic per incident."""
    names, weights = [], []
    for m in models:
        n, _, w = m.partition("=")
        names.append(n)
        weights.append(float(w or 1))
    x = int(hashlib.md5(num.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * sum(weights)
    for n, w in zip(names, weights):
        x -= w
        if x <= 0:
            return n
    return names[-1]


def make_job(inc, model):
    cust, sc, st = inc["cust"], inc["scen"], inc["final"]
    ev = inc["mi_event"]
    facts = dict(
        customer=dict(name=cust.full, first=cust.first, title=cust.title, dept=cust.dept,
                      site=SITES[cust.site_key]["site"], persona=PERSONA_DESC.get(cust.persona, "")),
        reported_source=REPORTED_SOURCE[inc["source"]], service_type=SERVICE_TYPE[inc["stype"]],
        description_author=inc["text_author"], product=inc.get("true_product", sc["product"]),
        manufacturer=(inc.get("product_override") or (None, sc["mfr"]))[1],
        category=" > ".join(sc["op"]), ci=inc["ci"].name if inc.get("ci") else None,
        production_line=inc["ctx"].get("line") or None, site_of_issue=SITES[inc["site_key"]]["site"],
        error_text=inc["ctx"].get("err") or None, seed_summary=inc["summary"],
        seed_description=inc["description"], final_status=STATUS.get(st.get("Status")),
        resolution_seed=st.get("Resolution"), priority=PRIORITY.get(st.get("Priority")),
    )
    if inc.get("email_wrap"):
        hdr, greet, sig = inc["email_wrap"]
        facts["seed_description"] = inc["description"][len(hdr + greet):len(inc["description"]) - len(sig)]
    if ev:
        facts["major_incident"] = dict(role=inc["mi_role"], title=ev["title"], cause=ev["cause"] if inc["mi_role"] == "parent" else None)
    wls = []
    for lg in inc["tl"].logs:
        a = lg["author"]
        wls.append(dict(_lg=lg, kind=lg["kind"], type=WORK_LOG_TYPE[lg["type"]], author=a["name"], author_role=a["role"],
                        group=a.get("group"), visibility="public" if lg["view"] else "internal",
                        seed=lg["text"], ctx={k: v for k, v in lg["ctx"].items()}))
    return dict(num=inc["num"], model=model, persona=cust.persona, keep_summary=bool(inc.get("vague_summary")),
                scenario=sc["id"], ctx=inc["ctx"], mi_event=ev["id"] if ev else None, mi_role=inc["mi_role"],
                email_wrap=inc.get("email_wrap"), facts=facts, worklogs=wls)


def write_all(out, rows, gt, jobs, style="view", profile="bep"):
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, "_work")
    os.makedirs(work, exist_ok=True)
    for t, rs in rows.items():
        write_csv(os.path.join(out, f"{t}.csv"), t, rs, style)
        if t in ("HPD_Help_Desk", "HPD_WorkLog"):
            with gzip.open(os.path.join(work, f"{t}.rows.jsonl.gz"), "wt", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(r) + "\n")
    with open(os.path.join(out, "ground_truth.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(gt[0]))
        w.writeheader()
        w.writerows(gt)
    with open(os.path.join(work, "llm_jobs.jsonl"), "w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j) + "\n")
    write_dictionary(os.path.join(out, "data_dictionary.csv"))
    with open(os.path.join(work, "meta.json"), "w") as f:
        json.dump(dict(style=style, profile=profile), f)
