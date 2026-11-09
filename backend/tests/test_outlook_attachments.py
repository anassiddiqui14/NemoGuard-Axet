"""Validation tests for automatically captured Outlook attachments."""
import unittest

from app.domain.outlook_attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, validate_attachments


class OutlookAttachmentValidationTests(unittest.TestCase):
    def test_preserves_complete_text_and_untrusted_document_type(self):
        text = "head\n" + "x" * 20000 + "\nlast line"
        documents = validate_attachments({
            "attachments": [{"name": "error.log", "text": text}],
            "omitted_attachments": [{"name": "picture.png", "reason": "unsupported_format"}],
        })
        self.assertEqual(documents, [{"kind": "EMAIL_ATTACHMENT", "title": "error.log", "content": text}])

    def test_rejects_invalid_or_incomplete_payload(self):
        for payload in (
            {"attachments": [{"name": "../secret.log", "text": "error"}]},
            {"attachments": [{"name": "file.exe", "text": "error"}]},
            {"attachments": [{"name": "file.log", "text": "error\0"}]},
            {"attachments": [{"name": "file.log", "text": "x" * (MAX_ATTACHMENT_BYTES + 1)}]},
            {"attachments": [{"name": "file.log", "text": "x"}] * (MAX_ATTACHMENTS + 1)},
            {"omitted_attachments": [{"name": "file.png", "reason": "not specified"}]},
        ):
            with self.subTest(payload=str(payload)[:120]), self.assertRaises(ValueError):
                validate_attachments(payload)


if __name__ == "__main__":
    unittest.main()
