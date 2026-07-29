from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from apps.domains.students.models import StudentCustomFieldDefinition


MAX_CUSTOM_FIELDS_PER_TENANT = 50
MAX_TEXT_VALUE_LENGTH = 500
MAX_OPTIONS = 100


class StudentCustomFieldError(ValueError):
    def __init__(self, detail: str | dict[str, str]):
        self.detail = detail
        super().__init__(str(detail))


def normalize_custom_field_header(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"\s+", "", normalized)


def _core_excel_header_tokens() -> frozenset[str]:
    from academy.application.services.excel_parsing_service import HEADER_ALIASES

    return frozenset(
        normalize_custom_field_header(alias)
        for aliases in HEADER_ALIASES.values()
        for alias in aliases
        if normalize_custom_field_header(alias)
    )


def normalize_string_list(
    values: Any,
    *,
    field_name: str,
    max_items: int,
    max_length: int = 50,
) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise StudentCustomFieldError({field_name: "목록 형식이어야 합니다."})
    if len(values) > max_items:
        raise StudentCustomFieldError(
            {field_name: f"최대 {max_items}개까지 등록할 수 있습니다."}
        )

    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if len(value) > max_length:
            raise StudentCustomFieldError(
                {field_name: f"각 값은 {max_length}자 이하여야 합니다."}
            )
        token = normalize_custom_field_header(value)
        if token in seen:
            continue
        seen.add(token)
        result.append(value)
    return result


def validate_definition_headers(
    *,
    tenant,
    label: str,
    aliases: list[str],
    exclude_definition_id: int | None = None,
) -> None:
    candidates = {
        normalize_custom_field_header(value)
        for value in [label, *aliases]
        if normalize_custom_field_header(value)
    }
    if not candidates:
        raise StudentCustomFieldError({"label": "표시명은 필수입니다."})
    if candidates & _core_excel_header_tokens():
        raise StudentCustomFieldError(
            {"aliases": "기본 학생/Excel 컬럼명과 같은 표시명 또는 별칭은 사용할 수 없습니다."}
        )

    queryset = StudentCustomFieldDefinition.objects.filter(tenant=tenant)
    if exclude_definition_id is not None:
        queryset = queryset.exclude(pk=exclude_definition_id)
    for definition in queryset.only("label", "aliases"):
        existing = {
            normalize_custom_field_header(value)
            for value in [definition.label, *(definition.aliases or [])]
            if normalize_custom_field_header(value)
        }
        if candidates & existing:
            raise StudentCustomFieldError(
                {"aliases": "다른 사용자 정의 컬럼의 표시명 또는 별칭과 중복됩니다."}
            )


def active_custom_field_definitions(tenant) -> list[StudentCustomFieldDefinition]:
    return list(
        StudentCustomFieldDefinition.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("position", "id")
    )


def _normalize_number(value: Any, *, label: str) -> int | float:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise StudentCustomFieldError({label: "숫자 형식이어야 합니다."})
    if not number.is_finite():
        raise StudentCustomFieldError({label: "유한한 숫자만 입력할 수 있습니다."})
    if abs(number) >= Decimal("1000000000000"):
        raise StudentCustomFieldError({label: "숫자 범위를 벗어났습니다."})
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def normalize_custom_field_value(
    definition: StudentCustomFieldDefinition,
    value: Any,
) -> str | int | float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None

    label = definition.label
    if definition.field_type == StudentCustomFieldDefinition.NUMBER:
        return _normalize_number(value, label=label)
    if definition.field_type == StudentCustomFieldDefinition.DATE:
        raw = str(value).strip()
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise StudentCustomFieldError({label: "날짜는 YYYY-MM-DD 형식이어야 합니다."})

    text = str(value).strip()
    if len(text) > MAX_TEXT_VALUE_LENGTH:
        raise StudentCustomFieldError(
            {label: f"값은 {MAX_TEXT_VALUE_LENGTH}자 이하여야 합니다."}
        )
    if definition.field_type == StudentCustomFieldDefinition.SELECT:
        options = [str(option) for option in definition.options or []]
        if text not in options:
            raise StudentCustomFieldError({label: "허용된 선택지 중 하나여야 합니다."})
    return text


def normalize_custom_field_values(
    *,
    tenant,
    values: Any,
    definitions: Iterable[StudentCustomFieldDefinition] | None = None,
) -> dict[str, str | int | float | None]:
    if values in (None, ""):
        return {}
    if not isinstance(values, dict):
        raise StudentCustomFieldError({"custom_fields": "객체 형식이어야 합니다."})

    active = list(definitions) if definitions is not None else active_custom_field_definitions(tenant)
    by_key = {definition.key: definition for definition in active}
    unknown = sorted(str(key) for key in values if str(key) not in by_key)
    if unknown:
        raise StudentCustomFieldError(
            {"custom_fields": f"활성 사용자 정의 컬럼이 아닙니다: {', '.join(unknown)}"}
        )
    return {
        str(key): normalize_custom_field_value(by_key[str(key)], value)
        for key, value in values.items()
    }


def custom_field_values_from_import_row(
    *,
    tenant,
    row: dict[str, Any],
    definitions: Iterable[StudentCustomFieldDefinition] | None = None,
) -> dict[str, str | int | float | None]:
    active = list(definitions) if definitions is not None else active_custom_field_definitions(tenant)
    by_header: dict[str, StudentCustomFieldDefinition] = {}
    for definition in active:
        for header in [definition.label, *(definition.aliases or [])]:
            token = normalize_custom_field_header(header)
            if token:
                by_header[token] = definition

    imported: dict[str, Any] = {}
    extra_columns = row.get("_extra_columns") or {}
    if isinstance(extra_columns, dict):
        for header, value in extra_columns.items():
            definition = by_header.get(normalize_custom_field_header(header))
            if definition is not None:
                imported[definition.key] = value

    explicit = row.get("custom_fields") or {}
    if isinstance(explicit, dict):
        imported.update(explicit)
    return normalize_custom_field_values(
        tenant=tenant,
        values=imported,
        definitions=active,
    )
