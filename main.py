import json
import csv
import ast
import os
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, List, Tuple

from aliyunsdkcore.auth.credentials import (
    AccessKeyCredential,
)
from aliyunsdkcore.client import AcsClient
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import (
    DescribeInstancesRequest,
)
from aliyunsdkcore.http import protocol_type
from aliyunsdkcore.request import CommonRequest, set_default_protocol_type
from pprint import pprint
import argparse

try:
    from aliyunsdkvpc.request.v20160428.DescribeVpcsRequest import DescribeVpcsRequest
except Exception:
    DescribeVpcsRequest = None

MAX_RESULTS = 100
set_default_protocol_type(protocol_type.HTTPS)


class AliyunClient:
    def __init__(
        self,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        region_id: Optional[str] = None,
    ) -> None:
        self.region_id = region_id

        # Only create credentials/client if both keys are provided. This allows
        # running the script in dry-run mode on machines without the SDK or creds.
        if access_key_id and access_key_secret:
            credential = AccessKeyCredential(access_key_id, access_key_secret)
            self.client = AcsClient(region_id=region_id, credential=credential)
        else:
            self.client = None

    def do_request(
        self,
        domain: Optional[str] = None,
        version: Optional[str] = None,
        action_name: Optional[str] = None,
        **kwargs: Dict[str, str],
    ) -> Dict[str, Any]:
        if kwargs and "request" in kwargs.keys():
            response = self.client.do_action_with_exception(kwargs["request"])
        else:
            request = CommonRequest()
            if domain:
                request.set_domain(domain)
            request.set_version(version)
            request.set_action_name(action_name)
            if kwargs:
                for key, value in kwargs.items():
                    request.add_query_param(key, value)
            response = self.client.do_action_with_exception(request)
        return json.loads(response)


class ECSClient(AliyunClient):

    def _request_ecs_instances(self) -> List[Dict[str, Any]]:
        def _build_request(
            token: Optional[str] = None,
        ) -> CommonRequest:
            request = CommonRequest()
            request.set_accept_format("json")
            request.set_domain(f"ecs.{self.region_id}.aliyuncs.com")
            request.set_method("POST")
            request.set_protocol_type("https")
            request.set_version("2014-05-26")
            request.set_action_name("DescribeInstances")

            request.add_query_param("RegionId", self.region_id)
            request.add_query_param("MaxResults", MAX_RESULTS)
            if token:
                request.add_query_param("NextToken", token)
            return request

        response_results: List[Dict[str, Any]] = []
        if self.client is None:
            # dry-run or no client
            return response_results

        token: Optional[str] = None

        while True:
            request = _build_request(token)
            response = self.do_request(**{"request": request})
            from pprint import pprint

            pprint(response)
            instances = response.get("Instances", {}).get("Instance", [])
            if instances:
                response_results.extend(instances)

            next_token = response.get("NextToken") or response.get("nextToken") or None
            if not next_token:
                break
            token = next_token

        return response_results

    def collect(self):
        ecs_instances = self._request_ecs_instances()
        res = []
        for instance in ecs_instances["Instances"]["Instance"]:
            pprint(instance)
            res.append(instance)
        return res

    def collect_vpcs(self, **kwargs) -> List[Dict[str, Any]]:
        # Legacy method kept for compatibility; prefer collect_vpcs_to_csv for CSV output
        vpcs: List[Dict[str, Any]] = []
        request = DescribeVpcsRequest()
        request.set_accept_format("json")
        query_params = {"request": request}
        response = self.do_request(**query_params)
        for vpc in response.get("Vpcs", {}).get("Vpc", []):
            # Keep original structure but avoid calling undefined helpers
            vpcs.append(vpc)
        return vpcs

    def collect_vpcs_to_csv(
        self,
        csv_path: str = "vpcs.csv",
        cmp_type: str = "Aliyun",
        dry_run: bool = False,
    ) -> str:
        """Collect VPCs and write to CSV while preserving First/Last discovered timestamps.

        If dry_run is True, uses a small sample payload and does not call the API.
        Returns the path to the CSV file written.
        """

        def now_iso() -> str:
            return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        existing: Dict[Tuple[str, str], Dict[str, str]] = {}
        if os.path.exists(csv_path):
            try:
                with open(csv_path, newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        key = (row.get("TenantID", ""), row.get("VpcId", ""))
                        existing[key] = row
            except Exception:
                # If CSV is malformed, start fresh
                existing = {}

        if dry_run:
            response_vpcs = [
                {
                    "VpcId": "vpc-123456",
                    "VpcName": "sample-vpc",
                    "CidrBlock": "10.0.0.0/16",
                    "RegionId": "cn-hangzhou",
                    "OwnerId": "1234567890123456",
                }
            ]
        else:
            request = DescribeVpcsRequest()
            request.set_accept_format("json")
            response = self.do_request(**{"request": request})
            response_vpcs = response.get("Vpcs", {}).get("Vpc", [])

        rows: List[Dict[str, str]] = []
        for vpc in response_vpcs:
            vpc_id = vpc.get("VpcId", "")
            tenant = vpc.get("OwnerId") or vpc.get("OwnerAccount") or ""
            key = (tenant, vpc_id)
            first = now_iso()
            last = now_iso()
            if key in existing:
                first = existing[key].get("FirstDiscovered", first)
                # update last discovered to now
                last = now_iso()

            row = {
                "VpcId": vpc_id,
                "VpcName": vpc.get("VpcName", ""),
                "CidrBlock": vpc.get("CidrBlock", ""),
                "RegionID": vpc.get("RegionId", ""),
                "CMPType": cmp_type,
                "TenantID": tenant,
                "FirstDiscovered": first,
                "LastDiscovered": last,
            }
            rows.append(row)

        # Write CSV (overwrite) with canonical header
        header = [
            "VpcId",
            "VpcName",
            "CidrBlock",
            "RegionID",
            "CMPType",
            "TenantID",
            "FirstDiscovered",
            "LastDiscovered",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        return os.path.abspath(csv_path)

    def collect_instances_to_csv(
        self,
        csv_path: str = "instances.csv",
        cmp_type: str = "Aliyun",
        dry_run: bool = False,
    ) -> str:
        """Collect ECS instances (VMs) and write to CSV while preserving First/Last discovered timestamps.

        Fields collected: VM Name, VM ID, Private Address, MAC Address, Public Address,
        VM VPC (VpcId), VM VPC CIDR, VM Guest OS, CMPType, TenantID, First/Last.
        """

        def now_iso() -> str:
            return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        existing: Dict[Tuple[str, str], Dict[str, str]] = {}
        if os.path.exists(csv_path):
            try:
                with open(csv_path, newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        key = (row.get("TenantID", ""), row.get("VMID", ""))
                        existing[key] = row
            except Exception:
                existing = {}

        # Build VPC CIDR map for instances
        vpc_cidrs: Dict[str, str] = {}
        try:
            if DescribeVpcsRequest and not dry_run:
                req = DescribeVpcsRequest()
                req.set_accept_format("json")
                vres = self.do_request(**{"request": req})
                for v in vres.get("Vpcs", {}).get("Vpc", []):
                    vpc_cidrs[v.get("VpcId", "")] = v.get("CidrBlock", "")
        except Exception:
            # best-effort only
            vpc_cidrs = {}

        if dry_run:
            response_instances = [
                {
                    "InstanceId": "i-123456",
                    "InstanceName": "sample-vm",
                    "InnerIpAddress": {"IpAddress": ["10.0.1.5"]},
                    "PublicIpAddress": {"IpAddress": ["1.2.3.4"]},
                    "VpcAttributes": {"VpcId": "vpc-123456"},
                    "OSName": "Ubuntu 20.04",
                }
            ]
        else:
            response_instances = self._request_ecs_instances()

        rows: List[Dict[str, str]] = []
        for inst in response_instances:
            vm_id = inst.get("InstanceId", "")
            vm_name = inst.get("InstanceName") or inst.get("HostName") or ""

            # Private IP and MAC extraction handling multiple shapes, incl.
            # NetworkInterfaces -> NetworkInterface list with PrimaryIpAddress and PrivateIpSets
            private_ip = ""
            mac = ""

            # Prefer NetworkInterfaces structure if present
            nis = inst.get("NetworkInterfaces") or inst.get("NetworkInterfaceSet")
            if isinstance(nis, dict):
                items = nis.get("NetworkInterface", [])
            else:
                items = nis or []

            if items:
                # Try to find primary private IP from PrivateIpSets where Primary is True
                for iface in items:
                    # MAC from interface if available
                    if not mac:
                        mac = iface.get("MacAddress") or iface.get("Mac") or ""

                    # PrivateIpSets may be nested under different keys
                    psets = iface.get("PrivateIpSets") or iface.get("PrivateIpSet") or {}
                    candidate_ips = []
                    if isinstance(psets, dict):
                        candidate_ips = psets.get("PrivateIpSet", [])
                    elif isinstance(psets, list):
                        candidate_ips = psets

                    # Look for Primary=True entry
                    found = False
                    for pip in candidate_ips:
                        if pip.get("Primary") or pip.get("Primary") is True:
                            private_ip = pip.get("PrivateIpAddress") or pip.get("PrivateIp") or ""
                            found = True
                            break
                    if found:
                        break

                # If still not found, try PrimaryIpAddress on first interface
                if not private_ip:
                    first = items[0]
                    private_ip = first.get("PrimaryIpAddress") or first.get("PrimaryIp") or ""

            # Fallbacks: InnerIpAddress or other fields
            if not private_ip:
                if isinstance(inst.get("InnerIpAddress"), dict):
                    ips = inst.get("InnerIpAddress", {}).get("IpAddress", [])
                    if ips:
                        private_ip = ips[0]
            if not private_ip:
                private_ip = inst.get("PrivateIpAddress") or inst.get("PrivateIp") or ""

            # Public IP: prefer structured PublicIpAddress, then EipAddress/PublicIp.
            public_ip = ""
            if isinstance(inst.get("PublicIpAddress"), dict):
                pips = inst.get("PublicIpAddress", {}).get("IpAddress", [])
                if pips:
                    public_ip = pips[0]

            if not public_ip:
                eip_field = inst.get("EipAddress") or inst.get("PublicIp") or inst.get("Eip") or ""
                # If SDK returns a dict-like object, extract IpAddress
                if isinstance(eip_field, dict):
                    # EipAddress may be nested dict with IpAddress key
                    public_ip = eip_field.get("IpAddress") or eip_field.get("Ip") or ""
                elif isinstance(eip_field, list):
                    # list of IPs
                    if eip_field:
                        public_ip = eip_field[0]
                elif isinstance(eip_field, str) and eip_field:
                    # Sometimes CSV shows a Python dict literal as string: try to parse
                    try:
                        parsed = ast.literal_eval(eip_field)
                        if isinstance(parsed, dict):
                            public_ip = parsed.get("IpAddress") or parsed.get("Ip") or ""
                        elif isinstance(parsed, list) and parsed:
                            public_ip = parsed[0]
                        else:
                            # fallback to the string itself if it's a plain IP
                            public_ip = eip_field
                    except Exception:
                        # not a literal dict or unparsable, assume it's an IP string
                        public_ip = eip_field

            vpc_id = ""
            vpc_attrs = inst.get("VpcAttributes") or {}
            vpc_id = vpc_attrs.get("VpcId") or inst.get("VpcId") or ""
            vpc_cidr = vpc_cidrs.get(vpc_id, "")

            guest_os = inst.get("OSName") or inst.get("OSType") or inst.get("Platform") or inst.get("ImageId") or ""

            tenant = inst.get("OwnerId") or inst.get("OwnerAccount") or ""
            key = (tenant, vm_id)

            first = now_iso()
            last = now_iso()
            if key in existing:
                first = existing[key].get("FirstDiscovered", first)
                last = now_iso()

            row = {
                "VMName": vm_name,
                "VMID": vm_id,
                "PrivateAddress": private_ip,
                "MACAddress": mac,
                "PublicAddress": public_ip,
                "VMVPC": vpc_id,
                "VMVPC_CIDR": vpc_cidr,
                "VMGuestOS": guest_os,
                "CMPType": cmp_type,
                "TenantID": tenant,
                "FirstDiscovered": first,
                "LastDiscovered": last,
            }
            rows.append(row)

        header = [
            "VMName",
            "VMID",
            "PrivateAddress",
            "MACAddress",
            "PublicAddress",
            "VMVPC",
            "VMVPC_CIDR",
            "VMGuestOS",
            "CMPType",
            "TenantID",
            "FirstDiscovered",
            "LastDiscovered",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        return os.path.abspath(csv_path)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Collect Aliyun VPCs and write to CSV")
    parser.add_argument("--dry-run", action="store_true", help="Run without calling Aliyun API")
    parser.add_argument("--csv-path", default="vpcs.csv", help="Output CSV path")
    parser.add_argument("--export-instances", action="store_true", help="Also export instances to CSV")
    parser.add_argument("--instances-csv", default="instances.csv", help="Instances CSV output path")
    parser.add_argument("--access-key-id", default=os.environ.get("ALIYUN_ACCESS_KEY_ID"))
    parser.add_argument("--access-key-secret", default=os.environ.get("ALIYUN_ACCESS_KEY_SECRET"))
    parser.add_argument("--region", default=os.environ.get("ALIYUN_REGION", "cn-hangzhou"))

    args = parser.parse_args()

    client = ECSClient(
        access_key_id=args.access_key_id,
        access_key_secret=args.access_key_secret,
        region_id=args.region,
    )

    csv_path = client.collect_vpcs_to_csv(csv_path=args.csv_path, dry_run=args.dry_run)
    print(f"Wrote VPCs to: {csv_path}")
    if args.export_instances:
        inst_csv = client.collect_instances_to_csv(csv_path=args.instances_csv, dry_run=args.dry_run)
        print(f"Wrote instances to: {inst_csv}")
