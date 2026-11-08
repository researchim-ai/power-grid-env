"""Выгрузка публичных JSON Schema из моделей pydantic."""

from __future__ import annotations

import json
from pathlib import Path

from power_grid_env.model import Network
from power_grid_env.scenarios import Scenario


def export_schemas(directory: Path) -> None:
    from power_grid_env.comparison import ComparisonPlan,ComparisonTrace
    directory.mkdir(parents=True,exist_ok=True)
    for name,model in [('comparison-plan',ComparisonPlan),('comparison-trace',ComparisonTrace)]:
        (directory/(name+'.schema.json')).write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.motor_engineering import MotorEngineeringPlan
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'motor-engineering.schema.json').write_text(json.dumps(MotorEngineeringPlan.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.llm_tasks import LLMGenerationPlan,LLMTask
    from power_grid_env.llm_training import LLMPlan,ToolRequest
    from power_grid_env.llm_fit import TrainingRecipe
    from power_grid_env.llm_inference import InferenceRecipe
    directory.mkdir(parents=True,exist_ok=True)
    for name,model in [('llm-generation',LLMGenerationPlan),('llm-task-private',LLMTask),('llm-plan',LLMPlan),('llm-tool-request',ToolRequest),('llm-training',TrainingRecipe),('llm-inference',InferenceRecipe)]:
        (directory/(name+'.schema.json')).write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.stator_resistance_anchor import AnchoredDynamicIdentification
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'anchored-dynamic-identification.schema.json').write_text(json.dumps(AnchoredDynamicIdentification.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.calibrated_motor_study import CalibratedMotorStudy
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'calibrated-motor-study.schema.json').write_text(json.dumps(CalibratedMotorStudy.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.dynamic_identification_audit import DynamicIdentificationAudit
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'dynamic-identification-audit.schema.json').write_text(json.dumps(DynamicIdentificationAudit.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.motor_recordings import RecordingImport
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'motor-recordings.schema.json').write_text(json.dumps(RecordingImport.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'dynamic-motor-identification.schema.json').write_text(json.dumps(DynamicMotorIdentification.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.campaign import CampaignPlan,CampaignGrid
    for name,model in [('campaign',CampaignPlan),('campaign-grid',CampaignGrid)]:
        directory.mkdir(parents=True,exist_ok=True)
        (directory/(name+'.schema.json')).write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.study_spec import StudySpec

    directory.mkdir(parents=True, exist_ok=True)
    from power_grid_env.protected_motor import ProtectedMotorStudy
    from power_grid_env.study_sensitivity import SensitivityRequest
    for name,model in [('protected-motor',ProtectedMotorStudy),('study-sensitivity',SensitivityRequest)]:
        (directory/(name+'.schema.json')).write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.flux_network import FluxNetworkSettings
    from power_grid_env.motor_identification import MotorIdentification
    (directory / 'motor-identification.schema.json').write_text(json.dumps(MotorIdentification.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    (directory / 'flux-network.schema.json').write_text(json.dumps(FluxNetworkSettings.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.network_automation import NetworkAutomation
    (directory / 'network-automation.schema.json').write_text(json.dumps(NetworkAutomation.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.industrial_dol import IndustrialDOLPack
    (directory / 'industrial-dol.schema.json').write_text(json.dumps(IndustrialDOLPack.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    from power_grid_env.motor_bundle import MotorReplayCriteria
    (directory / "motor-replay.schema.json").write_text(
        json.dumps(MotorReplayCriteria.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    from power_grid_env.fault_clearing import FaultClearingProtocol
    (directory / "fault-clearing.schema.json").write_text(
        json.dumps(FaultClearingProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    from power_grid_env.motor_restart import MotorRestartProtocol
    (directory / "motor-restart.schema.json").write_text(
        json.dumps(MotorRestartProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    from power_grid_env.motor_sensitivity import MotorSensitivityProtocol
    (directory / "motor-sensitivity.schema.json").write_text(
        json.dumps(MotorSensitivityProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    from power_grid_env.motor_trace import MotorTraceProtocol
    (directory / "motor-trace.schema.json").write_text(
        json.dumps(MotorTraceProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "study.schema.json").write_text(
        json.dumps(StudySpec.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "network.schema.json").write_text(
        json.dumps(Network.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (directory / "scenario.schema.json").write_text(
        json.dumps(Scenario.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
