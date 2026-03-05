# ======================================================================
# PATH: apps/api/config/settings/base.py
# ======================================================================
from pathlib import Path
from datetime import timedelta
import os

try:
    from corsheaders.defaults import default_headers
except ImportError:
    default_headers = []

"""
============================================================================
MULTI-TENANT SSOT NOTICE (CRITICAL)

- Tenant resolution is **Host-based only**.
- Headers or query params are intentionally ignored.
- Any fallback / auto-pick / default-tenant logic is a BUG.
- Internal / token endpoints are tenant-free by design.
============================================================================
"""

# ==================================================
# BASE
# ==================================================

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# ==================================================
# AWS / WORKER INSTANCE (SSOT)
# ==================================================

AWS_REGION = os.getenv("AWS_REGION")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", AWS_REGION)

AI_WORKER_INSTANCE_ID = os.getenv("AI_WORKER_INSTANCE_ID")
VIDEO_WORKER_INSTANCE_ID = os.getenv("VIDEO_WORKER_INSTANCE_ID")

# Lambda internal API (B1 backlog-count 등) 인증용
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY")
# Internal API 허용 소스 CIDR (쉼표 구분). Lambda VPC(10.1.0.0/16) + API VPC(172.30.0.0/16). 비어 있으면 IP 검사 생략.
INTERNAL_API_ALLOW_IPS = os.environ.get("INTERNAL_API_ALLOW_IPS", "").strip()

# ==================================================
# MULTI TENANT (SSOT – Host Based Only)
# ==================================================

# ❌ Legacy options (kept for backward awareness, intentionally unused)
TENANT_HEADER_NAME = None
TENANT_QUERY_PARAM_NAME = None
TENANT_DEFAULT_CODE = None
TENANT_STRICT = None
TENANT_ALLOW_INACTIVE = None

# ==================================================
# INTERNAL / WORKER (TENANT-FREE ZONE) 🔒
# - 아래 경로는 TenantMiddleware 를 반드시 bypass 해야 함.
# - tenant resolve 가 발생하면 운영 사고로 간주한다.
# ==================================================

TENANT_BYPASS_PATH_PREFIXES = [
    "/admin/",
    "/api/v1/token/",
    "/api/v1/token/refresh/",
    "/api-auth/",  # DRF Browsable API 로그인 (tenant 불필요)
    "/internal/",
    "/api/v1/internal/",
    "/swagger",
    "/redoc",
]

# ==================================================
# ALLOWED HOSTS
# - base.py: dev / staging friendly
# - prod.py: must be strict (tenant == host)
# ==================================================

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "hakwonplus.com",
    "www.hakwonplus.com",
    "api.hakwonplus.com",
    "limglish.kr",
    ".limglish.kr",
    "academy-frontend.pages.dev",
    ".trycloudflare.com",
    # 개발용
    "dev-web.hakwonplus.com",
    "dev-api.hakwonplus.com",
]

# ==================================================
# PROXY / FORWARDED HEADERS (ENV CONSISTENCY) ✅
# - dev / staging / prod 동일한 host 해석 보장
# ==================================================

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

AUTH_USER_MODEL = "core.User"

# ==================================================
# INSTALLED APPS
# ==================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "apps.api.common",
    "apps.core",

    "apps.domains.students",
    "apps.domains.teachers",
    "apps.domains.staffs",
    "apps.domains.parents",
    "apps.domains.lectures",
    "apps.domains.enrollment",
    "apps.domains.attendance",
    "apps.domains.schedule",
    "apps.domains.community",
    "apps.domains.exams",
    "apps.domains.homework",
    "apps.domains.submissions",
    "apps.domains.results",
    "apps.domains.homework_results",
    "apps.domains.clinic",
    "apps.domains.progress",
    "apps.domains.ai.apps.AIDomainConfig",
    "apps.domains.assets",
    "apps.domains.inventory",

    "apps.support.video",
    "apps.support.messaging",

    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_yasg",
    "corsheaders",

    "apps.shared",
    "django_extensions",
    "apps.domains.student_app",
]

# ==================================================
# MIDDLEWARE
# ==================================================

MIDDLEWARE = [
    "apps.api.common.middleware.CorsResponseFixMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",

    # 🔒 Tenant SSOT (Host-based, after host normalization)
    "apps.core.middleware.tenant.TenantMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.api.common.middleware.UnhandledExceptionMiddleware",
]

# ==================================================
# URL / WSGI / ASGI
# ==================================================

ROOT_URLCONF = "apps.api.config.urls"
WSGI_APPLICATION = "apps.api.config.wsgi.application"
ASGI_APPLICATION = "apps.api.config.asgi.application"

# ==================================================
# DATABASE
# ==================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "0")),  # 0=close after request (RDS slot 절약). 60=persist when using RDS Proxy/pool.
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# ==================================================
# GLOBAL
# ==================================================

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "storage" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==================================================
# TEMPLATES
# ==================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ==================================================
# DRF
# ==================================================

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",  # 브라우저 API 로그인용
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ==================================================
# JWT
# ==================================================

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=3),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ==================================================
# CORS / CSRF
# ==================================================

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
    "https://tchul.com",
    "https://www.tchul.com",
    "https://ymath.co.kr",
    "https://www.ymath.co.kr",
    "https://dev-web.hakwonplus.com",
]

CSRF_TRUSTED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
    "https://tchul.com",
    "https://www.tchul.com",
    "https://ymath.co.kr",
    "https://www.ymath.co.kr",
    "https://*.trycloudflare.com",  # dev only
]

# 중앙 API(api.hakwonplus.com) 호출 시 SPA가 테넌트 식별용으로 보내는 헤더 허용
CORS_ALLOW_HEADERS = list(default_headers) + [
    "X-Client-Version",
    "X-Client",
    "X-Tenant-Code",
]

# ==================================================
# VIDEO / CDN
# ==================================================

CDN_HLS_BASE_URL = "https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev"
VIDEO_PLAYBACK_TTL_SECONDS = int(os.getenv("VIDEO_PLAYBACK_TTL_SECONDS", "600"))

# ==================================================
# Cloudflare R2
# ==================================================

R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")
R2_AI_BUCKET = os.getenv("R2_AI_BUCKET", "academy-ai")
R2_VIDEO_BUCKET = os.getenv("R2_VIDEO_BUCKET", "academy-video")
R2_STORAGE_BUCKET = os.getenv("R2_STORAGE_BUCKET", "academy-storage")
# 엑셀 수강등록 업로드용 (워커와 동일 버킷 사용)
R2_EXCEL_BUCKET = os.getenv("R2_EXCEL_BUCKET", os.getenv("EXCEL_BUCKET_NAME", "academy-excel"))
# dev_app: 테넌트 로고 등 (academy-admin 버킷)
R2_ADMIN_BUCKET = os.getenv("R2_ADMIN_BUCKET", "academy-admin")
R2_ADMIN_PUBLIC_BASE_URL = os.getenv("R2_ADMIN_PUBLIC_BASE_URL", R2_PUBLIC_BASE_URL or "")

# ==================================================
# SITE (메시지 발송용 홈페이지 링크)
# ==================================================

SITE_URL = os.getenv("SITE_URL", "")  # 예: https://academy.example.com

# ==================================================
# SOLAPI (SMS/LMS 발송) — 환경변수 권장, 코드에 키 노출 금지
# ==================================================

SOLAPI_API_KEY = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
SOLAPI_SENDER = os.getenv("SOLAPI_SENDER", "")  # 발신 번호 (예: 01012345678)
# 알림톡: 카카오 검수 완료 템플릿만 ENV로 관리 (코드 수정 없이 교체)
SOLAPI_KAKAO_PF_ID = os.getenv("SOLAPI_KAKAO_PF_ID", "")
SOLAPI_KAKAO_TEMPLATE_ID = os.getenv("SOLAPI_KAKAO_TEMPLATE_ID", "")

# delete_r2 전용 SQS (Encoding = Batch ONLY)
VIDEO_SQS_QUEUE_DELETE_R2 = os.getenv("VIDEO_SQS_QUEUE_DELETE_R2", "academy-video-delete-r2")

# AWS Batch Video Encoding (SSOT: DB VideoTranscodeJob). 2-tier: standard / long (3h+).
VIDEO_BATCH_JOB_QUEUE = os.getenv("VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
VIDEO_BATCH_JOB_DEFINITION = os.getenv("VIDEO_BATCH_JOB_DEFINITION", "academy-v1-video-batch-jobdef")
VIDEO_BATCH_JOB_QUEUE_LONG = os.getenv("VIDEO_BATCH_JOB_QUEUE_LONG", "academy-v1-video-batch-long-queue")
VIDEO_BATCH_JOB_DEFINITION_LONG = os.getenv("VIDEO_BATCH_JOB_DEFINITION_LONG", "academy-v1-video-batch-long-jobdef")
# 3시간(10800초) 이상이면 long 큐 사용
VIDEO_LONG_DURATION_THRESHOLD_SECONDS = int(os.getenv("VIDEO_LONG_DURATION_THRESHOLD_SECONDS", "10800"))
# Stuck 판정: heartbeat_age 기반. standard 20분, long 45분
VIDEO_STUCK_HEARTBEAT_STANDARD_MINUTES = int(os.getenv("VIDEO_STUCK_HEARTBEAT_STANDARD_MINUTES", "20"))
VIDEO_STUCK_HEARTBEAT_LONG_MINUTES = int(os.getenv("VIDEO_STUCK_HEARTBEAT_LONG_MINUTES", "45"))
# R2 업로드 multipart + checkpoint (params SSOT)
R2_UPLOAD_PART_SIZE_MB = int(os.getenv("R2_UPLOAD_PART_SIZE_MB", "64"))
R2_UPLOAD_MAX_CONCURRENCY = int(os.getenv("R2_UPLOAD_MAX_CONCURRENCY", "8"))
R2_UPLOAD_MAX_ATTEMPTS = int(os.getenv("R2_UPLOAD_MAX_ATTEMPTS", "8"))
R2_UPLOAD_CHECKPOINT_TABLE = os.getenv("R2_UPLOAD_CHECKPOINT_TABLE", "academy-v1-video-upload-checkpoints")
# DynamoDB video job lock: 1 video 1 job, key=video_id, TTL 12h+, heartbeat로 연장
VIDEO_JOB_LOCK_TABLE_NAME = os.getenv("VIDEO_JOB_LOCK_TABLE_NAME", "academy-v1-video-job-lock")
VIDEO_JOB_LOCK_TTL_SECONDS = int(os.getenv("VIDEO_JOB_LOCK_TTL_SECONDS", "43200"))  # 12h (long 기준)
VIDEO_JOB_LOCK_TTL_ATTRIBUTE = os.getenv("VIDEO_JOB_LOCK_TTL_ATTRIBUTE", "ttl")
# Job timeout (Batch JobDef에 반영; 앱에서는 참고용)
VIDEO_JOB_TIMEOUT_STANDARD_SECONDS = int(os.getenv("VIDEO_JOB_TIMEOUT_STANDARD_SECONDS", "21600"))
VIDEO_JOB_TIMEOUT_LONG_SECONDS = int(os.getenv("VIDEO_JOB_TIMEOUT_LONG_SECONDS", "43200"))
# Reconcile orphan safety: min RUNNABLE age (minutes) before terminating; skip if CE desiredvCpus=0
RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES = int(os.getenv("RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES", "15"))
RECONCILE_ORPHAN_DISABLED = os.getenv("RECONCILE_ORPHAN_DISABLED", "").lower() in ("1", "true", "yes")
VIDEO_TENANT_MAX_CONCURRENT = int(os.getenv("VIDEO_TENANT_MAX_CONCURRENT", "2"))
VIDEO_GLOBAL_MAX_CONCURRENT = int(os.getenv("VIDEO_GLOBAL_MAX_CONCURRENT", "20"))
VIDEO_MAX_JOBS_PER_VIDEO = int(os.getenv("VIDEO_MAX_JOBS_PER_VIDEO", "10"))
VIDEO_CLOUDWATCH_NAMESPACE = os.getenv("VIDEO_CLOUDWATCH_NAMESPACE", "Academy/Video")
AI_SQS_QUEUE_NAME_LITE = os.getenv("AI_SQS_QUEUE_NAME_LITE", "academy-ai-jobs-lite")
AI_SQS_QUEUE_NAME_BASIC = os.getenv("AI_SQS_QUEUE_NAME_BASIC", "academy-ai-jobs-basic")
AI_SQS_QUEUE_NAME_PREMIUM = os.getenv("AI_SQS_QUEUE_NAME_PREMIUM", "academy-ai-jobs-premium")
# 메시지 발송 SQS 큐 (워커가 소비)
MESSAGING_SQS_QUEUE_NAME = os.getenv("MESSAGING_SQS_QUEUE_NAME", "academy-messaging-jobs")

# ==================================================
# INTERNAL WORKER
# ==================================================

INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "")
