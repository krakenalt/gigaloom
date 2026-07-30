"""Store for the evals subcontext."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
import yaml
from gigaloom.projects.api import HarnessProject
from gigaloom.automation.ports import exclusive_file_lock
from gigaloom.automation.ports import utc_now
from .checks import (
    _eval_run_status as _eval_run_status,
    _eval_spec_paths as _eval_spec_paths,
    _eval_summary as _eval_summary,
    _resolve_eval_spec_path as _resolve_eval_spec_path,
)
from .codec import (
    _mapping as _mapping,
    _read_json as _read_json,
    _safe_eval_name as _safe_eval_name,
    _write_json_atomic as _write_json_atomic,
    eval_case_result_to_dict as eval_case_result_to_dict,
    eval_run_from_dict as eval_run_from_dict,
    eval_run_to_dict as eval_run_to_dict,
    eval_spec_from_mapping as eval_spec_from_mapping,
)
from .constants import (
    EVAL_BASELINES_DIR as EVAL_BASELINES_DIR,
    EVAL_RUNS_DIR as EVAL_RUNS_DIR,
)
from .models import (
    EvalCaseRunResult as EvalCaseRunResult,
    EvalRunNotFoundError as EvalRunNotFoundError,
    EvalSpecLoadError as EvalSpecLoadError,
    EvalSpecNotFoundError as EvalSpecNotFoundError,
    HarnessEvalRun as HarnessEvalRun,
    HarnessEvalSpec as HarnessEvalSpec,
)
from .reports import (
    _eval_config_hash as _eval_config_hash,
    _git_sha as _git_sha,
    _result_identity as _result_identity,
)


class FilesystemHarnessEvalStore:
    """Persist eval scorecards under a project state directory."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()

    def save(self, project: HarnessProject, eval_run: HarnessEvalRun) -> HarnessEvalRun:
        """Persist one eval run."""
        directory = self._project_runs_dir(project)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{eval_run.id}.json"
        with exclusive_file_lock(path):
            _write_json_atomic(path, eval_run_to_dict(eval_run))
        return eval_run

    def upsert_result(
        self, eval_run_id: str, result: EvalCaseRunResult
    ) -> HarnessEvalRun:
        """Process-safely replace one case/harness result and recompute summary."""
        path = self._find_run_path(eval_run_id)
        with exclusive_file_lock(path):
            eval_run = eval_run_from_dict(_read_json(path))
            results = [
                item
                for item in eval_run.results
                if _result_identity(item) != _result_identity(result)
            ]
            results.append(result)
            order = {
                _result_identity(item): index
                for index, item in enumerate(eval_run.results)
            }
            results.sort(key=lambda item: order.get(_result_identity(item), len(order)))
            updated = replace(
                eval_run,
                status=_eval_run_status(results, expected_count=len(results)),
                updated_at=utc_now(),
                results=tuple(results),
                summary=_eval_summary(results),
            )
            _write_json_atomic(path, eval_run_to_dict(updated))
        return updated

    def get(self, project: HarnessProject, eval_run_id: str) -> HarnessEvalRun:
        """Return one eval run for a project."""
        path = self._project_runs_dir(project) / f"{eval_run_id}.json"
        try:
            return eval_run_from_dict(_read_json(path))
        except FileNotFoundError as exc:
            raise EvalRunNotFoundError(eval_run_id) from exc

    def get_any(self, eval_run_id: str) -> HarnessEvalRun:
        """Return an eval run by scanning project eval-run directories."""
        for path in sorted(
            self.data_dir.glob(f"projects/*/{EVAL_RUNS_DIR}/{eval_run_id}.json")
        ):
            try:
                return eval_run_from_dict(_read_json(path))
            except (OSError, ValueError):
                continue
        raise EvalRunNotFoundError(eval_run_id)

    def list_runs(
        self,
        project: HarnessProject,
        *,
        limit: int = 20,
    ) -> tuple[HarnessEvalRun, ...]:
        """List recent eval runs for a project."""
        runs: list[HarnessEvalRun] = []
        for path in sorted(self._project_runs_dir(project).glob("*.json")):
            try:
                runs.append(eval_run_from_dict(_read_json(path)))
            except (OSError, ValueError):
                continue
        runs.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(runs[: max(limit, 0)])

    def pin_baseline(
        self, project: HarnessProject, eval_run: HarnessEvalRun
    ) -> Mapping[str, Any]:
        """Pin an immutable scorecard snapshot with source/config identities."""
        directory = Path(project.state_dir).expanduser() / EVAL_BASELINES_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_safe_eval_name(eval_run.spec_name)}.json"
        payload = {
            "spec_name": eval_run.spec_name,
            "eval_run_id": eval_run.id,
            "pinned_at": utc_now(),
            "git_sha": _git_sha(project.root),
            "config_hash": _eval_config_hash(eval_run),
            "api_mode": eval_run.api_mode.value,
            "adapter_dimensions": list(eval_run.metadata.get("adapter_dimensions", ())),
            "summary": dict(eval_run.summary),
            "results": [eval_case_result_to_dict(item) for item in eval_run.results],
        }
        with exclusive_file_lock(path):
            _write_json_atomic(path, payload)
        return payload

    def get_baseline(
        self, project: HarnessProject, spec_name: str
    ) -> Mapping[str, Any] | None:
        """Return the pinned baseline for one eval spec, if present."""
        path = (
            Path(project.state_dir).expanduser()
            / EVAL_BASELINES_DIR
            / f"{_safe_eval_name(spec_name)}.json"
        )
        try:
            return _read_json(path)
        except FileNotFoundError:
            return None

    def _project_runs_dir(self, project: HarnessProject) -> Path:
        return Path(project.state_dir).expanduser() / EVAL_RUNS_DIR

    def _find_run_path(self, eval_run_id: str) -> Path:
        paths = sorted(
            self.data_dir.glob(f"projects/*/{EVAL_RUNS_DIR}/{eval_run_id}.json")
        )
        if not paths:
            raise EvalRunNotFoundError(eval_run_id)
        return paths[0]


def discover_eval_specs(
    project_root: str | Path,
) -> tuple[tuple[HarnessEvalSpec, ...], tuple[EvalSpecLoadError, ...]]:
    """Load all project eval specs, collecting parse errors safely."""
    specs: list[HarnessEvalSpec] = []
    errors: list[EvalSpecLoadError] = []
    for path in _eval_spec_paths(project_root):
        try:
            specs.append(load_eval_spec(project_root, path.stem))
        except (OSError, ValueError) as exc:
            errors.append(EvalSpecLoadError(path=str(path), message=str(exc)))
    specs.sort(key=lambda spec: spec.name)
    return tuple(specs), tuple(errors)


def load_eval_spec(project_root: str | Path, name: str) -> HarnessEvalSpec:
    """Load one project eval spec by safe name."""
    spec_name = _safe_eval_name(name)
    path = _resolve_eval_spec_path(project_root, spec_name)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalSpecNotFoundError(spec_name) from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid eval YAML: {path.name}") from exc
    return eval_spec_from_mapping(_mapping(data), path=path)
