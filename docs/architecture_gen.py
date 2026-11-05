import html, sys
E = html.escape
C = {1:(40,200), 2:(290,210), 3:(560,360), 4:(975,175), 5:(1190,140)}
N = {}
def node(id, col, y, title, subs=(), st="live", lane="itsm", x=None, w=None, mono=False, kind="box"):
    cx, cw = C[col]
    x = cx if x is None else x; w = cw if w is None else w
    h = 30 + 15*len(subs) + (8 if subs else 2)
    N[id] = dict(x=x, y=y, w=w, h=h, title=title, subs=subs, st=st, lane=lane, mono=mono, kind=kind)
EDG = []
def edge(a, b, st="live", label=None, how="h", pts=None, arrow=True, ax=None, bx=None):
    EDG.append(dict(a=a, b=b, st=st, label=label, how=how, pts=pts, arrow=arrow, ax=ax, bx=bx))

LANES = [("itsm","ITSM Next Best Step","Primary use case · Day-3 build · live against local models",70,365),
         ("cv","Computer-vision defect detection","Secondary · in-browser stand-in today, trained models in progress",385,665),
         ("shared","Shared across both use cases","",685,850)]

# ---- ITSM
node("gen",1,110,"it-tickets/generate.py",["helixgen: org, scenarios,","AR System schema simulation"])
node("llm",1,192,"llm_pool / llm_pass",["rewrites ticket text with","local Ollama models"])
node("bt",1,274,"tools/build_tickets.py",["cutover: open Helix incidents","copied to ServiceNow"])
node("helix",2,140,"BMC Helix archive",["10k synthetic incidents (CSV)","migrated read-only at cutover"])
node("snow",2,250,"ServiceNow",["62 migrated INC00100xx","live feed: future"],st="partial")
node("rag",3,110,"itsm-rag-server · FastAPI :8030",[
  "loaders  helix_csv, re-pointable",
  "index    Nemotron embed + BM25 → RRF → rerank",
  "router   logistic regression; LLM if conf < 0.7",
  "rag      recommend · copilot · self-service · SQL",
  "tiers    small model when evidence is strong"],mono=True)
for i,(id,t,s) in enumerate([("small","Small tier",["gemma4-e4b","Ollama · ~5 GB"]),
                              ("big","Big tier",["gpt-oss-20b","vLLM · 31 GB"]),
                              ("emb","Retrieval",["Nemotron embed","+ rerank 1b-v2"])]):
    node(id,3,250,t,s,x=560+i*124,w=112,kind="model")
node("v_itsm",4,110,"ITSM Next Best Step",["ranked steps, citations,","routing, copilot chat"],kind="view")
node("v_help",4,192,"IT Self-Service",["PII-scrubbed self-help,","opens pre-routed ticket"],kind="view")
node("v_ins",4,274,"IT Insights",["plain-English questions","over the archive"],kind="view")
node("u_agent",5,117,"IT Support Agent",["< 20 agents"],kind="user")
node("u_emp",5,199,"Employee",["self-help first"],kind="user")
node("u_mgr",5,281,"IT Manager",["MTTR, trends"],kind="user")
edge("gen","llm",how="v"); edge("llm","bt",how="v")
edge("llm","helix",label="CSV"); edge("bt","snow",label="JSON")
edge("helix","rag"); edge("snow","rag",st="partial")
for m in ("small","big","emb"): edge("rag",m,how="v",ax=N[m]["x"]+56,bx=N[m]["x"]+56)
edge("rag","v_itsm",label="/itsm/*"); edge("rag","v_help",label="/selfservice/*"); edge("rag","v_ins",label="/analytics/ask")
for v,u in (("v_itsm","u_agent"),("v_help","u_emp"),("v_ins","u_mgr")): edge(v,u,arrow=False)

# ---- CV
node("sp",1,425,"synthetic_print",["fictional engraved banknotes,","8 defect classes, seeded"],lane="cv")
node("cam",1,556,"Line cameras",["line stream + rules-based","reject logs (future)"],st="planned",lane="cv")
node("sheets",2,425,"Press-sheet dataset",["sheets, masks, labels;","11.3k 640 px tiles"],lane="cv")
node("fo",2,505,"FiftyOne :5151",["browse sheets, predictions,","false negatives"],lane="cv")
node("train",3,425,"Training on-prem · MLflow tracked",[
  "PatchCore    good sheets only → heatmap",
  "RF-DETR-Seg  boxes · 8 classes · masks",
  "Qwen3-VL-8B  LoRA on defect crops (TRL/PEFT)",
  "overnight.sh sized to free GPU memory"],mono=True,lane="cv",st="partial")
node("now",3,548,"Today: in the browser",["golden template (PaDiM)","+ PoC RF-DETR detections","160 sheets · manifest.json"],x=746,w=174,lane="cv",st="placeholder")
node("rt",3,548,"Planned: line runtime",["DeepStream 9.2, TensorRT","MQTT → results API (SSE)","Qwen3-VL on flagged only"],x=560,w=174,lane="cv",st="planned")
node("v_vis",4,540,"Defect Inspection",["review queue, heatmaps,","defect description,","disposition, thresholds"],lane="cv",kind="view")
node("u_qa",5,520,"QA Inspector",["final disposition"],lane="cv",kind="user")
node("u_qe",5,588,"Quality Engineer",["tunes thresholds"],lane="cv",kind="user")
edge("sp","sheets"); edge("sheets","train"); edge("sheets","fo",how="v",arrow=False,ax=395,bx=395)
edge("train","now",how="v",ax=833,bx=833,label="detections")
edge("train","rt",how="v",ax=647,bx=647,label="TensorRT",st="planned")
edge("cam","rt",st="planned")
edge("now","v_vis")
edge("rt","v_vis",st="planned",pts=[(647,631),(647,648),(953,648),(953,606),(975,606)])
edge("v_vis","u_qa",arrow=False); edge("v_vis","u_qe",arrow=False)

# ---- shared
node("gb10",1,725,"On-prem infrastructure",["no external API calls; data stays inside","the agency boundary; a human makes","every final decision · prod: PowerEdge XE"],w=460,lane="shared",kind="platform")
node("store",3,725,"js/store.js · browser storage",["roles, audit trail, provenance stamps,","accepted ServiceNow resolutions"],lane="shared")
node("ov",4,722,"Mission Overview",["KPIs for both use cases"],lane="shared",kind="view")
node("au",4,784,"Governance & Audit",["every AI output + review"],lane="shared",kind="view")
node("u_all",5,722,"Everyone",["role-filtered nav"],lane="shared",kind="user")
node("u_adm",5,784,"Administrator",["sees every view"],lane="shared",kind="user")
edge("store","ov"); edge("store","au"); edge("ov","u_all",arrow=False); edge("au","u_adm",arrow=False)

W, H = 1370, 870
o = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="dgt"><title id="dgt">Architecture of the BEP Operations AI lab: two use cases from data generation through models and web views to users</title>']
o.append('<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arh"/></marker>'
         '<marker id="arp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arh-p"/></marker></defs>')
heads = [(1,"Data generation"),(2,"Data & systems of record"),(3,"AI services & models"),(4,"Web UI · nginx :8080"),(5,"Users")]
for c,t in heads:
    x,w = C[c]; o.append(f'<text x="{x}" y="30" class="colh">{E(t.upper())}</text>')
    o.append(f'<line x1="{x}" y1="40" x2="{x+w}" y2="40" class="colrule"/>')
o.append(f'<text x="{C[4][0]}" y="55" class="colsub">/api → :8030</text>')
for id,t,s,y0,y1 in LANES:
    o.append(f'<rect x="20" y="{y0}" width="{W-40}" height="{y1-y0}" rx="6" class="lane lane-{id}"/>')
    o.append(f'<rect x="20" y="{y0}" width="4" height="{y1-y0}" class="lanebar lanebar-{id}"/>')
    o.append(f'<text x="40" y="{y0+24}" class="lanet lanet-{id}">{E(t)}<tspan class="lanes" dx="12">{E(s)}</tspan></text>')
def anchor(n, side, xo=None):
    if side=="r": return (n["x"]+n["w"], n["y"]+n["h"]/2)
    if side=="l": return (n["x"], n["y"]+n["h"]/2)
    if side=="b": return (xo if xo is not None else n["x"]+n["w"]/2, n["y"]+n["h"])
    if side=="t": return (xo if xo is not None else n["x"]+n["w"]/2, n["y"])
for e in EDG:
    a,b = N[e["a"]],N[e["b"]]
    if e["pts"]: pts = e["pts"]
    elif e["how"]=="v":
        p,q = anchor(a,"b",e["ax"]), anchor(b,"t",e["bx"]); pts=[p,q]
    else:
        p,q = anchor(a,"r"), anchor(b,"l")
        mx = (p[0]+q[0])/2 if abs(p[1]-q[1])>1 else None
        # elbow just before destination column for fan-outs
        mx = q[0]-22 if mx is not None else None
        pts = [p,(mx,p[1]),(mx,q[1]),q] if mx else [p,q]
    d = "M"+" L".join(f"{x:.0f},{y:.0f}" for x,y in pts)
    cls = {"live":"e","partial":"e e-part","placeholder":"e e-ph","planned":"e e-plan"}[e["st"]]
    mk = (' marker-end="url(#arp)"' if e["st"]=="planned" else ' marker-end="url(#ar)"') if e["arrow"] else ""
    if not e["arrow"]: cls += " e-user"
    o.append(f'<path d="{d}" class="{cls}"{mk}/>')
    if e["label"]:
        x0,y0 = pts[0]; x1,y1 = pts[1]
        if e["how"]=="v": lx,ly,anc = x0+6,(y0+y1)/2+4,"start"
        else:
            lx,ly,anc = pts[-1][0]-26, pts[-1][1]-5, "end"
        o.append(f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="{anc}" class="elab">{E(e["label"])}</text>')
BADGE = {"live":"LIVE","partial":"PARTIAL","placeholder":"STAND-IN","planned":"PLANNED"}
for id,n in N.items():
    k = n["kind"]; st = n["st"]
    o.append(f'<g class="n n-{k} st-{st} ln-{n["lane"]}">')
    rx = 22 if k=="user" else 4
    o.append(f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" rx="{rx}" class="nb"/>')
    o.append(f'<text x="{n["x"]+10}" y="{n["y"]+20}" class="nt">{E(n["title"])}</text>')
    for i,s in enumerate(n["subs"]):
        o.append(f'<text x="{n["x"]+10}" y="{n["y"]+38+15*i}" class="{"nsm" if n["mono"] else "ns"}" xml:space="preserve">{E(s)}</text>')
    if k in ("box","platform") and n["w"]>=200 and st!="live":
        o.append(f'<text x="{n["x"]+n["w"]-8}" y="{n["y"]+19}" text-anchor="end" class="badge b-{st}">{BADGE[st]}</text>')
    o.append('</g>')
o.append('</svg>')
svg = "\n".join(o)
tpl = open(sys.argv[1]).read()
open(sys.argv[2],"w").write(tpl.replace("<!--SVG-->", svg))
