import unittest
from pathlib import Path

class StaticTests(unittest.TestCase):
    def test_local_assets_and_safe_rendering(self):
        page=Path('static/index.html').read_text();script=Path('static/admin.js').read_text()
        self.assertIn('/static/admin.js',page)
        self.assertIn('type="password"',page)
        self.assertNotIn('innerHTML',script)
        self.assertNotIn('eval(',script)
        self.assertIn('textContent',script)
        self.assertIn('HTTP',page)
        self.assertIn('aria-live',page)
        self.assertIn('zh:',script);self.assertIn('en:',script)
        self.assertNotIn('localStorage.setItem(\'password',script)

if __name__=='__main__':unittest.main()
