# BacklogCount 메트릭 최근 10분 조회 (PowerShell)
$s = (Get-Date).AddMinutes(-10).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$e = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
aws cloudwatch get-metric-statistics `
  --namespace Academy/VideoProcessing `
  --metric-name BacklogCount `
  --dimensions Name=WorkerType,Value=Video Name=AutoScalingGroupName,Value=academy-video-worker-asg `
  --start-time $s --end-time $e `
  --period 60 --statistics Average `
  --region ap-northeast-2 --output table
