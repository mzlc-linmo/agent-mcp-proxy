import unittest
from admin_auth import Auth, AuthError, password_hash

class AuthTests(unittest.TestCase):
    def test_session_logout_and_no_plaintext_password(self):
        encoded=password_hash('long-test-password')
        self.assertNotIn('long-test-password',encoded)
        auth=Auth(encoded,'admin');token,csrf=auth.login('admin','long-test-password','client')
        self.assertEqual(auth.authenticate(token)['csrf'],csrf)
        auth.logout(token)
        with self.assertRaises(AuthError):auth.authenticate(token)

    def test_failed_login_is_rate_limited_and_expiry_is_absolute(self):
        auth=Auth(password_hash('long-test-password'),'admin')
        for _ in range(5):
            with self.assertRaises(AuthError):auth.login('admin','bad','client')
        with self.assertRaises(AuthError) as raised:auth.login('admin','long-test-password','client')
        self.assertEqual(raised.exception.status,429)
        token,_=auth.login('admin','long-test-password','other')
        auth.sessions[token]['expires']=0
        with self.assertRaises(AuthError):auth.authenticate(token)

    def test_missing_hash_never_enables_default_login(self):
        with self.assertRaises(ValueError):Auth('','admin')

if __name__=='__main__':unittest.main()
