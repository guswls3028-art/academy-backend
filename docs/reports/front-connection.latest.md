# Front V1 인프라 기준 연결 검증

**Generated:** 2026-07-06T17:41:55.7865416+09:00

## SSOT front.* / r2.* 확인
| 항목 | 값 | 비고 |
|------|-----|------|
| front.domains.app | hakwonplus.com | 설정됨 |
| front.domains.api | https://api.hakwonplus.com | 설정됨 |
| r2.publicBaseUrl |  | 미설정/선택 |
| front.r2StaticBucket/prefix |  / static/front | |
| CORS allowedOrigins | https://hakwonplus.com, https://www.hakwonplus.com | OK |

## 연결 검증 결과
| 항목 | 결과 | 근거 |
|------|------|------|
| app 도메인 200 | PASS | URL: https://hakwonplus.com/ |
| API 공개 /health | OK | https://api.hakwonplus.com |
| index.html Cache-Control | no-cache 계열 | |
| 해시 자산 Cache-Control | 1년 | 샘플: https://hakwonplus.com/assets/index-DcQDShRi.js |
| CORS 정적 검사 | OK | app 도메인 포함됨 |
| R2 버킷 | OK (wrangler list success) | wrangler r2 bucket list |

**배포 후 purge:** SSOT front.purgeOnDeploy 반영 여부는 배포 파이프라인에서 확인.
