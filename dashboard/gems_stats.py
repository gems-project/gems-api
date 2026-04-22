"""statsmodels wrappers for the Modeling page.

The AI never does the math — this module (backed by statsmodels/numpy/scipy)
produces every number the Modeling page shows or sends to the LLM for
interpretation. The LLM only receives a pre-computed summary dict.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


# ---------------------------------------------------------------------------
# Formula builders
# ---------------------------------------------------------------------------


def _quote(term: str) -> str:
    """Wrap a column name in Q(...) so patsy tolerates odd characters."""
    return f'Q("{term}")'


def build_formula(response: str, predictors: list[str], intercept: bool = True) -> str:
    """Build a patsy formula for OLS or the fixed part of a mixed model."""
    if not response:
        raise ValueError("Response variable is required.")
    rhs = " + ".join(_quote(p) for p in predictors) if predictors else "1"
    if not intercept:
        rhs = "0 + " + rhs
    return f"{_quote(response)} ~ {rhs}"


def build_re_formula(random_slopes: list[str] | None) -> str | None:
    """Build a random-effects formula: random intercept + optional random slopes."""
    if not random_slopes:
        return None
    parts = ["1"] + [_quote(s) for s in random_slopes]
    return "~ " + " + ".join(parts)


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------


def fit_ols(df: pd.DataFrame, formula: str):
    return smf.ols(formula=formula, data=df).fit()


def fit_mixedlm(
    df: pd.DataFrame,
    formula: str,
    group_col: str,
    re_formula: str | None = None,
):
    """Backwards-compatible single-grouping-factor MixedLM fit."""
    if group_col not in df.columns:
        raise ValueError(f"Grouping column '{group_col}' not in dataframe")
    return smf.mixedlm(
        formula=formula,
        data=df,
        groups=df[group_col],
        re_formula=re_formula,
    ).fit(method="lbfgs")


def _make_group_args(
    df: pd.DataFrame,
    group_cols: list[str],
    nested: bool,
) -> tuple[pd.DataFrame, pd.Series, dict[str, str] | None]:
    """Prepare ``(data, groups, vc_formula)`` for a possibly multi-grouping MixedLM.

    * ``len(group_cols) == 1`` — single grouping factor, no variance components.
    * ``nested == True`` with N ≥ 2 factors — the first column is the primary
      group; every subsequent factor is treated as nested within it. To keep
      inner IDs globally unique (so an animal named "A1" in study 1 is not
      conflated with animal "A1" in study 2), we build synthetic
      concatenated id columns on the fly and expose them as variance
      components.
    * ``nested == False`` (crossed) with N ≥ 2 factors — every factor becomes
      its own variance component. Because statsmodels requires a grouping
      series, we use a constant "all rows" column as the pseudo-group.
    """
    if not group_cols:
        raise ValueError("At least one grouping column is required for MixedLM.")

    if len(group_cols) == 1:
        c = group_cols[0]
        if c not in df.columns:
            raise ValueError(f"Grouping column '{c}' not in dataframe.")
        return df, df[c], None

    df = df.copy()
    for c in group_cols:
        if c not in df.columns:
            raise ValueError(f"Grouping column '{c}' not in dataframe.")

    if nested:
        primary = group_cols[0]
        vc: dict[str, str] = {}
        concat = df[primary].astype(str)
        for c in group_cols[1:]:
            concat = concat + "__" + df[c].astype(str)
            synth = f"__nested__{c}"
            df[synth] = concat.values
            vc[c] = f"0 + C({synth})"
        return df, df[primary], vc

    const_col = "__gems_allrows__"
    df[const_col] = 1
    vc = {c: f"0 + C({c})" for c in group_cols}
    return df, df[const_col], vc


def fit_mixedlm_multi(
    df: pd.DataFrame,
    formula: str,
    group_cols: list[str],
    nested: bool = True,
    re_formula: str | None = None,
):
    """Fit a MixedLM with one, multiple nested, or multiple crossed groupings.

    Random slopes (``re_formula``) are applied to the **first** grouping
    variable only, and are silently ignored in crossed mode (where the
    primary group is artificial).
    """
    augmented, groups, vc = _make_group_args(df, group_cols, nested)

    kwargs: dict[str, Any] = {
        "formula": formula,
        "data": augmented,
        "groups": groups,
    }
    if vc:
        kwargs["vc_formula"] = vc
    crossed = bool(vc) and not nested
    if re_formula and not crossed:
        kwargs["re_formula"] = re_formula

    return smf.mixedlm(**kwargs).fit(method="lbfgs")


# ---------------------------------------------------------------------------
# Fit statistics
# ---------------------------------------------------------------------------


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def fit_statistics(model, kind: str) -> dict:
    """Goodness-of-fit metrics shown prominently on the Modeling page.

    For OLS: R², adj R², F-statistic + p-value, log-likelihood, AIC, BIC,
             number of observations, residual df, model df.
    For MixedLM: no R² (not well-defined); conditional R² via Nakagawa method
             is reported as a pseudo-R² when it can be computed.
    """
    out: dict[str, float | int | None] = {
        "nobs": _safe_float(getattr(model, "nobs", None)),
        "df_model": _safe_float(getattr(model, "df_model", None)),
        "df_resid": _safe_float(getattr(model, "df_resid", None)),
        "llf": _safe_float(getattr(model, "llf", None)),
        "aic": _safe_float(getattr(model, "aic", None)),
        "bic": _safe_float(getattr(model, "bic", None)),
    }

    if kind == "OLS":
        out["rsquared"] = _safe_float(getattr(model, "rsquared", None))
        out["rsquared_adj"] = _safe_float(getattr(model, "rsquared_adj", None))
        out["fvalue"] = _safe_float(getattr(model, "fvalue", None))
        out["f_pvalue"] = _safe_float(getattr(model, "f_pvalue", None))
        out["scale"] = _safe_float(getattr(model, "scale", None))
    else:
        out["scale"] = _safe_float(getattr(model, "scale", None))
        try:
            marginal_r2, conditional_r2 = _mixed_pseudo_r2(model)
            out["pseudo_r2_marginal"] = marginal_r2
            out["pseudo_r2_conditional"] = conditional_r2
        except Exception:
            out["pseudo_r2_marginal"] = None
            out["pseudo_r2_conditional"] = None

    return out


def _mixed_pseudo_r2(model) -> tuple[float | None, float | None]:
    """Nakagawa & Schielzeth (2013) pseudo-R² for a Gaussian MixedLM.

    Marginal R²   = Var(X β)          / (Var(X β) + Var(Z u) + σ²_ε)
    Conditional R² = (Var(X β) + Var(Z u)) / (Var(X β) + Var(Z u) + σ²_ε)

    Where:
      * Var(X β)  — variance across observations of the fixed-effect
        linear predictor. Computed directly from the design matrix and
        fitted fixed-effect coefficients (NOT from fittedvalues, which
        for MixedLM already includes the random-effect contribution).
      * Var(Z u) — expected variance contributed by the random effects.
        For a random intercept only, this reduces to the intercept
        variance. With random slopes we use the trace formulation
        tr( (Zᵀ Z / n) · Σ_u ), which matches Nakagawa's approach.
      * σ²_ε    — residual variance (`model.scale`).

    Returns (marginal, conditional), or (None, None) if anything is
    unavailable (e.g. pre-fit model, singular design, missing cov_re).
    """
    try:
        exog = np.asarray(model.model.exog)
        fe_params = np.asarray(model.fe_params)
        fixed_pred = exog @ fe_params
        var_fixed = float(np.var(fixed_pred, ddof=0))
    except Exception:
        return None, None

    var_re = 0.0
    try:
        cov_re_raw = getattr(model, "cov_re", None)
        if cov_re_raw is not None:
            cov_re = (
                np.asarray(cov_re_raw.values)
                if hasattr(cov_re_raw, "values")
                else np.asarray(cov_re_raw)
            )
            exog_re = getattr(model.model, "exog_re", None)
            if exog_re is not None and np.asarray(exog_re).size > 0:
                Z = np.asarray(exog_re)
                n = Z.shape[0] if Z.ndim == 2 else len(Z)
                if n > 0:
                    ZtZ_over_n = (Z.T @ Z) / n
                    var_re = float(np.trace(ZtZ_over_n @ cov_re))
                else:
                    var_re = float(np.trace(cov_re))
            else:
                var_re = float(np.trace(cov_re))
    except Exception:
        var_re = 0.0

    # Add any additional variance components (nested / crossed intercepts).
    try:
        vcomp = getattr(model, "vcomp", None)
        if vcomp is not None:
            vcomp_arr = np.asarray(vcomp, dtype=float)
            if vcomp_arr.size > 0:
                var_re += float(np.nansum(vcomp_arr))
    except Exception:
        pass

    try:
        var_resid = float(model.scale)
    except Exception:
        return None, None

    total = var_fixed + var_re + var_resid
    if total <= 0 or not np.isfinite(total):
        return None, None
    marginal = var_fixed / total
    conditional = (var_fixed + var_re) / total
    return _safe_float(marginal), _safe_float(conditional)


# ---------------------------------------------------------------------------
# Coefficient table + JSON-safe summary for the LLM
# ---------------------------------------------------------------------------


def coefficient_table(model) -> pd.DataFrame:
    """Full coefficient table (term, estimate, SE, z/t, p-value, CI)."""
    rows: list[dict] = []
    params = model.params
    try:
        conf = model.conf_int()
    except Exception:
        conf = pd.DataFrame(index=params.index, columns=[0, 1])
    se = getattr(model, "bse", None)
    pvals = getattr(model, "pvalues", None)
    tvals = getattr(model, "tvalues", None)

    for idx in params.index:
        term = str(idx)
        if term.startswith('Q("') and term.endswith('")'):
            pretty = term[3:-2]
        else:
            pretty = (
                term.replace('Q("', "").replace('"):', ":").replace('")', "")
            )
        rows.append(
            {
                "term": pretty,
                "estimate": _safe_float(params.loc[idx]),
                "std_error": _safe_float(se.loc[idx]) if se is not None else None,
                "t_or_z": _safe_float(tvals.loc[idx]) if tvals is not None else None,
                "p_value": _safe_float(pvals.loc[idx]) if pvals is not None else None,
                "ci_low": _safe_float(conf.loc[idx, 0]) if idx in conf.index else None,
                "ci_high": _safe_float(conf.loc[idx, 1]) if idx in conf.index else None,
            }
        )
    return pd.DataFrame(rows)


def variance_components(model) -> list[dict]:
    """Flat list of every variance component (intercept, slopes, vc_formula)."""
    rows: list[dict] = []
    try:
        cov_re = getattr(model, "cov_re", None)
        if cov_re is not None:
            mat = (
                np.asarray(cov_re.values)
                if hasattr(cov_re, "values")
                else np.asarray(cov_re)
            )
            names: list[str]
            if hasattr(cov_re, "index"):
                names = [str(x) for x in list(cov_re.index)]
            else:
                names = [f"re[{i}]" for i in range(mat.shape[0])]
            for i, name in enumerate(names):
                pretty = name
                if pretty.startswith('Q("') and pretty.endswith('")'):
                    pretty = pretty[3:-2]
                rows.append(
                    {
                        "name": pretty,
                        "kind": "primary",
                        "variance": _safe_float(mat[i, i]),
                        "std_dev": _safe_float(float(mat[i, i]) ** 0.5)
                        if mat[i, i] is not None and float(mat[i, i]) >= 0
                        else None,
                    }
                )
    except Exception:
        pass

    try:
        vcomp = getattr(model, "vcomp", None)
        if vcomp is not None:
            vc_names: list[str] = []
            for attr in ("vc_names", "_vc_names", "exog_vc_names"):
                v = getattr(model, attr, None) or getattr(model.model, attr, None)
                if v is not None:
                    vc_names = list(v)
                    break
            vcomp_arr = np.asarray(vcomp, dtype=float)
            for i, v in enumerate(vcomp_arr):
                name = vc_names[i] if i < len(vc_names) else f"vc[{i}]"
                rows.append(
                    {
                        "name": name,
                        "kind": "component",
                        "variance": _safe_float(v),
                        "std_dev": _safe_float(float(v) ** 0.5) if v >= 0 else None,
                    }
                )
    except Exception:
        pass

    try:
        scale = _safe_float(getattr(model, "scale", None))
        if scale is not None:
            rows.append(
                {
                    "name": "residual",
                    "kind": "residual",
                    "variance": scale,
                    "std_dev": _safe_float(scale ** 0.5) if scale >= 0 else None,
                }
            )
    except Exception:
        pass

    return rows


def summary_dict(model, kind: str) -> dict:
    """JSON-safe dict passed to the AI for plain-language interpretation."""
    out: dict = {"kind": kind, "fit_statistics": fit_statistics(model, kind)}
    try:
        coefs = coefficient_table(model)
        out["coefficients"] = coefs.to_dict(orient="records")
    except Exception as e:
        out["coefficients_error"] = str(e)

    if kind == "MixedLM":
        try:
            out["variance_components"] = variance_components(model)
        except Exception:
            pass

    try:
        if hasattr(model, "cov_re") and model.cov_re is not None:
            cov = model.cov_re
            if hasattr(cov, "to_dict"):
                out["random_effect_cov"] = cov.to_dict()
            else:
                out["random_effect_cov"] = {"matrix": np.asarray(cov).tolist()}
    except Exception:
        pass

    return out
