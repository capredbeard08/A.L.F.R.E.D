"""Professional role registry for A.L.F.R.E.D.

The taxonomy lives in ``prompts/domains/07_roles.md`` as plain markdown, one
``### role: <id>`` block per role. This module parses that file and returns the
block for a selected role; there is no separate data format to keep in sync.

Roles are additive: ``apply_role`` appends the selected role's block to the
assembled system prompt, so BLUF, citation, and honesty rules still apply.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from .runtime import AlfredError, find_repo_root

ROLES_RELATIVE_PATH = Path("prompts") / "domains" / "07_roles.md"
ROLE_MARKER = "### role: "
_ROLE_ID_RE = re.compile(rf"^{re.escape(ROLE_MARKER)}(\S+)\s*$", re.MULTILINE)

ROLE_INTRODUCTION = """## ACTIVE PROFESSIONAL ROLE

You are currently operating under a specific professional role. Apply that
role's lens, methodology, and output format to this request.

Constraints that the role does NOT relax:
- Lead with BLUF in the first one to two sentences.
- Quantify claims; label assumptions versus observations.
- Give best-case, base-case, and worst-case scenarios for non-trivial advice.
- Cite sources inline as [1], [2] when web evidence was retrieved.
- Never fabricate data, citations, or credentials.

When no role is specified, say so rather than silently impersonating a
specialist.
"""


class RoleError(AlfredError):
    """The role registry is missing, malformed, or the role id is unknown."""


def _normalise(role_id: str) -> str:
    """Fold whitespace/underscores to hyphens and lowercase an id."""
    return re.sub(r"[-_\s]+", "-", role_id.strip().lower()).strip("-")


def registry_path() -> Path:
    """Absolute path to the role registry markdown file."""
    return find_repo_root() / ROLES_RELATIVE_PATH


def load_registry(path: Path | str | None = None) -> dict[str, str]:
    """Parse the registry into ``{role_id: markdown_block}``.

    Raises RoleError if the file is missing or contains no role blocks.
    """
    target = Path(path).resolve() if path else registry_path()
    if not target.is_file():
        raise RoleError(
            f"role registry not found at {target}. "
            "Expected prompts/domains/07_roles.md in the repository root."
        )
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RoleError(f"role registry is not valid UTF-8: {target}") from exc

    matches = list(_ROLE_ID_RE.finditer(text))
    if not matches:
        raise RoleError(f"no '### role: <id>' blocks found in {target}")

    registry: dict[str, str] = {}
    for index, match in enumerate(matches):
        role_id = _normalise(match.group(1))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if role_id in registry:
            raise RoleError(f"duplicate role id in registry: {role_id!r}")
        registry[role_id] = block
    return registry


def role_ids(path: Path | str | None = None) -> list[str]:
    """Sorted list of every registered role id."""
    return sorted(load_registry(path))


def get_role(role_id: str, path: Path | str | None = None) -> str:
    """Return the markdown block for one role, or raise RoleError with hints."""
    registry = load_registry(path)
    key = _normalise(role_id)
    if key not in registry:
        suggestions = difflib.get_close_matches(key, registry.keys(), n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise RoleError(
            f"unknown role {role_id!r}.{hint} "
            "Run `python3 -m alfred roles` to list every available role."
        )
    return registry[key]


def role_title(role_id: str, path: Path | str | None = None) -> str:
    """Human-readable title for a role, read from its ``**Role:**`` field."""
    block = get_role(role_id, path)
    match = re.search(r"^\*\*Role:\*\*\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else _normalise(role_id)


def role_category(role_id: str, path: Path | str | None = None) -> str:
    """Category for a role, read from its ``**Category:**`` field."""
    block = get_role(role_id, path)
    match = re.search(r"^\*\*Category:\*\*\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else "Uncategorised"


def categories(path: Path | str | None = None) -> dict[str, list[str]]:
    """Map each category to its sorted role ids."""
    registry = load_registry(path)
    grouped: dict[str, list[str]] = {}
    for role_id in sorted(registry):
        grouped.setdefault(role_category(role_id, path), []).append(role_id)
    return grouped


def apply_role(system_prompt: str, role_id: str, path: Path | str | None = None) -> str:
    """Activate one professional role against an assembled system prompt.

    The taxonomy is already compiled into the built prompt, so the role block is
    normally present. In that case only an activation note is inserted, pointing
    at the role — duplicating a definition already in context would waste tokens
    and risk the two copies drifting apart.

    If the block is genuinely absent (for example a stripped-down prompt), the
    full definition is appended so the role still resolves.
    """
    if not system_prompt or not system_prompt.strip():
        raise RoleError("cannot apply a role to an empty system prompt.")

    resolved_id = _normalise(role_id)  # validates the role exists
    block = get_role(resolved_id, path)

    # Re-activating the role that is already active is a no-op, so repeated
    # calls cannot stack duplicate activation notes.
    if active_role(system_prompt) == resolved_id:
        return system_prompt
    title = role_title(resolved_id, path)

    activation = (
        f"**Adopted role:** `{resolved_id}` — {title}\n\n"
        "Apply this role's lens, methodology, and output format to the current "
        "request. Its full definition appears in section 2.7 above."
    )

    body = activation if block in system_prompt else f"{activation}\n\n{block}"
    return f"{system_prompt.rstrip()}\n\n{ROLE_INTRODUCTION}\n{body}\n"


def active_role(system_prompt: str) -> str | None:
    """Return the role id activated in a prompt, or None if none is active."""
    matches = re.findall(r"^\*\*Adopted role:\*\*\s+`([^`]+)`", system_prompt, re.MULTILINE)
    return matches[-1] if matches else None
