"""Guard rails -- ISSUE-001 FR5.

Each of these encodes a mistake the reference implementation actually made and
paid for later. They are cheap now and expensive to retrofit, which is the only
reason they belong in a scaffold.

Every failure message names the offending file and line: a guard rail that only
says "failed" makes the next developer hunt for what they did.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

# Only this module may call the Anthropic API.
LLM_GATEWAY = Path("core") / "llm_gateway.py"

MESSAGES_CREATE = re.compile(r"messages\s*\.\s*create")
# Matches the parameter being *set*, so prose explaining why we do not send
# it stays legal.
SAMPLING_PARAMS = re.compile(r"\b(temperature|top_p|top_k)\s*=")
NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _offences(
    source_files: list[Path], package_root: Path, pattern: re.Pattern, skip: Path | None = None
) -> list[tuple[str, int, str]]:
    """Return (relative path, line number, line) for every pattern match."""
    found = []
    for path in source_files:
        relative = path.relative_to(package_root)
        if skip is not None and relative == skip:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                # as_posix: a path printed half with slashes and half with
                # backslashes is harder to paste into an editor.
                found.append((relative.as_posix(), number, line.strip()))
    return found


def test_anthropic_is_called_only_through_the_gateway(source_files, package_root):
    """One door to the model API, so token accounting is never a retrofit.

    The reference implementation grew nine call sites across five modules;
    adding usage capture afterwards meant finding all nine.
    """
    offences = _offences(source_files, package_root, MESSAGES_CREATE, skip=LLM_GATEWAY)
    assert not offences, (
        "messages.create may only be called from owl_mind/core/llm_gateway.py. "
        "Route this through LLMGateway.complete(component=...) so the call is "
        "counted and rate-limited. Offending lines:\n"
        + "\n".join(f"  owl_mind/{path}:{line}: {text}" for path, line, text in offences)
    )


def test_no_local_package_shadows_the_mcp_sdk(repo_root):
    """A top-level mcp/ package hides the official SDK on sys.path.

    The import failure that follows is confusing and costs an afternoon. The
    tooling package is named toolkit/ for exactly this reason.
    """
    shadow = repo_root / "mcp"
    assert not shadow.exists(), (
        f"{shadow} shadows the official MCP SDK on sys.path. "
        "Name the local package toolkit/ instead."
    )


@pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="mcp SDK not installed yet; the shadowing check above still applies",
)
def test_mcp_import_resolves_to_the_installed_sdk(package_root):
    """'import mcp' must find an installed distribution, not project source.

    Checking "outside the repository" would be wrong: a .venv inside the repo
    is normal. What matters is that the module comes from site-packages rather
    than from a directory someone added to the project.
    """
    import mcp

    resolved = Path(mcp.__file__).resolve()
    assert "site-packages" in resolved.parts, (
        f"'import mcp' resolved to {resolved}, which is not an installed "
        "distribution. The official SDK is being shadowed by local code."
    )
    assert package_root.resolve() not in resolved.parents, (
        f"'import mcp' resolved to {resolved}, inside the owl_mind package."
    )


def test_no_sampling_parameters(source_files, package_root):
    """temperature / top_p / top_k are rejected with a 400 on current models.

    Reproducibility in this design comes from deterministic tools
    (grade_answer, schedule_review), never from a sampling parameter -- so
    there is no reason to reach for one, and a 400 in production is a poor way
    to rediscover that.
    """
    offences = _offences(source_files, package_root, SAMPLING_PARAMS)
    assert not offences, (
        "temperature, top_p, and top_k are rejected by the current models. "
        "Use output_config.effort for depth, and deterministic tools for "
        "reproducibility. Offending lines:\n"
        + "\n".join(f"  owl_mind/{path}:{line}: {text}" for path, line, text in offences)
    )


def test_sources_are_ascii_only(source_files, package_root):
    """Delivery language is English (plan section 1, decision 1).

    Plan section 9 flags translation leakage as a medium risk with a grep sweep
    on the last day. On a greenfield repository it is just a test, and the leak
    never happens.
    """
    offences = _offences(source_files, package_root, NON_ASCII)
    assert not offences, (
        "Owl Mind source must be ASCII-only. Offending lines:\n"
        + "\n".join(f"  owl_mind/{path}:{line}: {text}" for path, line, text in offences)
    )


def test_environment_is_read_only_by_the_config_module(source_files, package_root):
    """Settings have one source, so a new key is trustworthy everywhere."""
    pattern = re.compile(r"os\.(environ|getenv)")
    offences = _offences(source_files, package_root, pattern, skip=Path("core") / "config.py")
    assert not offences, (
        "Read configuration through owl_mind.core.config.get_settings(), not "
        "os.environ. Offending lines:\n"
        + "\n".join(f"  owl_mind/{path}:{line}: {text}" for path, line, text in offences)
    )


def test_every_module_imports_cleanly(source_files, package_root):
    """A stub must still be importable, or the scaffold is not a scaffold."""
    import importlib

    for path in source_files:
        relative = path.relative_to(package_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(["owl_mind", *parts])
        importlib.import_module(module)
