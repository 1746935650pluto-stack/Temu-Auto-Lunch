"""Loopback-only fixture server with a file allowlist; no application or DB import."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {'/tests/virtual_list_browser_review.html': ('tests/virtual_list_browser_review.html', 'text/html'),
         '/styles.css': ('styles.css', 'text/css'), '/virtual-list.js': ('virtual-list.js', 'text/javascript'),
         '/shared-automation.js': ('shared-automation.js', 'text/javascript')}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        target = FILES.get(self.path.split('?', 1)[0])
        if not target:
            self.send_error(404)
            return
        path, content_type = target
        data = (ROOT / path).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8879), Handler).serve_forever()
