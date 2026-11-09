"""Validation for untrusted text attachments delivered by the Outlook bridge."""

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024


def validate_attachments(payload):
    attachments = payload.get("attachments", [])
    omitted = payload.get("omitted_attachments", [])
    if not isinstance(attachments, list) or not isinstance(omitted, list):
        raise ValueError("Invalid attachment list.")
    if len(attachments) + len(omitted) > MAX_ATTACHMENTS:
        raise ValueError("Too many attachments.")
    documents = []
    total = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Invalid attachment.")
        name, text = attachment.get("name"), attachment.get("text")
        if (not isinstance(name, str) or not name or len(name) > 200
                or any(c in name for c in ("/", "\\", "\x00"))
                or not name.lower().endswith((".log", ".txt", ".csv"))):
            raise ValueError("Invalid text attachment name.")
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise ValueError("Invalid attachment text.")
        size = len(text.encode("utf-8"))
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment exceeds the supported size.")
        total += size
        documents.append({"kind": "EMAIL_ATTACHMENT", "title": name, "content": text})
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Attachments exceed the combined size limit.")
    for attachment in omitted:
        if not isinstance(attachment, dict):
            raise ValueError("Invalid omitted attachment.")
        name, reason = attachment.get("name"), attachment.get("reason")
        if (not isinstance(name, str) or not name or len(name) > 200
                or any(c in name for c in ("/", "\\", "\x00"))
                or reason != "unsupported_format"):
            raise ValueError("Invalid omitted attachment metadata.")
    return documents
