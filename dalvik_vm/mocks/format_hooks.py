"""A printf-style hook for java.lang.String.format / java.util.Formatter.

Running the real Formatter bytecode off device DEX is a deep rabbit hole --
Formatter pulls in Locale, Collections, and the whole java.util.regex Pattern
engine, each with its own native leaves. So, exactly as with the other native
leaves (Character.digit, Math, new String), we implement the leaf directly:
interpret the Java format string in Python.

Supported: argument index (n$), flags - # + space 0 , (, width, .precision,
conversions d i o x X e E f g G s S c b B h H % n. Enough for the conversions
apps actually use; unknown conversions are passed through literally.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from ..types import DalvikObject, DalvikArray

# %[argument_index$][flags][width][.precision]conversion
_SPEC = re.compile(r'%(?:(\d+)\$)?([-#+ 0,(]*)(\d+)?(?:\.(\d+))?([a-zA-Z%])')


def _unwrap(v) -> Any:
    v = v.value if hasattr(v, "value") else v
    if isinstance(v, DalvikObject):
        return getattr(v, "internal_value", None)
    return v


def _as_str(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _group(digits: str) -> str:
    """Insert ',' every three digits (Java's ',' flag), sign-aware."""
    neg = digits.startswith("-")
    digits = digits.lstrip("-")
    out = ""
    while len(digits) > 3:
        out = "," + digits[-3:] + out
        digits = digits[:-3]
    out = digits + out
    return ("-" + out) if neg else out


def _pad(body: str, flags: str, width: Optional[str], numeric: bool, negative: bool) -> str:
    if not width:
        return body
    w = int(width)
    if len(body) >= w:
        return body
    fill = w - len(body)
    if "-" in flags:
        return body + " " * fill
    if "0" in flags and numeric:
        # zero-pad after any sign / paren
        if negative and (body.startswith("-") or body.startswith("(")):
            lead = body[0]
            return lead + "0" * fill + body[1:]
        return "0" * fill + body
    return " " * fill + body


def _fmt_int(val: int, flags: str, width, prec, conv: str) -> str:
    negative = val < 0
    if conv in ("d", "i"):
        digits = str(abs(val))
        if "," in flags:
            digits = _group(digits)
        if negative:
            body = "(" + digits + ")" if "(" in flags else "-" + digits
        else:
            sign = "+" if "+" in flags else (" " if " " in flags else "")
            body = sign + digits
        return _pad(body, flags, width, True, negative)
    # x/X/o operate on the 32-bit unsigned value in Java for ints
    u = val & 0xFFFFFFFF if val < 0 else val
    if conv in ("x", "X"):
        s = format(u, "x")
        if "#" in flags:
            s = "0x" + s
        if conv == "X":
            s = s.upper()
        return _pad(s, flags, width, True, False)
    if conv == "o":
        s = format(u, "o")
        if "#" in flags:
            s = "0" + s
        return _pad(s, flags, width, True, False)
    return str(val)


def _fmt_float(val: float, flags: str, width, prec, conv: str) -> str:
    p = int(prec) if prec is not None else 6
    negative = val < 0
    a = abs(val)
    if conv in ("e", "E"):
        body = format(a, f".{p}e")
    elif conv in ("g", "G"):
        body = format(a, f".{p}g")
    else:  # f
        body = format(a, f".{p}f")
        if "," in flags and "." in body:
            ip, fp = body.split(".")
            body = _group(ip) + "." + fp
        elif "," in flags:
            body = _group(body)
    if conv in ("E", "G"):
        body = body.upper()
    if negative:
        body = "(" + body + ")" if "(" in flags else "-" + body
    else:
        sign = "+" if "+" in flags else (" " if " " in flags else "")
        body = sign + body
    return _pad(body, flags, width, True, negative)


def java_format(fmt: str, argv: List[Any]) -> str:
    idx = 0  # ordinary (non-indexed) argument cursor

    def render(m: re.Match) -> str:
        nonlocal idx
        arg_index, flags, width, prec, conv = m.groups()
        if conv == "%":
            return "%"
        if conv == "n":
            return "\n"
        if arg_index is not None:
            arg = argv[int(arg_index) - 1] if int(arg_index) - 1 < len(argv) else None
        else:
            arg = argv[idx] if idx < len(argv) else None
            idx += 1
        c = conv.lower()
        try:
            if c in ("d", "i", "x", "o"):
                return _fmt_int(int(arg), flags, width, prec, conv)
            if c in ("e", "f", "g"):
                return _fmt_float(float(arg), flags, width, prec, conv)
            if c == "c":
                s = chr(arg) if isinstance(arg, int) else _as_str(arg)
                return _pad(s, flags, width, False, False)
            if c == "b":
                s = ("false" if arg is None else ("true" if arg else "false")) if isinstance(arg, (bool, type(None))) \
                    else ("false" if arg is None else "true")
                if conv == "B":
                    s = s.upper()
                return _pad(s, flags, width, False, False)
            if c == "h":
                s = "null" if arg is None else format(hash(arg) & 0xFFFFFFFF, "x")
                if conv == "H":
                    s = s.upper()
                return _pad(s, flags, width, False, False)
            # s / S and anything else
            s = _as_str(arg)
            if prec is not None:
                s = s[: int(prec)]
            if conv == "S":
                s = s.upper()
            return _pad(s, flags, width, False, False)
        except (ValueError, TypeError):
            return _as_str(arg)

    return _SPEC.sub(render, fmt)


def _collect_argv(arr) -> List[Any]:
    if isinstance(arr, DalvikArray):
        return [_unwrap(e) for e in arr.data]
    if isinstance(arr, list):
        return [_unwrap(e) for e in arr]
    return []


def _hook_string_format(vm, args: List, trace_str: str) -> Any:
    """String.format(String, Object[]) and format(Locale, String, Object[])."""
    if not args:
        return None
    # Decide the overload by the parameter descriptor: the Locale form
    # (Locale, String, Object[]) puts the format string in arg 1, not arg 0.
    desc = ""
    if "(" in trace_str and ")" in trace_str:
        desc = trace_str[trace_str.index("(") + 1:trace_str.index(")")]
    locale_form = desc.strip().startswith("Ljava/util/Locale;")

    if locale_form:
        fmt = _unwrap(args[1]) if len(args) > 1 else None
        argv = _collect_argv(args[2].value if len(args) > 2 and hasattr(args[2], "value") else None)
    else:
        fmt = _unwrap(args[0])
        argv = _collect_argv(args[1].value if len(args) > 1 and hasattr(args[1], "value") else None)

    if not isinstance(fmt, str):
        return None
    out = DalvikObject("Ljava/lang/String;")
    out.internal_value = java_format(fmt, argv)
    return out
