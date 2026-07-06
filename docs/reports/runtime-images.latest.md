# V1 Runtime Images — API 인스턴스 실제 실행 이미지

**Generated:** 2026-07-06T17:41:19.9026682+09:00
**SSOT:** docs/ssot/params.yaml
**Container:** academy-api

### CI vs Runtime
**MISMATCH** — 하나 이상의 API 인스턴스 런타임 RepoDigests가 CI digest와 다릅니다.
- CI digest (academy-api): sha256:ad945f4c99646411da758fcd9c6f91912a88ff0579bec8d866d3380ffb967801
- Instance count: 1

| InstanceId | Container | State | ConfigImage | ImageId | RepoDigests | CI Match | Error |
|------------|-----------|-------|-------------|---------|-------------|----------|-------|
| i-0e77e903787b2d638 | academy-api | running | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest | sha256:c9833bf2ec7731a47886d314565f8e8afd12971b9b41786fb0419199377d0d0a | ["809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api@sha256:dcf1502e45b15c429dcbd374ef78c1c19ae09c109601eb1c413a3259a8c2152c"] | MISMATCH | - |
