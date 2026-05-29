"""TeamSpeak 3 ServerQuery protocol: escape/unescape and response parsing."""

from __future__ import annotations

from dataclasses import dataclass, field

# Characters that need escaping: char -> escaped form
_ESCAPE_CHARS: dict[str, str] = {
    "\\": "\\\\",
    "/": "\\/",
    " ": "\\s",
    "|": "\\p",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}

# Reverse mapping: two-char escape sequence -> original char
_UNESCAPE_CHARS: dict[str, str] = {
    "\\\\": "\\",
    "\\/": "/",
    "\\s": " ",
    "\\p": "|",
    "\\a": "\a",
    "\\b": "\b",
    "\\f": "\f",
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
    "\\v": "\v",
}


def ts3_escape(text: str) -> str:
    """Escape special characters for TS3 ServerQuery protocol."""
    # Build result char-by-character to avoid double-escaping
    result: list[str] = []
    for ch in text:
        result.append(_ESCAPE_CHARS.get(ch, ch))
    return "".join(result)


def ts3_unescape(text: str) -> str:
    """Unescape TS3 ServerQuery escaped characters."""
    i = 0
    result: list[str] = []
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            two = text[i : i + 2]
            if two in _UNESCAPE_CHARS:
                result.append(_UNESCAPE_CHARS[two])
                i += 2
                continue
        result.append(text[i])
        i += 1
    return "".join(result)


@dataclass
class SQResponse:
    """Parsed ServerQuery response."""

    data: list[dict[str, str]] = field(default_factory=list)
    error_id: int = 0
    error_msg: str = ""

    @property
    def ok(self) -> bool:
        return self.error_id == 0


def parse_record(record: str) -> dict[str, str]:
    """Parse a single key=value record (space-separated pairs)."""
    result: dict[str, str] = {}
    for part in record.split(" "):
        if "=" in part:
            key, _, value = part.partition("=")
            result[ts3_unescape(key)] = ts3_unescape(value)
        elif part:
            result[ts3_unescape(part)] = ""
    return result


def parse_record_list(data: str) -> list[dict[str, str]]:
    """Parse pipe-separated records into a list of dicts."""
    if not data.strip():
        return []
    return [parse_record(record) for record in data.split("|")]


def parse_response(raw: str) -> SQResponse:
    """Parse a full ServerQuery response.

    A response consists of:
    - Optional welcome/banner lines (ignored, start with 'TS3' or 'Welcome')
    - Zero or more data lines
    - A final 'error id=X msg=Y' line
    """
    lines = raw.strip().split("\n")
    data_lines: list[str] = []
    error_line = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip welcome/banner lines
        if line.startswith("TS3") or line.startswith("Welcome"):
            continue
        if line.startswith("error "):
            error_line = line
        else:
            data_lines.append(line)

    # Parse error line
    error_id = 0
    error_msg = ""
    if error_line:
        error_record = parse_record(error_line.removeprefix("error "))
        error_id = int(error_record.get("id", "0"))
        error_msg = error_record.get("msg", "")

    # Parse data lines
    data: list[dict[str, str]] = []
    if data_lines:
        # All data lines concatenated (usually just one)
        full_data = data_lines[-1]  # Last data line before error
        data = parse_record_list(full_data)

    return SQResponse(data=data, error_id=error_id, error_msg=error_msg)


def build_command(
    cmd: str,
    params: dict[str, str | int] | None = None,
    opts: list[str] | None = None,
) -> str:
    """Build a ServerQuery command string.

    Args:
        cmd: Command name (e.g., "sendtextmessage")
        params: Key-value parameters (will be escaped)
        opts: Options (e.g., ["-voice", "-virtual"])

    Returns:
        Complete command string ready to send
    """
    parts = [cmd]
    if params:
        for key, value in params.items():
            escaped = ts3_escape(str(value))
            parts.append(f"{key}={escaped}")
    if opts:
        parts.extend(opts)
    return " ".join(parts)
