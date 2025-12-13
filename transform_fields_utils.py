import ipaddress
from datetime import datetime
from typing import Any, List, Dict, Tuple
import ast

def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def cidr_to_ip_netmask(cidr_string):
    try:
        network = ipaddress.ip_network(cidr_string, strict=False)
        network_ip = str(network.network_address)
        netmask = str(network.netmask)
        return network_ip, netmask
    except ValueError as e:
        print(e)
        return None, None

def transform_ecs_instances(ecs_instances: List[Dict[str, Any]] = [], exists_data: List[Dict[str, str]]=[]) -> List[Dict[str, str]]:
    existing: Dict[str, Dict[str, str]] = {}
    for row in exists_data:
        key = row.get("EA-AliCloudVMId", "")
        existing[key] = row
    rows: List[Dict[str, str]] = []
    for ecs in ecs_instances:
        vm_id = ecs.get("InstanceId", "")
        vm_name = ecs.get("InstanceName") or ecs.get("HostName") or ""

        # Private IP and MAC extraction handling multiple shapes, incl.
        # NetworkInterfaces -> NetworkInterface list with PrimaryIpAddress and PrivateIpSets
        private_ip = ""
        mac = ""
        # Prefer NetworkInterfaces structure if present
        nis = ecs.get("NetworkInterfaces") or ecs.get("NetworkInterfaceSet")
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
            if isinstance(ecs.get("InnerIpAddress"), dict):
                ips = ecs.get("InnerIpAddress", {}).get("IpAddress", [])
                if len(ips) > 0:
                    private_ip = ips[0]
        if not private_ip:
            private_ip = ecs.get("PrivateIpAddress") or ecs.get("PrivateIp") or ""

        # Public IP: prefer structured PublicIpAddress, then EipAddress/PublicIp.
        public_ip = ""
        if isinstance(ecs.get("PublicIpAddress"), dict):
            pips = ecs.get("PublicIpAddress", {}).get("IpAddress", [])
            if pips:
                public_ip = pips[0]

        if not public_ip:
            eip_field = ecs.get("EipAddress") or ecs.get("PublicIp") or ecs.get("Eip") or ""
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

        vpc_attrs = ecs.get("VpcAttributes") or {}
        vpc_id = vpc_attrs.get("VpcId") or ecs.get("VpcId") or ""

        guest_os = ecs.get("OSName") or ecs.get("OSType") or ecs.get("Platform") or ecs.get("ImageId") or ""

        first = now_iso()
        last = now_iso()

        if vm_id in existing:
            first = existing[key].get("EA-AliCloudFirstDiscovered", first)
            last = now_iso()
        row = {
            "HEADER-FixedAddress": "FixedAddress",
            "ip_address": private_ip,
            "mac_address": mac,
            "EA-AliCloudVMName": vm_name,
            "EA-AliCloudVMID": vm_id,
            "EA-AliCloudVMPublicIP": public_ip,
            "EA-AliCloudVMOS": guest_os,
            "EA-AliCloudFirstDiscovered": first,
            "EA-AliCloudLastDiscovered": last,
            "EA-AliCloudVPCID": vpc_id,
        }
        rows.append(row)
    return rows

def transform_vpcs(vpcs: List[Dict[str, Any]] = [], exists_data: List[Dict[str, str]]=[]) -> List[Dict[str, str]]:
    existing: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in exists_data:
        key = (row.get("EA-AliCloudTenantID", ""), row.get("EA-AliCloudVPCID", ""))
        existing[key] = row
    rows: List[Dict[str, str]] = []

    for vpc in vpcs:
        vpc_id = vpc.get("VpcId", "")
        tenant = vpc.get("OwnerId") or vpc.get("OwnerAccount") or ""
        key = (tenant, vpc_id)

        first = now_iso()
        last = now_iso()
        cidr_block = vpc.get("CidrBlock", "0.0.0.0/8")
        address, netmask = cidr_to_ip_netmask(cidr_block)

        if key in existing:
            first = existing[key].get("EA-AliCloudFirstDiscovered", first)
            # update last discovered to now
            last = now_iso()

        row = {
            "HEADER-NetworkContainer": "NetworkContainer",
            "address": address or "",
            "netmask": netmask or "",
            "EA-AliCloudVPCID": vpc_id,
            "EA-AliCloudVPCName": vpc.get("VpcName", ""),
            "EA-AliCloudRegion": vpc.get("RegionId", ""),
            "EA-AliCloudTenantID": tenant,
            "EA-AliCloudFirstDiscovered": first,
            "EA-AliCloudLastDiscovered": last,
        }
        rows.append(row)
    return rows

def transform_VSwitches(VSwitches: List[Dict[str, Any]] = [], exists_data: List[Dict[str, str]]=[], region_id: str = "") -> List[Dict[str, str]]:
    existing: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in exists_data:
        key = (row.get("EA-AliCloudVPCID", ""), row.get("EA-AliCloudSubnetID", ""))
        existing[key] = row
    rows: List[Dict[str, str]] = []

    for vswitch in VSwitches:
        vpc_id = vswitch.get("VpcId", "")
        v_switch_id = vswitch.get("VSwitchId", "")
        v_switch_name = vswitch.get("VSwitchName", "")
        cidr_block = vswitch.get("CidrBlock", "")
        # region_id = vswitch.get("RegionId", "")
        tenant = vswitch.get("OwnerId") or vswitch.get("OwnerAccount") or ""
        address, netmask = cidr_to_ip_netmask(cidr_block)

        key = (vpc_id, v_switch_id)

        first = now_iso()
        last = now_iso()

        if key in existing:
            first = existing[key].get("EA-AliCloudFirstDiscovered", first)
            # update last discovered to now
            last = now_iso()

        row = {
            "HEADER-Network": "Network",
            "address": address or "",
            "netmask": netmask or "",
            "EA-AliCloudSubnetID": v_switch_id,
            "EA-AliCloudSubnetName": v_switch_name,
            "EA-AliCloudVPCID": vpc_id,
            "EA-AliCloudRegion": region_id,
            "EA-AliCloudTenantID": tenant,
            "EA-AliCloudFirstDiscovered": first,
            "EA-AliCloudLastDiscovered": last,
        }
        rows.append(row)
    return rows

