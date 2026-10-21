import unittest
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.exceptions import InvalidStatus
from proxy import NoRedirectConnect

class TransportTests(unittest.TestCase):
    def test_sdk_transport_raw_error_logs_are_suppressed(self):
        import logging
        import io
        from proxy import secure_logging
        stream=io.StringIO();handler=logging.StreamHandler(stream)
        root=logging.getLogger();root.addHandler(handler)
        try:
            secure_logging()
            logging.getLogger('mcp.client.streamable_http').error('synthetic-secret-must-not-leak')
            logging.getLogger('websockets.client').error('synthetic-secret-must-not-leak')
            self.assertNotIn('synthetic-secret',stream.getvalue())
        finally:root.removeHandler(handler)

    def test_cross_origin_and_same_origin_redirects_are_never_followed(self):
        connection=NoRedirectConnect('wss://api.xiaozhi.me/mcp/?token=synthetic')
        for target in ['wss://evil.example/mcp/','wss://api.xiaozhi.me/other']:
            error=InvalidStatus(Response(302,'Found',Headers({'Location':target})))
            self.assertIs(connection.process_redirect(error),error)

if __name__=='__main__':unittest.main()
