"""
Canonical Evidence Representation (CER).

Hierarchy: Study → Experiment → EvidenceSource → Observation
  - Study: a paper or report
  - Experiment: a distinct experimental component
  - EvidenceSource: a table/figure/section where data is reported
  - Observation: one row's worth of data from an evidence source

Separate from primary observations: StatisticalAnalysis → StatisticTable → StatisticRow
  Captures statistical inference (F, t, chi-sq, r, regression coefficients, etc.)
  as a distinct layer — these are statements about treatments, not measurements.

Key separation:
  - ExperimentalDimensions = what structures the observation space (varied or blocking)
  - ExperimentContext = what the researcher described but did not vary
  - EvidenceSource = how the data was reported (table, figure, section)
  - Observation = one combination of factor levels with its measurements
  - StatisticalAnalysis = inference about factors/interactions
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Literal


@dataclass
class Subject:
    """A canonical entity — crop, variety, genotype, etc.
    Shared for cross-paper resolution."""
    subject_id: str
    canonical_name: str
    subject_type: str          # crop | released_variety | advanced_line | check | farm | unknown
    aliases: list[str] = field(default_factory=list)


@dataclass
class Study:
    """A study (paper) that contains one or more experiments."""
    study_id: str
    citation: str = ""
    year: Optional[str] = None
    source_url: Optional[str] = None
    extraction_completeness: str = "full_text"
    unaccounted_tables: list[str] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)


@dataclass
class DimensionLevel:
    """A canonical level for a factor with stable ID and human-readable label."""
    id: str                    # stable short code, e.g. "P0", "P10", "W1", "W2"
    label: str                 # human-readable label, e.g. "0 kg/ha", "Unweeded"
    description: str = ""      # full description from paper


@dataclass
class ExperimentalDimension:
    """A dimension structuring the observation space of an experiment.

    The role field distinguishes dimensions the researcher intentionally
    varied (manipulated) from those that merely partition the data
    (stratification — soil type, multi-site location, multi-year season).
    This moves the model from descriptive (what was measured) to
    experimental (what was inferred)."""
    name: str                          # canonical key, e.g. "Nitrogen_rate", "Variety"
    levels: list[DimensionLevel] = field(default_factory=list)
    role: str = "unknown"              # manipulated | stratification | unknown


@dataclass
class RowGroup:
    """A homogeneous block within an evidence source where all rows share the same grain."""
    group_id: str
    varies: list[str] = field(default_factory=list)
    marginal_over: list[str] = field(default_factory=list)
    result_type: Literal["treatment_combination", "marginal_value", "statistical_test", "summary_statistic"] = "treatment_combination"
    row_labels: list[str] = field(default_factory=list)  # for provenance


@dataclass
class EvidenceSource:
    """A location in the paper where observations are reported.
    Sources may contain multiple row_groups with different grains."""
    source_id: str             # e.g. "Table 3", "Figure 2", "Results paragraph 5"
    type: str = "table"        # table | figure | section | supplementary
    purpose: str = "primary"   # primary | descriptive | anova | weather | economics | correlation | pedigree | other
    row_groups: list[RowGroup] = field(default_factory=list)  # REPLACES observation_grain
    page_number: Optional[int] = None  # PDF page number, set deterministically post-extraction


@dataclass
class ExperimentContext:
    """Things the researcher described but did not vary.
    Design parameters, site characterization, climate, soil properties, etc."""
    purpose: Optional[str] = None
    design: Optional[str] = None
    location: Optional[str] = None
    season: Optional[str] = None
    environment: Optional[str] = None      # field | greenhouse | lab
    replications: Optional[int] = None
    site_properties: dict[str, str] = field(default_factory=dict)
    # e.g. {"soil_pH": "6.1", "soil_texture": "Clay loam", "rainfall": "720 mm"}


@dataclass
class StatisticRow:
    """One row of statistical inference — a test statistic or estimate for one
    source (factor, interaction, contrast, or comparison) on one response variable.

    The general shape holds any inference result: a statistic type + value plus
    its significance, with optional type-specific parameters.  ANOVA-family
    extras (sum_sq, mean_sq) are documented `params` keys for statistic_type
    "F"; correlation uses n/df; regression uses R2, coefficients, etc.  When a
    table reports only significance (stars/NS) with no numeric value, the value
    and all statistic fields are None and only `significance` is populated."""
    source: str                    # Factor/interaction/contrast name, e.g. "Nitrogen_rate", "N_rate x P_source"
    source_type: str               # "main_effect" | "interaction" | "contrast" | "error" | "total" | "pair" | ...
    statistic_type: Optional[str] = None   # "F" | "t" | "chi_sq" | "r" | "R2" | "slope" | "ICC" | "AIC" | "BIC" | ...
    value: Optional[float] = None          # the statistic's value (F, t, r, slope, ...)
    df_numerator: Optional[float] = None
    df_denominator: Optional[float] = None  # Usually the error DF for F; single df for t/chi-sq lives here
    p_value: Optional[float] = None
    significance: Optional[str] = None     # e.g. "***", "**", "*", "ns", "NS"
    params: dict[str, Any] = field(default_factory=dict)
    # Type-specific parameters.  Documented keys:
    #   statistic_type "F":  sum_sq, mean_sq   (ANOVA SS/MS columns)
    #   statistic_type "r":  n                 (sample size)


@dataclass
class StatisticTable:
    """A complete statistical result table for one response variable."""
    response_variable: str         # e.g. "Grain yield", "Plant height"
    unit: Optional[str] = None
    rows: list[StatisticRow] = field(default_factory=list)


@dataclass
class StatisticalAnalysis:
    """Statistical inference results from one source (table/figure) in the paper.
    Contains one or more statistic tables, each for a different response variable."""
    analysis_id: str
    experiment_id: str
    source_id: str                 # e.g. "Table 4"
    analysis_type: str = "anova"   # anova | manova | ancova | correlation | regression | t_test | chi_sq | other
    design: Optional[str] = None
    tables: list[StatisticTable] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class Experiment:
    """A distinct experimental component within a study."""
    experiment_id: str
    study_id: str
    label: str
    subject: Optional[Subject] = None
    context: ExperimentContext = field(default_factory=ExperimentContext)
    dimensions: list[ExperimentalDimension] = field(default_factory=list)
    evidence_sources: list[EvidenceSource] = field(default_factory=list)
    statistical_analyses: list[StatisticalAnalysis] = field(default_factory=list)


@dataclass
class MeasurementValue:
    """One measured variable within an observation (one column of a table row)."""
    metric: str                           # e.g. "GY", "PH", "AUDPC"
    construct: str = ""                   # e.g. "grain_yield", "plant_height"
    value_raw: Optional[str] = None       # e.g. "2260.0h", "5.2"
    computed_value: Optional[float] = None
    significance_letter: Optional[str] = None
    unit: Optional[str] = None
    statistic: Literal["mean", "lsd", "cv", "se", "sd", "p_value", "range", "median", "other", "none"] = "mean"


@dataclass
class Observation:
    """One independent empirical datum — a row from an evidence source."""
    observation_id: str
    experiment_id: str
    source_id: str
    factor_values: dict[str, str] = field(default_factory=dict)
    # e.g. {"Nitrogen_rate": "92", "Variety": "Kubsa", "Location": "Kulumsa"}
    marginal_over: list[str] = field(default_factory=list)      # factors averaged over
    result_type: Literal["treatment_combination", "marginal_value", "statistical_test", "summary_statistic"] = "treatment_combination"
    measurements: list[MeasurementValue] = field(default_factory=list)
    provenance: Optional[str] = None      # e.g. "Table 3, row 4"
    confidence: str = "unstated"


@dataclass
class CompilationResult:
    """The complete compiled CER for one or more papers."""
    studies: list[Study] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
