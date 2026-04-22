"""Two small OpenAI helpers: interpret a chart, interpret a model."""

from __future__ import annotations

import os
from functools import lru_cache

_PLOT_SYSTEM = (
    "You are a concise data analyst assisting livestock/animal-science researchers.\n"
    "Given a chart description and summary statistics (no raw rows), write 3-6 short\n"
    "bullet points interpreting the visualization. Call out caveats about sample size,\n"
    "multiple comparisons, and causal language. Never invent values that are not in the\n"
    "provided context."
)

_MODEL_SYSTEM = (
    "You are a statistician assisting livestock/animal-science researchers.\n"
    "Given a model specification, coefficient table (estimates, CIs, p-values), and fit\n"
    "statistics (R-squared, AIC, random-effect variances for LMM), produce a plain-English\n"
    "interpretation in 4-8 bullet points. Flag the usual assumptions (linearity, residual\n"
    "normality, independence), and warn against overreach (causal claims without designed\n"
    "experiments, multiple-testing concerns). Do not invent values."
)


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=key)


def _model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _chat(system: str, user: str) -> str:
    resp = _client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def interpret_plot(context: str) -> str:
    return _chat(_PLOT_SYSTEM, context)


def interpret_model(context: str) -> str:
    return _chat(_MODEL_SYSTEM, context)
