"""Professional role registry for A.L.F.R.E.D.

The taxonomy lives in ``prompts/domains/07_roles.md`` as plain markdown:
``### role: <id>`` blocks for roles and ``### seniority: <id>`` blocks for
seniority modifiers. This module parses that file and composes a selected role
with a seniority, so any combination can be expressed without enumerating pairs.

Roles are additive: ``apply_role`` inserts an activation note referencing the
selected role, so BLUF, citation, and honesty rules still apply.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from .runtime import AlfredError, find_repo_root

ROLES_RELATIVE_PATH = Path("prompts") / "domains" / "07_roles.md"
ROLE_MARKER = "### role: "
SENIORITY_MARKER = "### seniority: "

_ROLE_ID_RE = re.compile(rf"^{re.escape(ROLE_MARKER)}(\S+)\s*$", re.MULTILINE)
_SENIORITY_ID_RE = re.compile(rf"^{re.escape(SENIORITY_MARKER)}(\S+)\s*$", re.MULTILINE)

ROLE_INTRODUCTION = """## ACTIVE PROFESSIONAL ROLE

You are currently operating under a specific professional role. Apply that
role's lens, methodology, and output format to this request.

Constraints that the role does NOT relax:
- Lead with BLUF in the first one to two sentences.
- Quantify claims; label assumptions versus observations.
- Give best-case, base-case, and worst-case scenarios for non-trivial advice.
- Cite sources inline as [1], [2] when web evidence was retrieved.
- Never fabricate data, citations, or credentials.
- Maintain zero-leakage handling of the Principal's data and secrets.

When no role is specified, say so rather than silently impersonating a
specialist.
"""


class RoleError(AlfredError):
    """The role registry is missing, malformed, or the role id is unknown."""


@dataclass(frozen=True)
class Resolution:
    """A resolved role, optionally combined with a seniority modifier."""

    requested: str
    role_id: str
    role_title: str
    category: str
    seniority_id: str | None
    seniority_title: str | None
    role_block: str
    seniority_block: str | None

    @property
    def display(self) -> str:
        """Human-readable label for the resolved role."""
        if self.seniority_id:
            return f"{self.seniority_title} {self.role_title}"
        return self.role_title

    @property
    def composed(self) -> bool:
        return self.seniority_id is not None


def _normalise(value: str) -> str:
    """Fold whitespace/underscores to hyphens and lowercase an id."""
    return re.sub(r"[-_\s]+", "-", value.strip().lower()).strip("-")


def registry_path() -> Path:
    """Absolute path to the role registry markdown file."""
    return find_repo_root() / ROLES_RELATIVE_PATH


def _read(path: Path | str | None) -> tuple[Path, str]:
    target = Path(path).resolve() if path else registry_path()
    if not target.is_file():
        raise RoleError(
            f"role registry not found at {target}. "
            "Expected prompts/domains/07_roles.md in the repository root."
        )
    try:
        return target, target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RoleError(f"role registry is not valid UTF-8: {target}") from exc


def _parse_blocks(text: str, pattern: re.Pattern[str], label: str) -> dict[str, str]:
    matches = list(pattern.finditer(text))
    if not matches:
        raise RoleError(f"no '{label}' blocks found in registry")
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        block_id = _normalise(match.group(1))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if block_id in blocks:
            raise RoleError(f"duplicate {label.rstrip('s')} id in registry: {block_id!r}")
        blocks[block_id] = text[start:end].strip()
    return blocks


def load_registry(path: Path | str | None = None) -> dict[str, str]:
    """Parse the registry into ``{role_id: markdown_block}``."""
    _, text = _read(path)
    return _parse_blocks(text, _ROLE_ID_RE, ROLE_MARKER.rstrip() + " <id>")


def load_seniority(path: Path | str | None = None) -> dict[str, str]:
    """Parse the registry into ``{seniority_id: markdown_block}``."""
    _, text = _read(path)
    return _parse_blocks(text, _SENIORITY_ID_RE, SENIORITY_MARKER.rstrip() + " <id>")


def role_ids(path: Path | str | None = None) -> list[str]:
    return sorted(load_registry(path))


def seniority_ids(path: Path | str | None = None) -> list[str]:
    return sorted(load_seniority(path))


def _field(block: str, name: str, fallback: str) -> str:
    match = re.search(rf"^\*\*{re.escape(name)}:\*\*\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def get_role(role_id: str, path: Path | str | None = None) -> str:
    """Return the markdown block for one role, or raise RoleError with hints."""
    registry = load_registry(path)
    key = _normalise(role_id)
    if key not in registry:
        raise RoleError(_unknown_message(key, registry, load_seniority(path)))
    return registry[key]


def get_seniority(seniority_id: str, path: Path | str | None = None) -> str:
    """Return the markdown block for one seniority modifier."""
    modifiers = load_seniority(path)
    key = _normalise(seniority_id)
    if key not in modifiers:
        raise RoleError(_unknown_message(key, load_registry(path), modifiers))
    return modifiers[key]


def role_title(role_id: str, path: Path | str | None = None) -> str:
    return _field(get_role(role_id, path), "Role", _normalise(role_id))


def role_category(role_id: str, path: Path | str | None = None) -> str:
    return _field(get_role(role_id, path), "Category", "Uncategorised")


def seniority_title(seniority_id: str, path: Path | str | None = None) -> str:
    return _field(get_seniority(seniority_id, path), "Title prefix", _normalise(seniority_id))


def categories(path: Path | str | None = None) -> dict[str, list[str]]:
    """Map each category to its sorted role ids."""
    registry = load_registry(path)
    grouped: dict[str, list[str]] = {}
    for role_id in sorted(registry):
        grouped.setdefault(role_category(role_id, path), []).append(role_id)
    return grouped


def _unknown_message(key: str, registry: dict[str, str], modifiers: dict[str, str]) -> str:
    """Build an error message, suggesting both roles and seniority combinations."""
    candidates = list(registry)
    matches = difflib.get_close_matches(key, candidates, n=3, cutoff=0.62)

    # Also try splitting: seniority prefix + role suffix.
    for modifier in sorted(modifiers, key=len, reverse=True):
        found = _consume_seniority(key, modifier, registry)
        if found:
            matches.insert(0, f"{modifier} + {found}")
            break
        if key.endswith(f"-{modifier}") and key[: -(len(modifier) + 1)] in registry:
            matches.insert(0, f"{key[: -(len(modifier) + 1)]} + {modifier}")
            break

    hint = f" Did you mean: {', '.join(matches[:3])}?" if matches else ""
    return (
        f"unknown role {key!r}.{hint} "
        "Run `python3 -m alfred roles` to list every available role and seniority."
    )


def _consume_seniority(key: str, modifier: str, registry: dict[str, str]) -> str | None:
    """Given a seniority id, try to peel it off `key` and return the role id.

    Handles three shapes:
      managing-director-equity-trader -> equity-trader   (plain prefix)
      head-of-credit-trader           -> credit-trader   ("of" connector)
      principal-data-scientist        -> researcher-data-scientist
                                         (role id carries its own prefix)
    """
    prefix = f"{modifier}-"
    if not key.startswith(prefix):
        return None
    remainder = key[len(prefix):]
    if remainder in registry:
        return remainder
    if remainder.startswith("of-") and remainder[3:] in registry:
        return remainder[3:]
    # Unique role id ending in the remainder, e.g. "data-scientist" ->
    # "researcher-data-scientist". Only used when unambiguous.
    candidates = [r for r in registry if r.endswith(f"-{remainder}")]
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve(role_id: str, path: Path | str | None = None) -> Resolution:
    """Resolve a role id, optionally composed with a seniority modifier.

    Resolution order:
    1. Exact role match — so ids that merely contain a seniority word
       (e.g. ``chief-of-staff``) resolve directly.
    2. Longest seniority prefix followed by a known role
       (``managing-director-equity-trader``).
    3. Known role followed by a seniority suffix.
    """
    requested = role_id.strip()
    if not requested:
        raise RoleError("role id must be non-empty")

    key = _normalise(requested)
    registry = load_registry(path)
    modifiers = load_seniority(path)

    if key in registry:
        return _resolution(requested, key, None, registry, modifiers)

    if key in modifiers:
        example_role = next(iter(sorted(registry)))
        raise RoleError(
            f"{key!r} is a seniority modifier, not a role. "
            f"Pair it with a role, e.g. `{key.replace('-', '_')}_{example_role.replace('-', '_')}`. "
            "Run `python3 -m alfred roles` to list roles."
        )

    for modifier in sorted(modifiers, key=len, reverse=True):
        role_id_found = _consume_seniority(key, modifier, registry)
        if role_id_found:
            return _resolution(requested, role_id_found, modifier, registry, modifiers)

    for modifier in sorted(modifiers, key=len, reverse=True):
        suffix = f"-{modifier}"
        if key.endswith(suffix) and key[: -len(suffix)] in registry:
            return _resolution(requested, key[: -len(suffix)], modifier, registry, modifiers)

    raise RoleError(_unknown_message(key, registry, modifiers))


def _resolution(requested, role_id, seniority_id, registry, modifiers) -> Resolution:
    block = registry[role_id]
    return Resolution(
        requested=requested,
        role_id=role_id,
        role_title=_field(block, "Role", role_id),
        category=_field(block, "Category", "Uncategorised"),
        seniority_id=seniority_id,
        seniority_title=(
            _field(modifiers[seniority_id], "Title prefix", seniority_id)
            if seniority_id else None
        ),
        role_block=block,
        seniority_block=modifiers[seniority_id] if seniority_id else None,
    )


def apply_role(system_prompt: str, role_id: str, path: Path | str | None = None) -> str:
    """Activate one professional role against an assembled system prompt.

    The taxonomy is already compiled into the built prompt, so role blocks are
    normally present. Only an activation note is inserted in that case;
    duplicating a definition already in context would waste tokens and risk the
    two copies drifting apart.
    """
    if not system_prompt or not system_prompt.strip():
        raise RoleError("cannot apply a role to an empty system prompt.")

    resolution = resolve(role_id, path)
    if active_role(system_prompt) == resolution.role_id and (
        active_seniority(system_prompt) == resolution.seniority_id
    ):
        return system_prompt

    lines = [f"**Adopted role:** `{resolution.role_id}` — {resolution.role_title}"]
    if resolution.seniority_id:
        lines.append(
            f"**Seniority:** `{resolution.seniority_id}` — {resolution.seniority_title}"
        )
    lines.append(
        "Apply this role's lens, methodology, and output format to the current "
        "request, at the authority level implied by the seniority above. The full "
        "definition appears in section 2.7."
    )
    activation = "\n".join(lines)

    additions: list[str] = []
    if resolution.role_block not in system_prompt:
        additions.append(resolution.role_block)
    if resolution.seniority_block and resolution.seniority_block not in system_prompt:
        additions.append(resolution.seniority_block)

    body = activation if not additions else f"{activation}\n\n" + "\n\n".join(additions)
    return f"{system_prompt.rstrip()}\n\n{ROLE_INTRODUCTION}\n{body}\n"


def active_role(system_prompt: str) -> str | None:
    """Return the role id activated in a prompt, or None."""
    matches = re.findall(r"^\*\*Adopted role:\*\*\s+`([^`]+)`", system_prompt, re.MULTILINE)
    return matches[-1] if matches else None


def active_seniority(system_prompt: str) -> str | None:
    """Return the seniority id activated in a prompt, or None."""
    matches = re.findall(r"^\*\*Seniority:\*\*\s+`([^`]+)`", system_prompt, re.MULTILINE)
    return matches[-1] if matches else None
