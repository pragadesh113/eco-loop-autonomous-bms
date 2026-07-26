# Submission Manifest

Last updated: 2026-07-26 19:32 IST

## Ready locally

- Source code for EnergyPlus simulation, FastMCP tools, deterministic safety,
  LangGraph roles, local Qwen provider, metrics, and Streamlit dashboard.
- Pinned baseline and controlled IDF models under `models/`.
- Locked Python dependency graph in `uv.lock`.
- Reproducible setup, baseline, controlled-run, dashboard, and test commands in
  `README.md`.
- Architecture and novelty report in `docs/architecture.md`.
- Three-minute recording script in `docs/demo-guide.md`.
- Compact accepted result under `artifacts/accepted-run/`.
- Rendered dashboard evidence at `artifacts/dashboard-accepted-run.png`.
- Interactive scenario-lab evidence at `artifacts/demo/06-live-scenario-lab.png`.
- Fresh real-EnergyPlus demonstration at
  `artifacts/demo/09-real-energyplus-live-demo.png`.
- Completed six-slide supplied-template presentation:
  `deliverables/EcoLoop_Building_Agents_Presentation.pptx`
  (`SHA-256 16DC7A1AC0755A9C221DC06E9E7357EDA95B82446BA7D50D527FCBE189C7D7D5`).
- Portal-ready presentation PDF:
  `deliverables/EcoLoop_Building_Agents_Presentation.pdf`
  (`SHA-256 C0102492F31E889CFA0A3CB8CC8AD9B71C3E967A0378B4077BAD7740A137F662`).
- 81.8-second, 1280x720 demonstration video:
  `deliverables/EcoLoop_Building_Agents_Demo.mp4`
  (`SHA-256 128624CFF3EA0AB316D464DC6205727F9BBBD04013A09AF955C42E314B720CF7`).
- Feature verification records under `evidence/`.
- Final local gate: 377 tests, 90.47% branch coverage, Ruff/Pyright/lock clean.

## Delivery-media verification

- Original template preserved at
  `V:\BMS_automation\ppt_template\IDEA_Presentation_Format.pptx`.
- Six-slide limit preserved after deleting the template instruction page.
- PPTX: zero overflow and zero template-fidelity issues; no unresolved prompt text.
- PDF: six pages, visually rendered and checked; no unresolved placeholders.
- Video: 81.8 seconds, 1280x720, 30 fps, valid MP4; live capture had zero browser
  console errors.
- Claims are evidence-bound: the accepted quantitative run uses the deterministic
  optimizer through the autonomous LangGraph/FastMCP/EnergyPlus loop; Qwen3 4B remains
  advisory and cannot bypass deterministic safety.

## Accepted metrics

- Baseline HVAC electricity: 40.330583833437416 kWh.
- Controlled HVAC electricity: 33.84084809588941 kWh.
- Savings: 6.489735737548003 kWh / 16.091350832782815%.
- Occupied PMV compliance: 76.0% baseline / 90.63636363636364% controlled.
- Emergency violations: 5 baseline / 5 controlled.
- Controlled run: 672 timesteps, 168/168 applied actions, zero severe errors.

## Approval-gated external deliverables

These are intentionally not claimed complete:

- Public GitHub repository URL: no remote is configured or pushed.
- Public dashboard deployment URL: not deployed.
- Demonstration video upload: local MP4 ready; not uploaded.
- Presentation upload: local PPTX and PDF ready; not uploaded.
- Portal PDF/ZIP upload: not performed.

Publishing, deployment, upload, and portal submission require an explicit user decision
and any necessary target/account access.
