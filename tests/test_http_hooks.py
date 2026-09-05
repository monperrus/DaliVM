"""Unit tests for the java.net HTTP / java.io stream hooks (http_hooks.py).

These drive the hook functions directly against a throwaway loopback server, so
they need no dex or SDK -- just prove the URL -> connection -> InputStream ->
ByteArrayOutputStream chain reads the real response bytes.
"""
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dalvik_vm.types import RegisterValue, DalvikObject, DalvikArray
from dalvik_vm.mocks import http_hooks as H

BODY = b"hello \x00\x01\xff world\n" + b"".join(b"n%02d\n" % i for i in range(20))


def _rv(x):
    return RegisterValue(x)


class TestHttpHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = BODY

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        host, port = cls.httpd.server_address
        cls.url = f"http://{host}:{port}/x"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _connect(self):
        url = DalvikObject("Ljava/net/URL;")
        H._hook_url_init(None, [_rv(url), _rv(self.url)], "")
        conn = H._hook_url_open_connection(None, [_rv(url)], "")
        H._hook_conn_set_request_method(None, [_rv(conn), _rv("GET")], "")
        return conn

    def test_response_code(self):
        conn = self._connect()
        code = H._hook_conn_get_response_code(None, [_rv(conn)], "")
        self.assertEqual(code, 200)

    def test_read_buffer_loop(self):
        conn = self._connect()
        stream = H._hook_conn_get_input_stream(None, [_rv(conn)], "")
        out = bytearray()
        buf = DalvikArray("B", 8)  # small buffer forces several reads
        while True:
            n = H._hook_stream_read(None, [_rv(stream), _rv(buf)], "")
            if n == -1:
                break
            out.extend(buf.data[i] & 0xFF for i in range(n))
        self.assertEqual(bytes(out), BODY)

    def test_read_offset_length(self):
        conn = self._connect()
        stream = H._hook_conn_get_input_stream(None, [_rv(conn)], "")
        buf = DalvikArray("B", 64)
        n = H._hook_stream_read(None, [_rv(stream), _rv(buf), _rv(4), _rv(10)], "")
        self.assertEqual(n, 10)
        self.assertEqual(bytes(b & 0xFF for b in buf.data[4:14]), BODY[:10])

    def test_read_single_byte(self):
        conn = self._connect()
        stream = H._hook_conn_get_input_stream(None, [_rv(conn)], "")
        first = H._hook_stream_read(None, [_rv(stream)], "")  # read() no buffer
        self.assertEqual(first, BODY[0])

    def test_byte_array_output_stream(self):
        baos = DalvikObject("Ljava/io/ByteArrayOutputStream;")
        H._hook_baos_init(None, [_rv(baos)], "")
        src = DalvikArray("B", 5)
        src.data = [0x41, 0x42, 0x43, 0x44, 0x45]  # ABCDE
        H._hook_baos_write(None, [_rv(baos), _rv(src), _rv(1), _rv(3)], "")  # BCD
        H._hook_baos_write(None, [_rv(baos), _rv(0x21)], "")  # '!'
        self.assertEqual(H._hook_baos_size(None, [_rv(baos)], ""), 4)
        s = H._hook_baos_to_string(None, [_rv(baos)], "")
        self.assertEqual(s.internal_value, "BCD!")
        arr = H._hook_baos_to_bytes(None, [_rv(baos)], "")
        self.assertEqual(bytes(arr.data), b"BCD!")

    def test_missing_url_is_graceful(self):
        conn = H._hook_url_open_connection(None, [_rv(DalvikObject("Ljava/net/URL;"))], "")
        self.assertEqual(H._hook_conn_get_response_code(None, [_rv(conn)], ""), -1)


if __name__ == "__main__":
    unittest.main()
