# ==============================================================================
# Video Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS ffmpeg-builder

ARG FFMPEG_COMMIT=db05df9d135fb56a4babb836d5e9f5c1d984e087
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    libx264-dev \
    nasm \
    pkg-config \
    yasm \
    && rm -rf /var/lib/apt/lists/*
RUN git init /tmp/ffmpeg \
    && git -C /tmp/ffmpeg remote add origin https://github.com/FFmpeg/FFmpeg.git \
    && git -C /tmp/ffmpeg fetch --depth 1 origin "${FFMPEG_COMMIT}" \
    && git -C /tmp/ffmpeg checkout --detach FETCH_HEAD \
    && test "$(git -C /tmp/ffmpeg rev-parse HEAD)" = "${FFMPEG_COMMIT}"
RUN cd /tmp/ffmpeg \
    && ./configure \
        --prefix=/opt/academy-ffmpeg \
        --disable-autodetect \
        --disable-debug \
        --disable-doc \
        --disable-static \
        --enable-gpl \
        --enable-libx264 \
        --enable-pthreads \
        --enable-shared \
    && make -j"$(nproc)" \
    && make install

FROM ${BASE_IMAGE} AS base

ARG FFMPEG_COMMIT=db05df9d135fb56a4babb836d5e9f5c1d984e087
LABEL academy.ffmpeg.commit="${FFMPEG_COMMIT}"
ENV PATH="/opt/academy-ffmpeg/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/academy-ffmpeg/lib"

USER root
# Video Worker 전용 시스템 의존성. Debian ffmpeg/libav 패키지는 설치하지
# 않는다. 위의 고정 upstream commit은 CVE-2026-70628/70632 수정 두 개를
# 모두 포함하며, ECR의 패키지 버전 휴리스틱과 실제 런타임을 일치시킨다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libx264-164 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg-builder /opt/academy-ffmpeg /opt/academy-ffmpeg
RUN set -eux; \
    ffmpeg -hide_banner -version | grep -F "db05df9d13"; \
    ffmpeg -hide_banner -encoders | grep -q "libx264"; \
    mkdir -p /tmp/ffmpeg-smoke; \
    ffmpeg -hide_banner -loglevel error \
      -f lavfi -i testsrc2=size=320x180:rate=15 \
      -f lavfi -i sine=frequency=1000:sample_rate=48000 \
      -t 2 -c:v libx264 -pix_fmt yuv420p -c:a aac \
      -f hls -hls_time 1 -hls_list_size 0 /tmp/ffmpeg-smoke/stream.m3u8; \
    ffprobe -v error -show_entries format=duration \
      -of default=noprint_wrappers=1:nokey=1 /tmp/ffmpeg-smoke/stream.m3u8; \
    rm -rf /tmp/ffmpeg-smoke

# /tmp transcode dirs (ECS_AL2023 default user may not be root)
RUN mkdir -p /tmp/video-worker /tmp/video-worker-locks && chown -R appuser:appuser /tmp/video-worker /tmp/video-worker-locks

USER appuser
# Shared requirements (API + Worker same file)
COPY --chown=appuser:appuser requirements/constraints.txt requirements/requirements.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/requirements.txt

# 자주 바뀌는 앱 소스는 apt/pip 뒤에 둬 의존성 캐시를 보존한다.
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./
# Batch must remain the only video entry boundary; validate the SSM bootstrap
# module without starting a job or touching production resources.
RUN python -c "from apps.worker.video_worker.batch_entrypoint import REQUIRED_KEYS, load_env_from_ssm_json; assert 'DJANGO_SETTINGS_MODULE' in REQUIRED_KEYS; print('video Batch entrypoint import OK')"
# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

# Video Worker 실행
# ENTRYPOINT: SSM /academy/workers/env fetch 후 batch_main exec.
# AWS Batch는 jobdef containerProperties.command로 항상 ["python","-m","...batch_main","Ref::job_id"]를 명시 → CMD는 fallback일 뿐.
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
