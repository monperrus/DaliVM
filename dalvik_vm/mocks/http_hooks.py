"""Hooks for java.net HTTP and the java.io stream plumbing apps read it with.

An app that fetches a URL walks a fixed little chain of framework classes that
have no Dalvik bytecode to resolve -- so DaliVM would return null at the first
call and the method dies. These hooks stand that chain up on top of Python's
urllib, so the emulated method makes the *real* request and reads the *real*
bytes back:

    new URL(spec)                 -> _hook_url_init          (invoke-direct)
    url.openConnection()          -> _hook_url_open_connection
    conn.setRequestMethod(m)      -> _hook_conn_set_request_method
    conn.setRequestProperty(k,v)  -> _hook_conn_set_request_property
    conn.getResponseCode()        -> _hook_conn_get_response_code   (does the GET)
    conn.getInputStream()         -> _hook_conn_get_input_stream    (does the GET)
    in.read(buf) / read(buf,o,l)  -> _hook_stream_read
    in.read()                     -> _hook_stream_read_one
    in.close(), conn.disconnect() -> no-ops
    new ByteArrayOutputStream()   -> _hook_baos_init          (invoke-direct)
    bos.write(buf,o,l)            -> _hook_baos_write
    bos.toByteArray()/toString()  -> _hook_baos_to_bytes / _to_string

The transport is a genuine network request (http/https via urllib); the read
side backs onto the actual response body. State lives as plain attributes on the
DalvikObject (_http_body, _http_pos, ...).
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, List, Optional

from ..types import DalvikObject, DalvikArray

# Requests that hang would hang the whole emulator; keep them bounded.
_HTTP_TIMEOUT = 20


def _pystr(arg) -> Optional[str]:
    """Unwrap a String argument (DalvikObject-with-internal_value or raw str)."""
    v = arg.value if hasattr(arg, "value") else arg
    if isinstance(v, DalvikObject):
        return getattr(v, "internal_value", None)
    if isinstance(v, str):
        return v
    return None if v is None else str(v)


def _recv(args: List):
    """The receiver of a virtual call is arg 0."""
    return args[0].value if args and hasattr(args[0], "value") else (args[0] if args else None)


def _make_string(text: str) -> DalvikObject:
    o = DalvikObject("Ljava/lang/String;")
    o.internal_value = text
    return o


def _make_byte_array(raw: bytes) -> DalvikArray:
    arr = DalvikArray("B", len(raw))
    # Dalvik bytes are signed; keep the raw 0..255 values, the byte<->int
    # conversions elsewhere already mask with 0xFF.
    arr.data = list(raw)
    return arr


# --- URL / connection --------------------------------------------------------

def _hook_url_init(vm, args: List, trace_str: str) -> Any:
    """new URL(String spec) -- stash the spec on the URL object."""
    url = _recv(args)
    spec = _pystr(args[1]) if len(args) > 1 else None
    if isinstance(url, DalvikObject):
        url._url_spec = spec
    return None


def _hook_url_open_connection(vm, args: List, trace_str: str) -> Any:
    """URL.openConnection() -> HttpURLConnection (unconnected)."""
    url = _recv(args)
    conn = DalvikObject("Ljava/net/HttpURLConnection;")
    conn._url_spec = getattr(url, "_url_spec", None)
    conn._req_method = "GET"
    conn._req_headers = {}
    conn._fetched = False
    conn._http_status = None
    conn._http_body = b""
    return conn


def _hook_conn_set_request_method(vm, args: List, trace_str: str) -> Any:
    conn = _recv(args)
    m = _pystr(args[1]) if len(args) > 1 else None
    if isinstance(conn, DalvikObject) and m:
        conn._req_method = m
    return None


def _hook_conn_set_request_property(vm, args: List, trace_str: str) -> Any:
    conn = _recv(args)
    key = _pystr(args[1]) if len(args) > 1 else None
    val = _pystr(args[2]) if len(args) > 2 else None
    if isinstance(conn, DalvikObject) and key is not None:
        conn._req_headers[key] = val
    return None


def _do_fetch(conn: DalvikObject) -> None:
    """Perform the request once; cache status + body on the connection."""
    if getattr(conn, "_fetched", False):
        return
    conn._fetched = True
    spec = getattr(conn, "_url_spec", None)
    if not spec:
        conn._http_status = -1
        conn._http_body = b""
        return
    req = urllib.request.Request(
        spec, method=getattr(conn, "_req_method", "GET") or "GET",
        headers=getattr(conn, "_req_headers", {}) or {})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            conn._http_status = resp.getcode()
            conn._http_body = resp.read()
    except urllib.error.HTTPError as e:
        # Java returns the code here and serves the body via getErrorStream; we
        # keep it on the same object so getInputStream still yields something.
        conn._http_status = e.code
        conn._http_body = e.read() if hasattr(e, "read") else b""
    except Exception:
        conn._http_status = -1
        conn._http_body = b""


def _hook_conn_get_response_code(vm, args: List, trace_str: str) -> Any:
    conn = _recv(args)
    if isinstance(conn, DalvikObject):
        _do_fetch(conn)
        return conn._http_status if conn._http_status is not None else -1
    return -1


def _hook_conn_get_content_length(vm, args: List, trace_str: str) -> Any:
    conn = _recv(args)
    if isinstance(conn, DalvikObject):
        _do_fetch(conn)
        return len(conn._http_body)
    return -1


def _hook_conn_get_input_stream(vm, args: List, trace_str: str) -> Any:
    """(Http)URLConnection.getInputStream() -> InputStream over the body."""
    conn = _recv(args)
    if not isinstance(conn, DalvikObject):
        return None
    _do_fetch(conn)
    stream = DalvikObject("Ljava/io/InputStream;")
    stream._http_body = conn._http_body
    stream._http_pos = 0
    return stream


def _hook_conn_noop(vm, args: List, trace_str: str) -> Any:
    """connect/disconnect/setXxx timeouts/setDoInput/... -- nothing to do."""
    return None


# --- InputStream -------------------------------------------------------------

def _hook_stream_read(vm, args: List, trace_str: str) -> Any:
    """InputStream.read(byte[]) and read(byte[], off, len) -> count or -1."""
    stream = _recv(args)
    if not isinstance(stream, DalvikObject):
        return -1
    buf = args[1].value if len(args) > 1 and hasattr(args[1], "value") else None
    if not isinstance(buf, DalvikArray):
        # read() with no buffer -- return a single unsigned byte (same pattern).
        return _hook_stream_read_one(vm, args, trace_str)
    ints = [a.value if hasattr(a, "value") else a for a in args[2:]]
    ints = [x for x in ints if isinstance(x, int)]
    off = ints[0] if len(ints) >= 2 else 0
    length = ints[1] if len(ints) >= 2 else len(buf.data)

    body = getattr(stream, "_http_body", b"")
    pos = getattr(stream, "_http_pos", 0)
    if pos >= len(body):
        return -1
    chunk = body[pos:pos + length]
    for i, b in enumerate(chunk):
        buf.data[off + i] = b
    stream._http_pos = pos + len(chunk)
    return len(chunk)


def _hook_stream_read_one(vm, args: List, trace_str: str) -> Any:
    """InputStream.read() -> next unsigned byte, or -1 at EOF."""
    stream = _recv(args)
    if not isinstance(stream, DalvikObject):
        return -1
    body = getattr(stream, "_http_body", b"")
    pos = getattr(stream, "_http_pos", 0)
    if pos >= len(body):
        return -1
    stream._http_pos = pos + 1
    return body[pos] & 0xFF


# --- ByteArrayOutputStream ---------------------------------------------------

def _hook_baos_init(vm, args: List, trace_str: str) -> Any:
    """new ByteArrayOutputStream([int]) -- start an empty sink."""
    baos = _recv(args)
    if isinstance(baos, DalvikObject):
        baos._baos = bytearray()
    return None


def _baos_buf(baos: DalvikObject) -> bytearray:
    buf = getattr(baos, "_baos", None)
    if buf is None:
        buf = bytearray()
        baos._baos = buf
    return buf


def _hook_baos_write(vm, args: List, trace_str: str) -> Any:
    """write(byte[]), write(byte[],off,len), write(int)."""
    baos = _recv(args)
    if not isinstance(baos, DalvikObject):
        return None
    src = args[1].value if len(args) > 1 and hasattr(args[1], "value") else None
    if isinstance(src, DalvikArray):
        ints = [a.value if hasattr(a, "value") else a for a in args[2:]]
        ints = [x for x in ints if isinstance(x, int)]
        off = ints[0] if len(ints) >= 2 else 0
        length = ints[1] if len(ints) >= 2 else len(src.data)
        _baos_buf(baos).extend((src.data[off + i] & 0xFF) for i in range(length))
    elif isinstance(src, int):
        _baos_buf(baos).append(src & 0xFF)
    return None


def _hook_baos_to_bytes(vm, args: List, trace_str: str) -> Any:
    baos = _recv(args)
    if isinstance(baos, DalvikObject):
        return _make_byte_array(bytes(_baos_buf(baos)))
    return _make_byte_array(b"")


def _hook_baos_to_string(vm, args: List, trace_str: str) -> Any:
    baos = _recv(args)
    raw = bytes(_baos_buf(baos)) if isinstance(baos, DalvikObject) else b""
    charset = _pystr(args[1]) if len(args) > 1 else None
    try:
        return _make_string(raw.decode(charset or "utf-8", errors="replace"))
    except LookupError:
        return _make_string(raw.decode("utf-8", errors="replace"))


def _hook_baos_size(vm, args: List, trace_str: str) -> Any:
    baos = _recv(args)
    return len(_baos_buf(baos)) if isinstance(baos, DalvikObject) else 0
