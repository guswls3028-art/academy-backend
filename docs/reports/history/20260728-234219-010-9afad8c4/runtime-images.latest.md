# V1 Runtime Images — API 인스턴스 실제 실행 이미지

**Generated:** 2026-07-28T23:41:35.5292371+09:00
**SSOT:** docs/ssot/params.yaml
**Container:** academy-api

### Successful Release vs Runtime
**MISMATCH** — 하나 이상의 API 인스턴스 런타임 RepoDigests가 성공 release digest와 다릅니다.
- Successful release digest (academy-api): sha256:c17ad8dfe7a5e5345140ea14f1e81b892142c2f853afacf66f4f8d8d738f6e60
- Release manifest SHA256: 54F17E7375CB53210BF0C5E8A3C94E1A3B4642729C7DE26F6DF9446F7533F1D3
- Instance count: 1

| InstanceId | Container | State | ConfigImage | ImageId | RepoDigests | CI Match | Error |
|------------|-----------|-------|-------------|---------|-------------|----------|-------|
| i-063524e0bc4aec5a0 | academy-api | running | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api@sha256:07732d5bd9a0015726414846043b1c802d13e437c638d4a9caea31a319ba144d | sha256:b60c4795a9d2d9a76ee893d5f3f4483fec1afc05a52384c80fadc841b35b3b90 | ["809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api@sha256:07732d5bd9a0015726414846043b1c802d13e437c638d4a9caea31a319ba144d"] | MISMATCH | - |


**Verification Run ID:** 6e1b4a02422640eb8e7e3777f03a7686
