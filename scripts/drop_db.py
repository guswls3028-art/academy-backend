#!/usr/bin/env python
"""
DB Drop + 재생성 (Big Bang 전 실행)
- .env의 DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT 사용
- PostgreSQL에 접속해 대상 DB를 DROP 후 CREATE
"""
import os
import sys

# 프로젝트 루트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env (배포) + .env.local (로컬) 로드 — .env.local 이 있으면 덮어씀
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for name in (".env", ".env.local"):
    p = os.path.join(_root, name)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

name = os.getenv("DB_NAME")
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST", "127.0.0.1")
port = os.getenv("DB_PORT", "5432")

if not name:
    print("DB_NAME이 비어 있습니다. .env 또는 환경 변수를 설정하세요.")
    sys.exit(1)

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    print("psycopg2가 필요합니다: pip install psycopg2-binary")
    sys.exit(1)

# postgres DB에 연결 (대상 DB가 아니어야 DROP 가능)
conn = psycopg2.connect(
    dbname="postgres",
    user=user or "postgres",
    password=password or "",
    host=host,
    port=port,
)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()

# 기존 연결 강제 종료 (선택)
cur.execute(
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid();",
    (name,),
)
cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
print(f"DROP DATABASE IF EXISTS {name};")
owner = user or "postgres"
cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner)))
print(f"CREATE DATABASE {name} OWNER {owner};")
cur.close()
conn.close()
print("Done. 이제 python manage.py migrate 를 실행하세요.")
