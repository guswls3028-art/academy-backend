# apps/support/media/views/video_policy_impact.py

from typing import Dict, Any, List
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.support.media.models import Video
# ⚠️ 너 프로젝트 기준으로 Enrollment / Permission 계산 로직이 이미 stats()에 있으니
# 그 로직을 재사용하는 게 베스트.
# 여기서는 "stats와 동일한 학생 리스트(학생명, enrollment, effective_rule)"를 뽑아오는 함수를 분리했다고 가정한다.

# =========================================================
# ✅ NOTE
# - 이 API는 "저장 전 미리보기" 용이라 DB 변경 ❌
# - stats()가 이미 학생 목록/룰 산출을 하고 있으니,
#   가능하면 stats 산출 함수(예: build_video_stats_students)를 import 해서 그대로 쓰는 걸 권장
# =========================================================

def parse_bool(v: str | None, default: bool) -> bool:
  if v is None:
    return default
  if isinstance(v, bool):
    return v
  s = str(v).strip().lower()
  return s in ("1", "true", "t", "yes", "y", "on")


def parse_float(v: str | None, default: float) -> float:
  if v is None:
    return default
  try:
    return float(v)
  except Exception:
    return default


class VideoPolicyImpactAPIView(APIView):
  permission_classes = [IsAuthenticated]

  def get(self, request, video_id: int):
    video = get_object_or_404(Video, pk=video_id)

    proposed_allow_skip = parse_bool(request.query_params.get("allow_skip"), video.allow_skip)
    proposed_max_speed = parse_float(request.query_params.get("max_speed"), float(video.max_speed or 1.0))
    proposed_show_watermark = parse_bool(request.query_params.get("show_watermark"), video.show_watermark)

    # 변경 여부
    changed_allow_skip = bool(video.allow_skip) != bool(proposed_allow_skip)
    changed_max_speed = float(video.max_speed or 1.0) != float(proposed_max_speed)
    changed_watermark = bool(video.show_watermark) != bool(proposed_show_watermark)
    changed_any = changed_allow_skip or changed_max_speed or changed_watermark

    # ------------------------------------------------------------------
    # ✅ 학생 리스트 + effective_rule 산출 (stats()와 "동일"해야 함)
    # ------------------------------------------------------------------
    # 여기서는 "이미 stats가 만들고 있는 구조"를 따라간다고 가정.
    # 너 stats()가 Enrollment를 루프 돌면서 아래 필드를 구성했었지:
    # enrollment, student_name, attendance_status, effective_rule ...
    #
    # 그러므로 가장 안전한 방법은:
    # 1) stats()에서 사용한 내부 함수로 학생 목록을 만들어서 재사용
    # 2) 또는 동일한 ORM/규칙 로직을 여기에도 복붙
    #
    # 아래는 "최소 안전" 버전: stats 엔드포인트를 호출하지 않고,
    # 같은 내부 쿼리/로직으로 구성해야 함.
    # ------------------------------------------------------------------

    # ✅ TODO: 여기 부분은 너 stats()의 학생 리스트 산출 코드를 그대로 가져와라.
    # 지금은 샘플로 Video 모델에 연결된 Enrollment 관계가 있다고 가정하지 않고,
    # "video.stats_students()" 같은 헬퍼가 있다고 가정하지 않는다.
    #
    # 따라서 실제 적용 시:
    # - 너의 기존 stats() 구현 파일에서
    #   students list 만드는 코드를 함수로 빼서 import 해서 써라.
    #
    # 예시:
    # from apps.support.media.services.video_stats import build_video_stats_students
    # students = build_video_stats_students(video)
    #
    # 여기서는 "형태"를 정확히 보여주는 게 목표.
    students: List[Dict[str, Any]] = []

    # ⚠️ 임시: 개발 중이면 아래처럼 에러로 알려주는 게 오히려 안전함
    # (실서비스에서는 반드시 구현)
    if students == []:
      return Response(
        {
          "detail": "policy-impact: students builder is not wired. Reuse the same student list builder from /stats/.",
          "hint": "Extract stats() student building logic into a shared function and import it here.",
        },
        status=status.HTTP_501_NOT_IMPLEMENTED,
      )

    # breakdown
    breakdown = {"free": 0, "once": 0, "blocked": 0}
    for s in students:
      r = s.get("effective_rule") or "free"
      if r not in breakdown:
        continue
      breakdown[r] += 1

    eligible_count = breakdown["free"] + breakdown["once"]  # 일반적으로 blocked 제외
    impacted_count = eligible_count if changed_any else 0

    sample = [
      {
        "enrollment": s.get("enrollment"),
        "student_name": s.get("student_name"),
        "effective_rule": s.get("effective_rule"),
      }
      for s in students[:20]
    ]

    return Response(
      {
        "eligible_count": eligible_count,
        "impacted_count": impacted_count,
        "changed_fields": {
          "allow_skip": {"before": bool(video.allow_skip), "after": bool(proposed_allow_skip)},
          "max_speed": {"before": float(video.max_speed or 1.0), "after": float(proposed_max_speed)},
          "show_watermark": {"before": bool(video.show_watermark), "after": bool(proposed_show_watermark)},
        },
        "breakdown_by_rule": breakdown,
        "sample": sample,
      },
      status=status.HTTP_200_OK,
    )
