# Synthetic BMC Helix incident archive

Generates a realistic archive of BMC Helix ITSM **incidents**, as if pulled from the AR System database, for a
fictional agency modeled on the BEP: the **Bureau of Currency Production (BCP)**. BCP prints paper currency
only. It has a headquarters, the Capital Production Facility (Washington DC), the Southwest Production Facility
(Fort Worth TX), and teleworkers. All people, hosts and facility details are invented.

Intended use: AI/ML training and evaluation (classification, routing, summarization, duplicate detection).

## Pipeline

```bash
PY=/opt/workshop/venv/bin/python3
# Stage 1: simulate the structure, write CSVs with template text (seconds)
$PY generate.py --n 10000 --out out/full --llm-fraction 0.7
# Stage 2: rewrite text with local Ollama models, merge into the CSVs (resumable; hours)
$PY llm_pass.py --out out/full --concurrency 4
# Rebuild the CSVs from the LLM cache without calling the models
$PY llm_pass.py --out out/full --merge-only
```

Key options:

| Option | Default | Notes |
|---|---|---|
| `--n` | 10000 | number of incidents |
| `--seed` | 20261104 | the same seed produces identical stage-1 output |
| `--columns` | `view` | `view` = AR SQL view names (`Incident_Number`); `cid` = `C1000000161` style |
| `--llm-fraction` | 0.7 | share of tickets rewritten by an LLM; the rest keep template text (hybrid) |
| `--models` | `gpt-oss:20b=3,gemma4:26b=3,granite4.2:latest=2,nemotron-3.5-lightning:30b=1` | weighted rotation, one model per ticket |

Stage 2 caches each result in `out/<run>/_work/llm_cache.jsonl`. Stopping and re-running continues where it
left off. Jobs run one model at a time, so models aren't swapped in and out of memory.

### Fast path: variant pool (~45 min for 10k)

Rewriting every ticket costs about 2 s each on the GB10 (generation-bound, about 130 tok/s aggregate), which
is about 5.5 h for 10k. `llm_pool.py` makes about 680 LLM calls instead. Each call writes several phrasing
variants with `{placeholders}` for one (field × scenario × author/persona). Each ticket then draws a variant
round-robin, re-fills it with its own CI, error, line and name, and gets persona noise applied.

```bash
$PY generate.py --n 10000 --out out/full --llm-fraction 1.0     # jobs for every ticket are needed
OLLAMA_HOST=http://localhost:11435 $PY llm_pool.py --out out/full --concurrency 8
$PY llm_pool.py --out out/full --estimate --scale 1.5          # more variants = less reuse, more time
```

Each description variant is shared by a handful of tickets. `ground_truth.csv` → `text_variant` names the
variant, so you can **group train/test splits by `text_variant`** to avoid near-duplicate leakage.

Ollama on this box handles one request at a time by default (`OLLAMA_NUM_PARALLEL` unset). For parallel
decoding, run a second user-level instance:
`OLLAMA_MODELS=/opt/workshop/models/ollama OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=8 OLLAMA_MAX_LOADED_MODELS=1 ollama serve`

## Output (`out/<run>/`)

| File | Form | Contents |
|---|---|---|
| `HPD_Help_Desk.csv` | HPD:Help Desk | the incidents |
| `HPD_WorkLog.csv` | HPD:WorkLog | work notes, customer emails, vendor notes, MI status updates |
| `HPD_HelpDesk_AuditLogSystem.csv` | HPD:HelpDesk_AuditLogSystem | every status, assignment and priority change |
| `HPD_Associations.csv` | HPD:Associations | duplicate/related incidents, problems (PBI), changes (CRQ), CIs |
| `CTM_People.csv` | CTM:People | customers and support staff |
| `CTM_Support_Group.csv`, `CTM_Support_Group_Association.csv` | CTM:* | 19 support groups and their members |
| `BMC_BaseElement.csv`, `BMC_BaseRelationship.csv` | BMC.CORE:* | CIs (services, apps, servers, network, presses, PLCs, HMIs, inspection) and relationships |
| `ground_truth.csv` | (sidecar) | true categories and group, noise flags, MI/duplicate links, persona, text source |
| `data_dictionary.csv` | (sidecar) | column → field ID, label, enum integer values, confidence |

Formats follow a raw AR pull. Dates are epoch seconds (UTC). Selection fields hold integer codes (Status 0–6,
Impact 1000–4000, …). BMC quirks such as `Detailed_Decription` are kept. The data dictionary marks each field ID
and enum value as `documented` (widely published BMC IDs) or `approximate` (best effort). Check the approximate
ones against your own instance if exact IDs matter.

## What's simulated

- **Arrivals**: weekday and business-hours patterns, federal holidays, a slight upward trend, and the plants'
  24x7 shift pattern for OT. Spikes on Patch Tuesday, at fiscal year-end (September, ERP/contracts), on payroll
  deadlines (TimeWeb) and during open season.
- **Lifecycle**: Service Desk triage and first-contact resolution, then reassignment to Tier 2/3. Business-hours
  calendars per group, with on-call for High/Critical. Pending with real status reasons (client action, vendor,
  approval, change). Vendor cases, change requests, reopens, auto-close after 5 days, cancellations.
- **Archive cutoff**: the extract runs 2026-11-03 06:00 ET. About 98.8% of tickets are Closed or Cancelled, with
  an open tail from late October 2026 and a few stale tickets stuck in Pending.
- **Noise for ML**: messy user text (persona-driven typos, casing, vague summaries, email signatures, forwarded
  chains), miscategorization (~25%: blank / lazy / wrong / partial / missing product), reassignment ping-pong
  (~15% of tickets), random duplicates (~1.5%), and major-incident bursts (16 events with 4–70 related tickets each;
  ~65% of those tickets are formally linked to the parent).
- **Domains**: standard IT (identity/PIV, endpoint, M365, VPN/network, ERP/HR/time apps, security) plus
  production OT/SCADA (press HMIs/PLCs, inline inspection, PlantTrack MES, historian, OT network, label printers,
  scanners, ink dispensing, engineering-workstation licenses, vendor remote access).

## Code map

- `helixgen/arschema.py` – forms, columns, field IDs, enums, priority matrix
- `helixgen/org.py` – sites, departments, support groups, people, CMDB
- `helixgen/scenarios.py` – 55 incident scenarios plus 16 major-incident event templates (edit here to change the mix)
- `helixgen/simulate.py` – arrival, lifecycle, cutoff and categorization-noise simulation
- `helixgen/textgen.py` – template rendering and text noise
- `helixgen/export.py` – row building, CSV/ground-truth/LLM-job writers
- `llm_pass.py` – Ollama rewriting (structured JSON output), validation, merge
