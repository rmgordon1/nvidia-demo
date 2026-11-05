# BEP Operations AI — web UI

A static, dependency-free web console for the GB10 workshop's three use cases. It runs fully air-gapped: no CDNs, no external fonts or APIs, and no build step.

| Module | Route | Who sees it | What it does |
|---|---|---|---|
| Mission Overview | `#/overview` | everyone | Headline KPIs for all three use cases, MTTR by month with ServiceNow-cutover projection, guardrails, recent activity |
| ITSM Next Best Step (primary, Day-3) | `#/itsm` | IT Agent, Admin | Post-cutover: agents work ServiceNow incidents; the knowledge index searches live ServiceNow incidents plus the migrated BMC Helix archive. Ticket intake (paste / structured / replay a Helix ticket), ranked next-best steps with cited source tickets, accept / reject / edit feedback |
| Defect Inspection (stretch) | `#/vision` | QA Inspector, Admin | Simulated line stream, review queue, heatmap viewer, VLM-style description, human disposition, threshold tuning vs rules baseline, defect trends, image upload |
| Material Accountability (next phase) | `#/materials` | Material Analyst, Admin | Yield / spoilage vs standard, variance attribution with built-in known-answer scenarios, reconciliation ledger, procurement forecast, NL "ask the data", ERP-style record tables |
| Governance & Audit | `#/audit` | Admin | Every AI output + reviewer decision with provenance; filters; CSV/JSON export; model registry; policy controls |

The **Viewing as** dropdown in the header is a demo role switcher. It filters the navigation and does no authentication.

## Build and run

```bash
cd webapp
docker compose up -d --build        # → http://<gb10-host>:8080
# or, without compose:
docker build -t bep-ops-ai .
docker run -d --name bep-ops-ai -p 8080:80 bep-ops-ai
```

For quick local iteration without Docker, run `python3 -m http.server 8080` from `webapp/`.

## Data (all synthetic or public, baked into the image)

| File | Built by | Source |
|---|---|---|
| `data/tickets.json` | `tools/build_tickets.py [../it-tickets/out/<run>]` | Synthetic BMC Helix extract from `it-tickets/` (default `out/sample`). Contact fields are dropped. |
| `data/servicenow.json` | same run of `tools/build_tickets.py` | Cutover migration: the 62 incidents still open in Helix, copied to ServiceNow as INC0010001–INC0010062 with their work notes (`correlationId` = Helix number). Their Helix records are closed as "Transferred to ServiceNow", so Helix is history only. Feeds the ITSM open-incident queue and itsm-rag-server (`/itsm/queue`, `/selfservice/my-tickets`). Build from `out/full` to match the server: `python3 tools/build_tickets.py ../it-tickets/out/full`. |
| `data/cv/{sheets,thumbs,model}/`, `data/cv/manifest.json` | `tools/build_cv_samples.py` (needs numpy + Pillow; run `synthetic_print/generate.py` first) | 160 labeled banknote press sheets (96 good, 8 per defect class) from the test split of `synthetic_print/dataset`, rectified to a 1024×969 canonical frame, with precomputed stage-1 scores + 64×61 heatmaps, a rules-baseline verdict, ground-truth defect boxes, and the stage-1 template model (per design + side mean/std PNGs and per-cell norms) the browser uses to score uploads |
| materials data | generated in the browser by `js/data/materials.js` (seeded) | Synthetic POs, lots, inventory transactions, BOM standards and production runs with three known-answer variance scenarios |
| `data/materials/*.csv`, `answer_key.json` | `tools/build_materials.py [--seed N]` (stdlib only) | Realistic 12-month extract: Oracle EBS-style ERP tables + a separate MES run log that must be joined on `TRANSACTION_REFERENCE` = `RUN_ID`. 6 presses × 3 shifts, 7 denomination-like products, ~55 active materials, a mix of metered and backflushed materials, and 10 planted scenarios (root cause S1–S5, forecast F1–F2, data quality D1–D3) recorded in `answer_key.json`. Not yet loaded by the UI. |

When the full ticket run is ready:

```bash
/opt/workshop/venv/bin/python3 tools/build_tickets.py ../it-tickets/out/full
docker compose up -d --build
```

## Plugging in the real models

Every model call goes through `js/ai/`. Each file runs a deterministic in-browser stand-in today and has a `TODO` where the real call goes:

| File | Functions | Placeholder today | Replace with |
|---|---|---|---|
| `js/ai/itsm.js` | `retrieveSimilar`, `recommendNextSteps` | BM25 over ticket text + templated synthesis from cited resolutions | Local LLM + RAG trained on the `it-tickets` data |
| `js/ai/vision.js` | `scoreImage`, `describeDefect` | Registered golden template (sheet registration + per-pixel Gaussian of good sheets, PaDiM-style), template descriptions | PatchCore / PaDiM stage 1 + local VLM stage 2 |
| `js/ai/materials.js` | `attributeVariance`, `forecast`, `ask` | Statistical contribution analysis + keyword-routed answers | Variance attribution / forecasting models + local LLM |

To switch, set `MODE: "api"` in `js/ai/config.js`, implement the `callApi(...)` branches, and un-comment the `/api/` proxy in `nginx.conf` (plus the `model-server` service in `docker-compose.yml`). Model names and versions in `AI.models` feed the provenance stamp on every audit record.

## ServiceNow cutover model

The ITSM module assumes the ServiceNow cutover is complete (`CUTOVER` in `js/ai/itsm.js`, also used for the Overview MTTR marker):

- New incidents get ServiceNow numbers (`INC0010001`, …), and all fields are labeled with ServiceNow columns.
- The bundled ticket history is the **BMC Helix archive**, migrated read-only at cutover. Migrated records keep their Helix incident numbers, and the detail drawer shows each ServiceNow field with its source Helix column (`short_description ← Description`).
- **ServiceNow incidents resolved since cutover:** there's no live feed yet, so a new incident joins the index when the agent accepts its recommendation (stored in the browser; **Reset ServiceNow** clears them). Replace this with a ServiceNow export / API feed later.
- The agent can turn either source on or off, for example to show how little a ServiceNow-only index would know without the carried-forward history.

## Persistence

Audit events, reviewer decisions, the selected role, the CV threshold and the knowledge-source choice are kept in browser `localStorage` (`js/store.js`). Replace the `backend` object there with API calls to make them durable and shared.

## Layout

```
index.html          app shell (banner, sidebar, top bar)
css/base.css        design tokens + shared components (dark mission-control theme)
css/<view>.css      view-specific styles
js/main.js          role-filtered nav + hash router
js/ui.js            DOM helpers, icons, formatting, drawer/modal/toast
js/charts.js        SVG line / bar / horizontal-bar / sparkline charts with hover tooltips
js/store.js         localStorage, roles, audit trail
js/data/            loaders, CV metrics, synthetic materials generator
js/ai/              model hooks (see above)
js/views/           one module per screen
tools/              offline data builders (not copied into the image)
```

Chart colors use a fixed categorical order, validated for color-vision deficiency against the dark surface (`#111821`). Status colors (good / warning / serious / critical) are reserved for state and always appear with an icon and a label.
