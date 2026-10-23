"""Advisor agents (CPA, Marketing) that answer questions from the Redshift marts.

Self-contained package so it doesn't collide with the team's agents/ skeleton.
Import with:  from advisors import build_advisor, PERSONAS
"""
from .personas import PERSONAS, build_advisor  # noqa: F401
