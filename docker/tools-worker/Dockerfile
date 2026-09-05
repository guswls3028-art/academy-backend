# ==============================================================================
# Tools Worker Dockerfile
# ==============================================================================
# Lightweight worker for deterministic document conversion, bounded OCR, and
# spreadsheet tasks. Keep this separate from AI worker model caches and ML deps.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS curl-fixed-system
ARG LIBCURL_VERSION=8.21.0-2~bpo13+1
ARG LIBNGHTTP3_VERSION=1.15.0-1~bpo13+1
ARG LIBNGTCP2_VERSION=1.22.1-1~bpo13+1

ENV WORKER_TYPE=TOOLS

USER root
# Problem Studio 원본 이관은 스캔 PDF/이미지의 제한적 OCR까지 tools worker에서
# 끝내야 API 60초 경계를 피하면서도 산출물 품질이 API와 일치한다.
# perl-base exists only while Debian configures this same apt transaction.
RUN printf '%s\n' 'deb https://deb.debian.org/debian trixie-backports main' \
    > /etc/apt/sources.list.d/academy-trixie-backports.list
RUN apt-get update && apt-get install -y --no-install-recommends \
    perl-base \
    libnghttp3-9="${LIBNGHTTP3_VERSION}" \
    libngtcp2-16="${LIBNGTCP2_VERSION}" \
    libngtcp2-crypto-ossl0="${LIBNGTCP2_VERSION}" \
    libcurl4t64="${LIBCURL_VERSION}" \
    tesseract-ocr \
    tesseract-ocr-kor \
    && dpkg --configure -a \
    && libcurl_version="$(dpkg-query -W -f='${Version}' libcurl4t64)" \
    && libnghttp3_version="$(dpkg-query -W -f='${Version}' libnghttp3-9)" \
    && libngtcp2_version="$(dpkg-query -W -f='${Version}' libngtcp2-16)" \
    && libngtcp2_crypto_version="$(dpkg-query -W -f='${Version}' libngtcp2-crypto-ossl0)" \
    && test "$libcurl_version" = "${LIBCURL_VERSION}" \
    && test "$libnghttp3_version" = "${LIBNGHTTP3_VERSION}" \
    && test "$libngtcp2_version" = "${LIBNGTCP2_VERSION}" \
    && test "$libngtcp2_crypto_version" = "${LIBNGTCP2_VERSION}" \
    && (tesseract --version > /dev/null 2>&1 || (echo "tesseract not in PATH" && exit 1)) \
    && dpkg --purge --force-remove-essential perl-base \
    && apt-get autoremove -y --purge \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/sources.list.d/academy-trixie-backports.list \
    && test -z "$(dpkg --audit)" \
    && apt-get check \
    && ! dpkg-query -W perl-base >/dev/null 2>&1 \
    && for package in perl 'perl-modules-*' 'libperl*' libdpkg-perl liberror-perl; do ! dpkg-query -W "$package" >/dev/null 2>&1; done \
    && ! command -v perl >/dev/null 2>&1

USER appuser

FROM curl-fixed-system AS base

COPY --chown=appuser:appuser requirements/constraints.txt requirements/common.txt requirements/worker-tools.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-tools.txt

# 앱 소스는 의존성 설치 뒤에 둬 일반 코드 변경 시 pip 캐시를 보존한다.
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./

RUN python -c "from academy.application.use_cases.tools.worker_dispatcher import handle_tools_job; from academy.application.services.excel_parsing_service import ExcelParsingService; print(handle_tools_job.__name__, ExcelParsingService.__name__)"
RUN python -c "import apps.worker.tools_worker.sqs_main; from academy.framework.workers.ai_sqs_worker import run_ai_sqs_worker; print('tools entrypoint import OK')"

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

CMD ["python", "-m", "apps.worker.tools_worker.sqs_main"]
