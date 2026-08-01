"""
Evidence agent: wraps ResearchEngine for backward compatibility.
New code should use ResearchEngine directly.
"""

from .engine import ResearchEngine, Answer, ResearchSession


class EvidenceAgent:
    def __init__(self, model=None, api_key=None, api_base=None):
        self._engine = ResearchEngine(model=model, api_key=api_key, api_base=api_base)

    def ask(self, question: str) -> str:
        return self._engine.answer(question).text


def ask_question(question: str, model=None, api_key=None, api_base=None) -> str:
    agent = EvidenceAgent(model=model, api_key=api_key, api_base=api_base)
    return agent.ask(question)
