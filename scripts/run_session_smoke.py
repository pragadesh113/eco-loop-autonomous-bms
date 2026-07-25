"""Run the real one-day SIM-003 actuator smoke test."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, replace

from bms_agent.cli import project_root
from bms_agent.simulation.session import (
    ControlObservation,
    SessionStatus,
    SimulationSession,
    default_session_config,
)


def main() -> int:
    root = project_root()
    run_id = f"sim003-smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    config = replace(
        default_session_config(root),
        max_weather_timesteps=96,
        action_wait_seconds=0.25,
    )
    session = SimulationSession(config, run_id)
    session.start()
    submitted = False
    observations: list[ControlObservation] = []
    action_observation: ControlObservation | None = None
    while not session.join(0):
        observation = session.await_observation(2.0)
        if observation is None:
            continue
        observations.append(observation)
        occupied = any(zone.occupancy_people > 0 for zone in observation.zones)
        if not submitted and occupied:
            action_observation = observation
            session.submit_action(
                decision_id=observation.decision_id,
                observation_sequence=observation.sequence,
                setpoint_c=25.0,
            )
            submitted = True
    result = session.result()
    audits = session.action_audits
    passed = (
        result.status is SessionStatus.COMPLETED
        and result.exit_code == 0
        and result.weather_timesteps >= 96
        and submitted
        and len(audits) == 1
        and audits[0].actuator_value_after_write_c == 25.0
        and audits[0].observed_schedule_value_c == 25.0
        and audits[0].observed_zone_setpoints_c == (25.0,) * 5
    )
    payload = {
        "schemaVersion": "1.0",
        "feature": "SIM-003",
        "passed": passed,
        "result": result.to_dict(),
        "observationsPublished": len(observations),
        "firstObservation": (
            {
                **asdict(observations[0]),
                "zones": [asdict(zone) for zone in observations[0].zones],
            }
            if observations
            else None
        ),
        "actionObservation": (
            {
                **asdict(action_observation),
                "zones": [asdict(zone) for zone in action_observation.zones],
            }
            if action_observation is not None
            else None
        ),
        "actions": [asdict(audit) for audit in audits],
    }
    evidence_path = session.run_dir / "smoke-result.json"
    evidence_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
