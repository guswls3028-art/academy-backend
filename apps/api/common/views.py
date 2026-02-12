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
