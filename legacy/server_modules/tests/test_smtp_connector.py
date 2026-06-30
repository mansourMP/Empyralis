import importlib.util
import json
import sys
import tempfile
import unittest
from email.mime.text import MIMEText
from pathlib import Path

from server_modules.connectors import smtp_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_smtp_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_smtp_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int = 0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.sent = None
        self.calls = []

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username: str, password: str):
        self.login_args = (username, password)
        self.calls.append("login")

    def noop(self):
        self.calls.append("noop")
        return 250, b"OK"

    def sendmail(self, sender, recipients, message):
        self.sent = (sender, recipients, message)
        self.calls.append("sendmail")
        return {}

    def quit(self):
        self.calls.append("quit")


class FakeIMAP:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.calls = []

    def login(self, username: str, password: str):
        self.calls.append(("login", username, password))
        return "OK", [b"logged-in"]

    def select(self, folder: str, readonly: bool = True):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def search(self, charset, query: str):
        self.calls.append(("search", query))
        return "OK", [b"1 2"]

    def fetch(self, message_id: bytes, _spec: str):
        message = MIMEText(f"Body for {message_id.decode('utf-8')}", "plain", "utf-8")
        message["Subject"] = f"Subject {message_id.decode('utf-8')}"
        message["From"] = "sender@example.com"
        message["To"] = "receiver@example.com"
        return "OK", [(b"RFC822", message.as_bytes())]

    def logout(self):
        self.calls.append(("logout",))


class SmtpConnectorTests(unittest.TestCase):
    def test_send_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "smtp__send_email",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "to_email": "user@example.com",
                                    "subject": "Hello",
                                    "body": "SMTP body",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "smtp",
                    "label": "SMTP Email",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["mailboxes.read"],
                    "write_actions": ["send_email", "fetch_emails"],
                    "approval_required_actions": ["send_email"],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "smtp")
        self.assertEqual(payload["actions"][0]["action"], "send_email")

    def test_email_built_correctly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("attachment-body")
            attachment_path = Path(handle.name)
        try:
            message = smtp_connector.build_email_message(
                "person@example.com",
                "Quarterly update",
                "Plain body",
                sender="ops@example.com",
                html_body="<p>HTML body</p>",
                attachments=[str(attachment_path)],
            )
            raw = message.as_string()
            self.assertEqual(message["To"], "person@example.com")
            self.assertEqual(message["From"], "ops@example.com")
            self.assertEqual(message["Subject"], "Quarterly update")
            self.assertIn("multipart/mixed", raw)
            self.assertIn("multipart/alternative", raw)
            self.assertIn("text/plain", raw)
            self.assertIn("text/html", raw)
            self.assertIn(f'filename="{attachment_path.name}"', raw)
        finally:
            attachment_path.unlink(missing_ok=True)

    def test_send_uses_mock_smtp(self):
        result = smtp_connector.send_email(
            {
                "host": "smtp.example.com",
                "port": 587,
                "username": "ops@example.com",
                "password": "secret",
                "use_tls": True,
            },
            "person@example.com",
            "Hello",
            "SMTP body",
            smtp_cls=FakeSMTP,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["smtp_host"], "smtp.example.com")
        self.assertEqual(result["to"], ["person@example.com"])
        self.assertEqual(result["subject"], "Hello")

    def test_fetch_emails_with_mock_imap(self):
        result = smtp_connector.fetch_emails(
            {
                "host": "smtp.example.com",
                "port": 587,
                "username": "ops@example.com",
                "password": "secret",
                "use_tls": True,
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "use_imap_ssl": True,
            },
            limit=2,
            imap_ssl_cls=FakeIMAP,
            imap_cls=FakeIMAP,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"][0]["subject"], "Subject 2")
        self.assertIn("Body for 2", result["items"][0]["snippet"])


if __name__ == "__main__":
    unittest.main()
