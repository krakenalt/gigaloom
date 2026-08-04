"""Route-local parser for explicit product evidence export."""

from __future__ import annotations

import argparse


def register(
    subparsers: argparse._SubParsersAction,
    _common: argparse.ArgumentParser,
) -> None:
    """Register `giga evidence product-beta` without root composition."""
    evidence = subparsers.add_parser("evidence")
    evidence_subparsers = evidence.add_subparsers(dest="evidence_command")
    product_beta = evidence_subparsers.add_parser("product-beta")
    product_beta.add_argument("--project", dest="project_id", required=True)
    product_beta.add_argument("--output", required=True)
    product_beta.add_argument("--since", default=None)
    product_beta.add_argument("--until", default=None)
    product_beta.add_argument("--json", action="store_true")
    product_beta.set_defaults(handler="_handle_product_beta_evidence")


__all__ = ["register"]
