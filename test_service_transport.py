import unittest
from service_transport import validate_target, auth_headers, address_allowed, TargetError

class TargetTests(unittest.TestCase):
    def test_public_https_and_explicit_private_http(self):
        validate_target('https://example.com/mcp',False)
        validate_target('http://192.168.1.8:8080/mcp',True)
        for url,private in [('http://example.com/mcp',False),('http://192.168.1.8/mcp',False),('http://127.0.0.1/',True),('http://169.254.169.254/',True),('https://user:pass@example.com/mcp',False)]:
            with self.subTest(url=url),self.assertRaises(TargetError):validate_target(url,private)
    def test_dns_address_classification(self):
        self.assertFalse(address_allowed('169.254.169.254',True))
        self.assertFalse(address_allowed('::1',True))
        self.assertFalse(address_allowed('10.0.0.8',False))
        self.assertTrue(address_allowed('10.0.0.8',True))
        self.assertTrue(address_allowed('2606:4700:4700::1111',False))
    def test_auth_does_not_accept_reserved_or_injected_headers(self):
        self.assertEqual(auth_headers({'mode':'none'}),{})
        self.assertEqual(auth_headers({'mode':'bearer','value':'abc'}),{'Authorization':'Bearer abc'})
        for header in ['Host','Cookie','X-Forwarded-User','Connection','from']:
            with self.assertRaises(TargetError):auth_headers({'mode':'api-key','header':header,'value':'abc'})
        with self.assertRaises(TargetError):auth_headers({'mode':'bearer','value':'x\r\ny'})

if __name__=='__main__':unittest.main()
