import csv
import os
from typing import List, Dict, LiteralString

ECS_HEADER = [
    "HEADER-FixedAddress",
    "ip_address",
    "mac_address",
    "EA-AliCloudVMName",
    "EA-AliCloudVMID",
    "EA-AliCloudVMPublicIP",
    "EA-AliCloudVMOS",
    "EA-AliCloudFirstDiscovered",
    "EA-AliCloudLastDiscovered",
    "EA-AliCloudVPCID",
    "EA-AliCloudVPCName",
    "EA-AliCloudRegion",
    "EA-AliCloudZone",
    "EA-AliCloudSubnetID",
    "EA-AliCloudSubnetName",
    "EA-AliCloudTenantID",
]

VPC_HEADER = [
    "header-networkcontainer",
    "address*",
    "netmask*",
    "EA-AliCloudVPCID",
    "EA-AliCloudVPCName",
    "EA-AliCloudRegion",
    "EA-AliCloudTenantID",
    "EA-AliCloudFirstDiscovered",
    "EA-AliCloudLastDiscovered",
]

VSWITCHE_HEADER = [
    "HEADER-Network",
    "address",
    "netmask",
    "EA-AliCloudSubnetID",
    "EA-AliCloudSubnetName",
    "EA-AliCloudVPCID",
    "EA-AliCloudRegion",
    "EA-AliCloudTenantID",
    "EA-AliCloudFirstDiscovered",
    "EA-AliCloudLastDiscovered",
]

def write_csv(csv_path: LiteralString, header: List[str], rows: List[Dict[str, str]]) -> None:
    with open(csv_path, 'w', newline='', encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

def read_csv(csv_path: LiteralString) -> List[Dict[str, str]]:
    if os.path.exists(csv_path):
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                return [row for row in reader]
        except Exception:
            return []
    else:
        return []
