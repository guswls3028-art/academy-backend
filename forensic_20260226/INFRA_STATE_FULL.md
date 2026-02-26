# AWS 인프라 상태 — 전달용 (한 번에 복사)

**Region:** ap-northeast-2  
**수집일:** forensic_20260226  
**용도:** 인프라 상태 전체 전달 — 아래 내용 전체 선택 후 복사하여 전달.

---

# REPORT

# AWS Infra Forensic Report

Region: ap-northeast-2  |  OutDir: C:\academy\forensic_20260226

---
## 1. Network structure

| Item | Evidence file |
|------|---------------|
| VPC | 02_vpcs.json |
| Subnets | 02_subnets.json |
| Route Tables | 02_route_tables.json |
| NAT Gateways | 02_nat_gateways.json |
| Internet Gateways | 02_internet_gateways.json |
| VPC Endpoints | 02_vpc_endpoints.json |
| Security Groups | 02_security_groups.json |

## 2. Internet path (API / Build / Worker)

- API: 03_api_instances.json -> SubnetId -> 02_route_tables / 02_nat_gateways
- Build: 04_build_instances.json, 04_build_subnet_route_tables.json
- Worker: 05_batch_compute_environments.json -> subnets -> 02_route_tables

## 3. SSOT check list

- Video CE: academy-video-batch-ce-final, state ENABLED, status VALID, instanceTypes c6g.large only -> 05_batch_compute_environments.json
- Video Queue: single CE only -> 05_batch_job_queues.json
- JobDef: vcpus 2, memory 3072, retryStrategy attempts 1 -> 05_batch_job_definitions.json
- EventBridge reconcile: rate 15 minutes, target Ops Queue -> 07_eventbridge_*.json

## 4. Potential failure points

- Build: no 0.0.0.0/0 to nat or igw -> STS/ECR timeout
- Batch CE INVALID -> 05_batch_compute_environments.json statusReason
- ECS container instances 0 with desiredvCpus gt 0 -> RUNNABLE stuck

## 5. Rebuild needed?

Review JSON in this folder for sections 2-4.

---

# Evidence: 01_caller_identity.json

```json
{"UserId":"AIDA3Y572RZN7SEXGFCJP","Account":"809466760795","Arn":"arn:aws:iam::809466760795:user/admin97"}
```

---

# Evidence: 02_vpcs.json

```json
{"Vpcs":[{"OwnerId":"809466760795","InstanceTenancy":"default","CidrBlockAssociationSet":[{"AssociationId":"vpc-cidr-assoc-0eb65a413a6c343ad","CidrBlock":"10.1.0.0/16","CidrBlockState":{"State":"associated"}}],"IsDefault":false,"Tags":[{"Key":"Name","Value":"academy-lambda-metric-vpc"}],"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"VpcId":"vpc-009e3ea6265c7a203","State":"available","CidrBlock":"10.1.0.0/16","DhcpOptionsId":"dopt-0639ddd9e11ef86e3"},{"OwnerId":"809466760795","InstanceTenancy":"default","CidrBlockAssociationSet":[{"AssociationId":"vpc-cidr-assoc-05d9d0062f6dfe575","CidrBlock":"172.31.0.0/16","CidrBlockState":{"State":"associated"}}],"IsDefault":true,"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"VpcId":"vpc-0b89e02241aae4b0e","State":"available","CidrBlock":"172.31.0.0/16","DhcpOptionsId":"dopt-0639ddd9e11ef86e3"},{"OwnerId":"809466760795","InstanceTenancy":"default","CidrBlockAssociationSet":[{"AssociationId":"vpc-cidr-assoc-0cd6c1b1ae5b95f01","CidrBlock":"172.30.0.0/16","CidrBlockState":{"State":"associated"}}],"IsDefault":false,"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"VpcId":"vpc-0831a2484f9b114c2","State":"available","CidrBlock":"172.30.0.0/16","DhcpOptionsId":"dopt-0639ddd9e11ef86e3"}]}
```

---

# Evidence: 02_subnets.json

<details>
<summary>02_subnets.json (펼치기)</summary>

```json
{"Subnets":[{"AvailabilityZoneId":"apne2-az4","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-049e711f41fdff71b","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-049e711f41fdff71b","State":"available","VpcId":"vpc-0831a2484f9b114c2","CidrBlock":"172.30.3.0/24","AvailableIpAddressCount":243,"AvailabilityZone":"ap-northeast-2d","DefaultForAz":false,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az1","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-07a8427d3306ce910","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-07a8427d3306ce910","State":"available","VpcId":"vpc-0831a2484f9b114c2","CidBlock":"172.30.0.0/24","AvailableIpAddressCount":242,"AvailabilityZone":"ap-northeast-2a","DefaultForAz":false,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az2","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-09231ed7ecf59cfa4","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-09231ed7ecf59cfa4","State":"available","VpcId":"vpc-0831a2484f9b114c2","CidrBlock":"172.30.1.0/24","AvailableIpAddressCount":243,"AvailabilityZone":"ap-northeast-2b","DefaultForAz":false,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az1","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"Tags":[{"Key":"Name","Value":"academy-lambda-metric-subnet"}],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-0759ef4e7f3817e6d","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-0759ef4e7f3817e6d","State":"available","VpcId":"vpc-009e3ea6265c7a203","CidrBlock":"10.1.1.0/24","AvailableIpAddressCount":250,"AvailabilityZone":"ap-northeast-2a","DefaultForAz":false,"MapPublicIpOnLaunch":false},{"AvailabilityZoneId":"apne2-az4","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-01c026861ea3cdecb","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-01c026861ea3cdecb","State":"available","VpcId":"vpc-0b89e02241aae4b0e","CidrBlock":"172.31.48.0/20","AvailableIpAddressCount":4091,"AvailabilityZone":"ap-northeast-2d","DefaultForAz":true,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az3","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-0548571ac21b3bbf3","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-0548571ac21b3bbf3","State":"available","VpcId":"vpc-0831a2484f9b114c2","CidrBlock":"172.30.2.0/24","AvailableIpAddressCount":244,"AvailabilityZone":"ap-northeast-2c","DefaultForAz":false,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az2","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-0e887178ed8cd65fa","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-0e887178ed8cd65fa","State":"available","VpcId":"vpc-0b89e02241aae4b0e","CidrBlock":"172.31.16.0/20","AvailableIpAddressCount":4091,"AvailabilityZone":"ap-northeast-2b","DefaultForAz":true,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az1","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-0f576f190bcfbdfff","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-0f576f190bcfbdfff","State":"available","VpcId":"vpc-0b89e02241aae4b0e","CidrBlock":"172.31.0.0/20","AvailableIpAddressCount":4091,"AvailabilityZone":"ap-northeast-2a","DefaultForAz":true,"MapPublicIpOnLaunch":true},{"AvailabilityZoneId":"apne2-az3","MapCustomerOwnedIpOnLaunch":false,"OwnerId":"809466760795","AssignIpv6AddressOnCreation":false,"Ipv6CidrBlockAssociationSet":[],"SubnetArn":"arn:aws:ec2:ap-northeast-2:809466760795:subnet/subnet-013323294fee4889e","EnableDns64":false,"Ipv6Native":false,"PrivateDnsNameOptionsOnLaunch":{"HostnameType":"ip-name","EnableResourceNameDnsARecord":false,"EnableResourceNameDnsAAAARecord":false},"BlockPublicAccessStates":{"InternetGatewayBlockMode":"off"},"SubnetId":"subnet-013323294fee4889e","State":"available","VpcId":"vpc-0b89e02241aae4b0e","CidrBlock":"172.31.32.0/20","AvailableIpAddressCount":4091,"AvailabilityZone":"ap-northeast-2c","DefaultForAz":true,"MapPublicIpOnLaunch":true}]}
```

</details>

---

# Evidence: 02_route_tables.json

```json
{"RouteTables":[{"Associations":[{"Main":true,"RouteTableAssociationId":"rtbassoc-098d7fc38894fc996","RouteTableId":"rtb-0c6a0b68df5c49578","AssociationState":{"State":"associated"}}],"PropagatingVgws":[],"RouteTableId":"rtb-0c6a0b68df5c49578","Routes":[{"DestinationCidrBlock":"172.30.0.0/16","Origin":"CreateRoute","State":"active","VpcPeeringConnectionId":"pcx-0b5aa682ec93e25be"},{"DestinationCidrBlock":"10.1.0.0/16","GatewayId":"local","Origin":"CreateRouteTable","State":"active"}],"Tags":[],"VpcId":"vpc-009e3ea6265c7a203","OwnerId":"809466760795"},{"Associations":[{"Main":true,"RouteTableAssociationId":"rtbassoc-0c501005d9ba46c1b","RouteTableId":"rtb-0a770875852c8a220","AssociationState":{"State":"associated"}}],"PropagatingVgws":[],"RouteTableId":"rtb-0a770875852c8a220","Routes":[{"DestinationCidrBlock":"172.30.0.0/16","GatewayId":"local","Origin":"CreateRouteTable","State":"active"},{"DestinationCidrBlock":"10.1.0.0/16","Origin":"CreateRoute","State":"active","VpcPeeringConnectionId":"pcx-0b5aa682ec93e25be"},{"DestinationCidrBlock":"0.0.0.0/0","GatewayId":"igw-0d0ff2f33976b474f","Origin":"CreateRoute","State":"active"},{"DestinationPrefixListId":"pl-78a54011","GatewayId":"vpce-05e329f9317c25a6c","Origin":"CreateRoute","State":"active"}],"Tags":[],"VpcId":"vpc-0831a2484f9b114c2","OwnerId":"809466760795"},{"Associations":[{"Main":true,"RouteTableAssociationId":"rtbassoc-021616f87d8e01c2b","RouteTableId":"rtb-09c52697da9c13176","AssociationState":{"State":"associated"}}],"PropagatingVgws":[],"RouteTableId":"rtb-09c52697da9c13176","Routes":[{"DestinationCidrBlock":"172.31.0.0/16","GatewayId":"local","Origin":"CreateRouteTable","State":"active"},{"DestinationCidrBlock":"0.0.0.0/0","GatewayId":"igw-088988aa4a4dd0cad","Origin":"CreateRoute","State":"active"}],"Tags":[],"VpcId":"vpc-0b89e02241aae4b0e","OwnerId":"809466760795"}]}
```

---

# Evidence: 02_nat_gateways.json

```json
{"NatGateways":[]}
```

---

# Evidence: 02_internet_gateways.json

```json
{"InternetGateways":[{"Attachments":[{"State":"available","VpcId":"vpc-0b89e02241aae4b0e"}],"InternetGatewayId":"igw-088988aa4a4dd0cad","OwnerId":"809466760795","Tags":[]},{"Attachments":[{"State":"available","VpcId":"vpc-0831a2484f9b114c2"}],"InternetGatewayId":"igw-0d0ff2f33976b474f","OwnerId":"809466760795","Tags":[]}]}
```

---

# Evidence: 02_vpc_endpoints.json

(내용 길이로 인해 동일 폴더의 `02_vpc_endpoints.json` 파일 참조. 전달 시 해당 파일을 함께 첨부하거나, 아래 명령으로 재수집 가능.)  
`aws ec2 describe-vpc-endpoints --region ap-northeast-2 --output json`

---

# Evidence: 02_security_groups.json

(내용 길이로 인해 동일 폴더의 `02_security_groups.json` 파일 참조. 전달 시 해당 파일을 함께 첨부.)

---

# Evidence: 03_api_instances.json — 수집 오류

DescribeInstances 필터 오류로 미수집. 전달 전 아래 명령으로 수동 수집 권장:

`aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=*api*" "Name=instance-state-name,Values=running" --output json`

---

# Evidence: 04 ~ 09 (Build, Batch, EventBridge, ECR, IAM)

해당 구간은 포렌식 스크립트 재실행 후 생성되는 JSON으로 대체하여 전달하세요.

```powershell
.\scripts\infra\infra_forensic_collect.ps1 -Region ap-northeast-2 -OutDir "C:\academy\forensic_20260226"
```

재실행 후 생성된 파일:  
04_build_instances.json, 05_batch_compute_environments.json, 05_batch_job_queues.json, 05_batch_job_definitions.json, 06_ops_jobs_*.json, 07_eventbridge_*.json, 08_ecr_*.json, 09_iam_role_*.json

---

**전달 방법:** 이 문서 전체 선택(Ctrl+A) 후 복사(Ctrl+C)하여 전달. 02_vpc_endpoints.json, 02_security_groups.json은 파일 자체를 첨부하면 됩니다.
