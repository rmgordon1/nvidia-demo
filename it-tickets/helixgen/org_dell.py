"""Dell profile: the same BEP agency, sites and people, with an all-Dell estate.

Client (Dell CSG): Latitude / OptiPlex / Precision bought before mid-2025, Dell Pro / Dell Pro Max after
(mixed fleet), Dell monitors and docks. Infrastructure (Dell ISG): PowerEdge, PowerStore, PowerScale,
PowerVault, PowerProtect, PowerSwitch, VxRail, PowerEdge XR + NativeEdge at the plants. Hardware Dell
does not make (printers, phones, Wi-Fi APs, firewalls, PLCs, scanners) keeps its original vendors.
Every Dell asset carries a service tag (CI.serial).
"""
import hashlib
from datetime import date

from .org import LINES, Org

# support organization -> {group: (members, calendar, home site or None, role)}
SUPPORT_DELL = {
    "IT Service Management": {
        "Service Desk": (11, "sd", None, "Help Desk"),
    },
    "Client Services": {
        "Desktop Support - HQ": (5, "bus", "HQ", "Tier 2"),
        "Desktop Support - CPF": (5, "bus", "CPF", "Tier 2"),
        "Desktop Support - SPF": (4, "bus", "SPF", "Tier 2"),
        "Endpoint Engineering": (4, "bus", "HQ", "Tier 3"),
        "Telecom & Mobility": (3, "bus", "HQ", "Tier 2"),
    },
    "Infrastructure Services": {
        "Compute": (4, "24x7", "HQ", "Tier 3"),
        "Storage": (3, "bus", "HQ", "Tier 3"),
        "Data Protection": (3, "bus", "HQ", "Tier 3"),
        "Network Operations": (5, "24x7", "HQ", "Tier 3"),
        "Virtualization & HCI": (3, "bus", "HQ", "Tier 3"),
        "Database Administration": (2, "bus", "HQ", "Tier 3"),
    },
    "Cybersecurity & Identity": {
        "Identity & Access Management": (4, "bus", "HQ", "Tier 2"),
        "Cybersecurity Operations": (4, "24x7", "HQ", "Tier 3"),
    },
    "Enterprise Applications": {
        "Messaging & Collaboration": (3, "bus", "HQ", "Tier 3"),
        "ERP & Financial Systems": (3, "bus", "HQ", "Tier 3"),
        "HR & Time Systems": (2, "bus", "HQ", "Tier 3"),
        "Business Applications": (2, "bus", "HQ", "Tier 3"),
    },
    "Production Technology": {
        "OT Support - CPF": (4, "24x7", "CPF", "Tier 2"),
        "OT Support - SPF": (4, "24x7", "SPF", "Tier 2"),
        "MES Support": (3, "24x7", "CPF", "Tier 3"),
        "Edge Infrastructure": (2, "24x7", "CPF", "Tier 3"),
    },
}

TAG_CHARS = "0123456789BCDFGHJKLMNPQRSTVWXYZ"
REBRAND = date(2025, 6, 1)  # purchases after this get the 2025 Dell Pro / Pro Max names


def service_tag(*parts):
    h = int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest(), 16)
    out = ""
    for _ in range(7):
        h, r = divmod(h, len(TAG_CHARS))
        out += TAG_CHARS[r]
    return out


class DellOrg(Org):
    SUPPORT = SUPPORT_DELL

    def _ci(self, cls, name, desc, cat, typ, item, model, mfr, site_key, owner="", tags=(), status=3, purchased=None):
        c = super()._ci(cls, name, desc, cat, typ, item, model, mfr, site_key, owner=owner, tags=tags, status=status)
        if mfr == "Dell" and cls != "BMC_Application":
            c.serial = service_tag(name, model)
        c.purchased = purchased
        return c

    def _purchase(self):
        """Fleet purchase date: spread over 2021-2026, weighted to recent years."""
        y = int(self.rng.choice([2021, 2022, 2023, 2024, 2025, 2026], p=[.08, .14, .2, .24, .22, .12]))
        m = int(self.rng.integers(1, 13)) if y < 2026 else int(self.rng.integers(1, 10))
        return date(y, m, 1)

    def _laptop(self, purchased, persona_title):
        heavy = any(w in persona_title for w in ("Engineer", "Designer", "Engraver", "Architect", "Chemist"))
        if purchased < REBRAND:
            if heavy:
                return self.rng.choice(["Precision 5690", "Precision 3591"]), "Mobile Workstation"
            return self.rng.choice(["Latitude 7450", "Latitude 5450", "Latitude 7440", "Latitude 5440", "Latitude 9450 2-in-1"]), "Laptop"
        if heavy:
            return self.rng.choice(["Dell Pro Max 16", "Dell Pro Max 14"]), "Mobile Workstation"
        return self.rng.choice(["Dell Pro 14 Plus", "Dell Pro 14", "Dell Pro 16 Plus", "Dell Pro 13 Premium"]), "Laptop"

    def _desktop(self, purchased):
        if purchased < REBRAND:
            return self.rng.choice(["OptiPlex 7020 Micro", "OptiPlex 7010 Tower", "OptiPlex 7020 SFF"]), "Desktop"
        return self.rng.choice(["Dell Pro Micro", "Dell Pro Slim", "Dell Pro Tower"]), "Desktop"

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
            ("Compute Platform", "Infrastructure"), ("Virtualization (VxRail)", "Infrastructure"),
            ("Backup & Recovery", "Infrastructure"), ("Database Services", "Infrastructure"),
            ("Financial Management (ERP)", "Business"), ("HR, Payroll & Time", "Business"),
            ("Intranet & Business Apps", "Business"), ("Security Operations", "Security"),
            ("Currency Production - CPF", "Production"), ("Currency Production - SPF", "Production"),
            ("Manufacturing Execution (MES)", "Production"), ("Quality Inspection", "Production"),
            ("Production Data & Historian", "Production"), ("OT Network", "Production"),
            ("Edge Computing", "Production"),
        ]
        for n, cat in services:
            svc[n] = self._ci("BMC_BusinessService", n, f"{n} business service", "Service", cat, "Business Service",
                              "", "", "HQ", owner="Office of the CIO", tags=("service", n))
        self.services = svc

        apps = [
            ("Exchange Online", "Email & Calendaring", "Microsoft"), ("Microsoft Teams", "Collaboration (Teams/SharePoint)", "Microsoft"),
            ("SharePoint Online", "Collaboration (Teams/SharePoint)", "Microsoft"), ("OneDrive for Business", "File & Storage Services", "Microsoft"),
            ("GlobalProtect VPN", "Remote Access (VPN)", "Palo Alto Networks"), ("ActivClient", "Identity & PIV Authentication", "HID Global"),
            ("Active Directory", "Identity & PIV Authentication", "Microsoft"), ("Ledgerline ERP", "Financial Management (ERP)", "Ledgerline Software"),
            ("TimeWeb", "HR, Payroll & Time", "TimeWeb Systems"), ("HR Connect Portal", "HR, Payroll & Time", "In-house"),
            ("BEP Intranet", "Intranet & Business Apps", "In-house"), ("Contract Writing System", "Intranet & Business Apps", "In-house"),
            ("CrowdStrike Falcon", "Security Operations", "CrowdStrike"), ("PlantTrack MES", "Manufacturing Execution (MES)", "PlantTrack Inc."),
            ("NoteScan Inspection", "Quality Inspection", "Opti-Verify Systems"), ("Process Historian", "Production Data & Historian", "AVEVA"),
            ("PaperCut Print Management", "Enterprise Printing", "PaperCut"),
            # Dell management software
            ("OpenManage Enterprise", "Compute Platform", "Dell"), ("CloudIQ", "Compute Platform", "Dell"),
            ("SupportAssist Enterprise", "Compute Platform", "Dell"), ("PowerProtect Data Manager", "Backup & Recovery", "Dell"),
            ("Dell Command | Update", "Endpoint Computing", "Dell"), ("SupportAssist for Business PCs", "Endpoint Computing", "Dell"),
            ("Dell Optimizer", "Endpoint Computing", "Dell"), ("Dell Display and Peripheral Manager", "Endpoint Computing", "Dell"),
            ("NativeEdge Orchestrator", "Edge Computing", "Dell"),
        ]
        self.apps = {}
        for name, s, mfr in apps:
            a = self._ci("BMC_Application", name, name, "Software", "Application", name.split()[0], name, mfr, "HQ",
                         tags=("app", name))
            self.apps[name] = a
            self._rel("BMC_Dependency", "APPLICATIONSERVICE", svc[s].recon_id, a.recon_id)

        # ---- compute: PowerEdge rack servers hosting the apps, plus VxRail clusters
        hosted = ["Exchange Online", "Active Directory", "Ledgerline ERP", "TimeWeb", "HR Connect Portal", "BEP Intranet",
                  "Contract Writing System", "PaperCut Print Management", "OpenManage Enterprise",
                  "SupportAssist Enterprise", "PowerProtect Data Manager", "GlobalProtect VPN"]
        k = 1
        for app in hosted:
            if app in ("Exchange Online",):
                continue
            for _ in range(int(r.integers(1, 3))):
                model = str(r.choice(["PowerEdge R660", "PowerEdge R760", "PowerEdge R7625", "PowerEdge R650"]))
                srv = self._ci("BMC_ComputerSystem", f"HQ-PE-{app.split()[0][:4].upper()}{k:02d}", f"{app} server",
                               "Hardware", "Processing Unit", "Server", model, "Dell", "HQ", tags=("server", app, "poweredge"))
                self._rel("BMC_HostedSystemComponents", "HOSTEDSYSTEMCOMPONENTS", srv.recon_id, self.apps[app].recon_id)
                self._rel("BMC_Dependency", "DEPENDENCY", svc["Compute Platform"].recon_id, srv.recon_id)
                k += 1
        for i in range(1, 7):
            host = "HQ" if i <= 4 else "CPF"
            kind = "DB" if i % 2 else "FS"
            srv = self._ci("BMC_ComputerSystem", f"{host}-PE-{kind}{i:02d}", "Database server" if kind == "DB" else "File server",
                           "Hardware", "Processing Unit", "Server", "PowerEdge R760", "Dell", host,
                           tags=("server", "db" if kind == "DB" else "fileserver", "poweredge"))
            self._rel("BMC_Dependency", "DEPENDENCY",
                      svc["Database Services" if kind == "DB" else "File & Storage Services"].recon_id, srv.recon_id)
        for site, n in (("HQ", 4), ("CPF", 3)):
            for i in range(1, n + 1):
                node = self._ci("BMC_ComputerSystem", f"{site}-VXR-N{i:02d}", f"VxRail cluster node ({site})", "Hardware",
                                "Processing Unit", "HCI Node", "VxRail VE-660", "Dell", site, tags=("vxrail",))
                self._rel("BMC_Dependency", "DEPENDENCY", svc["Virtualization (VxRail)"].recon_id, node.recon_id)

        # ---- storage + data protection
        for name, model, site, tag, desc in [
            ("HQ-PSTORE-01", "PowerStore 1200T", "HQ", "powerstore", "Primary block/file array"),
            ("CPF-PSTORE-01", "PowerStore 500T", "CPF", "powerstore", "Plant block/file array"),
            ("SPF-PVAULT-01", "PowerVault ME5024", "SPF", "powervault", "Plant SAN array"),
            ("HQ-PSCALE-01", "PowerScale F710 (4-node)", "HQ", "powerscale", "File shares / home drives cluster"),
            ("HQ-DD-01", "PowerProtect DD6900", "HQ", "datadomain", "Backup target"),
            ("CPF-DD-01", "PowerProtect DD3300", "CPF", "datadomain", "Plant backup target"),
        ]:
            c = self._ci("BMC_ComputerSystem", name, desc, "Hardware", "Storage", "Storage Array", model, "Dell", site,
                         tags=("storage", tag))
            target = "Backup & Recovery" if tag == "datadomain" else "File & Storage Services"
            self._rel("BMC_Dependency", "DEPENDENCY", svc[target].recon_id, c.recon_id)

        # ---- network: Dell PowerSwitch; Wi-Fi APs and firewalls stay non-Dell
        for sk, n_sw in (("HQ", 30), ("CPF", 42), ("SPF", 32)):
            netsvc = svc[f"Enterprise Network - {sk}"]
            for i in range(1, 3):
                c = self._ci("BMC_ComputerSystem", f"{sk}-PSW-CORE{i:02d}", "Core switch", "Hardware", "Network",
                             "Switch", "PowerSwitch S5248F-ON", "Dell", sk, tags=("netcore",))
                self._rel("BMC_Dependency", "DEPENDENCY", netsvc.recon_id, c.recon_id)
            for i in range(1, n_sw + 1):
                bldg = r.choice(list("ABCD"))
                c = self._ci("BMC_ComputerSystem", f"{sk}-PSW-{bldg}{r.integers(1, 5)}-{i:02d}", "Access switch",
                             "Hardware", "Network", "Switch", "PowerSwitch N3248TE-ON", "Dell", sk, tags=("switch",))
                self._rel("BMC_Dependency", "DEPENDENCY", netsvc.recon_id, c.recon_id)
            for i in range(1, {"HQ": 40, "CPF": 18, "SPF": 14}[sk] + 1):
                self._ci("BMC_ComputerSystem", f"{sk}-WAP-{i:03d}", "Wireless access point", "Hardware", "Network",
                         "Wireless Access Point", "CW9166", "Cisco", sk, tags=("wap",))
        for i in range(1, 3):
            c = self._ci("BMC_ComputerSystem", f"HQ-FW-VPN{i:02d}", "VPN gateway / firewall", "Hardware", "Network",
                         "Firewall", "PA-5410", "Palo Alto Networks", "HQ", tags=("vpn",))
            self._rel("BMC_Dependency", "DEPENDENCY", svc["Remote Access (VPN)"].recon_id, c.recon_id)

        # ---- printers / scanners (non-Dell)
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

        # ---- plant: Dell edge (PowerEdge XR, NativeEdge, OptiPlex XE HMIs); PLCs stay Rockwell
        prodsvc = {"CPF": svc["Currency Production - CPF"], "SPF": svc["Currency Production - SPF"]}
        self.lines = {"CPF": [], "SPF": []}
        for sk, lines in LINES.items():
            for i in range(1, 3):
                xr = self._ci("BMC_ComputerSystem", f"{sk}-XR-EDGE{i:02d}", "Plant edge server (MES / historian)",
                              "Hardware", "Processing Unit", "Edge Server", str(r.choice(["PowerEdge XR8000", "PowerEdge XR4000"])),
                              "Dell", sk, tags=("edge", "poweredge"))
                self._rel("BMC_Dependency", "DEPENDENCY", svc["Edge Computing"].recon_id, xr.recon_id)
                self._rel("BMC_HostedSystemComponents", "HOSTEDSYSTEMCOMPONENTS", xr.recon_id,
                          self.apps["PlantTrack MES" if i == 1 else "Process Historian"].recon_id)
            otsw = []
            for i in range(1, 7):
                c = self._ci("BMC_ComputerSystem", f"{sk}-OT-PSW{i:02d}", "OT network switch (production cell)",
                             "Hardware", "Network", "Industrial Switch", "PowerSwitch N2248X-ON", "Dell", sk,
                             tags=("otswitch",))
                self._rel("BMC_Dependency", "DEPENDENCY", svc["OT Network"].recon_id, c.recon_id)
                otsw.append(c)
            for j, (ln, kind) in enumerate(lines):
                code = {"offset": "OP", "intaglio": "IP", "overprint": "OV", "finishing": "FL"}[kind] + ln.split()[-1]
                line = self._ci("BMC_Equipment", f"{sk} {ln}", f"{ln} ({kind})", "Equipment", "Production Line",
                                kind.title(), "", "", sk, owner=f"Currency Manufacturing - {sk}", tags=("line", kind))
                self.lines[sk].append(line)
                self._rel("BMC_Dependency", "DEPENDENCY", prodsvc[sk].recon_id, line.recon_id)
                plc = self._ci("BMC_ComputerSystem", f"{sk}-{code}-PLC01", f"{ln} main PLC", "Hardware",
                               "Controller", "PLC", "ControlLogix 5580", "Rockwell Automation", sk,
                               tags=("plc", kind, line.name))
                hmi = self._ci("BMC_ComputerSystem", f"{sk}-{code}-HMI01", f"{ln} operator HMI PC", "Hardware",
                               "Processing Unit", "Industrial PC", "OptiPlex XE4", "Dell", sk, tags=("hmi", kind, line.name))
                for c in (plc, hmi):
                    self._rel("BMC_Component", "SYSTEMCOMPONENT", line.recon_id, c.recon_id)
                    self._rel("BMC_Dependency", "NETWORKCONNECTION", c.recon_id, otsw[j % len(otsw)].recon_id)
                if kind in ("intaglio", "overprint", "finishing"):
                    insp = self._ci("BMC_ComputerSystem", f"{sk}-{code}-INSP01", f"{ln} inline inspection PC",
                                    "Hardware", "Processing Unit", "Workstation", "Precision 3680 Tower", "Dell", sk,
                                    tags=("insp", kind, line.name))
                    self._rel("BMC_Component", "SYSTEMCOMPONENT", line.recon_id, insp.recon_id)
                    self._rel("BMC_Dependency", "DEPENDENCY", insp.recon_id, self.apps["NoteScan Inspection"].recon_id)
            for i in range(1, 21):
                self._ci("BMC_ComputerSystem", f"{sk}-MES-KSK{i:02d}", "Shop-floor MES kiosk", "Hardware",
                         "Processing Unit", "Kiosk", str(r.choice(["OptiPlex 7020 Micro", "Dell Pro Micro"])), "Dell", sk,
                         tags=("meskiosk",))
            for i in range(1, 9):
                self._ci("BMC_ComputerSystem", f"{sk}-RGD-{i:02d}", "Plant-floor rugged tablet", "Hardware",
                         "Processing Unit", "Rugged Tablet", "Latitude 7230 Rugged Extreme", "Dell", sk, tags=("rugged",))

        # ---- conference rooms: Dell room displays (codec stays Cisco)
        for sk, n in (("HQ", 18), ("CPF", 6), ("SPF", 5)):
            for i in range(1, n + 1):
                self._ci("BMC_ComputerSystem", f"{sk}-CONF-{r.integers(1, 6)}{i:02d}", "Conference room display",
                         "Hardware", "Peripheral", "Room Display", "Dell 75 4K Conference Room Monitor C7520QT", "Dell", sk,
                         tags=("confroom",))

        # ---- end-user devices, one per person (shared kiosks on plant floors), mixed generations
        for p in self.people:
            if p.production and p.title not in ("Production Supervisor", "Press Supervisor", "Quality Engineer",
                                                "Production Planner", "Industrial Engineer", "Maintenance Supervisor",
                                                "Production Controller", "Chemist") and r.random() < 0.75:
                continue
            bought = self._purchase()
            if r.random() < 0.8:
                model, item = self._laptop(bought, p.title)
                kind = "LT"
            else:
                model, item = self._desktop(bought)
                kind = "WS"
            hn = f"{p.site_key if p.site_key != 'REM' else 'HQ'}-{kind}-{r.integers(10000, 99999)}"
            c = self._ci("BMC_ComputerSystem", hn, item, "Hardware", "Processing Unit", item, str(model), "Dell", p.site_key,
                         owner=p.full, tags=("workstation",), purchased=bought)
            p.workstation, p.workstation_rid = c.name, c.recon_id
            self._rel("BMC_Dependency", "DEPENDENCY", svc["Endpoint Computing"].recon_id, c.recon_id)
