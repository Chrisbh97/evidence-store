from pathlib import Path

HERE = Path(__file__).parent


def read_prompt(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


discovery = read_prompt("discovery.md")
extraction = read_prompt("extraction.md")
statistical_extraction = read_prompt("statistical_extraction.md")
validation = read_prompt("validation.md")
normalization = read_prompt("normalization.md")
