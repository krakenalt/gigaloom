"""Provider command metadata and handlers."""

from __future__ import annotations

import argparse


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    provider = subparsers.add_parser("provider")
    provider_subparsers = provider.add_subparsers(dest="provider_command")
    provider_list = provider_subparsers.add_parser("list")
    provider_list.add_argument("--json", action="store_true")
    provider_list.set_defaults(handler="_handle_provider_list")
    provider_show = provider_subparsers.add_parser("show")
    provider_show.add_argument("provider_id")
    provider_show.add_argument("--json", action="store_true")
    provider_show.set_defaults(handler="_handle_provider_show")
    provider_add = provider_subparsers.add_parser("add")
    provider_add.add_argument("provider_id")
    provider_add.add_argument("--name", required=True)
    provider_add.add_argument(
        "--protocol",
        required=True,
        choices=("openai_compatible", "anthropic_compatible", "gemini_compatible"),
    )
    provider_add.add_argument("--dialect", default=None)
    provider_add.add_argument("--base-url", required=True)
    provider_add.add_argument("--route-prefix", default=None)
    _add_provider_auth_arguments(provider_add, optional=False)
    _add_provider_model_arguments(provider_add)
    provider_add.add_argument("--offline", action="store_true")
    provider_add.add_argument("--disabled", action="store_true")
    provider_add.add_argument("--json", action="store_true")
    provider_add.set_defaults(handler="_handle_provider_add")
    provider_edit = provider_subparsers.add_parser("edit")
    provider_edit.add_argument("provider_id")
    provider_edit.add_argument("--expected-revision", type=int, required=True)
    provider_edit.add_argument("--name", default=None)
    provider_edit.add_argument(
        "--protocol",
        choices=("openai_compatible", "anthropic_compatible", "gemini_compatible"),
        default=None,
    )
    provider_edit.add_argument("--dialect", default=None)
    provider_edit.add_argument("--base-url", default=None)
    provider_edit.add_argument("--route-prefix", default=None)
    _add_provider_auth_arguments(provider_edit, optional=True)
    _add_provider_model_arguments(provider_edit)
    provider_edit.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    provider_edit.add_argument(
        "--offline",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    provider_edit.add_argument("--json", action="store_true")
    provider_edit.set_defaults(handler="_handle_provider_edit")
    for command, handler in (
        ("test", "_handle_provider_test"),
        ("discover", "_handle_provider_discover"),
    ):
        provider_probe = provider_subparsers.add_parser(command)
        provider_probe.add_argument("provider_id")
        provider_probe.add_argument("--json", action="store_true")
        provider_probe.set_defaults(handler=handler)
    provider_migrate = provider_subparsers.add_parser(
        "migrate-legacy", aliases=("migrate",)
    )
    provider_migrate.add_argument("--backup", default=None)
    provider_migrate.add_argument("--dry-run", action="store_true")
    provider_migrate.add_argument("--json", action="store_true")
    provider_migrate.set_defaults(handler="_handle_provider_migrate")


def _add_provider_auth_arguments(
    parser: argparse.ArgumentParser,
    *,
    optional: bool,
) -> None:
    parser.add_argument(
        "--authentication",
        choices=("secret_reference", "provider_native", "none"),
        default=None if optional else "secret_reference",
    )
    parser.add_argument(
        "--secret-reference-kind",
        choices=("environment", "keychain"),
        default=None if optional else "environment",
    )
    parser.add_argument("--secret-reference-name", default=None)
    parser.add_argument("--keychain-service", default=None)
    parser.add_argument("--keychain-account", default=None)


def _add_provider_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coding-model", default=None)
    parser.add_argument("--title-model", default=None)
    parser.add_argument("--evaluation-model", default=None)
    parser.add_argument("--fallback-model", default=None)
