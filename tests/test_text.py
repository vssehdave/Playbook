"""Redaction: every pattern takes its secret out and leaves ordinary prose alone."""
import unittest

import helpers  # noqa: F401  (a private PLAYBOOK_HOME before the package loads)

from getplaybook.text import redact

CASES = {
    "private key without its END line": ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\nabcd", "MIIEowIBAAKCAQEA1234"),
    "private key block": ("a -----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY----- b", "MIIEvQIBADANBgkqhkiG9w0B"),
    "PuTTY key": ("Private-Lines: 2\nAAAAB3NzaC1yc2EAAAADAQABAAABAQC1\nAAAAB3NzaC1yc2EAAAADAQABAAABAQC2", "AAAAB3NzaC1yc2EAAAADAQABAAABAQC1"),
    "Google access token": ("t ya29." + "a1B2c3D4e5" * 3, "a1B2c3D4e5"),
    "Google refresh token": ("t 1//" + "a1B2c3D4e5" * 4, "a1B2c3D4e5"),
    "Telegram bot token": ("bot 123456789:AA" + "a1B2c3D4e5" * 3 + "abc", "a1B2c3D4e5"),
    "Azure SAS signature": ("https://x.blob.core.windows.net/c?sv=1&sig=AbCdEf123456%2Bxyz&se=2", "AbCdEf123456"),
    "Stripe webhook secret": ("whsec_" + "a1B2c3D4" * 3, "a1B2c3D4"),
    "Slack webhook": ("hooks.slack.com/services/T000/B000/XXXXXXXXXXXX", "XXXXXXXXXXXX"),
    "SendGrid key": ("SG." + "a1B2c3D4e5" * 2 + "." + "f6G7h8I9j0" * 2, "a1B2c3D4e5"),
    "cookie header": ("Cookie: session=abc123def456; theme=dark", "abc123def456"),
    "database URL": ("postgres://admin:p@ss/word@db.example.com:5432/app", "p@ss/word"),
    "curl user:password": ("curl -u admin:hunter2secret https://example.com", "hunter2secret"),
    "ODBC Pwd": ("Server=db;Uid=sa;Pwd=Sup3rSecret;Database=x", "Sup3rSecret"),
    "quoted _KEY literal": ('STRIPE_KEY = "abcdef"', "abcdef"),
    "bare _KEY with digit": ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI1K7MDENG", "wJalrXUtnFEMI1K7MDENG"),
    "licence-shaped _KEY": ("LICENSE_KEY=ABCD-EFGH-IJKL-MNOP", "ABCD-EFGH-IJKL-MNOP"),
    "JSON-escaped API_KEY": ('{"command": "API_KEY=\\"correct-horse-battery\\" run"}', "correct-horse-battery"),
}
KEEP = ("max_tokens: 150000", "token = get_token()", "MY_KEY = get_key()", "pwd=$(pwd)", "see the basic error-handling guide", "PASSWORD = None")


class RedactTest(unittest.TestCase):
    def test_each_pattern_takes_its_secret_out(self):
        for name, (text, secret) in CASES.items():
            with self.subTest(name):
                out = redact(text)
                self.assertNotIn(secret, out)
                self.assertIn("removed", out)

    def test_ordinary_text_is_left_alone(self):
        for text in KEEP:
            with self.subTest(text):
                self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
