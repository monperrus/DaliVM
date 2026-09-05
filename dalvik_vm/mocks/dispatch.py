"""Hook dispatch registry and lookup functions.

Maps method patterns to their corresponding hook functions.
"""
from typing import Optional, Callable, Dict, Any

from ..types import DalvikObject
from .config import mock_config
from .context_hooks import (
    _hook_context_get_package_manager,
    _hook_context_get_package_name,
    _hook_pm_get_package_info,
    _hook_pm_get_installed_packages,
    _hook_signature_to_byte_array,
    _hook_signature_to_chars_string,
    _hook_signature_hashcode,
)
from .utility_hooks import (
    _hook_base64_encode,
    _hook_base64_encode_to_string,
    _hook_base64_decode,
)
from .reflection_hooks import (
    _hook_class_forname,
    _hook_class_getmethod,
    _hook_class_getfield,
    _hook_method_invoke,
    _hook_field_get,
    _hook_throwable_getcause,
)
from .http_hooks import (
    _hook_url_open_connection,
    _hook_conn_set_request_method,
    _hook_conn_set_request_property,
    _hook_conn_get_response_code,
    _hook_conn_get_content_length,
    _hook_conn_get_input_stream,
    _hook_conn_noop,
    _hook_stream_read,
    _hook_baos_write,
    _hook_baos_to_bytes,
    _hook_baos_to_string,
    _hook_baos_size,
)


# Virtual method hooks: pattern -> hook_fn
ANDROID_VIRTUAL_HOOKS: Dict[str, Callable] = {
    # Context methods
    "Context;->getPackageManager": _hook_context_get_package_manager,
    "Context;->getPackageName": _hook_context_get_package_name,
    
    # PackageManager methods
    "PackageManager;->getPackageInfo": _hook_pm_get_package_info,
    "PackageManager;->getInstalledPackages": _hook_pm_get_installed_packages,
    
    # Signature methods
    "Signature;->toByteArray": _hook_signature_to_byte_array,
    "Signature;->toCharsString": _hook_signature_to_chars_string,
    "Signature;->hashCode": _hook_signature_hashcode,
    
    # Reflection - virtual methods
    "Class;->getMethod": _hook_class_getmethod,
    "Class;->getField": _hook_class_getfield,
    "Method;->invoke": _hook_method_invoke,
    "Field;->get": _hook_field_get,
    
    # Throwable
    "Throwable;->getCause": _hook_throwable_getcause,

    # java.net HTTP -- backed by a real urllib request (see http_hooks.py).
    # Substrings, so HttpURLConnection matches the URLConnection patterns too.
    "URL;->openConnection": _hook_url_open_connection,
    "URLConnection;->setRequestMethod": _hook_conn_set_request_method,
    "URLConnection;->setRequestProperty": _hook_conn_set_request_property,
    "URLConnection;->addRequestProperty": _hook_conn_set_request_property,
    "URLConnection;->getResponseCode": _hook_conn_get_response_code,
    "URLConnection;->getContentLength": _hook_conn_get_content_length,
    "URLConnection;->getInputStream": _hook_conn_get_input_stream,
    "URLConnection;->connect": _hook_conn_noop,
    "URLConnection;->disconnect": _hook_conn_noop,
    "URLConnection;->setConnectTimeout": _hook_conn_noop,
    "URLConnection;->setReadTimeout": _hook_conn_noop,
    "URLConnection;->setDoInput": _hook_conn_noop,
    "URLConnection;->setDoOutput": _hook_conn_noop,
    "URLConnection;->setUseCaches": _hook_conn_noop,
    "URLConnection;->setInstanceFollowRedirects": _hook_conn_noop,

    # java.io stream plumbing apps read the response with.
    "InputStream;->read": _hook_stream_read,   # read([B) / read([BII)
    "InputStream;->close": _hook_conn_noop,
    "ByteArrayOutputStream;->write": _hook_baos_write,
    "ByteArrayOutputStream;->toByteArray": _hook_baos_to_bytes,
    "ByteArrayOutputStream;->toString": _hook_baos_to_string,
    "ByteArrayOutputStream;->size": _hook_baos_size,
    "ByteArrayOutputStream;->close": _hook_conn_noop,
}

# Static method hooks
ANDROID_STATIC_HOOKS: Dict[str, Callable] = {
    # Reflection - static methods
    "Class;->forName": _hook_class_forname,

    # android.util.Base64 (encodeToString must come before encode: substring match)
    "Base64;->encodeToString": _hook_base64_encode_to_string,
    "Base64;->encode": _hook_base64_encode,
    "Base64;->decode": _hook_base64_decode,
}


def get_android_virtual_hook(trace_str: str) -> Optional[Callable]:
    """Find a matching virtual hook for the given trace string."""
    for pattern, hook in ANDROID_VIRTUAL_HOOKS.items():
        if pattern in trace_str:
            return hook
    return None


def get_android_static_hook(trace_str: str) -> Optional[Callable]:
    """Find a matching static hook for the given trace string."""
    for pattern, hook in ANDROID_STATIC_HOOKS.items():
        if pattern in trace_str:
            return hook
    return None


# =============================================================================
# Static Field Mocks
# =============================================================================

def _create_boolean_true():
    obj = DalvikObject("Ljava/lang/Boolean;")
    obj.internal_value = True
    return obj

def _create_boolean_false():
    obj = DalvikObject("Ljava/lang/Boolean;")
    obj.internal_value = False
    return obj


ANDROID_STATIC_FIELDS: Dict[str, Any] = {
    # Android OS
    "Landroid/os/Build$VERSION;->SDK_INT": mock_config.sdk_int,
    
    # Boolean constants
    "Ljava/lang/Boolean;->TRUE": _create_boolean_true(),
    "Ljava/lang/Boolean;->FALSE": _create_boolean_false(),
    
    # Primitive type classes (for reflection)
    "Ljava/lang/Integer;->TYPE": "int",
    "Ljava/lang/Long;->TYPE": "long",
    "Ljava/lang/Boolean;->TYPE": "boolean",
    "Ljava/lang/Byte;->TYPE": "byte",
    "Ljava/lang/Character;->TYPE": "char",
    "Ljava/lang/Short;->TYPE": "short",
    "Ljava/lang/Float;->TYPE": "float",
    "Ljava/lang/Double;->TYPE": "double",
    "Ljava/lang/Void;->TYPE": "void",
}


def get_android_static_field(field_sig: str) -> Optional[Any]:
    """Get a mock value for an Android static field."""
    return ANDROID_STATIC_FIELDS.get(field_sig)
