#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment="${GIGALOOM_VENV:-${repository_root}/.venv}"
python="${environment}/bin/python"

if [[ ! -x "${python}" ]]; then
  echo "run ./scripts/ci-base.sh sync-all-extras first" >&2
  exit 1
fi

cd "${repository_root}"
"${python}" -I - <<'PY'
import importlib.metadata
from pathlib import Path
import tomllib

from gigaloom.gpt2giga_preset import require_gpt2giga_preset

repository_root = Path.cwd()
with (repository_root / "uv.lock").open("rb") as file:
    lock = tomllib.load(file)
with (repository_root / "pyproject.toml").open("rb") as file:
    metadata = tomllib.load(file)

expected_requirement = metadata["project"]["optional-dependencies"]["gpt2giga"][0]
assert expected_requirement == "gpt2giga>=0.2.6,<0.3.0"
assert "sources" not in metadata.get("tool", {}).get("uv", {})

packages = {package["name"]: package for package in lock["package"]}
expected_version = packages["gpt2giga"]["version"]
assert expected_version == "0.2.6"
assert packages["gpt2giga"]["version"] == expected_version
assert packages["gpt2giga"]["source"] == {"registry": "https://pypi.org/simple"}
assert packages["gigachat"]["version"] == "0.2.3"
assert packages["gigachat"]["source"] == {"registry": "https://pypi.org/simple"}
assert packages["gigaloom"]["source"] == {
    "editable": "."
}
for name, package in packages.items():
    if name == "gigaloom":
        continue
    assert package["source"] == {"registry": "https://pypi.org/simple"}, (
        name,
        package["source"],
    )

assert importlib.metadata.version("gpt2giga") == expected_version
runtime = require_gpt2giga_preset()
assert runtime.client_type.__module__.split(".", 1)[0] == "gigachat"
assert runtime.load_config.__module__ == "gpt2giga.cli"
PY
