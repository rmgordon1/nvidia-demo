"""Fictional BEP-like agency: sites, departments, support groups, people and CIs.

The Bureau of Engraving and Printing (BEP) prints paper currency only. All names,
hosts and facility details are invented.
"""
import hashlib
from dataclasses import dataclass, field

from faker import Faker

COMPANY = "Bureau of Engraving and Printing"
EMAIL_DOMAIN = "bep.example.gov"

SITES = {
    "HQ": dict(site="BEP Headquarters", city="Washington, DC", region="East",
               tz="America/New_York", headcount=520),
    "CPF": dict(site="Capital Production Facility", city="Washington, DC", region="East",
                tz="America/New_York", headcount=760),
    "SPF": dict(site="Southwest Production Facility", city="Fort Worth, TX", region="Southwest",
                tz="America/Chicago", headcount=560),
    "REM": dict(site="Telework - Remote", city="Remote", region="East",
                tz="America/New_York", headcount=60),
}

# department -> (organization, [job titles], is_production)
DEPARTMENTS = {
    "HQ": {
        "Office of the Director": ("Executive Office", ["Director", "Deputy Director", "Chief of Staff", "Executive Assistant", "Senior Advisor"], False),
        "Chief Financial Office": ("Office of the CFO", ["Accountant", "Budget Analyst", "Financial Analyst", "Supervisory Accountant", "Accounting Technician"], False),
        "Human Capital": ("Office of Human Capital", ["HR Specialist", "Benefits Specialist", "Payroll Technician", "Training Coordinator", "Recruiter"], False),
        "Acquisition": ("Office of Acquisition", ["Contract Specialist", "Contracting Officer", "Procurement Analyst", "Purchasing Agent"], False),
        "Office of Chief Counsel": ("Office of Chief Counsel", ["Attorney-Advisor", "Paralegal Specialist", "Legal Assistant"], False),
        "Office of the CIO": ("Office of the CIO", ["IT Specialist", "Project Manager", "Enterprise Architect", "Business Analyst", "IT Program Manager"], False),
        "Product Development": ("Office of Product Development", ["Currency Designer", "Engraver", "Product Engineer", "Program Analyst"], False),
        "Quality & Compliance": ("Office of Quality", ["Quality Assurance Specialist", "Compliance Analyst", "Auditor"], False),
        "Public Affairs": ("Office of External Relations", ["Public Affairs Specialist", "Writer-Editor", "Visitor Center Coordinator"], False),
        "Office of Security": ("Office of Security", ["Security Specialist", "Personnel Security Specialist", "Badging Clerk"], False),
    },
    "CPF": {
        "Offset Printing": ("Currency Manufacturing - CPF", ["Press Operator", "Press Supervisor", "Plate Printer", "Production Technician"], True),
        "Intaglio Printing": ("Currency Manufacturing - CPF", ["Plate Printer", "Press Supervisor", "Plate Printer Helper", "Production Technician"], True),
        "Finishing & Packaging": ("Currency Manufacturing - CPF", ["Finishing Operator", "Packaging Technician", "Production Supervisor", "Material Handler"], True),
        "Plate Making": ("Currency Manufacturing - CPF", ["Plate Maker", "Engraving Technician", "Prepress Specialist"], True),
        "Production Control": ("Currency Manufacturing - CPF", ["Production Controller", "Production Planner", "Accountability Clerk"], True),
        "Quality Inspection": ("Currency Manufacturing - CPF", ["Quality Inspector", "Quality Engineer", "Inspection Technician"], True),
        "Maintenance & Engineering": ("Plant Engineering - CPF", ["Maintenance Mechanic", "Electrician", "Controls Technician", "Industrial Engineer", "Maintenance Supervisor"], True),
        "Ink & Materials": ("Plant Engineering - CPF", ["Ink Maker", "Chemist", "Materials Handler"], True),
        "Plant Administration": ("Plant Administration - CPF", ["Administrative Officer", "Program Assistant", "Safety Specialist", "Plant Manager"], False),
        "Warehouse & Logistics": ("Plant Administration - CPF", ["Warehouse Worker", "Logistics Specialist", "Inventory Specialist"], False),
    },
    "SPF": {
        "Offset Printing": ("Currency Manufacturing - SPF", ["Press Operator", "Press Supervisor", "Plate Printer", "Production Technician"], True),
        "Intaglio Printing": ("Currency Manufacturing - SPF", ["Plate Printer", "Press Supervisor", "Plate Printer Helper", "Production Technician"], True),
        "Finishing & Packaging": ("Currency Manufacturing - SPF", ["Finishing Operator", "Packaging Technician", "Production Supervisor", "Material Handler"], True),
        "Production Control": ("Currency Manufacturing - SPF", ["Production Controller", "Production Planner", "Accountability Clerk"], True),
        "Quality Inspection": ("Currency Manufacturing - SPF", ["Quality Inspector", "Quality Engineer", "Inspection Technician"], True),
        "Maintenance & Engineering": ("Plant Engineering - SPF", ["Maintenance Mechanic", "Electrician", "Controls Technician", "Industrial Engineer", "Maintenance Supervisor"], True),
        "Plant Administration": ("Plant Administration - SPF", ["Administrative Officer", "Program Assistant", "Safety Specialist", "Plant Manager", "Visitor Center Coordinator"], False),
        "Warehouse & Logistics": ("Plant Administration - SPF", ["Warehouse Worker", "Logistics Specialist", "Inventory Specialist"], False),
    },
    "REM": {
        "Chief Financial Office": ("Office of the CFO", ["Accountant", "Budget Analyst", "Financial Analyst"], False),
        "Office of the CIO": ("Office of the CIO", ["IT Specialist", "Business Analyst", "Project Manager"], False),
        "Acquisition": ("Office of Acquisition", ["Contract Specialist", "Procurement Analyst"], False),
        "Human Capital": ("Office of Human Capital", ["HR Specialist", "Recruiter"], False),
    },
}

# support organization -> {group: (members, calendar, home site or None, role)}
SUPPORT = {
    "IT Service Management": {
        "Service Desk": (11, "sd", None, "Help Desk"),
    },
    "End User Services": {
        "Desktop Support - HQ": (5, "bus", "HQ", "Tier 2"),
        "Desktop Support - CPF": (5, "bus", "CPF", "Tier 2"),
        "Desktop Support - SPF": (4, "bus", "SPF", "Tier 2"),
        "Telecom & Mobility": (3, "bus", "HQ", "Tier 2"),
    },
    "Infrastructure Services": {
        "Network Operations": (5, "24x7", "HQ", "Tier 3"),
        "Server & Storage": (4, "bus", "HQ", "Tier 3"),
        "Messaging & Collaboration": (3, "bus", "HQ", "Tier 3"),
        "Database Administration": (2, "bus", "HQ", "Tier 3"),
    },
    "Cybersecurity & Identity": {
        "Identity & Access Management": (4, "bus", "HQ", "Tier 2"),
        "Cybersecurity Operations": (4, "24x7", "HQ", "Tier 3"),
    },
    "Enterprise Applications": {
        "ERP & Financial Systems": (3, "bus", "HQ", "Tier 3"),
        "HR & Time Systems": (2, "bus", "HQ", "Tier 3"),
        "Business Applications": (2, "bus", "HQ", "Tier 3"),
    },
    "Production Technology": {
        "OT Support - CPF": (4, "24x7", "CPF", "Tier 2"),
        "OT Support - SPF": (4, "24x7", "SPF", "Tier 2"),
        "MES Support": (3, "24x7", "CPF", "Tier 3"),
        "Inspection Systems": (2, "bus", "CPF", "Tier 3"),
        "Press Controls Engineering": (2, "bus", "SPF", "Tier 3"),
    },
}
GROUP_ORG = {g: org for org, gs in SUPPORT.items() for g in gs}
GROUP_CAL = {g: v[1] for gs in SUPPORT.values() for g, v in gs.items()}

# Production lines per plant: (name, kind)
LINES = {
    "CPF": [("Offset Press %d" % i, "offset") for i in range(1, 5)]
           + [("Intaglio Press %d" % i, "intaglio") for i in range(1, 7)]
           + [("Overprint Line %d" % i, "overprint") for i in range(1, 4)]
           + [("Finishing Line %d" % i, "finishing") for i in range(1, 4)],
    "SPF": [("Offset Press %d" % i, "offset") for i in range(1, 4)]
           + [("Intaglio Press %d" % i, "intaglio") for i in range(1, 6)]
           + [("Overprint Line %d" % i, "overprint") for i in range(1, 3)]
           + [("Finishing Line %d" % i, "finishing") for i in range(1, 3)],
}


def _hexid(*parts, n=24):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:n].upper()


@dataclass
class Person:
    person_id: str
    first: str
    last: str
    login: str
    email: str
    phone: str
    site_key: str
    dept: str
    org: str
    title: str
    production: bool
    support: bool = False
    vip: bool = False
    groups: list = field(default_factory=list)
    weight: float = 1.0  # how often this person raises tickets
    persona: str = "average"
    workstation: str = None
    workstation_rid: str = None

    @property
    def full(self):
        return f"{self.first} {self.last}"


@dataclass
class CI:
    instance_id: str
    recon_id: str
    class_id: str
    name: str
    desc: str
    category: str
    ctype: str
    item: str
    model: str
    manufacturer: str
    site_key: str
    owner: str = ""
    status: int = 3
    tags: tuple = ()


PERSONAS = [  # (name, weight, description used by the LLM prompt + noise level)
    ("terse", 0.22, "very short, lowercase, little punctuation, a typo or two"),
    ("average", 0.33, "plain, a little informal, mostly correct spelling"),
    ("detailed", 0.15, "thorough, lists steps already tried, pastes exact error text"),
    ("frustrated", 0.12, "annoyed, mentions deadlines, some caps or exclamation marks"),
    ("polite", 0.12, "formal and polite, greeting and sign-off, slightly wordy"),
    ("nontechnical", 0.06, "vague, unsure of technical terms, describes symptoms loosely"),
]


class Org:
    def __init__(self, rng, seed):
        self.rng = rng
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.people = []
        self.by_site = {k: [] for k in SITES}
        self.groups = {}       # name -> dict(id, org, cal, site, role, members=[Person])
        self.cis = []
        self.ci_by_name = {}
        self.rels = []         # (class, name, src_rid, dst_rid, src_class, dst_class)
        self._logins = set()
        self._build_people()
        self._build_groups()
        self._build_cis()

    # ------------------------------------------------------------ people
    def _login(self, first, last):
        base = (first[0] + last).lower().replace("'", "").replace(" ", "")[:12]
        login, n = base, 1
        while login in self._logins:
            n += 1
            login = f"{base}{n}"
        self._logins.add(login)
        return login

    def _new_person(self, site_key, dept, org, title, production, support=False):
        f = self.fake
        first, last = f.first_name(), f.last_name()
        login = self._login(first, last)
        idx = len(self.people) + 1
        area = {"HQ": "202", "CPF": "202", "SPF": "817", "REM": "202"}[site_key]
        p = Person(
            person_id=f"PPL{idx:012d}", first=first, last=last, login=login,
            email=f"{first}.{last}".lower().replace("'", "").replace(" ", "") + f"{'' if login[-1].isalpha() else login[-1]}@{EMAIL_DOMAIN}",
            phone=f"({area}) 555-{self.rng.integers(1000, 9999)}",
            site_key=site_key, dept=dept, org=org, title=title,
            production=production, support=support)
        names = [x[0] for x in PERSONAS]
        w = [x[1] for x in PERSONAS]
        p.persona = str(self.rng.choice(names, p=w))
        # heavy-tailed ticket frequency ("frequent flyers")
        p.weight = float(self.rng.pareto(2.2) + 0.3)
        self.people.append(p)
        self.by_site[site_key].append(p)
        return p

    def _build_people(self):
        for sk, s in SITES.items():
            depts = DEPARTMENTS[sk]
            names = list(depts)
            # production departments are bigger at plants
            w = [3.0 if depts[d][2] else 1.0 for d in names]
            w = [x / sum(w) for x in w]
            for _ in range(s["headcount"]):
                d = str(self.rng.choice(names, p=w))
                org, titles, prod = depts[d]
                t = str(self.rng.choice(titles))
                p = self._new_person(sk, d, org, t, prod)
                if t in ("Director", "Deputy Director", "Chief of Staff", "Plant Manager"):
                    p.vip = True
                    p.weight *= 1.5

    def _build_groups(self):
        gidx = 1
        for org, groups in SUPPORT.items():
            for g, (n, cal, home, role) in groups.items():
                gid = f"SGP{gidx:012d}"
                gidx += 1
                members = []
                for i in range(n):
                    if home is None:  # service desk split across sites
                        sk = ["HQ", "HQ", "HQ", "CPF", "SPF", "REM"][i % 6]
                    else:
                        sk = home
                    t = {"Help Desk": "IT Specialist (Customer Support)", "Tier 2": "IT Specialist",
                         "Tier 3": "IT Specialist (Systems)"}[role]
                    if org == "Production Technology":
                        t = str(self.rng.choice(["Controls Technician", "OT Systems Engineer", "IT Specialist (OT)"]))
                    p = self._new_person(sk, "Office of the CIO" if sk != "CPF" and sk != "SPF" else "Maintenance & Engineering",
                                         "Office of the CIO", t, False, support=True)
                    p.groups.append(g)
                    p.weight *= 0.3
                    members.append(p)
                self.groups[g] = dict(id=gid, org=org, cal=cal, site=home, role=role, members=members)
        # a few people belong to two groups (cross-trained)
        sd = self.groups["Service Desk"]["members"]
        for g in ("Desktop Support - HQ", "Identity & Access Management"):
            p = sd[int(self.rng.integers(len(sd)))]
            p.groups.append(g)
            self.groups[g]["members"].append(p)

    # ------------------------------------------------------------ CIs
    def _ci(self, cls, name, desc, cat, typ, item, model, mfr, site_key, owner="", tags=(), status=3):
        ci = CI(instance_id="OI-" + _hexid(cls, name, n=32),
                recon_id="RE" + _hexid("re", cls, name, n=30),
                class_id=cls, name=name, desc=desc, category=cat, ctype=typ, item=item,
                model=model, manufacturer=mfr, site_key=site_key, owner=owner, tags=tuple(tags),
                status=status)
        self.cis.append(ci)
        self.ci_by_name[name] = ci
        return ci

    def _rel(self, cls, name, src, dst):
        self.rels.append((cls, name, src, dst))

    def find(self, tag, site_key=None):
        out = [c for c in self.cis if tag in c.tags and (site_key is None or c.site_key == site_key)]
        return out

    def _build_cis(self):
        r = self.rng
        svc = {}
        services = [
            ("Email & Calendaring", "Messaging"), ("Collaboration (Teams/SharePoint)", "Messaging"),
            ("Remote Access (VPN)", "Network"), ("Identity & PIV Authentication", "Identity"),
            ("Enterprise Network - HQ", "Network"), ("Enterprise Network - CPF", "Network"),
            ("Enterprise Network - SPF", "Network"), ("Wireless Network", "Network"),
            ("Endpoint Computing", "Endpoint"), ("Enterprise Printing", "Endpoint"),
            ("Telephony & Mobile", "Telecom"), ("File & Storage Services", "Infrastructure"),
            ("Backup & Recovery", "Infrastructure"), ("Database Services", "Infrastructure"),
            ("Financial Management (ERP)", "Business"), ("HR, Payroll & Time", "Business"),
            ("Intranet & Business Apps", "Business"), ("Security Operations", "Security"),
            ("Currency Production - CPF", "Production"), ("Currency Production - SPF", "Production"),
            ("Manufacturing Execution (MES)", "Production"), ("Quality Inspection", "Production"),
            ("Production Data & Historian", "Production"), ("OT Network", "Production"),
        ]
        for n, cat in services:
            svc[n] = self._ci("BMC_BusinessService", n, f"{n} business service", "Service", cat, "Business Service",
                              "", "", "HQ", owner="Office of the CIO", tags=("service", n))
        self.services = svc

        apps = [  # name, service, manufacturer, hosts at
            ("Exchange Online", "Email & Calendaring", "Microsoft", None),
            ("Microsoft Teams", "Collaboration (Teams/SharePoint)", "Microsoft", None),
            ("SharePoint Online", "Collaboration (Teams/SharePoint)", "Microsoft", None),
            ("OneDrive for Business", "File & Storage Services", "Microsoft", None),
            ("GlobalProtect VPN", "Remote Access (VPN)", "Palo Alto Networks", "HQ"),
            ("ActivClient", "Identity & PIV Authentication", "HID Global", None),
            ("Active Directory", "Identity & PIV Authentication", "Microsoft", "HQ"),
            ("Ledgerline ERP", "Financial Management (ERP)", "Ledgerline Software", "HQ"),
            ("TimeWeb", "HR, Payroll & Time", "TimeWeb Systems", "HQ"),
            ("HR Connect Portal", "HR, Payroll & Time", "In-house", "HQ"),
            ("BEP Intranet", "Intranet & Business Apps", "In-house", "HQ"),
            ("Contract Writing System", "Intranet & Business Apps", "In-house", "HQ"),
            ("Records Management (RMS)", "Intranet & Business Apps", "In-house", "HQ"),
            ("CrowdStrike Falcon", "Security Operations", "CrowdStrike", None),
            ("PlantTrack MES", "Manufacturing Execution (MES)", "PlantTrack Inc.", "CPF"),
            ("NoteScan Inspection", "Quality Inspection", "Opti-Verify Systems", "CPF"),
            ("Process Historian", "Production Data & Historian", "AVEVA", "CPF"),
            ("PaperCut Print Management", "Enterprise Printing", "PaperCut", "HQ"),
            ("Veeam Backup", "Backup & Recovery", "Veeam", "HQ"),
            ("Cisco Webex Calling", "Telephony & Mobile", "Cisco", None),
        ]
        self.apps = {}
        sidx = 1
        for name, s, mfr, host in apps:
            a = self._ci("BMC_Application", name, name, "Software", "Application", name.split()[0],
                         name, mfr, host or "HQ", tags=("app", name))
            self.apps[name] = a
            self._rel("BMC_Dependency", "APPLICATIONSERVICE", svc[s].recon_id, a.recon_id)
            if host:
                for k in range(int(r.integers(1, 4))):
                    hn = f"{host}-SRV-{name.split()[0][:4].upper()}{sidx:02d}"
                    sidx += 1
                    srv = self._ci("BMC_ComputerSystem", hn, f"{name} server", "Hardware", "Processing Unit",
                                   "Server", "PowerEdge R760", "Dell", host, tags=("server", name))
                    self._rel("BMC_HostedSystemComponents", "HOSTEDSYSTEMCOMPONENTS", srv.recon_id, a.recon_id)
        for k in range(1, 9):  # database / file servers
            for host in ("HQ", "CPF", "SPF"):
                if k > 4 and host != "HQ":
                    continue
                kind = "DB" if k % 2 else "FS"
                hn = f"{host}-SRV-{kind}{k:02d}"
                srv = self._ci("BMC_ComputerSystem", hn, "Database server" if kind == "DB" else "File server",
                               "Hardware", "Processing Unit", "Server", "PowerEdge R660", "Dell", host,
                               tags=("server", "db" if kind == "DB" else "fileserver"))
                self._rel("BMC_Dependency", "DEPENDENCY",
                          svc["Database Services" if kind == "DB" else "File & Storage Services"].recon_id, srv.recon_id)

        # network gear
        for sk, n_sw in (("HQ", 30), ("CPF", 42), ("SPF", 32)):
            netsvc = svc[f"Enterprise Network - {sk}"]
            for i in range(1, 3):
                c = self._ci("BMC_ComputerSystem", f"{sk}-NET-CORE{i:02d}", "Core switch", "Hardware", "Network",
                             "Switch", "Catalyst 9500", "Cisco", sk, tags=("netcore",))
                self._rel("BMC_Dependency", "DEPENDENCY", netsvc.recon_id, c.recon_id)
            for i in range(1, n_sw + 1):
                bldg = r.choice(list("ABCD"))
                c = self._ci("BMC_ComputerSystem", f"{sk}-NET-SW-{bldg}{r.integers(1, 5)}-{i:02d}", "Access switch",
                             "Hardware", "Network", "Switch", "Catalyst 9300", "Cisco", sk, tags=("switch",))
                self._rel("BMC_Dependency", "DEPENDENCY", netsvc.recon_id, c.recon_id)
            for i in range(1, {"HQ": 40, "CPF": 18, "SPF": 14}[sk] + 1):
                self._ci("BMC_ComputerSystem", f"{sk}-WAP-{i:03d}", "Wireless access point", "Hardware", "Network",
                         "Wireless Access Point", "CW9166", "Cisco", sk, tags=("wap",))
        for i in range(1, 3):
            c = self._ci("BMC_ComputerSystem", f"HQ-NET-VPN{i:02d}", "VPN gateway", "Hardware", "Network",
                         "Firewall", "PA-5410", "Palo Alto Networks", "HQ", tags=("vpn",))
            self._rel("BMC_Dependency", "DEPENDENCY", svc["Remote Access (VPN)"].recon_id, c.recon_id)

        # printers
        for sk, n in (("HQ", 45), ("CPF", 40), ("SPF", 30)):
            for i in range(1, n + 1):
                fl = r.integers(1, 6)
                model, mfr = [("imageRUNNER ADVANCE C5840i", "Canon"), ("LaserJet Enterprise M611", "HP"),
                              ("VersaLink C7130", "Xerox")][i % 3]
                c = self._ci("BMC_ComputerSystem", f"{sk}-PRN-{fl}{i:02d}", f"Printer floor {fl}", "Hardware",
                             "Peripheral", "Printer", model, mfr, sk, tags=("printer",))
                self._rel("BMC_Dependency", "DEPENDENCY", svc["Enterprise Printing"].recon_id, c.recon_id)
        for sk in ("CPF", "SPF"):
            for i in range(1, 9):
                self._ci("BMC_ComputerSystem", f"{sk}-PRN-LBL{i:02d}", "Pallet/skid label printer", "Hardware",
                         "Peripheral", "Label Printer", "ZT610", "Zebra", sk, tags=("labelprinter",))
            for i in range(1, 25):
                self._ci("BMC_ComputerSystem", f"{sk}-SCN-{i:03d}", "Handheld barcode scanner", "Hardware",
                         "Peripheral", "Barcode Scanner", "TC58", "Zebra", sk, tags=("scanner",))

        # OT: production lines with PLC/HMI/inspection
        prodsvc = {"CPF": svc["Currency Production - CPF"], "SPF": svc["Currency Production - SPF"]}
        self.lines = {"CPF": [], "SPF": []}
        for sk, lines in LINES.items():
            otsw = []
            for i in range(1, 7):
                c = self._ci("BMC_ComputerSystem", f"{sk}-OT-SW{i:02d}", "OT network switch (production cell)",
                             "Hardware", "Network", "Industrial Switch", "Stratix 5700", "Rockwell Automation", sk,
                             tags=("otswitch",))
                self._rel("BMC_Dependency", "DEPENDENCY", svc["OT Network"].recon_id, c.recon_id)
                otsw.append(c)
            for j, (ln, kind) in enumerate(lines):
                code = {"offset": "OP", "intaglio": "IP", "overprint": "OV", "finishing": "FL"}[kind] + ln.split()[-1]
                line = self._ci("BMC_Equipment", f"{sk} {ln}", f"{ln} ({kind})", "Equipment", "Production Line",
                                kind.title(), "", "", sk, owner=f"Currency Manufacturing - {sk}",
                                tags=("line", kind))
                self.lines[sk].append(line)
                self._rel("BMC_Dependency", "DEPENDENCY", prodsvc[sk].recon_id, line.recon_id)
                plc = self._ci("BMC_ComputerSystem", f"{sk}-{code}-PLC01", f"{ln} main PLC", "Hardware",
                               "Controller", "PLC", "ControlLogix 5580", "Rockwell Automation", sk,
                               tags=("plc", kind, line.name))
                hmi = self._ci("BMC_ComputerSystem", f"{sk}-{code}-HMI01", f"{ln} operator HMI", "Hardware",
                               "Processing Unit", "HMI Panel", "PanelView 5510", "Rockwell Automation", sk,
                               tags=("hmi", kind, line.name))
                for c in (plc, hmi):
                    self._rel("BMC_Component", "SYSTEMCOMPONENT", line.recon_id, c.recon_id)
                    self._rel("BMC_Dependency", "NETWORKCONNECTION", c.recon_id, otsw[j % len(otsw)].recon_id)
                if kind in ("intaglio", "overprint", "finishing"):
                    insp = self._ci("BMC_ComputerSystem", f"{sk}-{code}-INSP01", f"{ln} inline inspection station",
                                    "Hardware", "Processing Unit", "Inspection System", "NoteScan 4", "Opti-Verify Systems",
                                    sk, tags=("insp", kind, line.name))
                    self._rel("BMC_Component", "SYSTEMCOMPONENT", line.recon_id, insp.recon_id)
                    self._rel("BMC_Dependency", "DEPENDENCY", insp.recon_id, self.apps["NoteScan Inspection"].recon_id)
                if kind == "offset":
                    ink = self._ci("BMC_ComputerSystem", f"{sk}-{code}-INK01", f"{ln} ink dispensing controller",
                                   "Hardware", "Controller", "Ink Dispenser", "InkMix 300", "Dosatec", sk,
                                   tags=("ink", kind, line.name))
                    self._rel("BMC_Component", "SYSTEMCOMPONENT", line.recon_id, ink.recon_id)
            for i in range(1, 7):
                ews = self._ci("BMC_ComputerSystem", f"{sk}-OT-EWS{i:02d}", "OT engineering workstation", "Hardware",
                               "Processing Unit", "Workstation", "Precision 3680", "Dell", sk, tags=("ews",))
            for i in range(1, 21):
                self._ci("BMC_ComputerSystem", f"{sk}-MES-KSK{i:02d}", "Shop-floor MES kiosk", "Hardware",
                         "Processing Unit", "Kiosk", "OptiPlex 7020", "Dell", sk, tags=("meskiosk",))
            hist = self._ci("BMC_ComputerSystem", f"{sk}-OT-HIST01", "Process historian collector", "Hardware",
                            "Processing Unit", "Server", "PowerEdge R660", "Dell", sk, tags=("historian",))
            self._rel("BMC_HostedSystemComponents", "HOSTEDSYSTEMCOMPONENTS", hist.recon_id,
                      self.apps["Process Historian"].recon_id)
        self._ci("BMC_ComputerSystem", "CPF-CTP-01", "Computer-to-plate imaging workstation", "Hardware",
                 "Processing Unit", "Workstation", "Precision 5860", "Dell", "CPF", tags=("ctp",))

        # conference rooms
        for sk, n in (("HQ", 18), ("CPF", 6), ("SPF", 5)):
            for i in range(1, n + 1):
                self._ci("BMC_ComputerSystem", f"{sk}-CONF-{r.integers(1, 6)}{i:02d}", "Conference room AV system",
                         "Hardware", "Peripheral", "Room System", "Room Kit Pro", "Cisco", sk, tags=("confroom",))

        # end-user workstations, one per person (plus shared ones on plant floors)
        for p in self.people:
            if p.production and p.title not in ("Production Supervisor", "Press Supervisor", "Quality Engineer",
                                                "Production Planner", "Industrial Engineer", "Maintenance Supervisor",
                                                "Production Controller", "Chemist") and r.random() < 0.75:
                continue  # floor staff use shared kiosks
            kind = "LT" if r.random() < 0.8 else "WS"
            hn = f"{p.site_key if p.site_key != 'REM' else 'HQ'}-{kind}-{r.integers(10000, 99999)}"
            model = "Latitude 7450" if kind == "LT" else "OptiPlex 7020"
            c = self._ci("BMC_ComputerSystem", hn, "Laptop" if kind == "LT" else "Desktop", "Hardware",
                         "Processing Unit", "Laptop" if kind == "LT" else "Desktop", model, "Dell", p.site_key,
                         owner=p.full, tags=("workstation",))
            p.workstation, p.workstation_rid = c.name, c.recon_id
            self._rel("BMC_Dependency", "DEPENDENCY", svc["Endpoint Computing"].recon_id, c.recon_id)
