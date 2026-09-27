"""Validators for CER completeness and correctness."""

from typing import Any, Optional
from src.schemas.canonical import (
    Observation, ExperimentalDimension, MeasurementValue,
    StatisticRow, EvidenceSource, RowGroup
)


def validate_observation_factor_completeness(
    obs: Observation,
    design_factors: list[str]
) -> list[str]:
    """
    Invariant: factor_values.keys() ∪ marginal_over == all_design_factors
    No overlap between factor_values.keys() and marginal_over.
    """
    present = set(obs.factor_values.keys())
    marginal = set(obs.marginal_over)
    all_factors = set(design_factors)

    errors = []
    if present & marginal:
        errors.append(f"Overlap between factor_values and marginal_over: {present & marginal}")
    missing = all_factors - present - marginal
    if missing:
        errors.append(f"Missing factors (neither in factor_values nor marginal_over): {missing}")
    extra = (present | marginal) - all_factors
    if extra:
        errors.append(f"Unknown factors (not in design): {extra}")
    return errors


def validate_factor_ids(
    obs: Observation,
    experiment_dimensions: list
) -> list[str]:
    """
    Every factor_values value must be a valid level ID for that factor.
    """
    errors = []
    level_ids = {}
    for d in experiment_dimensions:
        if isinstance(d, dict):
            fname = d.get("name")
            levels = d.get("levels", [])
            level_ids[fname] = {l.get("id") if isinstance(l, dict) else l for l in levels}
        else:
            level_ids[d.name] = {l.id for l in d.levels}

    for fname, fval in obs.factor_values.items():
        if fname not in level_ids:
            errors.append(f"Unknown factor: {fname}")
        elif fval not in level_ids[fname]:
            errors.append(
                f"Invalid level ID '{fval}' for factor {fname}. "
                f"Valid IDs: {sorted(level_ids[fname])}"
            )
    return errors


def validate_result_type_consistency(obs: Observation) -> list[str]:
    """
    Check that result_type matches the factor_values/marginal_over pattern.
    """
    errors = []
    design_factors_count = len(obs.factor_values) + len(obs.marginal_over)
    
    if obs.result_type == "treatment_combination":
        if obs.marginal_over:
            errors.append("treatment_combination should have empty marginal_over")
        if not obs.factor_values:
            errors.append("treatment_combination must have at least one factor value")
    elif obs.result_type == "marginal_value":
        if not obs.marginal_over:
            errors.append("marginal_value must have non-empty marginal_over")
        if not obs.factor_values:
            errors.append("marginal_value must have at least one factor in factor_values")
    elif obs.result_type == "summary_statistic":
        if obs.factor_values and obs.result_type != "statistical_test":
            pass  # summary stats can have grouping factors
    return errors


def validate_measurement_value(mv: 'MeasurementValue') -> list[str]:
    """Validate a single measurement value."""
    errors = []
    # Allow null values for mean statistic (empty table cells)
    if mv.statistic == "mean" and mv.computed_value is None and mv.value_raw is None:
        pass  # Empty cell is valid
    elif mv.statistic == "mean" and mv.computed_value is None:
        errors.append(f"Mean statistic requires computed_value or value_raw")
    return errors


def validate_observation(obs: Observation, experiment_dimensions: list) -> list[str]:
    """Run all observation-level validations."""
    errors = []
    # Handle both dict and object dimensions
    design_factor_names = []
    for d in experiment_dimensions:
        if isinstance(d, dict):
            design_factor_names.append(d.get("name"))
        else:
            design_factor_names.append(d.name)
    
    errors = []
    errors.extend(validate_observation_factor_completeness(obs, design_factor_names))
    errors.extend(validate_factor_ids(obs, experiment_dimensions))
    errors.extend(validate_result_type_consistency(obs))
    for mv in obs.measurements:
        errors.extend(validate_measurement_value(mv))
    return errors


def validate_evidence_source(source: 'EvidenceSource') -> list[str]:
    """Validate evidence source row_groups."""
    errors = []
    group_ids = set()
    for rg in source.row_groups:
        if rg.group_id in group_ids:
            errors.append(f"Duplicate group_id: {rg.group_id}")
        group_ids.add(rg.group_id)
        
        if rg.result_type in ("marginal_value", "statistical_test") and not rg.marginal_over:
            errors.append(f"Group {rg.group_id}: {rg.result_type} requires marginal_over")
        if rg.result_type == "treatment_combination" and rg.marginal_over:
            errors.append(f"Group {rg.group_id}: treatment_combination should have empty marginal_over")
        if rg.result_type == "summary_statistic" and rg.varies:
            pass  # summary stats can have varies if grouped by factor
    return errors


def validate_cer_experiment(exp, design_factors: list[str]) -> list[str]:
    """Validate all observations in an experiment."""
    errors = []
    for obs in exp.observations:
        obs_errors = validate_observation(obs, exp.dimensions)
        if obs_errors:
            errors.append(f"Observation {obs.observation_id}: {', '.join(obs_errors)}")
    return errors


def validate_cer_compilation(compilation_result, discovery_data: dict) -> dict:
    """
    Full validation of a compilation result against discovery.
    Returns dict with 'errors' (list) and 'warnings' (list).
    """
    errors = []
    warnings = []
    
    # Build experiment lookup
    exp_dims = {}
    for exp in discovery_data.get("experiments", []):
        exp_dims[exp["experiment_id"]] = [d for d in exp.get("dimensions", [])]
    
    # Build design factors per experiment
    design_factors = {}
    for exp_id, dims in exp_dims.items():
        design_factors[exp_id] = [d["name"] for d in dims if d.get("role") in ("manipulated", "stratification")]
    
    for obs in compilation_result.observations:
        exp_id = obs.experiment_id
        if exp_id in design_factors:
            obs_errors = validate_observation(obs, exp_dims.get(exp_id, []))
            if obs_errors:
                errors.append(f"{obs.observation_id}: {', '.join(obs_errors)}")
    
    # Check for duplicate observation IDs
    obs_ids = [o.observation_id for o in compilation_result.observations]
    dup = set([x for x in obs_ids if obs_ids.count(x) > 1])
    if dup:
        errors.append(f"Duplicate observation IDs: {dup}")
    
    return {"errors": errors, "warnings": warnings, "ok": len(errors) == 0}