#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""What every fake translation platform server needs (#3337): an HTTP
server on 127.0.0.1 in a thread, and a way for a test to run the GLib
main loop until the client has its reply. fakeweblate.py is the first
such fake; another platform's fake serves its own API the same way.

Never point Virtaal at a real server from a test.
"""

import socketserver
import threading
import time
from http.server import ThreadingHTTPServer


class LoopbackServer(ThreadingHTTPServer):
    """http.server without its reverse lookup of the address
    (HTTPServer.server_bind calls socket.getfqdn), which on macOS CI
    runners takes over 30 s for 127.0.0.1."""

    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FakeServer:
    """Serves handler (a BaseHTTPRequestHandler class) on a free port
    of 127.0.0.1 while started; the handler finds this object as
    self.server.fake."""

    handler = None

    def __init__(self):
        self._server = None

    def start(self):
        self._server = LoopbackServer(('127.0.0.1', 0), self.handler)
        self._server.fake = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def url(self):
        """The server's address, as a user types it."""
        return 'http://127.0.0.1:%d/' % self._server.server_address[1]

    def serve(self, port):
        """Serves on port in this thread, for hand checks."""
        server = LoopbackServer(('127.0.0.1', port), self.handler)
        server.fake = self
        server.serve_forever()


def run_until(done, timeout=10):
    """Runs the GLib main loop until done() is true, or fails after
    timeout seconds. A 20 ms tick keeps the loop from blocking for
    good once the last request is answered."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    tick = GLib.timeout_add(20, lambda: True)
    deadline = time.monotonic() + timeout
    try:
        while not done():
            if time.monotonic() > deadline:
                raise AssertionError('Still waiting after %s s' % timeout)
            context.iteration(True)
    finally:
        GLib.source_remove(tick)
