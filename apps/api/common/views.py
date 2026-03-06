"""
공통 API 뷰
"""
from django.http import JsonResponse
from django.db import connection
from django.conf import settings


def health_check(request):
    """
    헬스체크 엔드포인트
    
    Returns:
        - 200: 모든 시스템 정상
        - 503: 데이터베이스 연결 실패
    """
    try:
        # 데이터베이스 연결 확인
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        return JsonResponse({
            "status": "healthy",
            "service": "academy-api",
            "database": "connected",
        }, status=200)
    except Exception as e:
        return JsonResponse({
            "status": "unhealthy",
            "service": "academy-api",
            "database": "disconnected",
            "error": str(e),
        }, status=503)


def healthz(request):
    """
    ALB/로드밸런서용 라이브니스. DB 검사 없이 항상 200.
    TG health check를 이 경로로 두면 DB 장애 시에도 인스턴스가 healthy 유지.
    """
    return JsonResponse({"status": "ok", "service": "academy-api"}, status=200)


def readyz(request):
    """
    Readiness 엔드포인트. 의존성(DB 등)까지 포함해 준비 상태를 판단한다.
    - 200: 준비 완료
    - 503: 준비되지 않음 (예: DB 연결 실패)
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ready", "service": "academy-api", "database": "connected"}, status=200)
    except Exception as e:
        return JsonResponse(
            {"status": "not_ready", "service": "academy-api", "database": "disconnected", "error": str(e)},
            status=503,
        )
