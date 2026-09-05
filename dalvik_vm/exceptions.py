"""Dalvik-level exception propagation.

A `throw` raises a DalvikThrow carrying the thrown object; the execution loop
catches it, looks up the method's try/catch table, and either jumps to a
matching handler or re-raises so the caller's frame can catch it. It subclasses
BaseException so it is never swallowed by the interpreter's broad
``except Exception`` guards on its way up the Python call stack.
"""


class DalvikThrow(BaseException):
    def __init__(self, exception_obj):
        super().__init__()
        self.exception_obj = exception_obj

    def type_name(self):
        cn = getattr(self.exception_obj, 'class_name', None)
        return cn or "Ljava/lang/Throwable;"
