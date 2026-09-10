import argparse
import logging
import os
import sys

from alicloud_utils import AliyunClient
from csv_utils import read_csv, write_csv, ECS_HEADER, VPC_HEADER, VSWITCHE_HEADER
from transform_fields_utils import transform_ecs_instances, transform_vpcs, transform_VSwitches

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("infoblox-eip")


def push_to_infoblox(args, ecs_instances_raw, vpcs_raw, vswitches_raw):
    from infoblox_wapi_client import InfobloxWAPIClient

    client = InfobloxWAPIClient(
        base_url=args.infoblox_url,
        username=args.infoblox_user,
        password=args.infoblox_password,
        wapi_version=args.wapi_version,
        network_view=args.network_view,
        verify_ssl=not args.infoblox_no_verify_ssl,
    )

    client.ensure_extattr_defs()

    if vpcs_raw:
        log.info(f"━━━ 推送 {len(vpcs_raw)} 个 VPC 到 Infoblox (networkcontainer) ━━━")
        for vpc in vpcs_raw:
            vpc_id = vpc.get("VpcId", "")
            vpc_name = vpc.get("VpcName", "")
            cidr_block = vpc.get("CidrBlock", "")
            region = vpc.get("RegionId", "")
            tenant = vpc.get("OwnerId") or vpc.get("OwnerAccount") or ""
            if vpc_id and cidr_block:
                try:
                    client.push_vpc(
                        vpc_id=vpc_id,
                        vpc_name=vpc_name,
                        cidr_block=cidr_block,
                        region=region,
                        tenant_id=tenant,
                    )
                except Exception as e:
                    log.error(f"  ❌ VPC {vpc_id} push failed: {e}")

    if vswitches_raw:
        vpc_name_map = {v.get("VpcId", ""): v.get("VpcName", "") for v in vpcs_raw}
        log.info(f"━━━ 推送 {len(vswitches_raw)} 个 VSwitch 到 Infoblox (network) ━━━")
        for vsw in vswitches_raw:
            vsw_id = vsw.get("VSwitchId", "")
            vsw_name = vsw.get("VSwitchName", "")
            cidr_block = vsw.get("CidrBlock", "")
            vpc_id = vsw.get("VpcId", "")
            vpc_name = vpc_name_map.get(vpc_id, "")
            region = vsw.get("RegionId", "") or args.region
            zone = vsw.get("ZoneId", "")
            tenant = vsw.get("OwnerId") or vsw.get("OwnerAccount") or ""
            if vsw_id and cidr_block:
                try:
                    client.push_vswitch(
                        vswitch_id=vsw_id,
                        vswitch_name=vsw_name,
                        cidr_block=cidr_block,
                        vpc_id=vpc_id,
                        vpc_name=vpc_name,
                        region=region,
                        zone=zone,
                        tenant_id=tenant,
                    )
                except Exception as e:
                    log.error(f"  ❌ VSwitch {vsw_id} push failed: {e}")

    if ecs_instances_raw:
        vpc_map = {v.get("VpcId", ""): v for v in vpcs_raw}
        vswitch_map = {v.get("VSwitchId", ""): v for v in vswitches_raw}
        log.info(f"━━━ 推送 {len(ecs_instances_raw)} 个 ECS 实例到 Infoblox (fixedaddress) ━━━")
        for ecs in ecs_instances_raw:
            vm_id = ecs.get("InstanceId", "")
            vm_name = ecs.get("InstanceName") or ecs.get("HostName") or ""

            # 提取私网 IP (复用 transform_fields_utils 的逻辑)
            private_ip = _extract_private_ip(ecs)
            public_ip = _extract_public_ip(ecs)
            mac = _extract_mac(ecs)
            os_name = ecs.get("OSName") or ecs.get("OSType") or ecs.get("Platform") or ecs.get("ImageId") or ""
            vpc_attrs = ecs.get("VpcAttributes") or {}
            vpc_id = vpc_attrs.get("VpcId") or ecs.get("VpcId") or ""
            subnet_id = vpc_attrs.get("VSwitchId", "")
            vpc = vpc_map.get(vpc_id, {})
            vswitch = vswitch_map.get(subnet_id, {})

            if vm_id:
                try:
                    client.push_ecs_instance(
                        vm_id=vm_id,
                        vm_name=vm_name,
                        private_ip=private_ip,
                        public_ip=public_ip,
                        mac_address=mac,
                        os_name=os_name,
                        vpc_id=vpc_id,
                        vpc_name=vpc.get("VpcName", ""),
                        region=ecs.get("RegionId", "") or args.region,
                        zone=ecs.get("ZoneId", ""),
                        subnet_id=subnet_id,
                        subnet_name=vswitch.get("VSwitchName", ""),
                        tenant_id=vpc.get("OwnerId") or vpc.get("OwnerAccount") or "",
                    )
                except Exception as e:
                    log.error(f"  ❌ ECS {vm_id} push failed: {e}")


def _extract_private_ip(ecs: dict) -> str:
    nis = ecs.get("NetworkInterfaces") or ecs.get("NetworkInterfaceSet")
    if isinstance(nis, dict):
        items = nis.get("NetworkInterface", [])
    else:
        items = nis or []

    if items:
        for iface in items:
            psets = iface.get("PrivateIpSets") or iface.get("PrivateIpSet") or {}
            candidate_ips = []
            if isinstance(psets, dict):
                candidate_ips = psets.get("PrivateIpSet", [])
            elif isinstance(psets, list):
                candidate_ips = psets
            for pip in candidate_ips:
                if pip.get("Primary") or pip.get("Primary") is True:
                    return pip.get("PrivateIpAddress") or pip.get("PrivateIp") or ""
        first = items[0]
        ip = first.get("PrimaryIpAddress") or first.get("PrimaryIp") or ""
        if ip:
            return ip

    if isinstance(ecs.get("InnerIpAddress"), dict):
        ips = ecs.get("InnerIpAddress", {}).get("IpAddress", [])
        if ips:
            return ips[0]
    return ecs.get("PrivateIpAddress") or ecs.get("PrivateIp") or ""


def _extract_public_ip(ecs: dict) -> str:
    import ast

    if isinstance(ecs.get("PublicIpAddress"), dict):
        pips = ecs.get("PublicIpAddress", {}).get("IpAddress", [])
        if pips:
            return pips[0]

    eip_field = ecs.get("EipAddress") or ecs.get("PublicIp") or ecs.get("Eip") or ""
    if isinstance(eip_field, dict):
        return eip_field.get("IpAddress") or eip_field.get("Ip") or ""
    elif isinstance(eip_field, list):
        return eip_field[0] if eip_field else ""
    elif isinstance(eip_field, str) and eip_field:
        try:
            parsed = ast.literal_eval(eip_field)
            if isinstance(parsed, dict):
                return parsed.get("IpAddress") or parsed.get("Ip") or ""
            elif isinstance(parsed, list) and parsed:
                return parsed[0]
            return eip_field
        except Exception:
            return eip_field
    return ""


def _extract_mac(ecs: dict) -> str:
    nis = ecs.get("NetworkInterfaces") or ecs.get("NetworkInterfaceSet")
    if isinstance(nis, dict):
        items = nis.get("NetworkInterface", [])
    else:
        items = nis or []
    if items:
        return items[0].get("MacAddress") or items[0].get("Mac") or ""
    return ""


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Collect Aliyun VPC/EIP/ECS data and push to Infoblox")

    parser.add_argument("--access-key-id", type=str, default=os.environ.get("ALIYUN_ACCESS_KEY_ID"))
    parser.add_argument("--access-key-secret", type=str, default=os.environ.get("ALIYUN_ACCESS_KEY_SECRET"))
    parser.add_argument("--region", type=str, default=os.environ.get("ALIYUN_REGION", "cn-hangzhou"))

    parser.add_argument("--csv-path", type=str, default="./", help="Output CSV path")
    parser.add_argument("--VM-csv-file-name", type=str, default="VM.csv", help="VM CSV output file name")
    parser.add_argument("--VPC-csv-file-name", type=str, default="VPC.csv", help="VPC CSV output file name")
    parser.add_argument("--vswitch-csv-file-name", type=str, default="vswitch.csv", help="vswitch CSV output file name")

    parser.add_argument("--infoblox-url", type=str, default=os.environ.get("INFOBLOX_URL"),
                        help="Infoblox WAPI base URL, e.g. https://10.0.0.1")
    parser.add_argument("--infoblox-user", type=str, default=os.environ.get("INFOBLOX_USER"),
                        help="Infoblox WAPI username")
    parser.add_argument("--infoblox-password", type=str, default=os.environ.get("INFOBLOX_PASSWORD"),
                        help="Infoblox WAPI password")
    parser.add_argument("--wapi-version", type=str, default="2.13.6", help="WAPI version")
    parser.add_argument("--network-view", type=str, default="default", help="Infoblox network view")
    parser.add_argument("--infoblox-no-verify-ssl", action="store_true", help="Skip SSL verification")

    parser.add_argument("--no-csv", action="store_true", help="Skip CSV output")
    parser.add_argument("--no-push", action="store_true", help="Skip Infoblox WAPI push")
    parser.add_argument("--dry-run", action="store_true", help="Collect data but don't write CSV or push")

    args = parser.parse_args()
    region_id = args.region

    if not args.access_key_id or not args.access_key_secret:
        log.error("Please provide access_key_id and access_key_secret (set ALIYUN_ACCESS_KEY_ID / ALIYUN_ACCESS_KEY_SECRET env vars).")
        sys.exit(1)

    aliyun_client = AliyunClient(
        access_key_id=args.access_key_id,
        access_key_secret=args.access_key_secret,
        region_id=region_id,
    )

    log.info(f"━━━ 从阿里云采集数据 (region={region_id}) ━━━")
    ecs_instances = aliyun_client.get_ecs_instances()
    log.info(f"  采集到 {len(ecs_instances)} 个 ECS 实例")

    vpcs = aliyun_client.get_vpcs()
    log.info(f"  采集到 {len(vpcs)} 个 VPC")

    vpc_ids = [vpc.get("VpcId", '') for vpc in vpcs if vpc.get("VpcId")]
    vswitches = aliyun_client.get_VSwitches(vpc_ids)
    log.info(f"  采集到 {len(vswitches)} 个 VSwitch")

    if not args.no_csv and not args.dry_run:
        log.info("━━━ writing CSV ━━━")
        VM_csv_file_path = os.path.join(args.csv_path, args.VM_csv_file_name)
        VPC_csv_file_path = os.path.join(args.csv_path, args.VPC_csv_file_name)
        vswitch_csv_file_path = os.path.join(args.csv_path, args.vswitch_csv_file_name)

        ecs_exists_data = read_csv(VM_csv_file_path)
        transformed_ecs = transform_ecs_instances(
            ecs_instances, exists_data=ecs_exists_data,
            vpcs=vpcs, vswitches=vswitches, region_id=region_id,
        )
        write_csv(VM_csv_file_path, header=ECS_HEADER, rows=transformed_ecs)
        log.info(f"  ✅ VM CSV -> {VM_csv_file_path}")

        VPC_exists_data = read_csv(VPC_csv_file_path)
        transformed_vpcs = transform_vpcs(vpcs, exists_data=VPC_exists_data)
        write_csv(VPC_csv_file_path, header=VPC_HEADER, rows=transformed_vpcs)
        log.info(f"  ✅ VPC CSV -> {VPC_csv_file_path}")

        vswitch_exists_data = read_csv(vswitch_csv_file_path)
        transformed_vswitches = transform_VSwitches(vswitches, vswitch_exists_data, region_id)
        write_csv(vswitch_csv_file_path, header=VSWITCHE_HEADER, rows=transformed_vswitches)
        log.info(f"  ✅ VSwitch CSV -> {vswitch_csv_file_path}")

    if not args.no_push and not args.dry_run:
        if not args.infoblox_url or not args.infoblox_user or not args.infoblox_password:
            log.warning("━━━ 跳过 Infoblox 推送 (未提供 --infoblox-url/--infoblox-user/--infoblox-password) ━━━")
        else:
            log.info("━━━ 推送到 Infoblox WAPI ━━━")
            push_to_infoblox(args, ecs_instances, vpcs, vswitches)

    if args.dry_run:
        log.info("━━━ Dry run 完成 (未写入 CSV, 未推送 WAPI) ━━━")

    log.info("━━━ done ━━━")
