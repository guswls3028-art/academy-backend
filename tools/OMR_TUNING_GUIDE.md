# OMR 인식 튜닝 가이드

## 준비물

1. **OMR 답안지 인쇄** (A4 가로, 흑백 프린터)
   - 브라우저에서 `/omr-sheet.html?mc=30&choices=5` 열기
   - Ctrl+P → PDF 저장 또는 직접 인쇄

2. **마킹 도구**: 컴퓨터용 사인펜 (권장), 연필/샤프 (테스트용)

3. **스캐너**: 300dpi 이상, JPEG/PNG 저장

4. **환경**: `pip install opencv-python numpy`

## 폴더 구조

```
backend/
  tools/
    omr_debug.py          # 디버그 CLI
    sample_scans/         # 스캔 이미지 폴더 (직접 생성)
      scan_01_normal.jpg
      scan_02_light.jpg
      scan_03_double.jpg
      expected_scan_01_normal.json  # 기대답안 (선택)
      expected_scan_02_light.json
```

## 기대답안 JSON 형식

```json
{
  "1": "3",
  "2": "1",
  "3": "5",
  "4": "2"
}
```
key = 문항번호(1-indexed), value = 마킹한 선택지("1"~"5")

## 실행 명령어

```bash
cd backend/

# 단일 이미지 디버그
python tools/omr_debug.py scan sample_scans/scan_01.jpg --mc=30 --debug-dir=./debug_out

# 일괄 검증 (폴더 내 모든 이미지)
python tools/omr_debug.py batch sample_scans/ --mc=30 --debug-dir=./batch_out

# 좌표만 시각화 (실제 이미지 없이)
python tools/omr_debug.py coords --mc=30 --output=coords.jpg

# 메타 JSON 출력
python tools/omr_debug.py meta --mc=30
```

## 출력 파일

| 파일 | 설명 |
|------|------|
| `aligned.jpg` | 워프 보정된 이미지 |
| `roi_overlay.jpg` | ROI/버블 좌표 시각화 (초록=ROI, 빨강=버블, 파랑=식별자) |
| `fills.json` | 문항별 fill ratio (튜닝 핵심) |
| `result.json` | 최종 감지 결과 (detected, status, confidence) |
| `identifier.json` | 식별번호 감지 결과 |
| `meta.json` | 사용된 좌표 메타 |

## 튜닝 파라미터

### 1. `--binarize-threshold` (기본: 140)

**역할**: 이미지를 흑백으로 변환하는 밝기 경계값

**조정 시점**:
- 마킹이 분명한데 `blank` 판정 → **값을 낮추기** (120~130)
- 배경 노이즈가 마킹으로 잡힘 → **값을 높이기** (150~170)

**증상 진단**:
```
fills.json에서:
- 마킹한 버블의 fill이 0.02~0.05 → binarize 값이 너무 높음
- 빈 버블의 fill이 0.03~0.05 → binarize 값이 너무 낮음
```

### 2. `--blank-threshold` (기본: 0.060)

**역할**: 이 값 미만이면 "마킹 안 함(blank)"으로 판정

**조정 시점**:
- 옅은 마킹이 `blank` 처리됨 → **값을 낮추기** (0.040)
- 빈 칸이 `single` 처리됨 → **값을 높이기** (0.080)

### 3. `--gap-threshold` (기본: 0.055)

**역할**: 1위와 2위 fill ratio 차이가 이 값 미만이면 "ambiguous"

**조정 시점**:
- 이중 마킹이 `single`로 처리됨 → **값을 높이기** (0.070~0.100)
- 정상 마킹이 `ambiguous`로 처리됨 → **값을 낮추기** (0.040)

### 4. `--roi-expand` (기본: 1.55)

**역할**: 버블 ROI를 반지름의 N배로 확장 (위치 오차 흡수)

**조정 시점**:
- ROI가 버블을 못 잡음 (overlay에서 확인) → **값을 높이기** (1.8~2.0)
- 인접 버블과 ROI 겹침 → **값을 낮추기** (1.3~1.4)

## 튜닝 순서 (권장)

```
1. binarize_threshold 먼저 조정
   → fills.json에서 마킹 vs 빈칸의 fill ratio 차이가 명확한지 확인
   → 마킹: 0.15~0.40, 빈칸: 0.00~0.02 가 이상적

2. blank_threshold 조정
   → 빈칸 fill의 최대값보다 약간 위로 설정

3. gap_threshold 조정
   → 이중마킹 샘플에서 top-2 gap 확인 → gap보다 약간 위로 설정

4. roi_expand는 마지막
   → roi_overlay.jpg에서 ROI가 버블을 잘 감싸는지 시각적으로 확인
```

## 최소 검증 세트 (8장)

| # | 파일명 | 시나리오 | 목적 |
|---|--------|---------|------|
| 1 | `scan_01_normal.jpg` | 전문항 정확 마킹 (사인펜) | 기본 동작 |
| 2 | `scan_02_pencil.jpg` | 전문항 정확 마킹 (연필) | binarize 튜닝 |
| 3 | `scan_03_half.jpg` | 절반 마킹 + 절반 빈칸 | blank 감지 |
| 4 | `scan_04_double.jpg` | 3문항 이중 마킹 | ambiguous 감지 |
| 5 | `scan_05_light.jpg` | 옅게 마킹 | blank 경계 |
| 6 | `scan_06_erased.jpg` | 수정테이프 후 재마킹 | 수정 흔적 |
| 7 | `scan_07_tilted.jpg` | 5~10도 기울어짐 | warp 검증 |
| 8 | `scan_08_lowres.jpg` | 150dpi 스캔 | 최소 해상도 |

## 오조정 증상표

| 증상 | 원인 | 해결 |
|------|------|------|
| 모든 문항 blank | binarize 너무 높음 | binarize ↓ |
| 빈칸이 single | binarize 너무 낮음 or blank_threshold 너무 낮음 | binarize ↑ or blank ↑ |
| 모든 문항 ambiguous | gap_threshold 너무 높음 | gap ↓ |
| 이중마킹이 single | gap_threshold 너무 낮음 | gap ↑ |
| ROI가 버블 벗어남 | roi_expand 너무 작음 or 좌표 오차 | roi_expand ↑ or 좌표 확인 |
| 인접 버블 간섭 | roi_expand 너무 큼 | roi_expand ↓ |
