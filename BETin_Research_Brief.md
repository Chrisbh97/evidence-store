# Computational Evidence for Ethiopian Research: A Prototype Investigation

## 1. The Problem

Ethiopian experimental research produces valuable evidence, but it remains fragmented across individual publications. Systematic cross-study questions, such as which fertilizer rate consistently increases yield, or which varieties perform across locations, require manually locating, reading, and synthesizing evidence from many papers. This does not scale.

A natural question is why not build a database that researchers upload to directly. The answer is transitional. Decades of published Ethiopian research already exist in print, and papers from 2005, 2010, and 2015 cannot be retroactively uploaded. Extraction from existing publications is the only path to making that historical evidence computationally accessible.

The long-term goal is different. If the representation developed here proves sound, it can serve as a submission standard for new research, eliminating the extraction pipeline for future studies. Extraction from PDFs is a transitional necessity, not a permanent architectural component. The lasting contribution is the representation and the methodology, not the parsing pipeline.

This is also why the work fits BETin specifically: the institute's expansion into computational science and LLM applications needs exactly this kind of substrate, a representation and methodology that can generalize across research domains, not a single-purpose tool.

The question that motivated this investigation: can decades of experimental research be transformed from a collection of documents into a structured, queryable body of evidence that computational systems can reason over while preserving provenance?

## 2. Design Constraints

Several observations guided the work from the start.

The first is that scientific papers are not the right unit of knowledge for the questions researchers actually ask. A paper is a narrative argument, not a structured record of experimental evidence. Document retrieval brings back papers, not answers.

The second is that LLMs, left to their own devices, produce numbers that feel correct but are fabricated. When asked for a specific yield value, the model draws from its training distribution, which includes thousands of papers, and produces something locally plausible and impossible to verify.

These observations drove two hard constraints: measurements must be retrieved, not generated; and every reported value must trace to a retrievable source. The LLM's role is to call tools and compose narratives, not to supply factual content from parametric knowledge.

**Related work.** Closest in method is a 2026 pipeline that uses LLMs with structured schemas to extract metadata from published long-term agricultural experiment literature, enabling reuse and cross-experiment synthesis. That work targets experiment-level metadata; it does not extract measurement-level results paired with tool-gated LLM question answering and per-citation provenance back to source pages. Separately, ontology-grounded QA has been shown to substantially cut hallucination in structured-domain settings. One clinical knowledge-graph framework, for example, raised QA accuracy from the 37 to 52% range for baseline LLMs to 98%, by restricting answers to a validated graph, though it is not applied to legacy-document extraction. Combining retroactive extraction from published literature with a measurement-level evidence representation and tool-restricted grounded QA, for Ethiopian experimental research specifically, is the gap this investigation addresses. A more comprehensive review is in progress.

## 3. Current Prototype

*PDF to Extraction to CER to Evidence Store to Tools to Assistant*

**Extraction.** PDF tables are parsed into structured observations: treatment factors, levels, measured outcomes, and provenance (table, page, paper).

**Canonical Evidence Representation (CER).** Each observation is normalized into a common schema, where factors become typed dimensions and outcomes become metric-value-unit triples. Every measurement knows its source.

**Evidence Store.** A queryable database organized around experiments, observations, measurements, and evidence sources. Supports cross-experiment queries impossible over raw text.

**Scientific Tools.** Deterministic functions (`search_experiments`, `get_measurements`, `get_experiment_schema`) that return only records existing in the database. No generation, no interpolation.

**Grounded Assistant.** The LLM accesses evidence exclusively through these tools. Its output is post-processed: every cited measurement is validated against tool results from that conversation. Numbers without a tool trace are removed.

The prototype currently indexes ten Ethiopian papers on phosphorus and nitrogen management, comprising approximately 1,500 measurements across 42 source tables. It can answer comparative questions across factor levels, treatments, and locations within an experiment, with every value backed by a retrievable database source, and a direct PDF page link for the subset of measurements with page numbers indexed so far.

**[FIGURE 1: Pipeline diagram, PDF to Extraction to CER to Evidence Store to Tools to Assistant]**

**Worked example.**

&gt; **Question:** What was the grain yield of bread wheat at each phosphorus rate in P-04, combined across locations?
&gt;
&gt; **Answer:** At 0 kg P₂O₅/ha, yield was 986 kg/ha. At 46 kg P₂O₅/ha, yield was 3,535 kg/ha. The 69 and 92 kg P₂O₅/ha rates were tested but no numeric yield data were reported for these levels.
&gt;
&gt; **Evidence:**
&gt; [1] M-0732 · Table 4, control row (Combined): Grain yield = 986 kg/ha
&gt; [2] M-0702 · Table 4, Combined, 46P at 0N: Grain yield = 2,425 kg/ha
&gt; [3] M-0708 · Table 4, Combined, 46P at 115N: Grain yield = 3,272 kg/ha
&gt; [4] M-0714 · Table 4, Combined, 46P at 161N: Grain yield = 3,821 kg/ha
&gt; [5] M-0720 · Table 4, Combined, 46P at 207N: Grain yield = 4,470 kg/ha
&gt; [6] M-0726 · Table 4, Combined, 46P at 253N: Grain yield = 4,891 kg/ha

**[FIGURE 2: PLACEHOLDER — Insert Screenshot (43).png here]**
*Caption: Chat interface showing the worked example exchange. The assistant returns three verified values (4,188 kg/ha at Gerba, 4,751 kg/ha at Woyramba, 4,470 kg/ha Combined) with an evidence block listing measurement IDs, factor contexts, and source tables. The right panel shows the PDF viewer open to Table 4 at page 9, confirming the source data.*

## 4. Prototype Evaluation

The questions above demonstrate the architecture working as intended. The system was also tested against harder questions to understand where it succeeds, where it fails, and what those failures reveal about the open problems. Five representative cases are described below.

**Case 1: Within-experiment, single factor (works reliably).**

Questions that ask for measurements from a single experiment, varying one factor, with a metric that matches the database name, are handled correctly. The LLM retrieves the data, structures the comparison, and lists the evidence block with clickable M-IDs. This class covers straightforward queries such as yield by phosphorus rate, yield by nitrogen rate, or biomass by fertilizer treatment.

**[FIGURE 3: PLACEHOLDER — Insert Screenshot (41).png here]**
*Caption: A within-experiment query for grain yield by phosphorus rate in P-04. The assistant returns atomic values with ranges and location breakdowns. Note the qualitative synthesis line: "reflecting strong nitrogen interaction" — interpretive language that goes beyond the retrieved data.*

**Case 2: Multi-factor query, correct data exists but LLM initially fails (grounded generation gap).**

&gt; **Question:** What was the yield at 46 kg P₂O₅/ha combined with 207 kg N/ha in P-04?
&gt;
&gt; **Initial answer:** No grain yield data exists for this treatment combination.
&gt;
&gt; **After user correction:** Gerba: 4,188 kg/ha. Woyramba: 4,751 kg/ha. Combined: 4,470 kg/ha.

The data exists in the Evidence Store and is returned by the tool when queried correctly. The LLM's initial claim of missing data was not a retrieval failure but a generation failure: it made an incorrect inference about the tool results before reporting them. This pattern — where the LLM's narrative produces a statement unsupported by the retrieved data — is the grounded generation gap that remains the central unresolved problem. Importantly, the user can correct the system interactively, and the architecture makes the discrepancy visible because every value is traceable to a tool result.

**[FIGURE 4: PLACEHOLDER — Insert Screenshot (43).png here, cropped or reused from Figure 2]**
*Caption: The corrected exchange showing the three verified values and their evidence block. This demonstrates recovery after user correction, but also shows that the system requires human intervention to fix generation errors.*

**Case 3: Cross-study comparison requested, system lacks indexed papers (retrieval boundary).**

&gt; **Question:** Compare NP fertilizer response between P-02 and NPS fertilizer response in P-07.
&gt;
&gt; **Result:** The system reported that neither P-02 nor P-07 were present in the indexed evidence store. It offered to search for experiments in those papers but, upon user confirmation, fell back to a generic message stating it could not produce a verified numeric answer.

This case demonstrates a retrieval boundary rather than a generation failure. The system correctly identified that the requested papers were outside its current index. However, it did not demonstrate the metric-adaptation behavior described in earlier design iterations — discovering that "Grain yield" in one paper might be labeled "GY" in another. The system simply declined.

**[FIGURE 5: PLACEHOLDER — Insert Screenshot (50).png here]**
*Caption: The exchange showing the system's response to a cross-paper comparison request. The assistant states that P-02 and P-07 are not in the indexed set and falls back after the user confirms. No metric discovery or tool adaptation is demonstrated.*

**Case 4: Cross-study synthesis with unverified recommendations (the dangerous success).**

&gt; **Question:** Compare phosphorus response across P-04 (Vertisols) and P-10 (Nitisols).

When asked to compare experiments across different soil types, the system retrieved correct data from both experiments and presented it in a structured table. However, the prose then synthesized the numbers into agronomic conclusions that do not appear in any tool result:

- "Vertisols (P-04) require higher P rates (46 kg P₂O₅/ha) to achieve maximum yields"
- "Nitisols (P-10) achieve near-maximum yields at lower P rates (23 kg P₂O₅/ha)"
- "The economic efficiency of P application was higher in Nitisols at lower P rates"

These statements are not retrievable from the database. They are inferential leaps made by the LLM from correct numbers. This is the most dangerous failure mode the prototype exhibits: **value-accurate but interpretation-unchecked output.** The evidence block contains real measurements, but the prose generalizes beyond them into recommendation territory.

**[FIGURE 6: PLACEHOLDER — Insert Screenshot (44).png here (top portion)]**
*Caption: The beginning of a cross-study comparison response. The assistant correctly identifies the two experiments and their soil types, then begins presenting structured data.*

**[FIGURE 7: PLACEHOLDER — Insert Screenshot (46).png here]**
*Caption: The "Summary of Findings" section from the same cross-study response. The table shows correct values, but the bullet points below it contain unverified agronomic recommendations and economic judgments — exactly the synthesis the system is designed to prevent.*

**Case 5: Cross-study computation and trend characterization (unsanctioned synthesis).**

&gt; **Question:** Explore the yield vs phosphorus rate in bread wheat across the studies available.

The system produced a cross-study synthesis table and characterized trends: "Yield increased from 23 to 69 kg P ha⁻¹, then plateaued or slightly declined at 92 kg P ha⁻¹." It also computed means across nitrogen rates ("mean ≈ 3,036 kg ha⁻¹") — a statistical aggregation not requested and not verifiable from any single tool result.

This demonstrates that the system will perform unsanctioned computations and trend characterizations when the question invites synthesis. The individual values may be retrievable, but the means, ranges, and trend language are generated.

**[FIGURE 8: PLACEHOLDER — Insert Screenshot (39).png here]**
*Caption: Cross-study synthesis showing computed means across nitrogen rates ("mean ≈ 3,036 kg ha⁻¹") and trend characterization ("plateaued or slightly declined"). The right panel shows the PDF viewer open to Table 5. The prose contains interpretive claims not present in the source data.*

## 5. What Remains Open

The evaluation reveals several gaps that define the research direction.

**Grounded generation** is the central unresolved problem. The architecture ensures that any number that passes through a tool call is real and traceable. It does not ensure that the LLM's prose — the narrative it writes around those numbers — is faithful to the retrieved data. The LLM routinely inserts numerical claims during prose generation that no tool returned, particularly when the narrative expects a value at a given point. More dangerously, as Cases 4 and 5 demonstrate, it synthesizes correct numbers into incorrect conclusions: recommendations, economic judgments, and trend characterizations that are not present in the evidence store.

One direction under active exploration is removing the LLM from value generation entirely. In this approach, the model selects which measurement to cite while a deterministic layer renders the actual value into the response. An early prototype exists but has not yet been validated against the failure cases that motivate it.

**Synthesis gating.** Even if values are rendered deterministically, the LLM can still write "yield plateaued at 46P" while citing the correct measurements. Preventing this requires constraining the LLM to atomic presentation — reporting each measurement independently without comparative or interpretive language — or building a separate statistical-reasoning layer that validates claims against ANOVA results.

**Representation design.** CER is an attempt, not a standard. The right schema for experimental evidence across disciplines — factor taxonomies, unit normalization, and the relationships between experiments, observations, and measurements — remains an open research question.

**Metric vocabulary.** The same measured concept is labelled differently across papers. "Grain yield" in one study is "GY" in another. "Biomass" appears as "BY" elsewhere. A synonym system or normalized vocabulary is needed to make tools reliably find data across papers without requiring the LLM to discover the correct name mid-conversation.

**Scalability.** Current extraction is semi-manual. Automated ingestion at volume, paper prioritisation, and handling diverse document formats are unsolved.

**Domain adaptability.** The architecture was built for fertiliser experiments. Does the same CER model transfer to disease management, variety evaluation, livestock nutrition, or environmental monitoring? Each domain introduces new factor vocabularies and experimental designs.

**From retrieval to decision support.** The current system answers what does the evidence say? Extending it to what should I do? requires handling conflicting evidence, missing data, and recommendation under uncertainty — a fundamentally harder problem.

## 6. Research Direction

The long-term vision is a computational evidence infrastructure for Ethiopian experimental research — not an archive, but a decision support system where researchers and practitioners can interrogate accumulated evidence across domains, from fertiliser response to disease management to variety evaluation.

The immediate focus is grounded generation, since it is the blocking dependency for any confident cross-study synthesis. In sequence after that:

1. **Grounded generation.** Solving structurally rather than through prompting (in progress).
2. **Metric vocabulary.** A synonym system or LLM-driven normalisation so that tools can find data regardless of how a paper labels its measurements.
3. **Representation design.** Testing whether CER transfers across domains and supports cross-study reconciliation, which determines how far the current work generalises.
4. **Scalable ingestion.** Needed once representation is validated, to grow beyond ten papers.
5. **Decision support.** The longer-term extension, once evidence retrieval itself is fully trustworthy.

I would like to continue developing this direction as an adjunct researcher, investigating these questions in collaboration with BETin's domain experts and exploring what the institute's data, infrastructure, and research programs can contribute.