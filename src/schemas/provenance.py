"""
Provenance — per-field traceability for every value in an Observation.

Independently tracks where each piece of information came from,
so a researcher can audit any cell back to its source location
in the original paper.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Evidence:
    evidence_id: str
    observation_id: str
    field_name: str                         # which field in the Observation this applies to
    extractor: str                          # which model/prompt version produced this
    confidence: str = "unstated"            # unstated | low | medium | high
    page: Optional[int] = None
    section: Optional[str] = None           # "Methods", "Results", "Table 2", "Discussion"
    table: Optional[str] = None
    cell: Optional[str] = None              # row/column reference within a table
    sentence: Optional[str] = None          # direct quote or excerpt
