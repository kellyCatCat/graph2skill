"""Tell a command from what an extractor mistook for one.

Extraction from maintenance manuals and case write-ups produces three kinds of
junk that read like commands until you look closely:

* **Echo lines.** ``Peer : <ip>``, ``Interface Name : 100GE0/3/3`` — name/value
  pairs lifted off a screen. They describe one device at one moment.
* **Table rows.** ``1.3.1  4  100  0  0  0  0965h20m  Connect  0`` — a row of
  ``display bgp peer`` output that became a step name.
* **Prose.** 「但由于 Device A 是双路由上行，发包存在Hash问题」 — a sentence from
  the surrounding text that became a cause name.

A fourth problem is not junk but identity: the same command arrives abbreviated
in one source and spelled out in another (``display current-config config bgp``
vs ``display current-configuration configuration bgp``).  CLI abbreviation is
prefix truncation of each keyword, so two spellings are the same command when
their tokens line up pairwise with one a prefix of the other.  Nothing here
guesses a canonical spelling: the longest form wins because it is the one a
reader can look up.

Everything this module rejects is returned with a reason, never dropped
silently — the caller records it in ``reference/evidence.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: Verbs a line must open with to be read as a CLI command rather than an echo.
#: Display-only verbs first; the rest are configuration and diagnostic actions.
COMMAND_VERBS: Tuple[str, ...] = (
    "display",
    "dis",
    "show",
    "ping",
    "tracert",
    "traceroute",
    "reset",
    "undo",
    "debugging",
    "terminal",
    "save",
    "system-view",
    "quit",
    "return",
)

#: Words that make a table row of protocol state, not an instruction.
STATE_WORDS = frozenset(
    {
        "idle",
        "active",
        "connect",
        "established",
        "opensent",
        "openconfirm",
        "up",
        "down",
        "init",
        "full",
        "2way",
        "exstart",
        "exchange",
        "loading",
    }
)

#: Connectives that mark a sentence lifted out of running text.
PROSE_MARKERS: Tuple[str, ...] = (
    "但由于",
    "但是",
    "由于",
    "因为",
    "所以",
    "因此",
    "导致",
    "从而",
    "可以看到",
    "如下图",
    "如上图",
    "综上",
)

#: ``slot 3`` — a keyword followed by a bare number that came from one device.
HARDCODED_SLOT_RE = re.compile(
    r"\b(slot|chassis|lpu|spu|card|vlan|process|instance)\s+(\d+)\b", re.I
)

#: A CLI keyword: letters and hyphens only. A digit anywhere makes it a value.
_KEYWORD_RE = re.compile(r"^[a-z][a-z-]*$")
_NAME_VALUE_RE = re.compile(r"^[^:：`]{1,60}[:：]\s*\S")
#: ``BGP Peer is 1.1.1.1, remote AS 100`` — a screen header, not an instruction.
_COPULA_RE = re.compile(r"\b(?:is|are|was|were)\b", re.I)
_NUMERIC_RE = re.compile(r"^[\d.:/]+$|^\d+[a-z]\d+[a-z]?\d*[a-z]?$", re.I)
_PARAM_RE = re.compile(r"^<[^<>]+>$")
_CJK_RE = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class Rejection:
    """Something that looked like a command (or a name) but is not one."""

    text: str
    kind: str  # echo | table | prose | prompt
    reason: str

    def render(self) -> str:
        return f"{self.text} — {self.reason}"


#: Human wording for each rejection kind, reused in evidence.md.
REJECTION_REASONS: Dict[str, str] = {
    "echo": "回显行被当成命令（名值对，来自某台设备的现状而不是命令模板）",
    "table": "表格数据被当成命令或步骤名（纯数字与状态词）",
    "prose": "正文句子被当成命令或根因（带转折连词，不是名词短语）",
    "prompt": "设备提示符混进命令模板（提示符是回显，应换成进入视图的命令）",
}

#: ``[~DeviceA-bgp]`` / ``<DeviceA>`` — a prompt, not something you type.
PROMPT_RE = re.compile(r"^\s*[\[<][~*]?[A-Za-z0-9_.\-]+(?:-[A-Za-z0-9_.\-]+)*[\]>]")


def strip_prompt(command: str) -> Tuple[str, bool]:
    """Remove a leading device prompt; returns the command and whether one was cut."""
    match = PROMPT_RE.match(command or "")
    if not match:
        return (command or "").strip(), False
    return command[match.end() :].strip(), True


def tokens(command: str) -> List[str]:
    return [token for token in re.split(r"\s+", (command or "").strip()) if token]


def first_verb(command: str) -> str:
    parts = tokens(command)
    return parts[0].lower() if parts else ""


def looks_like_command(text: str) -> bool:
    """True when *text* opens with a CLI verb — the one reliable positive signal."""
    return first_verb(text) in COMMAND_VERBS


def classify(text: str, *, allow_config: bool = True) -> Optional[Rejection]:
    """Return why *text* is not a command, or ``None`` when it may be one.

    ``allow_config`` keeps bare configuration lines (``mtu <mtu-value>``,
    ``undo shutdown``) usable, because repair nodes legitimately carry them.
    Turn it off where only a diagnostic command makes sense.
    """
    raw = (text or "").strip()
    if not raw:
        return Rejection(raw, "echo", "空命令")
    body, had_prompt = strip_prompt(raw)
    if had_prompt and not body:
        return Rejection(raw, "prompt", REJECTION_REASONS["prompt"])
    candidate = body or raw

    if any(marker in candidate for marker in PROSE_MARKERS) or candidate.endswith("。"):
        return Rejection(raw, "prose", REJECTION_REASONS["prose"])

    parts = tokens(candidate)
    if len(parts) >= 3:
        countable = [p for p in parts if not _PARAM_RE.match(p)]
        if countable:
            noise = sum(1 for p in countable if _NUMERIC_RE.match(p) or p.lower() in STATE_WORDS)
            if noise / len(countable) >= 0.6:
                return Rejection(raw, "table", REJECTION_REASONS["table"])

    if looks_like_command(candidate):
        return None
    # A name/value pair is an echo unless the colon belongs to a command argument.
    if _NAME_VALUE_RE.match(candidate):
        return Rejection(raw, "echo", REJECTION_REASONS["echo"])
    # No CLI syntax has a copula; a screen header describing state does.
    if _COPULA_RE.search(candidate):
        return Rejection(raw, "echo", REJECTION_REASONS["echo"])
    if _CJK_RE.search(candidate):
        return Rejection(raw, "prose", REJECTION_REASONS["prose"])
    if not allow_config:
        return Rejection(raw, "echo", REJECTION_REASONS["echo"])
    return None


def filter_commands(
    commands: Iterable[str], *, allow_config: bool = True
) -> Tuple[List[str], List[Rejection]]:
    """Split *commands* into usable ones and rejected ones with their reason."""
    kept: List[str] = []
    rejected: List[Rejection] = []
    for command in commands:
        verdict = classify(command, allow_config=allow_config)
        if verdict is None:
            body, _ = strip_prompt(command)
            kept.append(body or command.strip())
        else:
            rejected.append(verdict)
    return kept, rejected


def looks_like_cause_name(name: str) -> Optional[Rejection]:
    """Reject a cause name that is really a sentence or a row of table data.

    A cause is a noun phrase (``两端接口 MTU 配置不一致``).  What extraction
    sometimes leaves instead is a sentence out of the running text, or a line of
    ``display`` output.  Only those two shapes are rejected — an ordinary
    Chinese noun phrase is exactly what a cause name looks like.
    """
    raw = (name or "").strip()
    if not raw:
        return None
    if any(marker in raw for marker in PROSE_MARKERS) or raw.endswith("。"):
        return Rejection(raw, "prose", REJECTION_REASONS["prose"])
    parts = tokens(raw)
    if len(parts) >= 3:
        countable = [p for p in parts if not _PARAM_RE.match(p)]
        if countable:
            noise = sum(1 for p in countable if _NUMERIC_RE.match(p) or p.lower() in STATE_WORDS)
            if noise / len(countable) >= 0.6:
                return Rejection(raw, "table", REJECTION_REASONS["table"])
    return None


# ------------------------------------------------------- command identity
def _token_compatible(left: str, right: str) -> bool:
    """True when two tokens are the same keyword at different abbreviations."""
    if left == right:
        return True
    if _PARAM_RE.match(left) or _PARAM_RE.match(right):
        return False  # parameter names must match exactly
    lower_left, lower_right = left.lower(), right.lower()
    if lower_left == lower_right:
        return True
    # Only keywords abbreviate. Anything carrying a digit is a value —
    # never fold `1` into `100`, or `ge0/1/1` into `ge0/1/10`.
    if not (_KEYWORD_RE.match(lower_left) and _KEYWORD_RE.match(lower_right)):
        return False
    shorter, longer = sorted((lower_left, lower_right), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter)


def same_command(left: str, right: str) -> bool:
    """True when two spellings are the same command, abbreviations folded."""
    left_tokens, right_tokens = tokens(left), tokens(right)
    if len(left_tokens) != len(right_tokens):
        return False
    return all(_token_compatible(a, b) for a, b in zip(left_tokens, right_tokens))


def longest_form(commands: Sequence[str]) -> str:
    """The spelling a reader can look up — the least abbreviated one."""
    return max(commands, key=lambda command: (len(command), command)) if commands else ""


@dataclass
class CommandForms:
    """One command and the other spellings sources gave for it."""

    command: str
    variants: List[str]

    @property
    def has_conflict(self) -> bool:
        return bool(self.variants)


def merge_forms(commands: Iterable[str]) -> List[CommandForms]:
    """Fold abbreviated and spelled-out forms of one command into one entry."""
    groups: List[List[str]] = []
    for command in commands:
        command = (command or "").strip()
        if not command:
            continue
        for group in groups:
            if same_command(group[0], command):
                if command not in group:
                    group.append(command)
                break
        else:
            groups.append([command])
    merged: List[CommandForms] = []
    for group in groups:
        primary = longest_form(group)
        merged.append(
            CommandForms(
                command=primary, variants=[form for form in group if form != primary]
            )
        )
    return merged


def command_group_key(command: str, known: Sequence[str]) -> str:
    """Identity of *command* among commands already seen, abbreviations folded."""
    for candidate in known:
        if same_command(candidate, command):
            return candidate
    return command


#: ``device B`` / ``设备C`` — the letter labels of a documentation topology diagram.
TOPOLOGY_LABEL_RE = re.compile(
    r"^(?:device|router|switch|设备|路由器)[\s_\-]?[a-z]$", re.I
)


def is_topology_label(name: str) -> bool:
    """True for a parameter that only names a box in the source's diagram.

    The field engineer has real hostnames in front of them; ``device B`` is
    unfillable, and a document that asks for it stalls before the first command.
    The criterion that used it has to name the device by its role instead.
    """
    return bool(TOPOLOGY_LABEL_RE.match((name or "").strip()))


def hardcoded_literals(command: str) -> List[str]:
    """Example values left in a command that break on the next device."""
    found: List[str] = []
    for keyword, value in HARDCODED_SLOT_RE.findall(command or ""):
        item = f"{keyword} {value}"
        if item not in found:
            found.append(item)
    return found
