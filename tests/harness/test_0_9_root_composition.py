"""Integrator-owned root registration for public foundation surfaces."""

import json

from gigaloom.cli_commands.main import main
from gigaloom.cli_commands.parser import build_parser
from gigaloom.sessions import FilesystemHarnessSessionStore


def test_gateway_schema_and_thread_relay_are_registered_at_root() -> None:
    parser = build_parser()

    assert parser.parse_args(["gateway", "list", "--json"]).handler == (
        "_handle_gateway_list"
    )
    assert parser.parse_args(["schema", "list", "--json"]).handler == (
        "_handle_schema_list"
    )
    threads = parser.parse_args(["session", "threads", "--json"])
    assert threads.handler == "_handle_thread_list"
    assert threads.project_id is None


def test_root_commands_reach_production_services(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(tmp_path))
    session = FilesystemHarnessSessionStore(tmp_path).create_session(
        title="Bound thread",
        metadata={"catalog_project_id": "project-1"},
    )

    assert main(["gateway", "list", "--json"]) == 0
    gateways = json.loads(capsys.readouterr().out)
    assert [item["gateway_id"] for item in gateways["gateways"]] == ["gpt2giga"]

    assert main(["schema", "list", "--json"]) == 0
    schemas = json.loads(capsys.readouterr().out)
    assert "agent" in json.dumps(schemas)

    assert (
        main(
            [
                "session",
                "threads",
                "--project-id",
                "project-1",
                "--json",
            ]
        )
        == 0
    )
    threads = json.loads(capsys.readouterr().out)
    assert [item["locator"]["thread_id"] for item in threads["threads"]] == [session.id]
