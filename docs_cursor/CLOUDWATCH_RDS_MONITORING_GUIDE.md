# CloudWatch RDS 모니터링 설정 가이드

**작업일**: 2026-02-18  
**목적**: DB 부하 감소 효과 확인 및 운영 안정성 모니터링

---

## 📊 모니터링 대상 메트릭

### 1. CPUUtilization (가장 중요)
- **목적**: RDS CPU 사용률 모니터링
- **임계값**: 80% 이상 시 알람
- **예상 효과**: 폴링 전환 후 즉시 감소 예상

### 2. DatabaseConnections
- **목적**: DB 연결 수 모니터링
- **임계값**: max_connections의 80% 이상 시 알람
- **예상 효과**: 폴링 제거로 연결 수 감소 예상

### 3. ReadLatency / WriteLatency (선택)
- **목적**: 쿼리 성능 모니터링
- **임계값**: 평균 대비 2배 이상 증가 시 알람

---

## 🔧 CloudWatch 알람 설정 방법

### 방법 1: AWS 콘솔에서 설정

#### 1. RDS 인스턴스 확인
```bash
# RDS 인스턴스 식별자 확인
aws rds describe-db-instances \
  --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceClass,Engine]' \
  --output table
```

#### 2. CloudWatch 콘솔 접속
1. AWS 콘솔 → CloudWatch → Alarms → Create alarm
2. Metric 선택 → RDS → Per-Instance Metrics
3. 다음 메트릭 선택:
   - **CPUUtilization**
   - **DatabaseConnections**

#### 3. CPUUtilization 알람 설정
- **Metric**: `CPUUtilization`
- **Statistic**: `Average`
- **Period**: `5 minutes`
- **Threshold**: `Greater than 80`
- **Alarm name**: `RDS-CPU-High-{DBInstanceIdentifier}`
- **SNS Topic**: 알람 수신용 SNS 토픽 선택 (없으면 생성)

#### 4. DatabaseConnections 알람 설정
- **Metric**: `DatabaseConnections`
- **Statistic**: `Average`
- **Period**: `5 minutes`
- **Threshold**: 
  - db.t4g.micro: `Greater than 60` (max_connections=87의 70%)
  - db.t4g.small: `Greater than 100` (max_connections=125의 80%)
  - db.t4g.medium: `Greater than 200` (max_connections=250의 80%)
- **Alarm name**: `RDS-Connections-High-{DBInstanceIdentifier}`

---

### 방법 2: AWS CLI로 설정

#### SNS 토픽 생성 (알람 수신용)
```bash
# SNS 토픽 생성
aws sns create-topic --name rds-alarms

# 이메일 구독 추가
aws sns subscribe \
  --topic-arn arn:aws:sns:region:account:rds-alarms \
  --protocol email \
  --notification-endpoint your-email@example.com
```

#### CPUUtilization 알람 생성
```bash
# RDS 인스턴스 식별자 확인 후 설정
DB_INSTANCE_ID="your-rds-instance-id"
SNS_TOPIC_ARN="arn:aws:sns:region:account:rds-alarms"

aws cloudwatch put-metric-alarm \
  --alarm-name "RDS-CPU-High-${DB_INSTANCE_ID}" \
  --alarm-description "RDS CPU utilization is above 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/RDS \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=DBInstanceIdentifier,Value=${DB_INSTANCE_ID} \
  --alarm-actions ${SNS_TOPIC_ARN}
```

#### DatabaseConnections 알람 생성
```bash
# max_connections에 따라 임계값 조정 필요
# db.t4g.micro: 60, db.t4g.small: 100, db.t4g.medium: 200
CONNECTION_THRESHOLD=60  # 인스턴스 타입에 맞게 조정

aws cloudwatch put-metric-alarm \
  --alarm-name "RDS-Connections-High-${DB_INSTANCE_ID}" \
  --alarm-description "RDS database connections are above threshold" \
  --metric-name DatabaseConnections \
  --namespace AWS/RDS \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold ${CONNECTION_THRESHOLD} \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=DBInstanceIdentifier,Value=${DB_INSTANCE_ID} \
  --alarm-actions ${SNS_TOPIC_ARN}
```

---

## 📈 모니터링 대시보드 생성

### CloudWatch Dashboard 생성
```bash
# 대시보드 JSON 생성
cat > rds-dashboard.json << 'EOF'
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "your-rds-instance-id"],
          [".", "DatabaseConnections", ".", "."]
        ],
        "period": 300,
        "stat": "Average",
        "region": "ap-northeast-2",
        "title": "RDS Performance Metrics"
      }
    }
  ]
}
EOF

# 대시보드 생성
aws cloudwatch put-dashboard \
  --dashboard-name "RDS-Monitoring" \
  --dashboard-body file://rds-dashboard.json
```

---

## ✅ 모니터링 체크리스트

### 폴링 전환 전 (Baseline)
- [ ] CPUUtilization 평균값 기록
- [ ] DatabaseConnections 평균값 기록
- [ ] Peak 시간대 CPU/Connection 값 기록

### 폴링 전환 후 (After)
- [ ] CPUUtilization 감소 확인 (예상: 30-50% 감소)
- [ ] DatabaseConnections 감소 확인 (예상: 진행률 조회 관련 연결 제거)
- [ ] Peak 시간대 부하 감소 확인

### 알람 테스트
- [ ] CPUUtilization 알람 정상 작동 확인
- [ ] DatabaseConnections 알람 정상 작동 확인
- [ ] SNS 알림 수신 확인

---

## 🎯 예상 효과

### DB 부하 감소 예상치
- **CPUUtilization**: 30-50% 감소 예상
  - 진행률 조회 관련 SELECT 쿼리 제거
  - 폴링 빈도: 1초마다 → Redis 조회로 변경

- **DatabaseConnections**: 20-30% 감소 예상
  - 진행률 조회용 DB 연결 제거
  - 동시 폴링 수에 비례하여 감소

### 모니터링 기간
- **즉시 효과**: 폴링 전환 직후 확인 가능
- **안정화 기간**: 24-48시간 모니터링 권장

---

## 📝 참고사항

### RDS 인스턴스별 max_connections
- **db.t4g.micro**: 87 connections
- **db.t4g.small**: 125 connections
- **db.t4g.medium**: 250 connections

### 알람 임계값 권장사항
- **CPUUtilization**: 80% (경고), 90% (심각)
- **DatabaseConnections**: max_connections의 70-80%

### 모니터링 주기
- **실시간**: CloudWatch Dashboard (1분 간격)
- **알람**: 5분 평균 기준
- **리포트**: 일일/주간 리포트 생성 권장

---

**CloudWatch 모니터링 설정 완료 후, 폴링 전환 전후 비교를 통해 DB 부하 감소 효과를 확인하세요.** ✅
