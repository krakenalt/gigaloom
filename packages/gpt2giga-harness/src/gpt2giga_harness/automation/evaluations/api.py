"""Public evals automation facade."""

from .constants import ADAPTER_COMPATIBILITY_CHECKS as ADAPTER_COMPATIBILITY_CHECKS
from .constants import CHECK_TYPES as CHECK_TYPES
from .constants import DEFAULT_EVAL_HARNESSES as DEFAULT_EVAL_HARNESSES
from .constants import EVALS_RELATIVE_DIR as EVALS_RELATIVE_DIR
from .constants import EVAL_BASELINES_DIR as EVAL_BASELINES_DIR
from .constants import EVAL_NAME_RE as EVAL_NAME_RE
from .constants import EVAL_RUNS_DIR as EVAL_RUNS_DIR
from .models import EvalCaseRunResult as EvalCaseRunResult
from .models import EvalCaseSpec as EvalCaseSpec
from .models import EvalCheckResult as EvalCheckResult
from .models import EvalCheckSpec as EvalCheckSpec
from .models import EvalRunNotFoundError as EvalRunNotFoundError
from .models import EvalSpecLoadError as EvalSpecLoadError
from .models import EvalSpecNotFoundError as EvalSpecNotFoundError
from .store import FilesystemHarnessEvalStore as FilesystemHarnessEvalStore
from .models import HarnessEvalRun as HarnessEvalRun
from .models import HarnessEvalSpec as HarnessEvalSpec
from .constants import MAX_EVAL_REPETITIONS as MAX_EVAL_REPETITIONS
from .constants import PROTOCOL_CONFORMANCE_FIXTURES as PROTOCOL_CONFORMANCE_FIXTURES
from .reports import adapter_compatibility_matrix as adapter_compatibility_matrix
from .reports import compare_eval_run_to_baseline as compare_eval_run_to_baseline
from .store import discover_eval_specs as discover_eval_specs
from .codec import eval_case_result_from_dict as eval_case_result_from_dict
from .codec import eval_case_result_to_dict as eval_case_result_to_dict
from .codec import eval_check_result_from_dict as eval_check_result_from_dict
from .codec import eval_check_result_to_dict as eval_check_result_to_dict
from .reports import eval_compatibility_matrix as eval_compatibility_matrix
from .codec import eval_run_from_dict as eval_run_from_dict
from .codec import eval_run_to_dict as eval_run_to_dict
from .codec import eval_spec_from_mapping as eval_spec_from_mapping
from .codec import eval_spec_load_error_to_dict as eval_spec_load_error_to_dict
from .codec import eval_spec_to_dict as eval_spec_to_dict
from .checks import evaluate_checks as evaluate_checks
from .store import load_eval_spec as load_eval_spec
from .reports import protocol_conformance_matrix as protocol_conformance_matrix
from .execution import queue_eval as queue_eval
from .execution import run_eval as run_eval
from .execution import sync_durable_eval_case as sync_durable_eval_case

__all__ = [
    "ADAPTER_COMPATIBILITY_CHECKS",
    "CHECK_TYPES",
    "DEFAULT_EVAL_HARNESSES",
    "EVALS_RELATIVE_DIR",
    "EVAL_BASELINES_DIR",
    "EVAL_NAME_RE",
    "EVAL_RUNS_DIR",
    "EvalCaseRunResult",
    "EvalCaseSpec",
    "EvalCheckResult",
    "EvalCheckSpec",
    "EvalRunNotFoundError",
    "EvalSpecLoadError",
    "EvalSpecNotFoundError",
    "FilesystemHarnessEvalStore",
    "HarnessEvalRun",
    "HarnessEvalSpec",
    "MAX_EVAL_REPETITIONS",
    "PROTOCOL_CONFORMANCE_FIXTURES",
    "adapter_compatibility_matrix",
    "compare_eval_run_to_baseline",
    "discover_eval_specs",
    "eval_case_result_from_dict",
    "eval_case_result_to_dict",
    "eval_check_result_from_dict",
    "eval_check_result_to_dict",
    "eval_compatibility_matrix",
    "eval_run_from_dict",
    "eval_run_to_dict",
    "eval_spec_from_mapping",
    "eval_spec_load_error_to_dict",
    "eval_spec_to_dict",
    "evaluate_checks",
    "load_eval_spec",
    "protocol_conformance_matrix",
    "queue_eval",
    "run_eval",
    "sync_durable_eval_case",
]
