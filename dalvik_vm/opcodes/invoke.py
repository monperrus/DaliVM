"""Invoke opcode handlers (0x6e-0x78)."""
from typing import TYPE_CHECKING
import traceback
if TYPE_CHECKING:
    from ..vm import DalvikVM
from ..types import RegisterValue, DalvikObject, DalvikArray
from .base import decode_invoke_args
from ..android_mocks import get_android_virtual_hook, get_android_static_hook

def execute_invoke_virtual(vm: 'DalvikVM'):
    """invoke-virtual {vC, vD, vE, vF, vG}, meth@BBBB (35c)"""
    method_idx, args = decode_invoke_args(vm)
    trace_str = getattr(vm, 'trace_map', {}).get(vm.pc - 1, ('', 0))[0]
    
    # Dispatch to hooks
    if hasattr(vm, 'method_hooks'):
        hook = vm.method_hooks.get_hook(trace_str)
        if hook:
            result = hook(vm, args, trace_str)
            if result is not None:
                vm.last_result = RegisterValue(result)
            vm.pc += 5
            return
    
    # Check for Android API hooks first
    android_hook = get_android_virtual_hook(trace_str)
    if android_hook:
        result = android_hook(vm, args, trace_str)
        if result is not None:
            vm.last_result = RegisterValue(result)
        else:
            vm.last_result = RegisterValue(None)
        vm.pc += 5
        return
    
    # Built-in hooks for common methods
    ret_val = _builtin_virtual_hooks(vm, args, trace_str)
    
    # If no builtin hook matched and we have a class loader, try to execute the method
    if ret_val is None and vm.class_loader:
        # Check if this is a method we should try to resolve
        # Skip known Java standard library methods that are hooked
        skip_patterns = ["StringBuilder", "PrintStream", "System"]
        should_try_resolve = not any(p in trace_str for p in skip_patterns)
        
        if should_try_resolve and "->" in trace_str:
            # Extract method info and try to execute. virtual=True so an
            # interface/abstract target re-resolves on the receiver's runtime
            # type (true virtual dispatch: Transform.apply -> Reverse.step).
            ret_val = vm.class_loader.resolve_and_execute(method_idx, args, vm, trace_str, virtual=True)
    
    if ret_val is not None:
        vm.last_result = RegisterValue(ret_val)
    else:
        vm.last_result = RegisterValue(None)
    
    vm.pc += 5

def descriptor_of(trace_str: str) -> str:
    """The argument descriptor of the invoked method, e.g. '(C)' -> 'C'.

    The overloads of append/valueOf/getBytes cannot be told apart from the
    runtime type of a register (a char and an int are both Python ints), so
    every hook that has overloads must look at the descriptor.
    """
    if "(" not in trace_str or ")" not in trace_str:
        return ""
    return trace_str[trace_str.index("(") + 1:trace_str.index(")")]


def _java_text(desc: str, arg) -> str:
    """Render an argument the way Java's String.valueOf would."""
    if desc == "C" and isinstance(arg, int):
        return chr(arg)
    if desc == "Z":
        return "true" if arg else "false"
    if desc in ("I", "S", "B", "J") and isinstance(arg, int):
        return str(arg)
    if isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
        return str(arg.internal_value)
    if isinstance(arg, DalvikArray):
        return "".join(chr(c) for c in arg.data)
    if isinstance(arg, int):
        return chr(arg)
    return str(arg)


def _map_key(k):
    """A hashable identity for a Map key.

    Two String objects with the same text must be the same key, so key on the
    string contents; box int/str directly; fall back to object identity."""
    if isinstance(k, DalvikObject) and getattr(k, 'internal_value', None) is not None:
        return ('s', k.internal_value)
    if isinstance(k, (str, int, bool)):
        return ('p', k)
    return ('o', id(k))


def _builtin_virtual_hooks(vm: 'DalvikVM', args, trace_str):
    """Built-in hooks for common virtual methods."""
    ret_val = None

    if "append" in trace_str and "Ljava/lang/StringBuilder;" in trace_str:
        sb = args[0].value
        arg = args[1].value if len(args) > 1 else None
        desc = descriptor_of(trace_str)
        if isinstance(sb, DalvikObject) and hasattr(sb, 'internal_value'):
            if desc:
                sb.internal_value += _java_text(desc, arg)
                return sb
            if isinstance(arg, int):
                sb.internal_value += chr(arg)
            elif isinstance(arg, str):
                sb.internal_value += arg
            elif isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                sb.internal_value += str(arg.internal_value)
            elif arg is not None:
                sb.internal_value += str(arg)
            ret_val = sb
            
    elif "Ljava/lang/StringBuilder;->reverse" in trace_str:
        sb = args[0].value
        if isinstance(sb, DalvikObject) and hasattr(sb, 'internal_value'):
            sb.internal_value = sb.internal_value[::-1]
            ret_val = sb

    elif "indexOf" in trace_str and "Ljava/lang/String;" in trace_str:
        s = args[0].value
        text = s.internal_value if isinstance(s, DalvikObject) and hasattr(s, 'internal_value') else s
        if isinstance(text, str) and len(args) >= 2:
            needle = args[1].value if hasattr(args[1], 'value') else args[1]
            # indexOf(int ch) passes a code point; indexOf(String) passes a String.
            if isinstance(needle, int):
                needle = chr(needle)
            elif isinstance(needle, DalvikObject) and hasattr(needle, 'internal_value'):
                needle = needle.internal_value
            ret_val = text.find(needle) if isinstance(needle, str) else -1

    elif "toString" in trace_str:
        obj = args[0].value
        if isinstance(obj, DalvikObject) and hasattr(obj, 'internal_value'):
            new_str = DalvikObject("Ljava/lang/String;")
            new_str.internal_value = obj.internal_value
            ret_val = new_str
    
    elif "println" in trace_str and "Ljava/io/PrintStream;" in trace_str:
        # Check if VM is in silent mode (during arg extraction)
        if not getattr(vm, 'silent_mode', False) and len(args) > 1:
            arg = args[1].value
            val_str = str(arg)
            if isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                val_str = arg.internal_value
            print(f"STDOUT: {val_str}")
            
    elif "length" in trace_str and "Ljava/lang/String;" in trace_str:
        s = args[0].value
        if isinstance(s, DalvikObject) and hasattr(s, 'internal_value'):
            ret_val = len(s.internal_value)
    
    elif "charAt" in trace_str and "Ljava/lang/String;" in trace_str:
        s = args[0].value
        idx = args[1].value if len(args) > 1 else 0
        if isinstance(s, DalvikObject) and hasattr(s, 'internal_value') and isinstance(idx, int):
            if 0 <= idx < len(s.internal_value):
                ret_val = ord(s.internal_value[idx])
            else:
                ret_val = 0
                
    elif "toCharArray" in trace_str:
        s = args[0].value
        # Handle DalvikObject with internal_value
        if isinstance(s, DalvikObject) and hasattr(s, 'internal_value'):
            arr = DalvikArray('C', len(s.internal_value))
            arr.data = [ord(c) for c in s.internal_value]
            ret_val = arr
        # Also handle plain Python strings (from captured args)
        elif isinstance(s, str):
            arr = DalvikArray('C', len(s))
            arr.data = [ord(c) for c in s]
            ret_val = arr

    elif "clone" in trace_str:
        arr = args[0].value
        if isinstance(arr, DalvikArray):
            new_arr = DalvikArray(arr.type_desc, arr.size)
            new_arr.data = list(arr.data)
            ret_val = new_arr
    
    elif "getBytes" in trace_str and "Ljava/lang/String;" in trace_str:
        # String.getBytes([charset]) -> byte array.
        # Java's default charset on Android is UTF-8, and getBytes("UTF-8") is
        # the common case; encoding as UTF-16LE regardless silently produced
        # twice as many bytes and wrong digests/ciphertexts downstream.
        if args and args[0] is not None:
            s = args[0].value
            text = s.internal_value if isinstance(s, DalvikObject) and hasattr(s, 'internal_value') else s
            if isinstance(text, str):
                charset = 'utf-8'
                if len(args) > 1:
                    named = args[1].value
                    if isinstance(named, DalvikObject) and hasattr(named, 'internal_value'):
                        named = named.internal_value
                    if isinstance(named, str) and named:
                        charset = named.lower().replace('utf_', 'utf-')
                try:
                    byte_data = text.encode(charset, errors='surrogatepass')
                except LookupError:
                    byte_data = text.encode('utf-8', errors='surrogatepass')
                arr = DalvikArray('B', len(byte_data))
                arr.data = list(byte_data)
                ret_val = arr
    
    elif "intern" in trace_str and "Ljava/lang/String;" in trace_str:
        # String.intern() -> returns the same string (canonical representation)
        if args and args[0] is not None:
            ret_val = args[0].value
    
    # Boolean.booleanValue() - unwrap Boolean to primitive boolean
    elif "booleanValue" in trace_str and "Boolean" in trace_str:
        if args:
            obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(obj, DalvikObject) and hasattr(obj, 'internal_value'):
                ret_val = obj.internal_value
            elif isinstance(obj, bool):
                ret_val = obj
            else:
                ret_val = False
    
    # Integer.intValue() - unwrap Integer to primitive int
    elif "intValue" in trace_str and "Integer" in trace_str:
        if args:
            obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(obj, DalvikObject) and hasattr(obj, 'internal_value'):
                ret_val = obj.internal_value
            elif isinstance(obj, int):
                ret_val = obj
            else:
                ret_val = 0
    
    # =========================================================================
    # List interface methods - work with _list_data attribute on DalvikObject
    # =========================================================================
    elif "Ljava/util/List;->add" in trace_str or "Ljava/util/ArrayList;->add" in trace_str:
        # List.add(e) -> boolean. new-instance does not run the (external)
        # <init>, so back the list lazily on first use.
        if len(args) >= 2:
            list_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(list_obj, DalvikObject):
                if not hasattr(list_obj, '_list_data'):
                    list_obj._list_data = []
                list_obj._list_data.append(args[1].value if hasattr(args[1], 'value') else args[1])
                ret_val = 1  # true

    elif "Ljava/util/List;->isEmpty" in trace_str or "Ljava/util/ArrayList;->isEmpty" in trace_str:
        if args:
            list_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            data = getattr(list_obj, '_list_data', None)
            ret_val = 1 if not data else 0

    elif "->iterator" in trace_str and any(t in trace_str for t in ("Ljava/util/List;", "Ljava/util/ArrayList;", "Ljava/util/Set;", "Ljava/util/Collection;")):
        # List.iterator() -> returns Iterator that wraps the list
        if args:
            list_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            # Create mock Iterator with reference to list data
            iterator = DalvikObject("Ljava/util/Iterator;")
            iterator._mock_type = "Iterator"
            if isinstance(list_obj, DalvikObject) and hasattr(list_obj, '_list_data'):
                iterator._list_data = list_obj._list_data
            elif isinstance(list_obj, DalvikArray):
                iterator._list_data = list_obj.data
            else:
                iterator._list_data = []
            iterator._iterator_index = 0
            ret_val = iterator
    
    elif "Ljava/util/List;->size" in trace_str or "Ljava/util/ArrayList;->size" in trace_str:
        # List.size() -> int
        if args:
            list_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(list_obj, DalvikObject) and hasattr(list_obj, '_list_data'):
                ret_val = len(list_obj._list_data)
            elif isinstance(list_obj, DalvikArray):
                ret_val = len(list_obj.data)
            else:
                ret_val = 0
    
    elif "Ljava/util/List;->get" in trace_str or "Ljava/util/ArrayList;->get" in trace_str:
        # List.get(index) -> Object
        if len(args) >= 2:
            list_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            index = args[1].value if hasattr(args[1], 'value') else args[1]
            if isinstance(list_obj, DalvikObject) and hasattr(list_obj, '_list_data'):
                if 0 <= index < len(list_obj._list_data):
                    ret_val = list_obj._list_data[index]
            elif isinstance(list_obj, DalvikArray):
                if 0 <= index < len(list_obj.data):
                    ret_val = list_obj.data[index]
    
    # =========================================================================
    # Map interface methods - work with _map_data (insertion-ordered) on the object
    # =========================================================================
    elif "Ljava/util/Map;->put" in trace_str or "Ljava/util/HashMap;->put" in trace_str:
        if len(args) >= 3:
            map_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(map_obj, DalvikObject):
                if not hasattr(map_obj, '_map_data'):
                    map_obj._map_data = {}
                k = args[1].value if hasattr(args[1], 'value') else args[1]
                val = args[2].value if hasattr(args[2], 'value') else args[2]
                prev = map_obj._map_data.get(_map_key(k))
                map_obj._map_data[_map_key(k)] = (k, val)
                ret_val = prev[1] if prev else None

    elif "Ljava/util/Map;->get" in trace_str or "Ljava/util/HashMap;->get" in trace_str:
        if len(args) >= 2:
            map_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            k = args[1].value if hasattr(args[1], 'value') else args[1]
            data = getattr(map_obj, '_map_data', {})
            entry = data.get(_map_key(k))
            ret_val = entry[1] if entry else None

    elif "Ljava/util/Map;->containsKey" in trace_str or "Ljava/util/HashMap;->containsKey" in trace_str:
        if len(args) >= 2:
            map_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            k = args[1].value if hasattr(args[1], 'value') else args[1]
            data = getattr(map_obj, '_map_data', {})
            ret_val = 1 if _map_key(k) in data else 0

    elif "Ljava/util/Map;->size" in trace_str or "Ljava/util/HashMap;->size" in trace_str:
        if args:
            map_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            ret_val = len(getattr(map_obj, '_map_data', {}))

    elif "Ljava/util/Map;->keySet" in trace_str or "Ljava/util/Map;->values" in trace_str \
            or "Ljava/util/Map;->entrySet" in trace_str:
        # Return a collection whose _list_data the iterator hooks already walk.
        if args:
            map_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            data = getattr(map_obj, '_map_data', {})
            coll = DalvikObject("Ljava/util/Set;")
            if "keySet" in trace_str:
                coll._list_data = [k for (k, _v) in data.values()]
            elif "values" in trace_str:
                coll._list_data = [v for (_k, v) in data.values()]
            else:  # entrySet -> Map.Entry objects
                coll._list_data = []
                for (k, v) in data.values():
                    entry = DalvikObject("Ljava/util/Map$Entry;")
                    entry._entry_key = k
                    entry._entry_value = v
                    coll._list_data.append(entry)
            ret_val = coll

    elif "Ljava/util/Map$Entry;->getKey" in trace_str:
        if args:
            entry = args[0].value if hasattr(args[0], 'value') else args[0]
            ret_val = getattr(entry, '_entry_key', None)

    elif "Ljava/util/Map$Entry;->getValue" in trace_str:
        if args:
            entry = args[0].value if hasattr(args[0], 'value') else args[0]
            ret_val = getattr(entry, '_entry_value', None)

    # =========================================================================
    # Iterator interface methods
    # =========================================================================
    elif "Ljava/util/Iterator;->hasNext" in trace_str:
        # Iterator.hasNext() -> boolean
        if args:
            iter_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(iter_obj, DalvikObject) and hasattr(iter_obj, '_list_data'):
                idx = getattr(iter_obj, '_iterator_index', 0)
                ret_val = idx < len(iter_obj._list_data)
            else:
                ret_val = False
    
    elif "Ljava/util/Iterator;->next" in trace_str:
        # Iterator.next() -> Object
        if args:
            iter_obj = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(iter_obj, DalvikObject) and hasattr(iter_obj, '_list_data'):
                idx = getattr(iter_obj, '_iterator_index', 0)
                if idx < len(iter_obj._list_data):
                    ret_val = iter_obj._list_data[idx]
                    iter_obj._iterator_index = idx + 1
                else:
                    ret_val = None
            else:
                ret_val = None
    
    return ret_val

def execute_invoke_super(vm: 'DalvikVM'):
    """invoke-super {vC, vD, vE, vF, vG}, meth@BBBB (35c)"""
    # Treat like virtual for now
    execute_invoke_virtual(vm)

def execute_invoke_direct(vm: 'DalvikVM'):
    """invoke-direct {vC, vD, vE, vF, vG}, meth@BBBB (35c)"""
    method_idx, args = decode_invoke_args(vm)
    trace_str = getattr(vm, 'trace_map', {}).get(vm.pc - 1, ('', 0))[0]
    
    # Handle String.<init>([C)
    if "Ljava/lang/String;-><init>" in trace_str:
        if len(args) >= 2:
            str_obj = args[0].value
            char_arr = args[1].value
            if isinstance(str_obj, DalvikObject) and isinstance(char_arr, DalvikArray):
                chars = "".join([chr(c) for c in char_arr.data])
                str_obj.internal_value = chars
    
    # Handle StringBuilder.<init>()
    elif "Ljava/lang/StringBuilder;-><init>" in trace_str:
        sb = args[0].value
        if isinstance(sb, DalvikObject):
            # new StringBuilder(String)/(CharSequence) seeds the buffer; the
            # (int capacity) and () overloads start empty. Ignoring the String
            # arg silently dropped the input of e.g. new StringBuilder(s).reverse().
            seed = ""
            if len(args) >= 2 and "(I)" not in trace_str:
                a = args[1].value if hasattr(args[1], 'value') else args[1]
                if isinstance(a, DalvikObject) and getattr(a, 'internal_value', None) is not None:
                    seed = a.internal_value
                elif isinstance(a, str):
                    seed = a
            sb.internal_value = seed

    # Any other invoke-direct is an app constructor or a private method: run it.
    # Without this, <init> never executes, so no instance field is ever set and
    # every constructed object is blank (Rot(13).shift read back as 0).
    elif "->" in trace_str and vm.class_loader:
        vm.class_loader.resolve_and_execute(method_idx, args, vm, trace_str)

    vm.pc += 5

def _builtin_static_hooks(vm: 'DalvikVM', args, trace_str):
    """Built-in hooks for common Java static methods.
    
    These implement actual logic (not mocks) for Java standard library methods.
    """
    ret_val = None
    
    # String.valueOf - converts various types to String
    if "String;->valueOf" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            str_obj = DalvikObject("Ljava/lang/String;")
            if arg is None:
                str_obj.internal_value = "null"
            elif isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                str_obj.internal_value = str(arg.internal_value)
            elif isinstance(arg, DalvikArray):
                # char[] variant
                if arg.type_desc in ('C', '[C'):
                    str_obj.internal_value = ''.join(chr(c) for c in arg.data)
                else:
                    str_obj.internal_value = str(arg.data)
            elif isinstance(arg, bool):
                str_obj.internal_value = "true" if arg else "false"
            else:
                str_obj.internal_value = str(arg)
            ret_val = str_obj
    
    # Integer.parseInt - parses string to int
    elif "Integer;->parseInt" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            try:
                if isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                    ret_val = int(arg.internal_value)
                elif isinstance(arg, str):
                    ret_val = int(arg)
                else:
                    ret_val = 0
            except (ValueError, TypeError):
                ret_val = 0
    
    # Long.parseLong - parses string to long
    elif "Long;->parseLong" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            try:
                if isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                    ret_val = int(arg.internal_value)
                elif isinstance(arg, str):
                    ret_val = int(arg)
                else:
                    ret_val = 0
            except (ValueError, TypeError):
                ret_val = 0
    
    # Math.abs - absolute value
    elif "Math;->abs" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            if isinstance(arg, (int, float)):
                ret_val = abs(arg)
            else:
                ret_val = 0
    
    # Math.max / Math.min
    elif "Math;->max" in trace_str:
        if len(args) >= 2:
            a = args[0].value if hasattr(args[0], 'value') else args[0]
            b = args[1].value if hasattr(args[1], 'value') else args[1]
            ret_val = max(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else 0
    
    elif "Math;->min" in trace_str:
        if len(args) >= 2:
            a = args[0].value if hasattr(args[0], 'value') else args[0]
            b = args[1].value if hasattr(args[1], 'value') else args[1]
            ret_val = min(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else 0
    
    # Arrays.copyOf - copy array with new size
    elif "Arrays;->copyOf" in trace_str:
        if len(args) >= 2:
            src_arr = args[0].value if hasattr(args[0], 'value') else args[0]
            new_len = args[1].value if hasattr(args[1], 'value') else args[1]
            if isinstance(src_arr, DalvikArray) and isinstance(new_len, int):
                new_arr = DalvikArray(src_arr.type_desc, new_len)
                copy_len = min(len(src_arr.data), new_len)
                new_arr.data = list(src_arr.data[:copy_len]) + [0] * (new_len - copy_len)
                ret_val = new_arr
    
    # TextUtils.isEmpty() - check if CharSequence is null or empty
    elif "TextUtils;->isEmpty" in trace_str:
        if not args:
            ret_val = True
        else:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            if arg is None:
                ret_val = True
            elif isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                ret_val = len(arg.internal_value) == 0 if arg.internal_value else True
            elif isinstance(arg, str):
                ret_val = len(arg) == 0
            else:
                ret_val = True
    
    # Boolean.valueOf(boolean) - wrap boolean in Boolean object
    elif "Boolean;->valueOf" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            bool_obj = DalvikObject("Ljava/lang/Boolean;")
            if isinstance(arg, bool):
                bool_obj.internal_value = arg
            elif isinstance(arg, int):
                bool_obj.internal_value = arg != 0
            elif isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                bool_obj.internal_value = bool(arg.internal_value)
            else:
                bool_obj.internal_value = False
            ret_val = bool_obj
    
    # Integer.valueOf(int) - wrap int in Integer object
    elif "Integer;->valueOf" in trace_str:
        if args:
            arg = args[0].value if hasattr(args[0], 'value') else args[0]
            int_obj = DalvikObject("Ljava/lang/Integer;")
            if isinstance(arg, int):
                int_obj.internal_value = arg
            elif isinstance(arg, DalvikObject) and hasattr(arg, 'internal_value'):
                int_obj.internal_value = int(arg.internal_value) if arg.internal_value else 0
            else:
                int_obj.internal_value = 0
            ret_val = int_obj
    
    # System.arraycopy(Object src, int srcPos, Object dest, int destPos, int length)
    # --- native floor -----------------------------------------------------
    # java.lang.Character bottoms out in CharacterData (native/tabular), so
    # framework methods that reach it (Integer.parseInt, Long.parseLong, ...)
    # need these leaves hooked for the real bytecode above them to complete.
    elif "Character;->digit" in trace_str and len(args) >= 2:
        cp = args[0].value if hasattr(args[0], 'value') else args[0]
        radix = args[1].value if hasattr(args[1], 'value') else args[1]
        ret_val = -1
        if isinstance(cp, int) and isinstance(radix, int) and 2 <= radix <= 36:
            if 0x30 <= cp <= 0x39:
                v = cp - 0x30
            elif 0x61 <= cp <= 0x7A:
                v = cp - 0x61 + 10
            elif 0x41 <= cp <= 0x5A:
                v = cp - 0x41 + 10
            else:
                v = -1
            ret_val = v if 0 <= v < radix else -1

    elif "Character;->isDigit" in trace_str and args:
        cp = args[0].value if hasattr(args[0], 'value') else args[0]
        ret_val = 1 if isinstance(cp, int) and 0x30 <= cp <= 0x39 else 0

    elif "Character;->toLowerCase" in trace_str and args:
        cp = args[0].value if hasattr(args[0], 'value') else args[0]
        ret_val = ord(chr(cp).lower()) if isinstance(cp, int) else cp

    elif "Character;->toUpperCase" in trace_str and args:
        cp = args[0].value if hasattr(args[0], 'value') else args[0]
        ret_val = ord(chr(cp).upper()) if isinstance(cp, int) else cp

    elif "Character;->forDigit" in trace_str and len(args) >= 2:
        digit = args[0].value if hasattr(args[0], 'value') else args[0]
        radix = args[1].value if hasattr(args[1], 'value') else args[1]
        if isinstance(digit, int) and isinstance(radix, int) and 0 <= digit < radix <= 36:
            ret_val = ord("0123456789abcdefghijklmnopqrstuvwxyz"[digit])
        else:
            ret_val = 0

    elif "System;->arraycopy" in trace_str:
        if len(args) >= 5:
            src = args[0].value if hasattr(args[0], 'value') else args[0]
            src_pos = args[1].value if hasattr(args[1], 'value') else args[1]
            dest = args[2].value if hasattr(args[2], 'value') else args[2]
            dest_pos = args[3].value if hasattr(args[3], 'value') else args[3]
            length = args[4].value if hasattr(args[4], 'value') else args[4]
            
            if isinstance(src, DalvikArray) and isinstance(dest, DalvikArray):
                # Ensure positions are valid integers
                src_pos = int(src_pos) if src_pos else 0
                dest_pos = int(dest_pos) if dest_pos else 0
                length = int(length) if length else 0
                
                # Perform the copy
                for i in range(length):
                    if src_pos + i < len(src.data) and dest_pos + i < len(dest.data):
                        dest.data[dest_pos + i] = src.data[src_pos + i]
        # Return special sentinel to indicate hook matched (void method)
        ret_val = "VOID_HOOK_HANDLED"
    
    return ret_val


def execute_invoke_static(vm: 'DalvikVM'):
    """invoke-static {vD, vE, vF, vG, vA}, meth@CCCC (35c)"""
    method_idx, args = decode_invoke_args(vm)
    trace_str = getattr(vm, 'trace_map', {}).get(vm.pc - 1, ('', 0))[0]
    
    # Verbose output for argument values
    if getattr(vm, 'verbose', False):
        arg_vals = []
        for a in args:
            val = a.value if hasattr(a, 'value') else a
            if isinstance(val, int):
                arg_vals.append(str(val))
            elif isinstance(val, DalvikObject) and hasattr(val, 'internal_value'):
                arg_vals.append(f'"{val.internal_value}"')
            elif isinstance(val, DalvikArray):
                arg_vals.append(f"Array[{val.size}]")
            else:
                arg_vals.append(str(val)[:20])
        print(f"        invoke-static args: ({', '.join(arg_vals)})")
    
    # Check for built-in Java static method hooks first (actual logic)
    ret_val = _builtin_static_hooks(vm, args, trace_str)
    if ret_val is not None:
        if getattr(vm, 'verbose', False):
            if isinstance(ret_val, int):
                print(f"        => {ret_val}")
            elif isinstance(ret_val, DalvikObject) and hasattr(ret_val, 'internal_value'):
                print(f"        => \"{ret_val.internal_value}\"")
            else:
                print(f"        => {ret_val}")
        vm.last_result = RegisterValue(ret_val)
        vm.pc += 5
        return
    
    # Check for Android API hooks (mocks/stubs)
    android_hook = get_android_static_hook(trace_str)
    if android_hook:
        result = android_hook(vm, args, trace_str)
        if result is not None:
            vm.last_result = RegisterValue(result)
        else:
            vm.last_result = RegisterValue(None)
        vm.pc += 5
        return
    
    # Check for user hook
    if vm.hook:
        result = vm.hook(method_idx, args)
        if result is not None:
            vm.last_result = RegisterValue(result)
            vm.pc += 5
            return
    
    # Try recursive execution
    if vm.method_resolver:
        try:
            target_code, target_regs_size, target_trace = vm.method_resolver(method_idx)
            if target_code:
                from ..vm import DalvikVM as VM
                new_vm = VM(target_code, vm.strings, target_regs_size, 
                           method_resolver=vm.method_resolver)
                new_vm.trace_map = target_trace
                
                # Pass arguments
                arg_count = len(args)
                start_reg = target_regs_size - arg_count
                for i, arg in enumerate(args):
                    new_vm.registers[start_reg + i] = arg
                
                # Execute
                from . import dispatch
                while new_vm.pc < len(target_code) and not new_vm.finished:
                    dispatch(new_vm)
                
                result = getattr(new_vm, 'last_result', RegisterValue(None))
                vm.last_result = result
                # Verbose output for return value
                if getattr(vm, 'verbose', False) and result and result.value is not None:
                    ret_val = result.value
                    if isinstance(ret_val, int):
                        print(f"        => {ret_val}")
                    elif isinstance(ret_val, DalvikObject) and hasattr(ret_val, 'internal_value'):
                        print(f"        => \"{ret_val.internal_value}\"")
                    else:
                        print(f"        => {ret_val}")
                vm.pc += 5
                return
        except Exception as e:
            print(f"Recursive execution error: {e}")
            traceback.print_exc()
    
    # Try class loader for cross-class static method calls
    if vm.class_loader:
        # Call the method - it may modify arguments in place (for void methods)
        # Pass trace_str for reliable method lookup in multi-dex APKs.
        ret_val = vm.class_loader.resolve_and_execute(method_idx, args, vm, trace_str)
        # Verbose output for return value
        if getattr(vm, 'verbose', False) and ret_val is not None:
            if isinstance(ret_val, int):
                print(f"        => {ret_val}")
            elif isinstance(ret_val, DalvikObject) and hasattr(ret_val, 'internal_value'):
                print(f"        => \"{ret_val.internal_value}\"")
            else:
                print(f"        => {ret_val}")
        # For void methods ret_val is None but the call still happened
        if ret_val is not None:
            vm.last_result = RegisterValue(ret_val)
        else:
            vm.last_result = RegisterValue(None)
        vm.pc += 5
        return
    
    vm.last_result = RegisterValue(None)
    vm.pc += 5

def execute_invoke_interface(vm: 'DalvikVM'):
    """invoke-interface {vC, vD, vE, vF, vG}, meth@BBBB (35c)"""
    execute_invoke_virtual(vm)

# Range variants (3rc format)
def _decode_invoke_range_args(vm: 'DalvikVM'):
    """
    Decode invoke-kind/range arguments (3rc format).
    Format: AA|op BBBB CCCC
    - AA = argument count
    - BBBB = method index
    - CCCC = starting register
    Returns (method_idx, [arg_registers])
    """
    arg_count = vm.bytecode[vm.pc]
    method_idx = vm.bytecode[vm.pc + 1] | (vm.bytecode[vm.pc + 2] << 8)
    start_reg = vm.bytecode[vm.pc + 3] | (vm.bytecode[vm.pc + 4] << 8)
    
    args = [vm.registers[start_reg + i] for i in range(arg_count)]
    return method_idx, args

def execute_invoke_virtual_range(vm: 'DalvikVM'):
    """invoke-virtual/range {vCCCC .. vNNNN}, meth@BBBB (3rc)"""
    method_idx, args = _decode_invoke_range_args(vm)
    trace_str = getattr(vm, 'trace_map', {}).get(vm.pc - 1, ('', 0))[0]
    
    # Built-in hooks for common methods
    ret_val = _builtin_virtual_hooks(vm, args, trace_str)
    
    # Try class loader for resolution
    if ret_val is None and vm.class_loader:
        ret_val = vm.class_loader.resolve_and_execute(method_idx, args, vm, trace_str)
    
    if ret_val is not None:
        vm.last_result = RegisterValue(ret_val)
    else:
        vm.last_result = RegisterValue(None)
    
    vm.pc += 5

def execute_invoke_static_range(vm: 'DalvikVM'):
    """invoke-static/range {vCCCC .. vNNNN}, meth@BBBB (3rc)"""
    method_idx, args = _decode_invoke_range_args(vm)
    trace_str = getattr(vm, 'trace_map', {}).get(vm.pc - 1, ('', 0))[0]
    
    # Check builtin static hooks first
    ret_val = _builtin_static_hooks(vm, args, trace_str)
    if ret_val is not None:
        # Handle void methods (sentinel value)
        if ret_val == "VOID_HOOK_HANDLED":
            vm.last_result = RegisterValue(None)
        else:
            vm.last_result = RegisterValue(ret_val)
        vm.pc += 5
        return
    
    # Try class loader for resolution
    if vm.class_loader:
        ret_val = vm.class_loader.resolve_and_execute(method_idx, args, vm, trace_str)
        if ret_val is not None:
            vm.last_result = RegisterValue(ret_val)
        else:
            vm.last_result = RegisterValue(None)
        vm.pc += 5
        return
    
    vm.last_result = RegisterValue(None)
    vm.pc += 5

execute_invoke_super_range = execute_invoke_virtual_range
execute_invoke_direct_range = execute_invoke_virtual_range
execute_invoke_interface_range = execute_invoke_virtual_range
