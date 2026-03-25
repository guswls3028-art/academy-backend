# conftest.py — 테스트 환경 검증 (프로젝트 루트)
#
# 모든 pytest 실행 시 자동으로 로드됨.
# DB ENGINE, HOST, NAME을 검증하여 운영 DB 오접속 및 SQLite 사용을 차단.

import os
import django
from django.conf import settings


def pytest_configure(config):
    """pytest 시작 시 테스트 환경 검증."""
    # .env 로드 (dotenv 사용 가능 시)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except ImportError:
        pass

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.test")
    django.setup()

    db = settings.DATABASES["default"]
    engine = db.get("ENGINE", "")
    db_name = db.get("NAME", "")
    db_host = db.get("HOST", "")
    test_db_name = db.get("TEST", {}).get("NAME", f"test_{db_name}")

    # 1. PostgreSQL 필수
    assert "postgresql" in engine, (
        f"[FATAL] 테스트 DB ENGINE이 postgresql이 아닙니다: {engine}\n"
        "SQLite는 프로덕션과 동작이 다릅니다. PostgreSQL 환경변수를 설정하세요."
    )

    # 2. 운영 DB 직접 사용 금지 (테스트 DB 이름 확인)
    _FORBIDDEN_DB_NAMES = {"production", "main", "live", "prod"}
    assert db_name.lower() not in _FORBIDDEN_DB_NAMES, (
        f"[FATAL] 운영 DB 이름({db_name})으로 테스트 실행 불가"
    )

    # 3. 정보 출력
    print(f"\n{'='*60}")
    print(f"  TEST SETTINGS: {os.environ['DJANGO_SETTINGS_MODULE']}")
    print(f"  DB ENGINE:     {engine}")
    print(f"  DB HOST:       {db_host}")
    print(f"  DB NAME:       {db_name}")
    print(f"  TEST DB NAME:  {test_db_name}")
    print(f"{'='*60}\n")
