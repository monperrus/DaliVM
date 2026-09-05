"""Static method hooks for Java utility classes.

Hooks for TextUtils, Integer, Boolean, etc.
"""
from typing import TYPE_CHECKING, Any, List
if TYPE_CHECKING:
    from ..vm import DalvikVM

from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode

from ..types import DalvikObject, DalvikArray


def _unwrap(arg) -> Any:
    value = arg.value if hasattr(arg, 'value') else arg
    if isinstance(value, DalvikObject) and hasattr(value, 'internal_value'):
        return value.internal_value
    return value


def _as_bytes(value) -> bytes:
    """Byte arrays live as DalvikArray/int list; strings arrive as str."""
    if isinstance(value, DalvikArray):
        return bytes(x & 0xFF for x in value.data)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, list):
        return bytes(x & 0xFF for x in value)
    return str(value).encode('utf-8', errors='replace')


def _make_byte_array(raw: bytes) -> DalvikArray:
    arr = DalvikArray('B', len(raw))
    arr.data = list(raw)
    return arr


def _make_string(text: str) -> DalvikObject:
    obj = DalvikObject("Ljava/lang/String;")
    obj.internal_value = text
    return obj


def _base64_text(args: List) -> str:
    """android.util.Base64 flags: NO_PADDING 1, NO_WRAP 2, URL_SAFE 8."""
    raw = _as_bytes(_unwrap(args[0]))
    flags = _unwrap(args[1]) if len(args) > 1 else 0
    flags = flags if isinstance(flags, int) else 0
    text = (urlsafe_b64encode(raw) if flags & 8 else b64encode(raw)).decode('ascii')
    if flags & 1:
        text = text.rstrip('=')
    if not flags & 2:
        text = "\n".join(text[i:i + 76] for i in range(0, len(text), 76)) + "\n"
    return text


def _hook_base64_encode_to_string(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Base64.encodeToString(byte[], int) -> String. Upstream had no encoder at
    all, so any app that base64-encodes its result returned nothing."""
    if not args:
        return None
    return _make_string(_base64_text(args))


def _hook_base64_encode(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Base64.encode(byte[], int) -> byte[]"""
    if not args:
        return None
    return _make_byte_array(_base64_text(args).encode('ascii'))


def _hook_base64_decode(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Base64.decode(String|byte[], int) -> byte[]"""
    if not args:
        return None
    value = _unwrap(args[0])
    raw = value.encode('ascii', errors='replace') if isinstance(value, str) else _as_bytes(value)
    raw += b'=' * (-len(raw) % 4)
    flags = _unwrap(args[1]) if len(args) > 1 else 0
    decoder = urlsafe_b64decode if isinstance(flags, int) and flags & 8 else b64decode
    try:
        return _make_byte_array(decoder(raw))
    except Exception:
        return None


def _hook_text_utils_is_empty(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """TextUtils.isEmpty(CharSequence) -> boolean"""
    if not args:
        return True
    
    arg = args[0].value if hasattr(args[0], 'value') else args[0]
    if arg is None:
        return True
    if isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
        return len(arg.internal_value) == 0
    if isinstance(arg, str):
        return len(arg) == 0
    return True


def _hook_integer_value_of(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Integer.valueOf(int) -> Integer"""
    if args:
        int_val = args[0].value if hasattr(args[0], 'value') else args[0]
        int_obj = DalvikObject("Ljava/lang/Integer;")
        int_obj.internal_value = int(int_val) if isinstance(int_val, (int, float)) else 0
        return int_obj
    return None


def _hook_boolean_boolean_value(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Boolean.booleanValue() -> boolean"""
    if args:
        bool_obj = args[0].value if hasattr(args[0], 'value') else args[0]
        if isinstance(bool_obj, DalvikObject) and hasattr(bool_obj, 'internal_value'):
            return bool_obj.internal_value
        if isinstance(bool_obj, bool):
            return bool_obj
    return False


def _hook_boolean_value_of(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """Boolean.valueOf(boolean) -> Boolean"""
    if args:
        val = args[0].value if hasattr(args[0], 'value') else args[0]
        bool_obj = DalvikObject("Ljava/lang/Boolean;")
        if isinstance(val, bool):
            bool_obj.internal_value = val
        elif isinstance(val, int):
            bool_obj.internal_value = val != 0
        elif isinstance(val, DalvikObject) and hasattr(val, 'internal_value'):
            bool_obj.internal_value = bool(val.internal_value)
        else:
            bool_obj.internal_value = False
        return bool_obj
    return None


def _hook_charsequence_tostring(vm: 'DalvikVM', args: List, trace_str: str) -> Any:
    """CharSequence.toString() -> String"""
    if args:
        cs_obj = args[0].value if hasattr(args[0], 'value') else args[0]
        if isinstance(cs_obj, DalvikObject) and hasattr(cs_obj, 'internal_value'):
            str_obj = DalvikObject("Ljava/lang/String;")
            str_obj.internal_value = cs_obj.internal_value
            return str_obj
        if isinstance(cs_obj, str):
            str_obj = DalvikObject("Ljava/lang/String;")
            str_obj.internal_value = cs_obj
            return str_obj
    return None
