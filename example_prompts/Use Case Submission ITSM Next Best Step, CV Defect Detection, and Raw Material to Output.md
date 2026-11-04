# Use Case Submission: ITSM Next Best Step, Computer-Vision Defect Detection, and Raw Material to Output

Mission Outcome Workshop Submission

## 1. Submitter Details

| Field | Value |
| :--- | :--- |
| Name | Robert Gordon |
| Role / Title | Systems Engineer |
| Organization / Team | Dell / IRS-Treasury |
| Email | Robert_gordon@federal.dell.com |
| Submission Date | October 13, 2026 |

## 2. Customer Context

* Customer / Agency Profile: A civilian federal agency responsible for currency production, from raw materials through finished printed product
* Mission Area: IT service operations supporting production and manufacturing (primary), production quality assurance (secondary), and manufacturing material accountability (secondary)
* Opportunity Stage: Existing footprint; expanding. The customer recently purchased PowerEdge XE servers and has said it may expand its AI infrastructure as needs are proven.
* Mission Owners / Adoption Decision-Makers:
  * Primary (ITSM): The IT service desk / operations manager overseeing the ITSM migration
  * Secondary 1 (computer-vision defect detection): The production quality-assurance / inspection manager responsible for print quality and reject rates
  * Secondary 2 (raw material to output): The manufacturing operations lead, working with the supply-chain / material-accountability lead who owns procurement and reconciliation

## 3. Mission Outcome

> We need to give IT support agents an AI-assisted "next best step" recommendation drawn from historical ticket resolutions, so that ticket resolution time does not regress during the move from the current ITSM platform to ServiceNow, measured by mean-time-to-resolve (MTTR) before and after the migration.

The primary use case is the Day-3 build. Two secondary use cases follow the same pattern: local AI running on-prem over the agency's own data.

* **Secondary 1: Computer-vision defect detection on printed sheets.** Production lines already have inspection cameras. The rules-based inspection rejects too many good sheets, which then need manual re-inspection or become avoidable spoilage. It also misses new or subtle defect types that its fixed rules don't cover. The customer wants AI-assisted anomaly detection that scans every printed sheet, flags and explains defects, cuts false rejects, and catches defects the current system lets through.
* **Secondary 2: Raw material to finished output.** The customer wants to understand how raw materials (substrate, inks, and other consumables) turn into finished product. The goals are to see yield and spoilage against expected usage standards and to forecast procurement needs from production plans and actual consumption.

This is a high-assurance production mission with strict material accountability. The cost of inaction:

* IT support productivity drops and historical resolution knowledge is lost during the platform migration.
* Good product is wasted through false rejects, and defects escape downstream.
* Material variance stays unexplained, which adds audit and accountability risk.

## 4. Users and Current Workflow

### Target users

* **ITSM:** A small, specialized IT support team of fewer than 20 agents and analysts
* **CV defect detection:** Line operators who run the presses and inspection cameras, a small QA inspection team per line, and quality engineers who tune the inspection rules
* **Raw material to output:** Production planners, inventory / material-control staff, and procurement and finance analysts (roughly 10–20 people; exact numbers TBD)

### Current workflow hypothesis

**ITSM**

1. A ticket is submitted.
2. An agent manually searches old tickets and knowledge-base entries for similar past issues.
3. Senior staff are consulted when the answer is not easily found.
4. The agent applies and documents the fix.

**CV defect detection**

1. Printed sheets pass the existing inspection cameras.
2. The rules-based system passes or rejects each sheet.
3. The QA team pulls rejected sheets and re-inspects them by eye.
4. Sheets are also reviewed manually when a problem is reported downstream.

**Raw material to output**

1. Analysts pull purchase orders, inventory movements, and production counts from separate systems.
2. They reconcile them in spreadsheets on a periodic cycle.
3. Variance is investigated after the fact.
4. Procurement is forecast mainly from historical averages.

### Friction points to validate during discovery

**ITSM**

* Resolution knowledge lives in senior staff's heads rather than anywhere searchable, so lookups are slow and inconsistent.
* The platform migration risks losing historical context unless it is captured and carried forward.

**CV defect detection**

* A high false-reject rate creates manual re-inspection work and spoils good sheets.
* Fixed rules and templates miss new or subtle defects, which are found later downstream.
* Flagged sheets come with a pass/fail result but no explanation, and defects are not classified, so trends and root causes are hard to see.

**Raw material to output**

* Data is split between procurement/ERP and production reporting.
* Manual, after-the-fact reconciliation means yield and spoilage problems show up late and are hard to trace to a material, line, or shift.
* Forecasts based on historical averages risk overstock or shortages of long-lead materials.

## 5. Data Landscape and Synthetic Stand-In Plan

No production data samples or real currency imagery are required for the workshop.

| Generalized Data Type | Format | Approximate Workshop Scale | Broad Production Sensitivity | Generalized Production Location | Workshop Stand-In |
| :--- | :--- | :--- | :--- | :--- | :--- |
| IT support tickets (category, description, resolution notes, closure codes), Primary | Structured fields and free text | Several thousand synthetic tickets (production: tens of thousands) | CUI (prod) | On-prem ITSM database, migrating to ServiceNow | Synthetic tickets that match the current ITSM schema, with realistic categories, resolution notes, and closure codes |
| Sheet inspection images / video, CV | High-resolution images or video; a continuous stream of every sheet in production | Pilot-scale image set of mostly "good" samples plus a small labeled defect set (production: est. millions of images per month) | CUI / highly controlled (prod) | Inspection camera systems on production lines (segmented OT network) | Hybrid: public industrial / print anomaly-detection datasets for training and validation, plus locally captured images and short videos of generic, non-currency printed test sheets with physically introduced defects, uploaded in place of the camera stream |
| Inspection results / reject logs (pass/reject, rule or defect code, line, timestamp), CV | Structured logs | One synthetic record per test image (production: one record per inspected sheet) | CUI (prod) | Existing inspection system / QA database | Synthetic rules-based baseline results for the same test images, used to compare false rejects and missed defects |
| Raw material purchase orders (PO number, vendor, material code, quantity, unit cost, order/delivery dates), Raw material | Structured ERP/procurement export; original POs may also exist as PDFs for audit | Pilot-scale synthetic PO set (production: est. thousands per year) | CUI (prod) | On-prem ERP / procurement system | Synthetic POs with generic material codes and vendors |
| Material inventory transactions (receipts, issues to production, returns, lot IDs), Raw material | Structured ERP export | Pilot-scale synthetic transactions (production: est. tens of thousands per year) | CUI (prod) | On-prem ERP / inventory system | Synthetic receipts and issues linked to the synthetic POs and lots |
| Bill of materials / standard usage rates, Raw material | Structured tables or spreadsheets | A small synthetic standards table (production: est. hundreds of standards) | CUI (prod) | ERP / production engineering | Synthetic expected-usage rates per unit of output |
| Production output and spoilage counts (per run / shift / line), Raw material | Structured records | Simulated runs covering a pilot period (production: est. thousands of records per month) | CUI (prod) | Production reporting / MES (TBD in discovery) | Synthetic output and spoilage counts with built-in variance scenarios: over-consumption, a bad material lot, and a high-spoilage shift |

Production volumes are estimates. To confirm in discovery:

* Camera resolution, frame rate, and stream access
* ERP export fields
* Whether production counts come from an MES or manual reports

In production, cameras would stream an image of every sheet. For the proof of concept, uploaded files will stand in for the stream. Every workshop record will preserve provenance and version information.

## 6. Operational Constraints and Environment

* Network posture: Hybrid. The enterprise IT network that hosts the ITSM tooling is connected (on-prem, moving to ServiceNow; hosting model TBD). The production floor, including the inspection cameras and line systems, sits on a segmented OT network isolated from the enterprise network.
* Data boundary: Models and data stay inside the agency boundary for all three use cases
* External services: External API calls are prohibited
* CV placement: CV inference must run on-prem at or near the line with no cloud dependency
* Production safety: CV must work alongside the existing inspection cameras as an assistive second opinion, without disrupting line operations, before any production cut-in
* Human oversight: A human makes the final decision on sheet disposition, applies ticket fixes, and acts on variance findings
* Mandated tools: Must integrate with ServiceNow once the migration completes. Raw-material analysis must consume ERP / procurement exports.
* Funding posture: Part of a larger program covering all three use cases. The customer may grow its AI infrastructure based on need.
* Implementation timeline: Within the next year for all three use cases. The ITSM chatbot is tied to the ServiceNow migration.
* Accreditation or impact-level target: Civilian federal agency, with production data treated as CUI. The specific ATO / impact level is TBD in discovery. New AI capabilities would be added within existing system boundaries.

## 7. What a Win Looks Like

### Primary success metrics

* **ITSM:** A measurable drop in MTTR as agents move onto ServiceNow, instead of the usual productivity dip during a platform migration
* **CV defect detection:** A higher defect detection rate (recall), a lower escape rate on a known defect set, and a lower false-reject rate, all measured against the existing camera system's baseline
* **Raw material to output:** The percentage of material variance (actual vs. standard usage) that is automatically explained and attributed to a material, lot, line, or shift. Material reconciliation time should drop from days to minutes.

### Day-3 proof-of-concept demo

**Primary: ITSM next best step**

1. An agent pastes a new ticket description into a chat interface.
2. A local LLM retrieves similar historical tickets from the synthetic ticket set.
3. The interface returns a ranked "next best step" recommendation, with the source tickets cited alongside.
4. The agent reviews and accepts or rejects the recommendation.
5. The team compares time-to-answer and answer quality with a manual search of the same tickets.

**Stretch, if time allows: CV defect detection**

1. Images or a short video of printed test sheets are uploaded to the GB10.
2. A local anomaly-detection model flags defective sheets and shows a heatmap of the defect region.
3. A local vision-language model describes and classifies each flagged defect in plain language (e.g., "ink void, lower-right quadrant").
4. A side-by-side scorecard compares false rejects and missed defects with a simple rules-based baseline.

**Raw material to output** is not demoed on Day 3. It is presented as the "what's next" phase.

### Evaluation measures

* ITSM: Time-to-answer, retrieval relevance, citation correctness, and agent acceptance of recommendations
* CV: Defect recall, escape rate, false-reject rate, defect-localization accuracy, VLM description accuracy, and images-per-second throughput on the GB10
* Raw material: Percentage of variance explained against the known-answer scenarios, reconciliation time, and forecast accuracy on the synthetic data
* All: Behavior under missing, malformed, or ambiguous inputs

The mission owners should see local AI running on customer-owned hardware: an evidence-backed recommendation for IT agents and, if time allows, explained defect flags for QA, with no external connectivity and no autonomous operational action.

## 8. Architecture Capability Considerations

The following are user-confirmed capability hypotheses for workshop validation, not architecture or product selections.

| Capability Area | Why It May Matter | User Hypothesis | Key Constraints | Open Questions | Workshop Validation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| User experience and workflow integration | IT agents, QA inspectors, and material analysts need different views | A lightweight chat UI for agents; a defect review view with heatmaps and descriptions for QA | Fewer than 20 ITSM users; human disposition required for CV | How do recommendations surface inside ServiceNow after cutover? | Observe representative users completing ticket lookups and defect review |
| Analytical model functions | Retrieval-augmented generation, anomaly detection, vision-language classification, and forecasting may reduce manual effort | A local LLM for tickets; a two-stage CV pipeline (anomaly detection, then a VLM on flagged sheets); variance attribution and forecasting for raw material | All inference local; results must be explainable and reviewable | What detection and false-reject thresholds are operationally acceptable? | Score each function against synthetic and labeled known-answer sets |
| Search and retrieval | Agents need relevant past resolutions with citations | Retrieval over synthetic tickets, designed to be re-pointed from the current ITSM platform to ServiceNow | No external API calls | Which retrieval approach performs best on ticket terminology? | Compare retrieval relevance, latency, and citation correctness |
| Computer vision pipeline | Every sheet must be inspected at line speed | Anomaly detection trained mostly on "good" samples catches new defect types; a VLM runs only on flagged sheets to explain and classify them | No real currency imagery in the workshop; must not disrupt line operations | What resolution and frame rate are needed, and what throughput can the GB10 sustain? | Measure detection quality and images-per-second on stand-in images and video |
| Data ingestion and preparation | Tickets, images, and ERP / production records need parsing, normalization, and provenance | File upload stands in for camera streams; ERP-style exports stand in for procurement and production data | Synthetic, public, or locally captured non-currency data only | What production refresh rates and interfaces apply? | Ingest stand-in data, detect malformed inputs, and verify provenance |
| Knowledge and state management | Recommendations, defect flags, and variance findings must stay traceable | Retain sources, versions, flags, and reviewer decisions | Data stays inside the agency boundary | What retention policy and authoritative sources apply? | Trace a recommendation and a defect flag from input to reviewer decision |
| Integration and interfaces | ServiceNow, inspection cameras, and ERP systems must exchange information | Retrieval layer re-pointable to ServiceNow; CV runs beside the existing cameras; raw material consumes ERP exports | No direct production write-back during the pilot | Which ServiceNow, camera, and ERP interfaces will be required? | Demonstrate controlled ingestion and output using stand-in interfaces |
| Security, identity, and governance | Production data is CUI and decisions need accountability | Role-based access, audit logging, and human approval | Hybrid network; segmented OT; on-prem only | What ATO / impact level and role mappings apply? | Verify access separation, audit records, and that prohibited actions are blocked |
| Deployment, compute, and network posture | CV must run at or near the line, and IT tooling runs on the enterprise network | GB10 for local proof of concept; scale-out to the existing PowerEdge XE footprint if proven | No cloud dependency; segmented OT network | Where would CV inference sit relative to the line and OT boundary? | Run all workloads locally with the network disconnected |
| Performance, scale, and resilience | Slow inference would not keep up with ticket volume or line speed | Interactive chat response; CV throughput sized to line speed | Production camera specifications unknown | What line speed and availability targets apply? | Benchmark chat latency and CV throughput on the GB10 |
| Evaluation, observability, and responsible use | Mission owners need evidence of quality and value | Measure MTTR proxy, citation correctness, defect recall, false rejects, and variance explained | Human decision required; source traceability mandatory | What thresholds justify progression beyond the proof of concept? | Use scored synthetic and labeled test sets and capture reviewer feedback |
| Lifecycle and operations | Models need retraining as defect types, materials, and ticket patterns change | Produce monitoring, retraining, and rollback recommendations | Operational ownership TBD | Who owns models, data, and ongoing support for each use case? | Produce an operational-readiness checklist |

### Areas considered but not needed in the initial pilot

* Autonomous sheet disposition or line control
* Replacing the existing inspection camera system
* Direct write-back to ServiceNow, ERP, or production systems
* External API or cloud dependencies
* Use of real currency imagery or production data

### Candidate technologies (not selected)

* A local LLM with retrieval (RAG) over ticket data, served through a lightweight chat UI
* PatchCore / PaDiM-style unsupervised anomaly detection for defect flagging and localization
* A local vision-language model for defect classification and description
* Dell Pro Max GB10 for the proof of concept, and the customer's existing PowerEdge XE servers as a potential scale-out target

These remain hypotheses for evidence-based comparison during Days 1–2.

### Architecture decisions deferred

* Final model selection for the LLM, anomaly detection, and VLM
* Retrieval and indexing design
* ServiceNow, camera, and ERP connector implementation
* CV inference placement relative to the line and the OT network boundary
* Identity integration and accreditation target
* Production write-back approach
* Throughput, availability, and capacity targets beyond the pilot
* Operational ownership and support model for each use case

## 9. Sanitization Attestation

* [x] Confirmed: The submitter attests that this record contains no classified information, Controlled Unclassified Information (CUI), Personally Identifiable Information (PII), sensitive system or location names, real operational designations, or protected program identifiers.
* Attested by: Robert Gordon
* Attestation date: October 13, 2026

This submission intentionally uses generalized language. It contains no real system names beyond commercial products, no facility or line identifiers, no currency imagery, no credentials, no vulnerabilities, and no production data.
