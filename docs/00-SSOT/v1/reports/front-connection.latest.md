# Front V1 인프라 기준 연결 검증

**Generated:** 2026-03-08T19:56:04.7206877+09:00

## SSOT front.* / r2.* 확인
| 항목 | 값 | 비고 |
|------|-----|------|
| front.domains.app |  | 비어있음(경고) |
| front.domains.api | https://api.hakwonplus.com | 설정됨 |
| r2.publicBaseUrl |  | 미설정/선택 |
| front.r2StaticBucket/prefix |  / static/front | |
| CORS allowedOrigins | (비어있음) | not checked |

## 연결 검증 결과
| 항목 | 결과 | 근거 |
|------|------|------|
| app 도메인 200 | FAIL/WARNING () | URL 미설정 |
| API 공개 /health | OK | https://api.hakwonplus.com |
| index.html Cache-Control |  | |
| 해시 자산 Cache-Control |  | - |
| CORS 정적 검사 | not checked |  |
| R2 버킷 | OK (wrangler list success) | wrangler r2 bucket list |

**배포 후 purge:** SSOT front.purgeOnDeploy 반영 여부는 배포 파이프라인에서 확인.

