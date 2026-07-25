from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_agent.simulation.model_prep import (
    BASELINE_MODEL_NAME,
    CONTROLLED_MODEL_NAME,
    MANIFEST_NAME,
    PEOPLE_COUNT,
    SOURCE_MODEL_NAME,
    SOURCE_SHA256,
    ModelPreparationError,
    default_energyplus_source,
    prepare_models,
    sha256_bytes,
    transform_model,
)


def _source_model(project_root: Path) -> Path:
    matches = list(
        (project_root / ".tools" / "energyplus" / "26.1.0").glob(
            "*/ExampleFiles/5ZoneAirCooled.idf"
        )
    )
    assert len(matches) == 1
    return matches[0]


def test_transform_model_adds_required_experiment_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source_text = _source_model(project_root).read_text(encoding="utf-8-sig").replace("\r\n", "\n")

    transformed = transform_model(source_text, "controlled")

    assert "EcoLoop New Delhi May 23-29" in transformed
    assert "New.Delhi-Safdarjung.AP IND TMYx.2011-2025" in transformed
    assert "New.Delhi-Safdarjung.AP Ann Htg 99% Condns DB" in transformed
    assert "New.Delhi-Safdarjung.AP Ann Clg 1% Condns DB=>MWB" in transformed
    assert (
        "\n  SizingPeriod:DesignDay,\n"
        "    CHICAGO_IL_USA Annual Heating 99% Design Conditions DB,"
    ) not in transformed
    assert (
        "\n  SizingPeriod:DesignDay,\n"
        "    CHICAGO_IL_USA Annual Cooling 1% Design Conditions DB/MCWB,"
    ) not in transformed
    assert transformed.count("Fanger;") == PEOPLE_COUNT
    assert transformed.count("EcoLoop-Work-Efficiency, !- Work Efficiency") == PEOPLE_COUNT
    assert transformed.count("EcoLoop-Summer-Clothing, !- Clothing Insulation") == PEOPLE_COUNT
    assert transformed.count("EcoLoop-Air-Velocity,    !- Air Velocity") == PEOPLE_COUNT
    assert "Output:Variable,*,Zone Mean Air Temperature,Timestep;" in transformed
    assert "Output:Variable,*,Zone Thermal Comfort Fanger Model PMV,Timestep;" in transformed
    assert "Output:Variable,*,Zone Thermal Comfort Fanger Model PPD,Timestep;" in transformed
    assert "Output:Variable,*,People Occupant Count,Timestep;" in transformed
    assert "Output:Variable,*,Site Outdoor Air Drybulb Temperature,Timestep;" in transformed
    assert "Output:Variable,*,Zone Thermostat Cooling Setpoint Temperature,Timestep;" in transformed
    assert "Output:Variable,Clg-SetP-Sch,Schedule Value,Timestep;" in transformed
    assert "Output:Meter,Electricity:HVAC,Timestep;" in transformed
    assert "Output:EnergyManagementSystem," in transformed


def test_baseline_and_controlled_differ_only_by_mode_comment() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source_text = _source_model(project_root).read_text(encoding="utf-8-sig").replace("\r\n", "\n")

    baseline = transform_model(source_text, "baseline")
    controlled = transform_model(source_text, "controlled")

    assert baseline.splitlines()[1:] == controlled.splitlines()[1:]


def test_prepare_models_preserves_source_and_writes_manifest(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = _source_model(project_root)

    prepared = prepare_models(source, tmp_path)

    assert prepared.source.name == SOURCE_MODEL_NAME
    assert prepared.baseline.name == BASELINE_MODEL_NAME
    assert prepared.controlled.name == CONTROLLED_MODEL_NAME
    assert prepared.manifest.name == MANIFEST_NAME
    assert prepared.source.read_bytes() == source.read_bytes()
    assert sha256_bytes(prepared.source.read_bytes()) == SOURCE_SHA256

    manifest = json.loads(prepared.manifest.read_text(encoding="utf-8"))
    assert manifest["source"]["byteIdenticalToUpstream"] is True
    assert manifest["experiment"]["runPeriod"] == {
        "begin_day": 23,
        "begin_month": 5,
        "end_day": 29,
        "end_month": 5,
    }
    assert manifest["experiment"]["coolingScheduleActuator"] == {
        "componentType": "Schedule:Compact",
        "controlType": "Schedule Value",
        "key": "Clg-SetP-Sch",
    }
    assert manifest["models"]["physicsDifference"] == (
        "None; only the leading mode comment differs."
    )


def test_transform_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        transform_model("", "other")


def test_transform_rejects_missing_source_contract() -> None:
    with pytest.raises(ModelPreparationError, match="RunPeriod"):
        transform_model("", "baseline")


def test_transform_rejects_missing_people_and_existing_ems() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source_text = _source_model(project_root).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    changed_people = source_text.replace(
        "    ActSchd;                 !- Activity Level Schedule Name",
        "    OtherActivity;           !- Activity Level Schedule Name",
        1,
    )
    with pytest.raises(ModelPreparationError, match="People activity"):
        transform_model(changed_people, "baseline")

    with pytest.raises(ModelPreparationError, match="EMS dictionary"):
        transform_model(
            source_text + "\nOutput:EnergyManagementSystem,None,None,None;\n", "baseline"
        )


def test_prepare_rejects_unpinned_source(tmp_path: Path) -> None:
    source = tmp_path / "changed.idf"
    source.write_text("Version,26.1;", encoding="utf-8")

    with pytest.raises(ModelPreparationError, match="does not match"):
        prepare_models(source, tmp_path / "models")


def test_default_energyplus_source_discovery(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert default_energyplus_source(project_root) == _source_model(project_root)

    with pytest.raises(ModelPreparationError, match="found 0"):
        default_energyplus_source(tmp_path)
