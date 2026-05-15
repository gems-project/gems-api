"""Two small LLM helpers: interpret a chart, interpret a model."""

from __future__ import annotations

from llm_client import get_llm_client, get_llm_model

_PLOT_SYSTEM = (
    "You are a concise data analyst assisting livestock/animal-science researchers.\n"
    "Given a chart description and summary statistics (no raw rows), return Markdown with:\n"
    "1) a short level-3 summary header, 2) 3-6 bullet key points interpreting the visualization,\n"
    "and 3) a closing italic line beginning with 'Caveats:'. Call out sample size,\n"
    "multiple comparisons, and causal language. Never invent values that are not in the provided context."
)

_MODEL_SYSTEM = (
    "You are a statistician assisting livestock/animal-science researchers.\n"
    "Given a model specification, coefficient table (estimates, CIs, p-values), and fit\n"
    "statistics (R-squared, AIC, random-effect variances for LMM), return Markdown with:\n"
    "1) a short level-3 summary header, 2) 3-6 bullet key points in plain English,\n"
    "and 3) a closing italic line beginning with 'Caveats:'. Flag the usual assumptions\n"
    "(linearity, residual normality, independence), and warn against overreach (causal claims\n"
    "without designed experiments, multiple-testing concerns). Do not invent values."
)


def _chat(system: str, user: str) -> str:
    resp = get_llm_client().chat.completions.create(
        model=get_llm_model(),
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
