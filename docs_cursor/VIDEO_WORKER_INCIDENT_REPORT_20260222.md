# VIDEO WORKER PIPELINE INCIDENT REPORT (FACT-ONLY)

**Generated:** 2026-02-22 13:00:45 +09:00ST (2026-02-22 04:00:45 UTC)  
**Data source:** C:\academy\backups\video_worker\incident_20260222_130024

---

## 1. ?꾩긽 ?붿빟 (?ъ슜??愿李?+ 痢≪젙媛?

| ??ぉ | 媛?|
|------|-----|
| ?ъ슜??愿李?| ?뚯빱媛 ???덈뒗???쇱쓣 ???섍굅?? 1?留??쇳븯嫄곕굹, ?좊졊(inflight/NotVisible) 硫붿떆吏媛 ?⑥쓬 |
| ?섏쭛 ?쒖젏 (KST) | 2026-02-22 13:00:45 +09:00ST |
| ?섏쭛 ?쒖젏 (UTC) | 2026-02-22 04:00:45 UTC |

---

## 2. ?섍꼍/援ъ꽦 ?ㅻ깄??

### 2.1 ASG
| ??ぉ | 媛?|
|------|-----|
| DesiredCapacity | 1 |
| MinSize | 1 |
| MaxSize | 20 |
| ?몄뒪?댁뒪 ??| 1 |
| InstanceId | LifecycleState | HealthStatus | AZ |
|------------|----------------|--------------|-----|
| i-0dd28a627debf579e | InService | Healthy | ap-northeast-2a |

### 2.2 Launch Template
**LT Spec (ASG ref):**
```json
{     "LaunchTemplateId": "lt-09f119c1af50af09b",     "LaunchTemplateName": "academy-video-worker-lt",     "Version": "$Latest" }
```
**LT Data (UserData/IamInstanceProfile/SecurityGroup/Subnet):** AMI=ami-0c7c64fed975b6737 IamProfile=academy-ec2-role
### 2.3 SSM ?깅줉
```
ASG=i-0dd28a627debf579e
SSM=i-0c8ae616abf345fd1,i-0dd28a627debf579e
ASG_NOT_SSM=
```
### 2.4 SQS (academy-video-jobs)
```
System.Management.Automation.RemoteException An error occurred (InvalidAttributeName) when calling the GetQueueAttributes operation: Unknown Attribute ApproximateAgeOfOldestMessage.
```
---

## 3. ?ъ떎 湲곕컲 ??꾨씪??

| ?쒖젏 | ?대깽??| 洹쇨굅 |
|------|--------|------|
| T0 | ?낅줈???꾨즺 (upload_complete) | (DB/API 濡쒓렇?먯꽌 ?뺤씤 ?꾩슂) |
| T1 | SQS 硫붿떆吏 ?앹꽦 (enqueue) | VIDEO_UPLOAD_ENQUEUE 濡쒓렇 ?먮뒗 SQS ApproximateNumberOfMessages |
| T2 | Worker媛 硫붿떆吏 ?섏떊 (claim) | Worker 濡쒓렇 "job claim" / ffmpeg ?쒖옉 |
| T3 | ?몄퐫???꾨즺 ?먮뒗 ?뺤? | Worker 濡쒓렇 / delete_message |
| (?섏쭛 ?쒖젏) | Visible / NotVisible | ?꾨옒 愿痢??곗씠??|
---

## 4. 愿痢??곗씠??

### 4.1 ASG Scaling Activities (理쒓렐 20嫄?
| StartTime | Activity | Description | StatusCode | StatusReason |
|-----------|----------|-------------|------------|--------------|
| 2026-02-22T03:29:08.972000+00:00 |  | Launching a new EC2 instance: i-0dd28a627debf579e | Successful |  |

### 4.2 SQS CloudWatch Metric (理쒓렐 15遺? 1遺?period)
**Visible:** Datapoints count=15 | 2026-02-22T12:45:00+09:00 Avg=0.0 | 2026-02-22T12:46:00+09:00 Avg=0.0 | 2026-02-22T12:47:00+09:00 Avg=0.0 | 2026-02-22T12:48:00+09:00 Avg=0.0 | 2026-02-22T12:49:00+09:00 Avg=0.0 | 2026-02-22T12:50:00+09:00 Avg=0.0 | 2026-02-22T12:51:00+09:00 Avg=0.0 | 2026-02-22T12:52:00+09:00 Avg=0.0 | 2026-02-22T12:53:00+09:00 Avg=0.0 | 2026-02-22T12:54:00+09:00 Avg=0.0 | 2026-02-22T12:55:00+09:00 Avg=0.0 | 2026-02-22T12:56:00+09:00 Avg=0.0 | 2026-02-22T12:57:00+09:00 Avg=0.0 | 2026-02-22T12:58:00+09:00 Avg=0.0 | 2026-02-22T12:59:00+09:00 Avg=0.0
**NotVisible:** Datapoints count=15 | 2026-02-22T12:45:00+09:00 Avg=0.0 | 2026-02-22T12:46:00+09:00 Avg=0.0 | 2026-02-22T12:47:00+09:00 Avg=0.0 | 2026-02-22T12:48:00+09:00 Avg=0.0 | 2026-02-22T12:49:00+09:00 Avg=0.0 | 2026-02-22T12:50:00+09:00 Avg=0.0 | 2026-02-22T12:51:00+09:00 Avg=0.0 | 2026-02-22T12:52:00+09:00 Avg=0.0 | 2026-02-22T12:53:00+09:00 Avg=0.0 | 2026-02-22T12:54:00+09:00 Avg=0.0 | 2026-02-22T12:55:00+09:00 Avg=0.0 | 2026-02-22T12:56:00+09:00 Avg=0.0 | 2026-02-22T12:57:00+09:00 Avg=0.0 | 2026-02-22T12:58:00+09:00 Avg=0.0 | 2026-02-22T12:59:00+09:00 Avg=0.0
### 4.3 Runtime Investigation (?몄뒪?댁뒪蹂?
```

```
### 4.4 ASG_NOT_SSM 肄섏넄 異쒕젰 (cloud-init/user-data)
(ASG_NOT_SSM ?놁쓬 - 紐⑤뱺 ASG ?몄뒪?댁뒪媛 SSM ?깅줉??
---

## 5. ?먯씤 (洹쇨굅 湲곕컲 遺꾨쪟)

媛??먯씤? ?꾨옒 A~E 以??대떦?섎뒗 ??ぉ留??ы븿. **異붿륫 湲덉?.**

| 肄붾뱶 | ?먯씤 | 洹쇨굅 | ?댁꽍 | 寃利?湲곗? |
|------|------|------|------|-----------|
| A | ASG Desired瑜??щ졇吏留??몄뒪?댁뒪媛 InService ?섏? 紐삵븿 | 1_asg.json, 1_asg_activities.json | Scaling Activity?먯꽌 Successful/InProgress/Failed ?뺤씤 | 紐⑤뱺 Activity媛 Successful?닿퀬 Instances媛 LifecycleState=InService |
| B | ?몄뒪?댁뒪 InService吏留?SSM 誘몃벑濡?| 3_ssm_registration.txt ASG_NOT_SSM | 遺???ㅽ듃?뚰겕/沅뚰븳 臾몄젣 | 6_console_*.txt?먯꽌 ECR login / SSM param / docker pull ?ㅽ뙣 ?쇱씤 |
| C | Worker 濡쒓렇??job claim/ffmpeg ?ㅻ쪟 | 4_runtime_investigation.txt | NO_FFMPEG ?먮뒗 worker ?먮윭 濡쒓렇 | ffmpegProcessCount > 0, worker 濡쒓렇??claim/encode ?깃났 |
| D | 硫붿떆吏 delete ??????NotVisible ?좊졊 | 5_sqs_attrs.json, 5_sqs_notvisible_metric.json | NotVisible > 0?몃뜲 worker??idle | NotVisible??visibility timeout ?댁뿉 0?쇰줈 媛먯냼 |
| E | 硫붿떆吏 enqueue ?꾨씫/以묐났 | API/DB 濡쒓렇, SQS Visible | upload_complete ?몄텧 ?ㅽ뙣 ?먮뒗 以묐났 | VIDEO_UPLOAD_ENQUEUE 濡쒓렇, SQS Visible ?쇱튂 |

**蹂??섏쭛 ?곗씠?곗뿉???뺤씤????ぉ:**  
(??愿痢??곗씠??4.1~4.4瑜?諛뷀깢?쇰줈 A~E 以??대떦?섎뒗 寃껊쭔 湲곗엯)

- A: ASG Activities??Failed ?먮뒗 InProgress ?湲??덉쓬? 
- B: ASG_NOT_SSM 鍮꾩뼱?덉? ?딆쓬? ??6_console_*.txt?먯꽌 cloud-init ?ㅽ뙣 ?쇱씤 ?몄슜
- C: 4_runtime_investigation?먯꽌 NO_FFMPEG ?먮뒗 worker ?먮윭?
- D: NotVisible??吏?띿쟻?쇰줈 > 0?
- E: (API 濡쒓렇 ?섏쭛 踰붿쐞 ??

---

## 6. ?닿껐梨?

### 6.1 利됱떆 議곗튂

| 議곗튂 | ?곸슜 諛⑸쾿 | ?깃났 ?먯젙 湲곗? |
|------|-----------|----------------|
| ASG_NOT_SSM ?몄뒪?댁뒪 媛뺤젣 援먯껜 | Instance refresh ?먮뒗 ?대떦 ?몄뒪?댁뒪 Terminate | ASG_NOT_SSM 鍮?吏묓빀, ???몄뒪?댁뒪 SSM ?깅줉 |
| NotVisible ?좊졊 硫붿떆吏 | visibility timeout ?湲??먮뒗 Redrive (Dead Letter) | NotVisible 0?쇰줈 ?섎졃 |
| Worker job claim ?ㅽ뙣 | Worker 而⑦뀒?대꼫 ?ъ떆???먮뒗 ?대?吏 ?щ같??| 4_runtime?먯꽌 ffmpeg ?꾨줈?몄뒪 ?뺤씤 |

### 6.2 洹쇰낯 議곗튂

| 議곗튂 | ?곸슜 諛⑸쾿 | ?깃났 ?먯젙 湲곗? |
|------|-----------|----------------|
| UserData retry/exitcode | video_worker_user_data.sh??set +e ?쒓굅, ?ㅽ뙣 ??exit 1 | 6_console?먯꽌 "cloud-init" ?ㅽ뙣 ?쇱씤 ?놁쓬 |
| SSM ?깅줉 蹂댁옣 | SSM Agent ?ㅼ튂/?쒖옉 ??대컢, IAM Role ?뺤씤 | 3_ssm_registration?먯꽌 ASG=SSM |
| Worker ?ㅽ뙣 ??delete_message | Worker 肄붾뱶?먯꽌 ?덉쇅 ?쒖뿉??delete_message ?몄텧 | NotVisible??visibility timeout ??媛먯냼 |
| Enqueue 蹂댁옣 | upload_complete ?⑥튂 (VIDEO_UPLOAD_ENQUEUE 濡쒓렇) | SQS Visible = 湲곕? 硫붿떆吏 ??|

---

## 7. ?щ컻 諛⑹? 泥댄겕由ъ뒪??

### 7.1 ?먮룞 寃利??ㅽ겕由쏀듃
```powershell
# ?곗씠???섏쭛 + 蹂닿퀬???앹꽦 (AWS ?먭꺽利앸챸 ?꾩슂)
.\scripts\collect_video_worker_incident_data.ps1

# SSM ?깅줉留?鍮좊Ⅴ寃??뺤씤
.\scripts\verify_video_worker_ssm.ps1

# ?꾩껜 吏꾨떒 (Lambda, SQS, ASG, CloudWatch)
.\scripts\diagnose_video_worker_full.ps1

# Runtime (?몄뒪?댁뒪蹂?docker/ffmpeg/worker 濡쒓렇)
.\scripts\investigate_video_worker_runtime.ps1
```

### 7.2 ?섎룞 泥댄겕

- [ ] ASG Desired vs ?ㅼ젣 InService ?몄뒪?댁뒪 ??
- [ ] SSM describe-instance-information??紐⑤뱺 ASG ?몄뒪?댁뒪 ?ы븿
- [ ] SQS ApproximateNumberOfMessagesNotVisible??visibility timeout(湲곕낯 30珥? ??0 ?섎졃
- [ ] Worker 濡쒓렇??"job claim" / ffmpeg ?쒖옉 / delete_message ?뺤씤
- [ ] Cloud-init / user-data 濡쒓렇?먯꽌 ECR login, SSM param, docker run ?깃났 ?뺤씤

---

**End of Report**