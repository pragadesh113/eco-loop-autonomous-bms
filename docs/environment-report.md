# Environment Feasibility Evidence

Recorded: 2026-07-26 IST

## Runtime inventory

| Component | Version / model | Project-local location |
|---|---|---|
| Python | 3.12.1 | `.venv` |
| EnergyPlus | 26.1.0, build `6f2e40d102` | `.tools/energyplus/26.1.0/EnergyPlus-26.1.0-6f2e40d102-Windows-x86_64` |
| Ollama | 0.32.4 | `.tools/ollama/0.32.4/ollama.exe` |
| Qwen | `qwen3:4b-instruct`, 4.0B, Q4_K_M | `.cache/ollama-models` |
| Weather | New Delhi Safdarjung TMYx 2011–2025 | `weather/IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025.epw` |

All large runtimes, model blobs, downloaded archives, weather files, and validation
outputs are excluded from Git. At validation time drive `V:` had 12.90 GB free.

## Provenance

- EnergyPlus release:
  `https://github.com/NatLabRockies/EnergyPlus/releases/tag/v26.1.0`
- Ollama Windows standalone distribution:
  `https://github.com/ollama/ollama/blob/main/docs/windows.mdx`
- Ollama release: `https://github.com/ollama/ollama/releases/tag/v0.32.4`
- New Delhi weather:
  `https://climate.onebuilding.org/WMO_Region_2_Asia/IND_India/DL_Delhi/IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025.zip`

## Integrity identifiers

- EnergyPlus archive SHA-256:
  `0BB6932D277EED62F996B625F37C533B8C35F9AF0C53710D961D8442FC4E70B3`
- Ollama executable SHA-256:
  `9648169DFEF645752FF8B25FDED65D57E4B519FDA9B0C9710A938AF025CEC2A1`
- New Delhi EPW SHA-256:
  `8201E41AA7517016558C369053A06B000ED038647DCDD0681512C3775DDE486B`
- Qwen model digest:
  `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`

## Acceptance evidence

EnergyPlus ran the official `5ZoneAirCooled.idf` with the New Delhi EPW and completed in
4.46 seconds with zero severe errors. Output is preserved in
`.cache/validation/energyplus-5zone`.

Ollama returned JSON matching a required schema from `qwen3:4b-instruct`. The bounded
follow-up produced a 25.0°C proposal in 4.34 seconds (35 generated tokens, 12.44
tokens/second). Ollama reported zero VRAM use, so the control design uses compact,
sequential, optional inference and deterministic fallback. The PMV safety finding and
mitigation are recorded in `docs/safety-log.md`.

The diagnostic command is:

```powershell
$env:OLLAMA_MODELS='V:\BMS_simulation\.cache\ollama-models'
$env:OLLAMA_HOST='127.0.0.1:11434'
.\.venv\Scripts\python.exe -m bms_agent.cli doctor --json
```

It reports the exact EnergyPlus executable/resources/weather, Ollama executable/API
version, configured model availability, Python version, and supporting tools.

## Independent Tester verdict

`ENV-001` passed independent verification on 2026-07-26. The Tester reran all quality
checks, validated the lockfile and ignore rules, produced a fresh EnergyPlus run at
`.cache/validation/tester-env001-energyplus-20260726-010919-162` (exit 0, zero severe
errors), and parsed a fresh bounded Qwen response in 4.246 seconds. The unreachable
Ollama failure path also degraded safely in diagnostic JSON.
