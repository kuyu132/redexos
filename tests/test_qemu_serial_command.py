import pathlib
import re
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from qemu_serial_command import execute


class CommandFramingTests(unittest.TestCase):
    def run_command(self, output, code=0, drop_initial_wakeup=False):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='uart-test-') as tmp:
            path = pathlib.Path(tmp)/'s'
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path)); server.listen(1)
            def serve():
                try:
                    with server.accept()[0] as connection:
                        connection.recv(1)
                        if drop_initial_wakeup:
                            connection.recv(1)
                        connection.sendall(b'\x1b[32mroot:~# \x1b[0m')
                        request = bytearray()
                        while not request.endswith(b'\r'):
                            request.extend(connection.recv(4096))
                        token = re.search(rb'QEMU_CMD_[a-f0-9]+', request).group()
                        separator = b'\n' if b'; echo; echo '+token in request else b''
                        connection.sendall(token+b'_BEGIN\r\n'+output+separator+token+b'_END:'+str(code).encode()+b'\r\n')
                finally:
                    server.close()
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            result = execute(path, 'printf value', timeout=2)
            worker.join(timeout=2)
            return result

    def test_stdout_without_newline(self):
        self.assertEqual(self.run_command(b'value'), (0, 'value'))

    def test_existing_newlines_are_preserved(self):
        self.assertEqual(self.run_command(b'line\n\n'), (0, 'line\n\n'))

    def test_empty_output_and_failed_exit(self):
        self.assertEqual(self.run_command(b'', 37), (37, ''))

    def test_lost_initial_wakeup_is_retried(self):
        self.assertEqual(self.run_command(b'recovered', drop_initial_wakeup=True), (0, 'recovered'))
