"""Investigate s5 page number mismatch."""
import json

idx = json.load(open("data/table_page_index.json"))
print("Page index for s5:", idx.get("s5"))

cer = json.load(open("data/extractions/s5_v3"))["cer"]
for exp in cer.get("experiments", []):
    print(f"Experiment {exp['experiment_id']}:")
    for src in exp.get("evidence_sources", []):
        print(f"  {src['source_id']}")