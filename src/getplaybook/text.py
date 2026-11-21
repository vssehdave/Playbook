"""Small text helpers: taking secrets out of what is stored, and reading what a person typed from a Claude Code message."""
import re

# ---- redaction: obvious secrets never reach disk (a typed request may hold one) -----------------------

_NAME_HEAD = (r"(?i)(?P<k>(?:SECRET[_-]?ACCESS[_-]?KEY|SECRET|TOKEN|PASSW(?:OR)?D|(?<![A-Z])PASS|API[_-]?KEY|ACCOUNT[_-]?KEY|PRIVATE[_-]?KEY)"
              r"(?:[_0-9][A-Z0-9_]{0,40}|KEY)?(?:\\?[\"'])?[ \t]*[=:][ \t]*\\?")
# A bare value that is code, not a secret: the WHOLE value is a call or subscript (`get_key()`, `os.environ.get("X")`,
# `keys[name]`), alone on its line or ending at a ; , or # comment. `s3cr3t(xyz)abc123` has text after the closing bracket,
# so it is not code. The inside may not hold a bracket, which keeps the check linear.
_CALL = (r"(?:[A-Za-z_][\w.]*[(\[][^\n)\]]{0,200}[)\]](?:\.?\w*[(\[][^\n)\]]{0,100}[)\]]|\.\w+){0,5}"
         r"(?=[ \t]*[;,]?[ \t]*(?:#[^\n]*)?(?:\r?\n|\Z|\\n|[\"'}])))")
# A name that is an identifier, so a value that is one is code: ALL_CAPS or snake_case (`DEFAULT_LIMIT`, `some_name`).
_IDENT = r"(?:[A-Z]+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?=[\s\"';,)]|\Z)"
_SECRETS = [
    # the body may not hold another BEGIN, so a pile of unclosed ones is scanned once, not once each
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----(?:(?!-----BEGIN ).)*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----", re.S), "[private key removed]"),
    # a key cut short (head -5, a truncated tool result) has no END line: take the rest of its body
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----(?:[A-Za-z0-9+/=\s]|\\n)*"), "[private key removed]"),
    # PuTTY .ppk: the lines of base64 after Private-Lines are the key
    (re.compile(r"(?i)(Private-Lines:[ \t]*\d+)(?:(?:\r?\n|\\n)[A-Za-z0-9+/=]{20,})+"), r"\1 [private key removed]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}"), "[key removed]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[key removed]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[key removed]"),
    (re.compile(r"\b(?:xox[abprs]|xapp)-[A-Za-z0-9-]{10,}"), "[key removed]"),
    (re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"), "[token removed]"),  # Google access token
    (re.compile(r"(?<![\w/:.])1//[A-Za-z0-9_-]{30,}"), "[token removed]"),  # Google refresh token
    (re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}(?![A-Za-z0-9_-])"), "[token removed]"),  # Telegram bot
    (re.compile(r"(?i)([?&;]sig=)[^&\s\"'\\]{8,}"), r"\1[removed]"),  # Azure SAS signature
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[token removed]"),
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), "[key removed]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[key removed]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "[key removed]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "[key removed]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{16,}"), "[key removed]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[key removed]"),
    (re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]+"), "hooks.slack.com/[removed]"),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"), "[key removed]"),
    # The credential must hold a digit, so prose such as "basic error-handling" stays readable.
    (re.compile(r"(?i)\b(Bearer|Basic)\s+(?=[A-Za-z._~+/=-]*[0-9])[A-Za-z0-9._~+/=-]{12,}"), r"\1 [removed]"),
    (re.compile(r"(?i)(Authorization:[ \t]*[A-Za-z]+[ \t]+)[^\s\"']{8,}"), r"\1[removed]"),
    (re.compile(r"(?i)\b((?:Set-)?Cookie:[ \t]*)[^\r\n\"'\\]+"), r"\1[removed]"),  # stops at a quote or a JSON-escaped break
    # a database or file-transfer URL: the password may be empty-user, or hold "/" or "@" (up to the last "@" of the word)
    (re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|sqlserver|oracle|ftps?|sftp|ldaps?)://)[^/\s:@]{0,64}:[^\s]{0,256}@"),
     r"\1[removed]@"),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://[removed]@"),
    # curl -u user:password (the user stays; the lazy gap is bounded so a line of "curl" words stays linear, and it stops at
    # a pipe or a semicolon so another command's -u is not read (a quoted argument is skipped whole, so a | or ; inside it
    # does not end the gap); a $variable is not the password)
    (re.compile(r"(?i)(\bcurl\b(?:[^\n|;'\"]|'[^'\n]*'|\"[^\"\n]*\"|'(?![^'\n]*')|\"(?![^\"\n]*\")){0,300}?[ \t](?:-u|--user)(?:[ \t]+|=)\\?['\"]?[^\s'\":]*:)(?!\$)[^\s'\"]+"), r"\1[removed]"),
    # ODBC style: Pwd=secret; the value ends at the semicolon. A bare upper-case PWD is the shell's working directory;
    # a path, a shell variable or a call (`pwd=$(pwd)`, `Pwd=$HOME`, `pwd = os.getcwd()`) is code, not a password.
    (re.compile(r"(?i)((?<![A-Za-z])(?:[A-Za-z0-9]{1,20}_PWD|(?-i:Pwd|pwd))[ \t]*=[ \t]*\\?[\"']?)"
                r"(?!\$[({]|(?-i:\$[A-Z_][A-Z0-9_]*)(?!\w)|(?:`[^`\s]*`|%[A-Za-z_]\w*%)(?=[;\s\"']|\Z)|\.{1,2}/|~/|/|[A-Za-z]:[\\/]|" + _CALL + r")[^;\s\"']+"), r"\1[removed]"),
    # AWS_SECRET_ACCESS_KEY, STRIPE_KEY, any UPPER_CASE_NAME_KEY/_TOKEN/_SECRET/_PASSWORD assignment of a literal: a quoted
    # string, or a bare value of 12+ characters with a digit and a letter in it, never a call (`MY_KEY = get_key()`)
    (re.compile(r"(?P<k>\b[A-Z][A-Z0-9_]{0,60}_(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[ \t]*=[ \t]*\\?[\"'])(?!(?:None|True|False|null)\b)[^\s\"']{3,}"), r"\g<k>[removed]"),
    (re.compile(r"(?P<k>\b[A-Z][A-Z0-9_]{0,60}_(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[ \t]*=[ \t]*)(?=[^\s\"']{0,100}\d)(?=[^\s\"']{0,100}[A-Za-z])"
                r"(?!" + _CALL + r")[^\s\"']{12,}"), r"\g<k>[removed]"),
    # a *_KEY value with no digit: 16+ characters that are not an identifier (`correcthorsebattery`), or a dashed licence shape
    (re.compile(r"(?P<k>\b[A-Z][A-Z0-9_]{0,60}_KEY[ \t]*=[ \t]*)(?!" + _CALL + r")"
                r"(?:[A-Z0-9]{3,8}(?:-[A-Z0-9]{3,8}){2,}(?![\w-])|(?!" + _IDENT + r")[^\s\"'(\[]{16,})"), r"\g<k>[removed]"),
    # NAME=value where NAME ends in secret, token, pass(word), api, account or private key (GITHUB_TOKEN, DB_PASS,
    # apiKey, SecretAccessKey), quoted or JSON-escaped (a tool call is stored as JSON: API_KEY=\"...\"),
    # but not tokens, tokenizer or passwords: `max_tokens: 150000` is not a secret. The name's tail is
    # bounded so a long run of such words cannot make the match slow. A quoted value may hold spaces.
    (re.compile(_NAME_HEAD + r"[\"'])[^\"'\\\n]{6,200}(?=\\?[\"'])"), r"\g<k>[removed]"),
    (re.compile(_NAME_HEAD + r"[\"']?)(?!" + _CALL + r")[^\s\"']{6,}"), r"\g<k>[removed]"),  # not a call: `token = get_token()`
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
