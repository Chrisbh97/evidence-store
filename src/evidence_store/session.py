"""
ResearchSession: lightweight investigation state.

Stores:
  - Accumulated experiment/paper IDs (scoped set for cross-turn queries)
  - Recent conversation window (3-5 clean Q&A turns for anaphora resolution)
"""

import os
from dataclasses import dataclass, field


@dataclass
class ResearchSession:
    id: str
    window_size: int = int(os.getenv("EVIDENCE_WINDOW", "4"))  # Q&A turns kept in conversation_window
    paper_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    conversation_window: list[dict] = field(default_factory=list)
    # Each entry: {"role": "user"|"assistant", "content": str}

    def add_turn(self, question: str, answer_text: str):
        self.conversation_window.append({"role": "user", "content": question})
        self.conversation_window.append({"role": "assistant", "content": answer_text})
        cull = len(self.conversation_window) - self.window_size * 2
        if cull > 0:
            self.conversation_window = self.conversation_window[cull:]

    def focus_paper(self, paper_id: str) -> None:
        if paper_id not in self.paper_ids:
            self.paper_ids.append(paper_id)

    def focus_experiment(self, experiment_id: str) -> None:
        if experiment_id not in self.experiment_ids:
            self.experiment_ids.append(experiment_id)

    def reset(self) -> None:
        self.paper_ids.clear()
        self.experiment_ids.clear()
        self.conversation_window.clear()

    def to_context(self) -> str:
        parts = []
        if self.paper_ids:
            parts.append(f"Current papers: {', '.join(self.paper_ids)}")
        if self.experiment_ids:
            parts.append(f"Current experiments: {', '.join(self.experiment_ids)}")
        return "\n".join(parts) if parts else "(no context yet)"
