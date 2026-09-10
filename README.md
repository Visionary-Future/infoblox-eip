# infoblox-eip

从阿里云采集 VPC / VSwitch / ECS(EIP) 数据，写入 CSV 并推送到 Infoblox WAPI。

## 安装

```bash
poetry install
```

## 用法

### 1. 仅采集 + 写 CSV（原有功能）

```bash
python3 main.py \
  --access-key-id "xxx" \
  --access-key-secret "xxx" \
  --region cn-hangzhou \
  --no-push
```

输出: `VM.csv`, `VPC.csv`, `vswitch.csv`

### 2. 采集 + 推送到 Infoblox WAPI

```bash
python3 main.py \
  --access-key-id "xxx" \
  --access-key-secret "xxx" \
  --region cn-hangzhou \
  --infoblox-url https://10.0.0.1 \
  --infoblox-user admin \
  --infoblox-password "xxx"
```

### 3. 仅推送（跳过 CSV）

```bash
python3 main.py \
  --access-key-id "xxx" \
  --access-key-secret "xxx" \
  --region cn-hangzhou \
  --no-csv \
  --infoblox-url https://10.0.0.1 \
  --infoblox-user admin \
  --infoblox-password "xxx"
```

### 4. Dry run（只采集，不写 CSV，不推送）

```bash
python3 main.py \
  --access-key-id "xxx" \
  --access-key-secret "xxx" \
  --region cn-hangzhou \
  --dry-run
```

## 数据映射

| 阿里云数据 | Infoblox 对象 | WAPI 端点 | extattrs |
|---|---|---|---|
| VPC | `networkcontainer` | `POST /wapi/v2.13.6/networkcontainer` | EA-AliCloudVPCID, EA-AliCloudVPCName, EA-AliCloudRegion, EA-AliCloudTenantID |
| VSwitch | `network` | `POST /wapi/v2.13.6/network` | EA-AliCloudSubnetID, EA-AliCloudSubnetName, EA-AliCloudVPCID, EA-AliCloudRegion, EA-AliCloudZone, EA-AliCloudTenantID |
| ECS + EIP | `fixedaddress` | `POST /wapi/v2.13.6/fixedaddress` | EA-AliCloudVMID, EA-AliCloudVMName, EA-AliCloudVMPublicIP, EA-AliCloudVMOS, EA-AliCloudVPCID, EA-AliCloudVPCName, EA-AliCloudRegion, EA-AliCloudZone, EA-AliCloudSubnetID, EA-AliCloudSubnetName, EA-AliCloudTenantID |

## 环境变量

| 变量 | 说明 |
|---|---|
| `ALIYUN_ACCESS_KEY_ID` | 阿里云 AK |
| `ALIYUN_ACCESS_KEY_SECRET` | 阿里云 SK |
| `ALIYUN_REGION` | 地域 (默认 cn-hangzhou) |
| `INFOBLOX_URL` | Infoblox WAPI 地址 |
| `INFOBLOX_USER` | Infoblox 用户名 |
| `INFOBLOX_PASSWORD` | Infoblox 密码 |
