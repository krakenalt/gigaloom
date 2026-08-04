from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.cli_commands.commands.product_evidence import register
from gigaloom.cli_commands.handlers.product_evidence import (
    _handle_product_beta_evidence,
)
from gigaloom.contracts.product_evidence import ProductEvidenceReportV1
from gigaloom.review.product_evidence import export_product_evidence_report
from gigaloom.ui.routers.product_evidence import create_router


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def _report() -> ProductEvidenceReportV1:
    return ProductEvidenceReportV1(
        report_id="product-beta-export",
        project_id="project-1",
        range_start=NOW - timedelta(days=1),
        range_end=NOW,
        generated_at=NOW,
        metrics=(),
        sources=(),
        omissions=("gateway", "sidecar", "thread_relay"),
    )


def test_local_export_is_deterministic_private_and_atomic(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "report.json"

    assert export_product_evidence_report(_report(), output) == output
    first = output.read_bytes()
    assert export_product_evidence_report(_report(), output) == output

    assert output.read_bytes() == first
    assert json.loads(first)["local_only"] is True
    assert output.stat().st_mode & 0o077 == 0
    assert list(output.parent.glob(".*.tmp")) == []


def test_route_local_cli_parser_matches_canonical_command() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register(subparsers, argparse.ArgumentParser(add_help=False))

    args = parser.parse_args(
        [
            "evidence",
            "product-beta",
            "--project",
            "project-1",
            "--output",
            "report.json",
        ]
    )

    assert args.handler == "_handle_product_beta_evidence"
    assert args.project_id == "project-1"
    assert args.output == "report.json"


def test_route_local_cli_handler_writes_only_explicit_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class Application:
        def report(self, **kwargs: object) -> ProductEvidenceReportV1:
            assert kwargs["project_id"] == "project-1"
            return _report()

    monkeypatch.setattr(
        "gigaloom.cli_commands.handlers.product_evidence.ProductEvidenceApplication.from_data_dir",
        lambda _data_dir: Application(),
    )
    output = tmp_path / "report.json"
    range_end = datetime.now(timezone.utc) - timedelta(minutes=1)
    args = argparse.Namespace(
        project_id="project-1",
        output=str(output),
        since=(range_end - timedelta(days=1)).isoformat(),
        until=range_end.isoformat(),
        json=True,
    )

    result = _handle_product_beta_evidence(
        args,
        SimpleNamespace(data_dir=tmp_path),
    )

    assert result == 0
    assert output.is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["uploaded"] is False
    assert payload["output"] == str(output)


def test_route_local_api_returns_report_without_upload() -> None:
    class Application:
        def report(self, **kwargs: object) -> ProductEvidenceReportV1:
            assert kwargs["project_id"] == "project-1"
            return _report()

    app = FastAPI()
    app.include_router(create_router(Application()))

    response = TestClient(app).get(
        "/api/evidence/product-beta",
        params={"project_id": "project-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["content_free"] is True
    assert payload["local_only"] is True
    assert payload["uploaded"] is False
    assert len(payload["report_sha256"]) == 64


def test_route_local_api_rejects_naive_time_range() -> None:
    app = FastAPI()
    app.include_router(
        create_router(SimpleNamespace(report=lambda **_kwargs: _report()))
    )

    response = TestClient(app).get(
        "/api/evidence/product-beta",
        params={
            "project_id": "project-1",
            "since": "2026-08-01T00:00:00",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "range timestamps must include a timezone"
