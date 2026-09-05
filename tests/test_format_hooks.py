"""Unit tests for the String.format printf hook (format_hooks.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dalvik_vm.types import RegisterValue, DalvikObject, DalvikArray
from dalvik_vm.mocks.format_hooks import java_format, _hook_string_format


class TestJavaFormat(unittest.TestCase):
    def test_conversions(self):
        cases = [
            ("x=%d", [42], "x=42"),
            ("%05d", [42], "00042"),
            ("%+d", [7], "+7"),
            ("%,d", [1234567], "1,234,567"),
            ("%(d", [-42], "(42)"),
            ("%#x", [255], "0xff"),
            ("%08X", [255], "000000FF"),
            ("%o", [8], "10"),
            ("%s and %s", ["a", "b"], "a and b"),
            ("%S", ["hi"], "HI"),
            ("%-5s|", ["ab"], "ab   |"),
            ("%.3s", ["abcdef"], "abc"),
            ("%b", [True], "true"),
            ("%b", [None], "false"),
            ("%c", [65], "A"),
            ("%.2f", [3.14159], "3.14"),
            ("%08.2f", [3.14159], "00003.14"),
            ("%,.2f", [1234.5], "1,234.50"),
            ("%.2f", [-3.14159], "-3.14"),
            ("%2$s %1$s", ["a", "b"], "b a"),
            ("100%%", [], "100%"),
            ("a%nb", [], "a\nb"),
        ]
        for fmt, argv, want in cases:
            self.assertEqual(java_format(fmt, argv), want, msg=fmt)

    def test_hook_unwraps_boxed_args(self):
        fmt = DalvikObject("Ljava/lang/String;")
        fmt.internal_value = "%s=%d"
        arr = DalvikArray("[Ljava/lang/Object;", 2)
        s = DalvikObject("Ljava/lang/String;")
        s.internal_value = "n"
        i = DalvikObject("Ljava/lang/Integer;")
        i.internal_value = 7
        arr.data = [s, i]
        trace = "Ljava/lang/String;->format(Ljava/lang/String; [Ljava/lang/Object;)Ljava/lang/String;"
        out = _hook_string_format(None, [RegisterValue(fmt), RegisterValue(arr)], trace)
        self.assertEqual(out.internal_value, "n=7")

    def test_hook_locale_overload(self):
        loc = DalvikObject("Ljava/util/Locale;")
        fmt = DalvikObject("Ljava/lang/String;")
        fmt.internal_value = "%d"
        arr = DalvikArray("[Ljava/lang/Object;", 1)
        i = DalvikObject("Ljava/lang/Integer;")
        i.internal_value = 5
        arr.data = [i]
        trace = "Ljava/lang/String;->format(Ljava/util/Locale; Ljava/lang/String; [Ljava/lang/Object;)Ljava/lang/String;"
        out = _hook_string_format(
            None, [RegisterValue(loc), RegisterValue(fmt), RegisterValue(arr)], trace)
        self.assertEqual(out.internal_value, "5")


if __name__ == "__main__":
    unittest.main()
