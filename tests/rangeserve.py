"""Static file server for the tests: threaded, with Range support (PMTiles needs it) and a deep listen backlog.

The default backlog of 5 refuses connections when the map loads dozens of tiles at once, which showed up as
intermittent ERR_CONNECTION_REFUSED console errors. Usage: python tests/rangeserve.py <port>  (serves the cwd).
"""
import http.server
import os
import sys

try:
    from RangeHTTPServer import RangeRequestHandler
except ImportError:  # a vendored copy in tools/ (git-ignored) also works
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "rangehttpserver"))
    from RangeHTTPServer import RangeRequestHandler

http.server.ThreadingHTTPServer.request_queue_size = 128
http.server.test(HandlerClass=RangeRequestHandler, ServerClass=http.server.ThreadingHTTPServer,
                 port=int(sys.argv[1]), bind="127.0.0.1")
