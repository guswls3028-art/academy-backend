"""Role-specific contact selection shared by account, messaging and worker code."""

NO_PHONE_PLACEHOLDER = "01000000000"


def contact_phone(value: str | None) -> str:
    """Normalize a stored contact without treating the no-phone placeholder as a number."""
    phone = "".join((value or "").split()).replace("-", "")
    if phone == NO_PHONE_PLACEHOLDER or not phone.isascii() or not phone.isdigit():
        return ""
    return phone if len(phone) in (10, 11) else ""


def student_contact_phone(student, role: str) -> str:
    if role == "parent":
        return contact_phone(getattr(student, "parent_phone", None))
    if role == "student" and not getattr(student, "uses_identifier", False):
        return contact_phone(getattr(student, "phone", None))
    return ""
