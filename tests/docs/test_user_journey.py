import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
RU_DOC_ROOT = ROOT / "docs-site/i18n/ru/docusaurus-plugin-content-docs/current"
USER_PAGES = (
    "quickstart.md",
    "agent-runtimes.md",
    "gateway-integration.md",
    "operations.md",
    "troubleshooting.md",
)
QUICKSTART_COMMANDS = (
    "giga agent search opencode --refresh --json",
    "giga agent add opencode --dry-run --json",
    "giga agent add opencode --yes --json",
    "giga agent inspect opencode --json",
    "giga --with gpt2giga --model GigaChat-2-Max --dry-run --json opencode",
)
GIGA_MAIN = "from gigaloom.entrypoint import main; raise SystemExit(main())"


def run_giga(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIGALOOM_DATA_DIR": str(tmp_path / "state"),
            "GIGALOOM_PROXY_URL": "http://127.0.0.1:1",
        }
    )
    return subprocess.run(
        [sys.executable, "-c", GIGA_MAIN, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_user_pages_have_english_and_russian_parity() -> None:
    for filename in USER_PAGES:
        assert (ROOT / "docs" / filename).is_file()
        assert (RU_DOC_ROOT / filename).is_file()

    assert len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) <= 300


def test_quickstarts_contain_the_same_copyable_agent_commands() -> None:
    english = (ROOT / "docs/quickstart.md").read_text(encoding="utf-8")
    russian = (RU_DOC_ROOT / "quickstart.md").read_text(encoding="utf-8")

    for command in QUICKSTART_COMMANDS:
        assert command in english
        assert command in russian


@pytest.mark.parametrize(
    "args",
    [
        ["agent", "search", "--help"],
        ["agent", "add", "--help"],
        ["agent", "list", "--help"],
        ["agent", "inspect", "--help"],
        ["agent", "probe", "--help"],
        ["agent", "outdated", "--help"],
        ["agent", "update", "--help"],
        ["agent", "rollback", "--help"],
        ["agent", "remove", "--help"],
    ],
)
def test_documented_agent_commands_exist(args: list[str], tmp_path: Path) -> None:
    result = run_giga(args, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_quickstart_gateway_dry_run_never_spawns(tmp_path: Path) -> None:
    result = run_giga(
        [
            "--with",
            "gpt2giga",
            "--model",
            "GigaChat-2-Max",
            "--dry-run",
            "--json",
            "opencode",
        ],
        tmp_path,
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["process_spawn"] is False
    assert payload["provider_traffic"] is False


def test_links_versions_and_user_journey_contracts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_docs.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Documentation validation passed."
