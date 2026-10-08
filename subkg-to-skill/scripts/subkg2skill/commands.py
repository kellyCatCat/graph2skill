"""Command text as the documents use it: normalised, compared, parameterised.

The builder, ``lint`` and ``verify`` all read commands through this module.
They have to read a command the same way — otherwise the verifier would call
the builder's own normalised output an invented command.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from subkg2skill import hygiene
from subkg2skill.graph import Node, _string_list, _text


#: ``{x}`` in source templates is re-bracketed to ``<x>``; the name never changes.
#: ``[ ... ]`` is left alone — in CLI reference syntax it marks an optional
#: argument, not a placeholder, and rewriting it would corrupt the command.
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


PARAM_RE = re.compile(r"<([^<>]+)>")


#: Literals that belong to the incident a case was written about, not to the reader.
CASE_LITERAL_RES = (
    ("IP 地址", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("IPv6 地址", re.compile(r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){3,}")),
    ("设备名", re.compile(r"\b(?:Device|Router|Switch|CE|PE|ASG|CSG|AGG|MER|MAR)[A-Z0-9]\b")),
    ("具体接口号", re.compile(r"\b\d+/\d+/\d+\b")),
    ("System ID", re.compile(r"\b[0-9a-fA-F]{4}(?:\.[0-9a-fA-F]{4}){2}\b")),
)


def case_literals(text: str) -> List[str]:
    """Case-specific literals found in *text*, as ``类型 值`` labels."""
    found: List[str] = []
    for label, pattern in CASE_LITERAL_RES:
        for match in pattern.findall(text or ""):
            value = match if isinstance(match, str) else match[0]
            item = f"{label} {value}"
            if item not in found:
                found.append(item)
    return found


def normalise_command(command: str) -> str:
    """Re-bracket ``{}`` placeholders to ``<>`` and drop extraction hashes.

    The name inside the brackets is never translated or reworded, but a hash
    the extractor glued on is not part of it: left in, one document asks for
    ``<peer ip c8be5e6454>`` where its 入参列表 says ``peer ip``, and the
    engineer is being asked to type an id that means nothing on their device.
    """
    text = _text(command)
    if not text:
        return ""
    text = PLACEHOLDER_RE.sub(lambda m: f"<{m.group(1).strip()}>", text)
    return PARAM_RE.sub(
        lambda m: f"<{hygiene.generalise_slot(m.group(1)) or m.group(1).strip()}>", text
    )


def command_signature(commands: Sequence[str]) -> str:
    """Identity used to merge collection steps that run the same thing."""
    return "\n".join(sorted(re.sub(r"\s+", " ", command).strip() for command in commands))


def same_command_set(left: Sequence[str], right: Sequence[str]) -> bool:
    """True when two steps collect the same thing, abbreviations folded.

    ``display current-config config bgp`` and its spelled-out twin produce one
    screen; collecting both is the repetition that makes a document unreadable.
    """
    if not left or len(left) != len(right):
        return False
    unmatched = list(right)
    for command in left:
        for index, candidate in enumerate(unmatched):
            if hygiene.same_command(command, candidate):
                unmatched.pop(index)
                break
        else:
            return False
    return True


def command_templates(node: Node) -> List[str]:
    """Usable command templates of *node*, with extraction noise removed."""
    return command_templates_with_rejections(node)[0]


def command_templates_with_rejections(node: Node) -> Tuple[List[str], List[hygiene.Rejection]]:
    """Commands of *node* plus what was rejected as echo / table data / prose.

    Extraction turns screen output into ``command_templates`` often enough that
    taking the field at face value puts ``Peer : <ip>`` in a document as
    something to type.
    """
    raw = [normalise_command(cmd) for cmd in _string_list(node.attrs.get("command_templates")) if _text(cmd)]
    # A check node may only ever read; a repair legitimately configures.
    kept, rejected = hygiene.filter_commands(raw, allow_config=node.node_type != "check")
    forms = hygiene.merge_forms(kept)
    return [form.command for form in forms], rejected


def command_variants(node: Node) -> List[Tuple[str, List[str]]]:
    """Commands of *node* whose sources disagreed on spelling, longest form first."""
    raw = [normalise_command(cmd) for cmd in _string_list(node.attrs.get("command_templates")) if _text(cmd)]
    kept, _rejected = hygiene.filter_commands(raw, allow_config=node.node_type != "check")
    return [(form.command, form.variants) for form in hygiene.merge_forms(kept) if form.has_conflict]


def parameters_in(commands: Iterable[str]) -> List[str]:
    """Parameter tokens actually appearing in *commands*, in order of appearance."""
    found: List[str] = []
    for command in commands:
        for token in PARAM_RE.findall(command):
            token = token.strip()
            if token and token not in found:
                found.append(token)
    return found


def param_key(token: str) -> str:
    """Identity used to merge a slot name with a CLI parameter name.

    An extraction hash is not part of the identity: the same input carries a
    different one in every source, so keeping them apart asks the field the
    same question several times over.
    """
    parts = [
        part
        for part in re.split(r"[\s_\-]+", token or "")
        if part and not hygiene.is_hash_token(part)
    ]
    return "".join(parts).lower()


def param_display(token: str) -> str:
    """Readable label that still matches the CLI name once spaces/hyphens are dropped."""
    if re.search(r"[A-Za-z0-9]", token) and not re.search(r"[^\x00-\x7f]", token):
        return token.replace("-", " ").replace("_", " ").strip()
    return token.strip()
