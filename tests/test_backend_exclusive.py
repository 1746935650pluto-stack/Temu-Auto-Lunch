import unittest
import threading
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen
from unittest.mock import patch

import server


class BackendExclusiveTests(unittest.TestCase):
    def test_exclusive_listener_can_restart_after_finishing_a_local_request(self):
        class EmptyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(204)
                self.end_headers()
            def log_message(self, *args):
                pass
        first = server.ThreadingHTTPServer(('127.0.0.1', 0), EmptyHandler)
        thread = threading.Thread(target=lambda: first.serve_forever(poll_interval=0.01))
        thread.start()
        try:
            address = first.server_address
            with urlopen(f'http://{address[0]}:{address[1]}/', timeout=3) as response:
                self.assertEqual(response.status, 204)
        finally:
            first.shutdown()
            thread.join(3)
            first.server_close()
        second = server.ThreadingHTTPServer(address, EmptyHandler)
        second.server_close()

    def test_second_actual_bind_fails_before_recovery_or_workers(self):
        first = server.ThreadingHTTPServer(('127.0.0.1', 0), server.AppHandler)
        self.addCleanup(first.server_close)
        port = first.server_address[1]
        with patch.dict(server.os.environ, {'HOST': '127.0.0.1', 'PORT': str(port)}), \
             patch.object(server, 'shared_automation') as service, \
             patch.object(server, 'resume_queued_automation_jobs_after_start') as resume, \
             patch.object(server.os, 'chdir'):
            with self.assertRaises(OSError):
                server.main()
        service.assert_not_called()
        resume.assert_not_called()


if __name__ == '__main__':
    unittest.main()
