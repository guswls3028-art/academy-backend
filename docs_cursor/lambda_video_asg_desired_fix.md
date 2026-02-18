# Lambda Video ASG desired 조정 수정

## 변경 파일 목록

| 파일 |
|------|
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` |

---

## 코드 diff

```diff
--- a/infra/worker_asg/queue_depth_lambda/lambda_function.py
+++ b/infra/worker_asg/queue_depth_lambda/lambda_function.py
@@ -66,23 +66,28 @@ def get_visible_count(sqs_client, queue_name: str) -> int:
     return visible
 
 
-def set_asg_desired(autoscaling_client, asg_name: str, visible: int, in_flight: int, min_capacity: int, max_capacity: int) -> None:
-    """워커 ASG desired capacity 조정. 큐 깊이에 따라 스케일."""
+def set_asg_desired(autoscaling_client, asg_name: str, visible: int, in_flight: int, min_capacity: int, max_capacity: int) -> None:
+    """워커 ASG desired capacity 조정. total=0이면 MIN, else min(MAX, max(MIN, ceil(total/20))). 항상 set_desired_capacity 호출."""
     total_for_scale = visible + in_flight
-    if total_for_scale > 0:
-        new_desired = min(max_capacity, max(min_capacity, math.ceil(total_for_scale / TARGET_MESSAGES_PER_INSTANCE)))
+    if total_for_scale == 0:
+        new_desired = min_capacity
     else:
-        new_desired = min_capacity  # 최소 용량 유지
+        new_desired = min(
+            max_capacity,
+            max(min_capacity, math.ceil(total_for_scale / TARGET_MESSAGES_PER_INSTANCE)),
+        )
 
     try:
         asgs = autoscaling_client.describe_auto_scaling_groups(
             AutoScalingGroupNames=[asg_name],
         )
         if not asgs.get("AutoScalingGroups"):
             logger.warning("ASG not found: %s", asg_name)
             return
         current = asgs["AutoScalingGroups"][0]["DesiredCapacity"]
-        if current == new_desired:
-            return
         autoscaling_client.set_desired_capacity(
             AutoScalingGroupName=asg_name,
             DesiredCapacity=new_desired,
         )
-        logger.info(
-            "%s desired %s -> %s (visible=%d in_flight=%d)",
-            asg_name, current, new_desired, visible, in_flight,
-        )
+        if current != new_desired:
+            logger.info(
+                "%s desired %s -> %s (visible=%d in_flight=%d)",
+                asg_name, current, new_desired, visible, in_flight,
+            )
     except Exception as e:
         logger.warning("set_asg_desired failed for %s: %s", asg_name, e)
```

---

## 배포 순서

1. Lambda 코드 배포  
   - `scripts/deploy_worker_asg.ps1` 실행 시 Queue depth Lambda가 해당 디렉터리에서 zip 후 배포됨.  
   - Lambda만 다시 배포하려면:
     - `infra/worker_asg/queue_depth_lambda/lambda_function.py`를 zip으로 패키징
     - `aws lambda update-function-code --function-name academy-worker-queue-depth-metric --zip-file fileb://...`

2. (선택) 환경변수  
   - `VIDEO_WORKER_ASG_MIN` 기본값 1. Min을 바꾼 경우 Lambda 환경변수에 설정.

---

## 검증 방법 (큐 0개일 때 desired=Min 확인)

1. Video 큐 비우기  
   - `academy-video-jobs`에 메시지 없음 확인. (visible=0, in_flight=0)

2. Lambda 1회 실행  
   - AWS 콘솔: Lambda → academy-worker-queue-depth-metric → Test (또는 EventBridge 1분 대기).

3. ASG desired 확인  
   - AWS 콘솔: EC2 → Auto Scaling Groups → academy-video-worker-asg → Desired capacity = Min (기본 1).  
   - CLI:
     ```bash
     aws autoscaling describe-auto-scaling-groups \
       --auto-scaling-group-names academy-video-worker-asg \
       --query "AutoScalingGroups[0].{DesiredCapacity:DesiredCapacity,MinSize:MinSize,MaxSize:MaxSize}"
     ```
   - DesiredCapacity 값이 MinSize와 같으면 통과.

4. CloudWatch Logs  
   - Lambda 로그에서 `academy-video-worker-asg desired N -> 1 (visible=0 in_flight=0)` 또는 desired 변경 로그 확인.
