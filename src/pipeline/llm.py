"""LLM stage functions — one LLM call each, pure data in / raw text out.

These are the "stages" of the pipeline: discovery, per-source extraction, and
statistical extraction.  Each takes structured inputs plus connection kwargs
and returns the raw model response.  No state, no caching — the orchestrator
owns sequencing and versioned storage.
"""

import os
import random
import sys
import time

from openai import OpenAI

from src.prompts import discovery, extraction, statistical_extraction

_last_retry_total: float = 0.0


def call_llm(system_prompt: str, user_msg: str, **kwargs) -> str:
    """One chat completion with rate-limit/transient retry and throttling."""
    global _last_retry_total
    request_timeout = kwargs.get("timeout", 300.0)
    client = OpenAI(
        api_key=kwargs.get("api_key", os.getenv("API_KEY", "sk-dummy")),
        base_url=kwargs.get("api_base", os.getenv("API_BASE_URL", "https://api.openai.com/v1")),
        max_retries=0,
        timeout=request_timeout,
    )
    model = kwargs.get("model", os.getenv("MODEL", "gpt-4o"))
    if _last_retry_total > 0:
        pre_wait = _last_retry_total * 0.4
        print(f"  Throttling: last call needed {_last_retry_total:.1f}s retry — waiting {pre_wait:.1f}s pre-emptively", file=sys.stderr)
        time.sleep(pre_wait)
    attempt = 0
    accumulated = 0.0
    while True:
        attempt += 1
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
            )
            _last_retry_total = accumulated
            return response.choices[0].message.content
        except Exception as e:
            err_str = str(e).lower()
            if hasattr(e, "response") and e.response is not None:
                retry_after = e.response.headers.get("retry-after")
                status = e.response.status_code
            else:
                retry_after = None
                status = getattr(e, "status_code", 0) or getattr(e, "http_status", 0)
            is_rate_limit = status == 429 or "rate limit" in err_str or "rate_limit" in err_str
            is_transient = status in (408, 429, 500, 502, 503, 504) or "timeout" in err_str
            if not (is_rate_limit or is_transient):
                _last_retry_total = 0.0
                raise
            if retry_after:
                wait = float(retry_after)
            else:
                wait = min(2 ** attempt + random.uniform(0, 1), 120)
            accumulated += wait
            print(f"  Rate limited (attempt {attempt}) — retrying in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)


def run_discovery(paper_text: str, **kwargs) -> str:
    """Run discovery prompt. Returns raw JSON text."""
    return call_llm(discovery, f"Full paper:\n\n{paper_text}", **kwargs)


def run_extraction(paper_text: str, experiment: dict, source: dict, **kwargs) -> str:
    """Run extraction prompt for one evidence source. Returns raw JSON text."""
    user_msg = (
        f"Full paper:\n\n{paper_text}\n\n"
        f"---\n\n"
        f"Experiment: {experiment.get('label', '')}\n"
        f"Source: {source['source_id']}\n"
        f"Observation grain: {source.get('observation_grain', [])}\n"
    )
    return call_llm(extraction, user_msg, **kwargs)


def run_statistical_extraction(paper_text: str, experiment: dict, stat_source: dict, **kwargs) -> str:
    """Run statistical extraction prompt for an ANOVA table."""
    user_msg = (
        f"Full paper:\n\n{paper_text}\n\n"
        f"---\n\n"
        f"Experiment: {experiment.get('label', '')}\n"
        f"Source: {stat_source['source_id']}\n"
        f"Analysis type: {stat_source.get('type', 'anova')}\n"
        f"Response variables: {stat_source.get('response_variables', [])}\n"
    )
    return call_llm(statistical_extraction, user_msg, **kwargs)
