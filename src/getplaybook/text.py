"""Small text helpers: taking secrets out of what is stored, and reading what a person typed from a Claude Code message."""
import re

# ---- redaction: obvious secrets never reach disk (a typed request may hold one) -----------------------

_SECRETS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), "[key removed]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}"), "[key removed]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[key removed]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[key removed]"),
    (re.compile(r"\b(?:xox[abprs]|xapp)-[A-Za-z0-9-]{10,}"), "[key removed]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[token removed]"),
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), "[key removed]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[key removed]"),
    (re.compile(r"\b(?:npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,})"), "[key removed]"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+(?=[A-Za-z._~+/=-]*[0-9])[A-Za-z0-9._~+/=-]{12,}"), r"\1 [removed]"),
    (re.compile(r"(?i)(Authorization:[ \t]*[A-Za-z]+[ \t]+)[^\s\"']{8,}"), r"\1[removed]"),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://[removed]@"),  # a password inside a URL
    # NAME=value where NAME ends in key, token, secret or password: GITHUB_TOKEN=..., db_password: "..."
    (re.compile(r"(?i)(\b[\w-]{0,40}(?:api[_-]?key|token|secret|passw(?:or)?d|passwd)[ \t]*[=:][ \t]*[\"']?)(?!\$|[\w.]+\(|None\b|null\b|true\b|false\b)[^\s\"']{8,}"),
     r"\1[removed]"),
]
_TAG = re.compile(r"</?[a-zA-Z][\w-]{0,40}>")
_REMINDER_TAG = re.compile(r"(?i)<\s*(?:/\s*)?system-reminder\s*>?")


def redact(text: str) -> str:
    """Take obvious secrets out of text before it is stored."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def clean(text: str) -> str:
    return redact(_REMINDER_TAG.sub(" ", _TAG.sub(" ", text or ""))).strip()


# ---- reading a Claude Code message ---------------------------------------------------------------------

REMINDER = "<system-reminder>"
SKILL_BODY = "Base directory for this skill:"  # Claude Code inserts a skill's text after a typed slash command
COMMAND = ("<command-name>", "<command-message>")  # a custom command's own body comes as the one text block after this one
STDOUT = "<local-command-stdout>"
NOT_TYPED = ("[Request interrupted", "<command-", "<local-command-", "<bash-", "Caveat: The messages below")
CONTINUE = re.compile(r"^\W*((yes|yep|ok(ay)?|sure|please|thanks?)\W+)*(yes|yep|ok(ay)?|sure|go (on|ahead)|continue|proceed|do it|sounds good|thanks?|thank you|lgtm|keep going)\W*$")
FENCED = re.compile(r"```.*?(```|$)", re.S)
PASTED = re.compile(r'(?m)^[ \t]*(File "|at |\d{4}-\d\d-\d\d|\[?(INFO|WARN|ERROR|DEBUG)).*$')


def blocks(msg) -> list:
    """A message's content as a list of blocks (a bare string becomes one text block)."""
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def is_tool_result(msg) -> bool:
    return isinstance(msg, dict) and msg.get("role") == "user" and any(b.get("type") == "tool_result" for b in blocks(msg))


def typed_parts(msg) -> list:
    """The text blocks of a user message a person could have typed: not reminders and not a skill's body, and not the one
    block straight after a command block (a custom command's body) unless that is the command's own output."""
    if not isinstance(msg, dict) or msg.get("role") != "user" or is_tool_result(msg):
        return []
    out, after_command = [], False
    for b in blocks(msg):
        if b.get("type") != "text":
            continue
        text = b.get("text") or ""
        head = text.lstrip()
        if not head or head.startswith(REMINDER):
            continue
        body = after_command and not head.startswith(STDOUT)
        after_command = head.startswith(COMMAND)
        if not body and not head.startswith(SKILL_BODY):
            out.append(text)
    return out


def person_text(msg) -> str:
    """What the person typed in a user message, without text Claude Code made up."""
    return "\n".join(p for p in typed_parts(msg) if p.strip() and not p.lstrip().startswith(NOT_TYPED)).strip()


def handwritten(text: str) -> str:
    """The part of a request the person wrote by hand: no fenced blocks, no pasted traces or logs, cut short."""
    return PASTED.sub("", FENCED.sub("", text))[:600].lower().strip()
