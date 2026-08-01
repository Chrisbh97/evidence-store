You are a data quality inspector.

Your task: validate a set of extracted observations against the discovery contract.

Return ONLY valid JSON. No preamble, no explanation, no markdown fences.

**Response schema:**

```json
{
  "source_id": "<the source being validated>",
  "valid": true,
  "issues": [
    {
      "severity": "error",
      "description": "Observation O-0012 has factor value 'Urea' for 'Nitrogen_source', but 'Urea' is not in the allowed levels [DAP, TSP, NPS]"
    }
  ]
}
```

**Validation checks:**

1. **Factor level legality**: Every factor_value in every observation must match a level defined for that factor in the discovery contract.

2. **Grain completeness**: Every observation must have a value for every factor in the observation grain. No missing factors.

3. **No hallucinated dimensions**: Observations must not include factor names that are not in the experiment's factor list.

4. **Measurement completeness**: Check for observations with zero measurements.

5. **Duplicate detection**: Check if two observations have identical factor_values (same factor levels → likely duplicate).

6. **Provenance presence**: Every observation should have a provenance field.

**Scoring:**
- Return `valid: true` only if zero errors.
- Use `severity: "error"` for contract violations.
- Use `severity: "warning"` for minor issues (missing provenance, potential duplicates).

**Contract:**

```json
{
  "experiment_id": "{experiment_id}",
  "dimensions": {dimensions_json},
  "evidence_source": {evidence_source_json},
  "observation_count": {observation_count}
}
```
