from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.domains.tools.problem_studio.models import (
    ProblemStudioGenerationReview,
    ProblemStudioVoiceProfile,
    ProblemStudioVoiceSample,
)
from apps.shared.utils.pii import mask_inline_phones


MAX_PROFILES_PER_TEACHER = 10
MAX_STYLE_INSTRUCTIONS = 1200
MAX_SAMPLE_PROBLEM_CHARS = 6000
MAX_SAMPLE_ANSWER_CHARS = 1200
MAX_SAMPLE_EXPLANATION_CHARS = 6000
MAX_SNAPSHOT_STYLE_SAMPLES = 6
MAX_SNAPSHOT_REFERENCE_SAMPLES = 8

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_RESIDENT_ID_RE = re.compile(r"\b\d{6}\s*[-–]\s*[1-4]\d{6}\b")
_IDENTITY_FIELD_RE = re.compile(
    r"(?i)(성명|학생명|이름|학번|수험번호)\s*[:：]\s*[^\s,;/|]{1,40}",
)
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"[.!?。]\s+|\n+")
_STYLE_API_ORIGINS = {
    ProblemStudioVoiceSample.Origin.TEACHER_AUTHORED,
}
_REFERENCE_API_ORIGINS = {
    ProblemStudioVoiceSample.Origin.PUBLISHER_REFERENCE,
    ProblemStudioVoiceSample.Origin.OTHER_REFERENCE,
    ProblemStudioVoiceSample.Origin.TEACHER_AUTHORED,
}
_STYLE_ELIGIBLE_ORIGINS = {
    ProblemStudioVoiceSample.Origin.TEACHER_AUTHORED,
    ProblemStudioVoiceSample.Origin.APPROVED_OUTPUT,
    ProblemStudioVoiceSample.Origin.MATCHUP_COMMENT,
}


def sanitize_voice_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").replace("\x00", " ")
    text = mask_inline_phones(text)
    text = _EMAIL_RE.sub("[이메일 가림]", text)
    text = _RESIDENT_ID_RE.sub("[주민번호 가림]", text)
    text = _IDENTITY_FIELD_RE.sub(lambda match: f"{match.group(1)}: [가림]", text)
    return text.strip()[:max_chars]


def _fingerprint(*parts: str) -> str:
    canonical = "\n".join(_SPACE_RE.sub(" ", part).strip() for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _profile_queryset(*, tenant: Any, user: Any):
    return ProblemStudioVoiceProfile.objects.filter(
        tenant=tenant,
        owner=user,
    )


def get_owned_voice_profile(*, tenant: Any, user: Any, profile_id: Any, active_only: bool = True):
    queryset = _profile_queryset(tenant=tenant, user=user)
    if active_only:
        queryset = queryset.filter(status=ProblemStudioVoiceProfile.Status.ACTIVE)
    return queryset.filter(id=profile_id).first()


def serialize_voice_profile(profile: ProblemStudioVoiceProfile, *, include_samples: bool = False) -> dict[str, Any]:
    counts = {
        row["usage_scope"]: row["count"]
        for row in profile.samples.filter(tenant=profile.tenant, is_active=True)
        .values("usage_scope")
        .annotate(count=Count("id"))
    }
    payload: dict[str, Any] = {
        "id": str(profile.id),
        "name": profile.name,
        "subject": profile.subject,
        "style_instructions": profile.style_instructions,
        "is_default": profile.is_default,
        "status": profile.status,
        "version": profile.version,
        "style_sample_count": counts.get(ProblemStudioVoiceSample.UsageScope.STYLE, 0),
        "reference_sample_count": counts.get(
            ProblemStudioVoiceSample.UsageScope.CONTENT_REFERENCE,
            0,
        ),
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
    if include_samples:
        payload["samples"] = [
            serialize_voice_sample(sample)
            for sample in profile.samples.filter(
                tenant=profile.tenant,
                is_active=True,
            ).order_by("-created_at")[:30]
        ]
    return payload


def serialize_voice_sample(sample: ProblemStudioVoiceSample) -> dict[str, Any]:
    return {
        "id": str(sample.id),
        "usage_scope": sample.usage_scope,
        "origin": sample.origin,
        "source_label": sample.source_label,
        "problem_text": sample.problem_text,
        "answer": sample.answer,
        "explanation": sample.explanation,
        "rights_confirmed": sample.rights_confirmed_at is not None,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
    }


@transaction.atomic
def create_voice_profile(
    *,
    tenant: Any,
    user: Any,
    name: Any,
    subject: Any = "",
    style_instructions: Any = "",
    is_default: bool = False,
) -> ProblemStudioVoiceProfile:
    clean_name = sanitize_voice_text(name, max_chars=80)
    if not clean_name:
        raise ValueError("문체 프로필 이름을 입력해 주세요.")
    if _profile_queryset(tenant=tenant, user=user).filter(name=clean_name).exists():
        raise ValueError("같은 이름의 문체 프로필이 이미 있습니다.")
    if _profile_queryset(tenant=tenant, user=user).filter(
        status=ProblemStudioVoiceProfile.Status.ACTIVE,
    ).count() >= MAX_PROFILES_PER_TEACHER:
        raise ValueError(f"문체 프로필은 최대 {MAX_PROFILES_PER_TEACHER}개까지 만들 수 있습니다.")
    should_default = is_default or not _profile_queryset(tenant=tenant, user=user).filter(
        status=ProblemStudioVoiceProfile.Status.ACTIVE,
        is_default=True,
    ).exists()
    if should_default:
        _profile_queryset(tenant=tenant, user=user).filter(is_default=True).update(
            is_default=False,
            updated_at=timezone.now(),
        )
    return ProblemStudioVoiceProfile.objects.create(
        tenant=tenant,
        owner=user,
        name=clean_name,
        subject=sanitize_voice_text(subject, max_chars=100),
        style_instructions=sanitize_voice_text(
            style_instructions,
            max_chars=MAX_STYLE_INSTRUCTIONS,
        ),
        is_default=should_default,
    )


@transaction.atomic
def update_voice_profile(
    profile: ProblemStudioVoiceProfile,
    *,
    name: Any | None = None,
    subject: Any | None = None,
    style_instructions: Any | None = None,
    is_default: bool | None = None,
    status: Any | None = None,
) -> ProblemStudioVoiceProfile:
    changed = False
    if name is not None:
        clean_name = sanitize_voice_text(name, max_chars=80)
        if not clean_name:
            raise ValueError("문체 프로필 이름을 입력해 주세요.")
        if _profile_queryset(tenant=profile.tenant, user=profile.owner).exclude(
            id=profile.id,
        ).filter(name=clean_name).exists():
            raise ValueError("같은 이름의 문체 프로필이 이미 있습니다.")
        profile.name = clean_name
        changed = True
    if subject is not None:
        profile.subject = sanitize_voice_text(subject, max_chars=100)
        changed = True
    if style_instructions is not None:
        profile.style_instructions = sanitize_voice_text(
            style_instructions,
            max_chars=MAX_STYLE_INSTRUCTIONS,
        )
        changed = True
    if is_default is True:
        _profile_queryset(tenant=profile.tenant, user=profile.owner).exclude(id=profile.id).filter(
            is_default=True,
        ).update(is_default=False, updated_at=timezone.now())
        profile.is_default = True
        changed = True
    if status is not None:
        normalized_status = str(status)
        if normalized_status not in ProblemStudioVoiceProfile.Status.values:
            raise ValueError("문체 프로필 상태가 올바르지 않습니다.")
        if normalized_status == ProblemStudioVoiceProfile.Status.ARCHIVED and profile.is_default:
            raise ValueError("기본 문체 프로필은 다른 프로필을 기본값으로 지정한 뒤 보관할 수 있습니다.")
        profile.status = normalized_status
        changed = True
    if changed:
        profile.version = F("version") + 1
        profile.save()
        profile.refresh_from_db()
    return profile


@transaction.atomic
def add_voice_sample(
    *,
    profile: ProblemStudioVoiceProfile,
    user: Any | None,
    usage_scope: Any,
    origin: Any,
    source_label: Any = "",
    problem_text: Any = "",
    answer: Any = "",
    explanation: Any = "",
    rights_confirmed: bool,
    rights_note: Any = "",
    metadata: dict[str, Any] | None = None,
    allow_internal_origin: bool = False,
) -> tuple[ProblemStudioVoiceSample, bool]:
    normalized_scope = str(usage_scope or "")
    normalized_origin = str(origin or "")
    if normalized_scope not in ProblemStudioVoiceSample.UsageScope.values:
        raise ValueError("샘플 사용 범위가 올바르지 않습니다.")
    allowed_origins = (
        set(ProblemStudioVoiceSample.Origin.values)
        if allow_internal_origin
        else (
            _STYLE_API_ORIGINS
            if normalized_scope == ProblemStudioVoiceSample.UsageScope.STYLE
            else _REFERENCE_API_ORIGINS
        )
    )
    if normalized_origin not in allowed_origins:
        if normalized_scope == ProblemStudioVoiceSample.UsageScope.STYLE:
            raise ValueError("문체 학습에는 선생님이 직접 작성한 해설만 사용할 수 있습니다.")
        raise ValueError("참고 자료 출처가 올바르지 않습니다.")
    if (
        normalized_scope == ProblemStudioVoiceSample.UsageScope.STYLE
        and normalized_origin not in _STYLE_ELIGIBLE_ORIGINS
    ):
        raise ValueError("출판사·외부 자료의 문체는 학습할 수 없습니다. 내용 참고로 등록해 주세요.")
    if not rights_confirmed:
        raise ValueError("자료를 문제 제작에 사용할 권리 확인이 필요합니다.")

    clean_problem = sanitize_voice_text(problem_text, max_chars=MAX_SAMPLE_PROBLEM_CHARS)
    clean_answer = sanitize_voice_text(answer, max_chars=MAX_SAMPLE_ANSWER_CHARS)
    clean_explanation = sanitize_voice_text(
        explanation,
        max_chars=MAX_SAMPLE_EXPLANATION_CHARS,
    )
    if normalized_scope == ProblemStudioVoiceSample.UsageScope.STYLE and not clean_explanation:
        raise ValueError("문체를 학습할 선생님 해설을 입력해 주세요.")
    if normalized_scope == ProblemStudioVoiceSample.UsageScope.CONTENT_REFERENCE and not (
        clean_problem or clean_explanation
    ):
        raise ValueError("내용 참고에 사용할 문제 또는 해설을 입력해 주세요.")

    fingerprint = _fingerprint(clean_problem, clean_answer, clean_explanation)
    sample, created = ProblemStudioVoiceSample.objects.get_or_create(
        tenant=profile.tenant,
        profile=profile,
        usage_scope=normalized_scope,
        fingerprint=fingerprint,
        defaults={
            "created_by": user,
            "origin": normalized_origin,
            "source_label": sanitize_voice_text(source_label, max_chars=160),
            "problem_text": clean_problem,
            "answer": clean_answer,
            "explanation": clean_explanation,
            "rights_confirmed_at": timezone.now(),
            "rights_note": sanitize_voice_text(rights_note, max_chars=240),
            "metadata": metadata if isinstance(metadata, dict) else {},
        },
    )
    if created:
        ProblemStudioVoiceProfile.objects.filter(
            id=profile.id,
            tenant=profile.tenant,
            owner=profile.owner,
        ).update(
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        profile.refresh_from_db()
    return sample, created


def _style_signature_from_explanations(explanations: list[str]) -> str:
    explanations = [value.strip() for value in explanations if value.strip()]
    if not explanations:
        return "학습 샘플 없음"
    sentences = [
        sentence.strip()
        for explanation in explanations
        for sentence in _SENTENCE_RE.split(explanation)
        if sentence.strip()
    ]
    average = round(sum(len(sentence) for sentence in sentences) / max(1, len(sentences)))
    endings = Counter(
        sentence[-4:]
        for sentence in sentences
        if len(sentence) >= 4
    ).most_common(3)
    ending_text = ", ".join(ending for ending, _count in endings) or "혼합 종결"
    return f"평균 문장 길이 약 {average}자 · 자주 쓰는 종결 {ending_text}"


def _style_signature(samples: list[ProblemStudioVoiceSample]) -> str:
    return _style_signature_from_explanations(
        [sample.explanation for sample in samples]
    )


def augment_voice_profile_with_source_items(
    voice_profile: dict[str, Any] | None,
    *,
    items: list[Any],
    enabled: bool,
    rights_confirmed: bool,
) -> dict[str, Any] | None:
    """Add job-scoped teacher-authored examples without persisting source text."""
    if not enabled or not rights_confirmed:
        return voice_profile
    source_examples: list[dict[str, str]] = []
    for item in items:
        getter = item.get if isinstance(item, dict) else lambda key, default="": getattr(item, key, default)
        explanation = sanitize_voice_text(
            getter("explanation", ""),
            max_chars=1600,
        )
        if len(explanation) < 20:
            continue
        source_examples.append({
            "problem": sanitize_voice_text(
                getter("prompt", ""),
                max_chars=1200,
            ),
            "answer": sanitize_voice_text(
                getter("answer", ""),
                max_chars=400,
            ),
            "explanation": explanation,
        })
        if len(source_examples) >= MAX_SNAPSHOT_STYLE_SAMPLES:
            break
    if not source_examples:
        return voice_profile

    augmented = dict(voice_profile or {})
    existing_examples = augmented.get("style_examples")
    existing_examples = existing_examples if isinstance(existing_examples, list) else []
    merged_examples = [*source_examples, *existing_examples][:MAX_SNAPSHOT_STYLE_SAMPLES]
    augmented.update({
        "name": str(augmented.get("name") or "업로드 자료 문체"),
        "subject": str(augmented.get("subject") or ""),
        "version": int(augmented.get("version") or 0),
        "style_instructions": str(augmented.get("style_instructions") or ""),
        "style_examples": merged_examples,
        "content_references": list(augmented.get("content_references") or []),
        "style_signature": _style_signature_from_explanations(
            [str(example.get("explanation") or "") for example in merged_examples]
        ),
        "style_sample_count": max(
            int(augmented.get("style_sample_count") or 0),
            len(merged_examples),
        ),
        "reference_sample_count": int(augmented.get("reference_sample_count") or 0),
        "ephemeral_source_style_sample_count": len(source_examples),
    })
    return augmented


def build_voice_profile_snapshot(
    *,
    tenant: Any,
    user: Any,
    profile_id: Any,
) -> dict[str, Any]:
    profile = get_owned_voice_profile(
        tenant=tenant,
        user=user,
        profile_id=profile_id,
        active_only=True,
    )
    if profile is None:
        raise ValueError("내 문체 프로필을 찾을 수 없습니다.")
    style_samples = list(
        profile.samples.filter(
            tenant=tenant,
            is_active=True,
            usage_scope=ProblemStudioVoiceSample.UsageScope.STYLE,
            origin__in=_STYLE_ELIGIBLE_ORIGINS,
            rights_confirmed_at__isnull=False,
        ).order_by("-created_at")[:MAX_SNAPSHOT_STYLE_SAMPLES]
    )
    reference_samples = list(
        profile.samples.filter(
            tenant=tenant,
            is_active=True,
            usage_scope=ProblemStudioVoiceSample.UsageScope.CONTENT_REFERENCE,
            rights_confirmed_at__isnull=False,
        ).order_by("-created_at")[:MAX_SNAPSHOT_REFERENCE_SAMPLES]
    )
    return {
        "id": str(profile.id),
        "name": profile.name,
        "subject": profile.subject,
        "version": profile.version,
        "style_instructions": profile.style_instructions,
        "style_signature": _style_signature(style_samples),
        "style_examples": [
            {
                "problem": sample.problem_text[:1200],
                "answer": sample.answer[:400],
                "explanation": sample.explanation[:1600],
            }
            for sample in style_samples
        ],
        "content_references": [
            {
                "source_label": sample.source_label,
                "problem": sample.problem_text[:1800],
                "answer": sample.answer[:400],
                "explanation": sample.explanation[:1800],
            }
            for sample in reference_samples
        ],
        "style_sample_count": len(style_samples),
        "reference_sample_count": len(reference_samples),
    }


def resolve_voice_profile_payload(
    payload: dict[str, Any],
    *,
    tenant: Any,
    user: Any,
) -> dict[str, Any]:
    resolved = dict(payload)
    resolved.pop("_resolved_voice_profile", None)
    profile_id = resolved.get("voice_profile_id")
    if not profile_id:
        resolved.pop("voice_profile_id", None)
        return resolved
    snapshot = build_voice_profile_snapshot(
        tenant=tenant,
        user=user,
        profile_id=profile_id,
    )
    resolved["voice_profile_id"] = snapshot["id"]
    resolved["_resolved_voice_profile"] = snapshot
    return resolved


def revalidate_resolved_voice_profile(
    resolved: dict[str, Any] | None,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Rebuild a worker voice snapshot from tenant/user-owned database state."""

    if not isinstance(resolved, dict):
        return None
    profile_id = resolved.get("id")
    if not profile_id:
        raise ValueError("내 문체 프로필 정보가 올바르지 않습니다.")
    profile = (
        ProblemStudioVoiceProfile.objects.filter(
            id=profile_id,
            tenant_id=tenant_id,
            owner_id=user_id,
            status=ProblemStudioVoiceProfile.Status.ACTIVE,
        )
        .select_related("tenant", "owner")
        .first()
    )
    if profile is None:
        raise ValueError("선택한 내 문체 프로필을 더 이상 사용할 수 없습니다.")
    return build_voice_profile_snapshot(
        tenant=profile.tenant,
        user=profile.owner,
        profile_id=profile.id,
    )


def normalize_review_question(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("검수한 문항 값이 올바르지 않습니다.")
    raw_choices = value.get("choices")
    choices = raw_choices if isinstance(raw_choices, list) else []
    return {
        "prompt": sanitize_voice_text(value.get("prompt"), max_chars=6000),
        "choices": [
            sanitize_voice_text(choice, max_chars=1000)
            for choice in choices[:10]
            if str(choice or "").strip()
        ],
        "answer": sanitize_voice_text(value.get("answer"), max_chars=MAX_SAMPLE_ANSWER_CHARS),
        "explanation": sanitize_voice_text(
            value.get("explanation"),
            max_chars=MAX_SAMPLE_EXPLANATION_CHARS,
        ),
    }


@transaction.atomic
def record_generation_review(
    *,
    tenant: Any,
    user: Any,
    profile: ProblemStudioVoiceProfile,
    job_id: str,
    question_index: int,
    original_question: dict[str, Any],
    final_question: dict[str, Any],
    outcome: Any,
    feedback_note: Any = "",
    learn_from_this: bool = False,
    rights_confirmed: bool = False,
) -> tuple[ProblemStudioGenerationReview, bool]:
    normalized_outcome = str(outcome or "")
    if normalized_outcome not in ProblemStudioGenerationReview.Outcome.values:
        raise ValueError("검수 결과가 올바르지 않습니다.")
    if question_index < 0 or question_index > 99:
        raise ValueError("검수할 문항 번호가 올바르지 않습니다.")
    original = normalize_review_question(original_question)
    final = normalize_review_question(final_question)
    if not final["prompt"]:
        raise ValueError("검수한 문제 본문이 필요합니다.")
    existing = ProblemStudioGenerationReview.objects.filter(
        tenant=tenant,
        reviewed_by=user,
        job_id=str(job_id),
        question_index=question_index,
    ).first()
    if existing is not None:
        return existing, False

    review, created = ProblemStudioGenerationReview.objects.get_or_create(
        tenant=tenant,
        reviewed_by=user,
        job_id=str(job_id),
        question_index=question_index,
        defaults={
            "profile": profile,
            "outcome": normalized_outcome,
            "original_payload": original,
            "final_payload": final,
            "feedback_note": sanitize_voice_text(feedback_note, max_chars=500),
        },
    )
    if not created:
        return review, False

    if learn_from_this:
        if normalized_outcome == ProblemStudioGenerationReview.Outcome.REJECTED:
            raise ValueError("사용하지 않는 문항은 문체 학습에 반영할 수 없습니다.")
        learned_sample, _created = add_voice_sample(
            profile=profile,
            user=user,
            usage_scope=ProblemStudioVoiceSample.UsageScope.STYLE,
            origin=ProblemStudioVoiceSample.Origin.APPROVED_OUTPUT,
            source_label="문제 제작 검수 승인",
            problem_text=final["prompt"],
            answer=final["answer"],
            explanation=final["explanation"],
            rights_confirmed=rights_confirmed,
            rights_note="선생님이 최종 검수한 생성 결과",
            metadata={"job_id": str(job_id), "question_index": question_index},
            allow_internal_origin=True,
        )
        review.learned_sample = learned_sample
        review.save(update_fields=["learned_sample", "updated_at"])
    return review, True
