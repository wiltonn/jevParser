"""Rulesets: the data that adapts the pipeline to an OEM and model-year range."""

from .context import DEFAULT_CONTEXT, PromptContext
from .model import RuleSet


def default_rules() -> RuleSet:
    from .default_gm import DEFAULT_GM

    return DEFAULT_GM


def built_in() -> dict[str, RuleSet]:
    """Every ruleset shipped with the package, by short name."""
    from .default_ford import DEFAULT_FORD_PDF
    from .default_gm import DEFAULT_GM

    return {"gm": DEFAULT_GM, "ford-pdf": DEFAULT_FORD_PDF}


#: Short name -> the seed file generated from it (``jev-diff rules export-default``).
SEED_FILES = {"gm": "gm_default.json", "ford-pdf": "ford_pdf.json"}


def load(path) -> RuleSet:
    from pathlib import Path

    return RuleSet.from_json(Path(path).read_text(encoding="utf-8"))


__all__ = ["DEFAULT_CONTEXT", "PromptContext", "RuleSet", "SEED_FILES", "built_in",
           "default_rules", "load"]
