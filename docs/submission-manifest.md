# Submission Manifest

Last updated: 2026-07-26 22:15 IST

## Ready locally

- Source code for EnergyPlus simulation, FastMCP tools, deterministic safety,
  LangGraph roles, local Qwen provider, metrics, and Streamlit dashboard.
- Pinned baseline and controlled IDF models under `models/`.
- Locked Python dependency graph in `uv.lock`.
- Reproducible setup, baseline, controlled-run, dashboard, and test commands in
  `README.md`.
- Architecture and novelty report in `docs/architecture.md`.
- Complete technical system reference in `docs/technical-document.md`.
- Three-minute recording script in `docs/demo-guide.md`.
- Compact accepted result under `artifacts/accepted-run/`.
- Rendered dashboard evidence at `artifacts/dashboard-accepted-run.png`.
- Interactive scenario-lab evidence at `artifacts/demo/06-live-scenario-lab.png`.
- Fresh real-EnergyPlus demonstration at
  `artifacts/demo/09-real-energyplus-live-demo.png`.
- Completed six-slide supplied-template presentation:
  `deliverables/EcoLoop_Building_Agents_Presentation.pptx`
  (`SHA-256 19E823B445E8CF5D67736B4FC94C01497AA89B8FEF80EF864DEAF4CC896F4542`).
- Portal-ready presentation PDF:
  `deliverables/EcoLoop_Building_Agents_Presentation.pdf`
  (`SHA-256 E5E6B4D5E63DF9205F58756C0C00DA360A4C1F32EC414BB4506DA59A7B878757`).
- 81.8-second, 1280x720 demonstration video:
  `deliverables/EcoLoop_Building_Agents_Demo.mp4`
  (`SHA-256 886889CE4CDAE916136F59C1AA459869F23C2C02CC1D6F693AC12DDA99EE0712`).
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

- Public GitHub repository URL: publication authorized to
  `https://github.com/gokulan21/eco-loop-building-agents-hcl`; commit `09fc8df` is
  prepared, but the push received HTTP 403 because cached account `pragadesh113` lacks
  write access. GitHub authentication or collaborator access is required.
- Public dashboard deployment URL: not deployed.
- Demonstration video upload: local MP4 ready; not uploaded.
- Presentation upload: local PPTX and PDF ready; not uploaded.
- Portal PDF/ZIP upload: not performed.

GitHub publication has explicit user authorization. Deployment, external media hosting,
and portal submission still require a separate explicit user decision and any necessary
target/account access.
