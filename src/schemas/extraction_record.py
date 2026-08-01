"""
Extraction Record — raw, permissive output from the LLM.

Every field is stored as-is from the paper. No normalization,
no resolution, no interpretation. The model writes what it sees.

This is an intermediate representation. It is NOT the canonical
evidence — that is built deterministically by the compiler.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawMeasurement:
    """One measurement value as the model found it in the paper."""
    metric_raw: str
    construct_raw: str = ""
    value_raw: Optional[str] = None
    computed_value: Optional[float] = None
    significance_letter: Optional[str] = None
    statistic: str = "mean"        # mean | lsd | cv | se | sd | p_value | range | median | none
    unit_raw: Optional[str] = None


@dataclass
class RawObservation:
    """One row of data from one evidence source."""
    factor_values: dict[str, str] = field(default_factory=dict)
    measurements: list[RawMeasurement] = field(default_factory=list)
    provenance: Optional[str] = None
    confidence: str = "unstated"


@dataclass
class ExtractionRecord:
    """The full output from extracting one evidence source.

    The extraction prompt always targets one evidence source
    (table, figure, results section) and returns all observations
    represented in that source."""
    experiment_id: str
    source_id: str
    observations: list[RawObservation] = field(default_factory=list)
    notes: Optional[str] = None
