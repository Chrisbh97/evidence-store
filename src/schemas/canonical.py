"""
Canonical Evidence Representation (CER).

Hierarchy: Study → Experiment → EvidenceSource → Observation
  - Study: a paper or report
  - Experiment: a distinct experimental component
  - EvidenceSource: a table/figure/section where data is reported
  - Observation: one row's worth of data from an evidence source

Separate from primary observations: StatisticalAnalysis → ANOVATable → ANOVAResultRow
  Captures statistical inference (F-values, p-values, significance) as a
  distinct layer — these are statements about treatments, not measurements.

Key separation:
  - ExperimentalDimensions = what structures the observation space (varied or blocking)
  - ExperimentContext = what the researcher described but did not vary
  - EvidenceSource = how the data was reported (table, figure, section)
  - Observation = one combination of factor levels with its measurements
  - StatisticalAnalysis = inference about factors/interactions
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Study:
    """A study (paper) that contains one or more experiments."""
    study_id: str
    citation: str = ""
    year: Optional[str] = None
    source_url: Optional[str] = None
    extraction_completeness: str = "full_text"
    unaccounted_tables: list[str] = field(default_factory=list)


@dataclass
class Subject:
    """A canonical entity — genotype, variety, farm, sample type, etc.
    Derived from factor values by the compiler.
    Shared for cross-paper resolution."""
    subject_id: str
    canonical_name: str
    subject_type: str          # crop | released_variety | advanced_line | check | farm | unknown
    aliases: list[str] = field(default_factory=list)


@dataclass
class ExperimentalDimension:
    """A dimension structuring the observation space of an experiment.

    The role field distinguishes dimensions the researcher intentionally
    varied (manipulated) from those that merely partition the data
    (stratification — soil type, multi-site location, multi-year season).
    This moves the model from descriptive (what was measured) to
    experimental (what was inferred).
    """
    name: str                  # canonical key, e.g. "Nitrogen_rate", "Variety"
    levels: list[str] = field(default_factory=list)
    role: str = "unknown"      # manipulated | stratification | unknown


@dataclass
class EvidenceSource:
    """A location in the paper where observations are reported.
    Each source has an observation grain — the set of factors whose levels
    define one row of the source."""
    source_id: str             # e.g. "Table 3", "Figure 2", "Results paragraph 5"
    type: str = "table"        # table | figure | section | supplementary
    purpose: str = "primary"   # primary | descriptive | anova | weather | economics | correlation | pedigree | other
    observation_grain: list[str] = field(default_factory=list)
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
class ANOVAResultRow:
    """One row from an ANOVA table — the effect of one factor or interaction
    on one response variable, with F-statistic and significance."""
    source: str                    # Factor/interaction name, e.g. "Nitrogen_rate", "N_rate x P_source"
    source_type: str               # "main_effect" | "interaction" | "error" | "total"
    df_numerator: Optional[float] = None
    df_denominator: Optional[float] = None  # Usually the error DF
    sum_sq: Optional[float] = None
    mean_sq: Optional[float] = None
    f_value: Optional[float] = None
    p_value: Optional[float] = None
    significance: Optional[str] = None   # e.g. "***", "**", "*", "ns"


@dataclass
class ANOVATable:
    """A complete ANOVA table for one response variable."""
    response_variable: str         # e.g. "Grain yield", "Plant height"
    unit: Optional[str] = None
    rows: list[ANOVAResultRow] = field(default_factory=list)


@dataclass
class StatisticalAnalysis:
    """Statistical inference results from one source (table/figure) in the paper.
    Contains one or more ANOVA tables, each for a different response variable."""
    analysis_id: str
    experiment_id: str
    source_id: str                 # e.g. "Table 4"
    analysis_type: str = "anova"   # anova | manova | ancova
    design: Optional[str] = None
    tables: list[ANOVATable] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class Experiment:
    """A distinct experimental component within a study."""
    experiment_id: str
    study_id: str
    label: str
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
    statistic: str = "mean"              # mean | lsd | cv | se | sd | p_value | range | median | other | none
    direction: str = "lower_better"       # lower_better | higher_better | n/a


@dataclass
class Observation:
    """One independent empirical datum — a row from an evidence source."""
    observation_id: str
    experiment_id: str
    source_id: str
    factor_values: dict[str, str] = field(default_factory=dict)
    # e.g. {"Nitrogen_rate": "92", "Variety": "Kubsa", "Location": "Kulumsa"}
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
