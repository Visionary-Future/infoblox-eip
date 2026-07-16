# -*- coding: utf-8 -*-
"""Infoblox WAPI 客户端 - 将阿里云采集的 VPC / VSwitch / ECS(EIP) 数据推送到 Infoblox

支持的对象:
  - VPC       -> networkcontainer  (POST /wapi/v2.13.6/networkcontainer)
  - VSwitch   -> network            (POST /wapi/v2.13.6/network)
  - ECS/EIP   -> fixedaddress       (POST /wapi/v2.13.6/fixedaddress)

字段策略: 严格对齐 PPT《NDB IPAM import》定义的 EA 字段格式。
所有云属性通过 extattrs 写入, 和 CSV 导入方案保持一致。
去重: 通过原生字段 (network / ipv4addr + network_view) 查询。
更新: 已存在时保留 FirstDiscovered, 更新其他所有字段。
"""

import logging
import urllib3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("infoblox-wapi")


def _now_iso() -> str:
    """UTC ISO 8601 时间戳, 和 CSV 里的 FirstDiscovered/LastDiscovered 格式一致"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class InfobloxWAPIClient:
    """Infoblox WAPI REST 客户端"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        wapi_version: str = "2.13.6",
        verify_ssl: bool = True,
        timeout: int = 30,
        network_view: str = "default",
        dns_view: str = "default",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/wapi/v{wapi_version}"
        self.network_view = network_view
        self.dns_view = dns_view
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({"Content-Type": "application/json"})

        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ── Extensible Attribute 定义 (对齐 PPT) ──────

    REQUIRED_EA_DEFS = [
        # VPC
        "EA-AliCloudVPCID",
        "EA-AliCloudVPCName",
        "EA-AliCloudRegion",
        "EA-AliCloudTenantID",
        "EA-AliCloudFirstDiscovered",
        "EA-AliCloudLastDiscovered",
        # VSwitch
        "EA-AliCloudSubnetID",
        "EA-AliCloudSubnetName",
        "EA-AliCloudZone",
        # VM
        "EA-AliCloudVMID",
        "EA-AliCloudVMName",
        "EA-AliCloudVMPublicIP",
        "EA-AliCloudVMOS",
    ]

    def ensure_extattr_defs(self):
        """确保所有 EA 属性定义已存在, 不存在则创建 (type=STRING)"""
        log.info("━━━ 检查/创建 Extensible Attribute 定义 ━━━")
        for name in self.REQUIRED_EA_DEFS:
            try:
                existing = self._get("extensibleattributedef", params={"name": name})
                if existing:
                    log.info(f"  ⏭️  EA '{name}' 已存在")
                    continue
            except Exception:
                pass
            payload = {"name": name, "type": "STRING"}
            try:
                url = f"{self.api_base}/extensibleattributedef"
                resp = self.session.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 201:
                    log.info(f"  ✅ 创建 EA '{name}'")
                elif resp.status_code == 400 and "already exists" in resp.text.lower():
                    log.info(f"  ⏭️  EA '{name}' 已存在")
                else:
                    log.error(f"  ❌ 创建 EA '{name}' HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                log.error(f"  ❌ 创建 EA '{name}' 失败: {e}")

    # ── 低层 HTTP ──────────────────────────────

    def _post(self, object_type: str, payload: dict) -> Optional[str]:
        url = f"{self.api_base}/{object_type}"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code == 201:
            ref = resp.text.strip().strip('"')
            log.info(f"  ✅ Created {object_type} -> {ref}")
            return ref
        if resp.status_code == 400 and "already exists" in resp.text.lower():
            log.info(f"  ⏭️  {object_type} already exists, skipped")
            return None
        if resp.status_code == 400:
            try:
                err = resp.json()
                msg = err.get("text", "") or str(err)
            except Exception:
                msg = resp.text[:500]
            log.error(f"  ❌ POST {object_type} 400: {msg}")
            if "extensible" in msg.lower() or "attribute" in msg.lower():
                log.error(f"  💡 请先在 Infoblox 中定义对应的 Extensible Attribute")
            return None
        log.error(f"  ❌ POST {object_type} HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    def _get(self, object_type: str, params: dict = None) -> list:
        url = f"{self.api_base}/{object_type}"
        resp = self.session.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _put(self, ref: str, payload: dict) -> Optional[str]:
        """PUT 更新对象, 返回 ref"""
        url = f"{self.api_base}/{ref}"
        resp = self.session.put(url, json=payload, timeout=self.timeout)
        if resp.status_code == 200:
            updated_ref = resp.text.strip().strip('"')
            return updated_ref
        if resp.status_code == 400:
            try:
                err = resp.json()
                msg = err.get("text", "") or str(err)
            except Exception:
                msg = resp.text[:500]
            log.error(f"  ❌ PUT 400: {msg}")
        else:
            log.error(f"  ❌ PUT HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    def _delete(self, ref: str):
        url = f"{self.api_base}/{ref}"
        resp = self.session.delete(url, timeout=self.timeout)
        resp.raise_for_status()

    def _search_native(
        self, object_type: str, search_fields: dict,
        return_fields: List[str] = None,
    ) -> List[dict]:
        """通过原生字段查询去重: GET /<obj>?field1=val1&field2=val2"""
        params = dict(search_fields)
        if return_fields:
            params["_return_fields"] = ",".join(return_fields)
        try:
            return self._get(object_type, params=params)
        except Exception as e:
            log.debug(f"  native search failed: {e}")
            return []

    @staticmethod
    def _build_extattrs(fields: Dict[str, str]) -> Dict[str, Any]:
        """构建 extattrs, 跳过空值, 所有值转 str"""
        extattrs = {}
        for k, v in fields.items():
            if v is not None and v != "":
                extattrs[k] = {"value": str(v)}
        return extattrs

    def _get_existing_first_discovered(self, existing_obj: dict) -> str:
        """从已存在对象中提取 FirstDiscovered 时间戳"""
        try:
            extattrs = existing_obj.get("extattrs", {})
            return extattrs.get("EA-AliCloudFirstDiscovered", {}).get("value", "")
        except Exception:
            return ""

    def _upsert(
        self,
        object_type: str,
        search_fields: dict,
        comment: str,
        extattr_fields: Dict[str, str],
        base_payload: Dict[str, Any],
    ) -> Optional[str]:
        """通用 upsert 逻辑: 不存在则 POST, 已存在则 PUT 全量更新

        - FirstDiscovered: 新建时设为 now, 已存在时保留原值
        - LastDiscovered: 始终更新为 now
        - 其他 extattrs: 始终用最新数据覆盖
        """
        existing = self._search_native(
            object_type,
            search_fields,
            return_fields=["extattrs", "comment"],
        )

        now = _now_iso()

        if existing:
            ref = existing[0]["_ref"]
            # 保留原有 FirstDiscovered
            first_discovered = self._get_existing_first_discovered(existing[0]) or now
            log.info(f"  🔄 Updating {object_type} -> {ref}")

            extattr_fields["EA-AliCloudFirstDiscovered"] = first_discovered
            extattr_fields["EA-AliCloudLastDiscovered"] = now
            extattrs = self._build_extattrs(extattr_fields)

            put_payload: Dict[str, Any] = {
                "comment": comment,
                "extattrs": extattrs,
            }
            updated_ref = self._put(ref, put_payload)
            if updated_ref:
                log.info(f"  ✅ Updated -> {updated_ref}")
            return updated_ref or ref

        # 新建
        extattr_fields["EA-AliCloudFirstDiscovered"] = now
        extattr_fields["EA-AliCloudLastDiscovered"] = now
        extattrs = self._build_extattrs(extattr_fields)

        payload = dict(base_payload)
        payload["comment"] = comment
        payload["extattrs"] = extattrs

        return self._post(object_type, payload)

    # ── VPC -> networkcontainer ────────────────

    def push_vpc(
        self,
        vpc_id: str,
        vpc_name: str,
        cidr_block: str,
        region: str = "",
        tenant_id: str = "",
    ) -> Optional[str]:
        """推送 VPC 为 networkcontainer 对象

        对齐 PPT slide 4 字段映射
        """
        comment = f"VPC: {vpc_name} ({vpc_id})"

        extattr_fields = {
            "EA-AliCloudVPCID":    vpc_id,
            "EA-AliCloudVPCName":  vpc_name,
            "EA-AliCloudRegion":   region,
            "EA-AliCloudTenantID": tenant_id,
        }

        base_payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
        }

        return self._upsert(
            "networkcontainer",
            {"network": cidr_block, "network_view": self.network_view},
            comment, extattr_fields, base_payload,
        )

    # ── VSwitch -> network ─────────────────────

    def push_vswitch(
        self,
        vswitch_id: str,
        vswitch_name: str,
        cidr_block: str,
        vpc_id: str,
        vpc_name: str = "",
        region: str = "",
        zone: str = "",
        tenant_id: str = "",
    ) -> Optional[str]:
        """推送 VSwitch 为 network 对象

        对齐 PPT slide 7 字段映射
        """
        comment = f"VSwitch: {vswitch_name} ({vswitch_id})"

        extattr_fields = {
            "EA-AliCloudSubnetID":   vswitch_id,
            "EA-AliCloudSubnetName": vswitch_name,
            "EA-AliCloudVPCID":      vpc_id,
            "EA-AliCloudRegion":     region,
            "EA-AliCloudTenantID":   tenant_id,
            "EA-AliCloudZone":       zone,
        }

        base_payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
        }

        return self._upsert(
            "network",
            {"network": cidr_block, "network_view": self.network_view},
            comment, extattr_fields, base_payload,
        )

    # ── ECS 实例 + EIP -> fixedaddress ─────────

    def push_ecs_instance(
        self,
        vm_id: str,
        vm_name: str,
        private_ip: str,
        public_ip: str = "",
        mac_address: str = "",
        os_name: str = "",
        vpc_id: str = "",
    ) -> Optional[str]:
        """推送 ECS 实例为 fixedaddress 对象

        对齐 PPT slide 8 字段映射
        """
        if not private_ip:
            log.warning(f"  ⚠️  ECS {vm_id} has no private IP, skipped")
            return None

        # 检查父网络是否存在
        import ipaddress
        net_24 = str(ipaddress.ip_network(f"{private_ip}/24", strict=False))
        net_16 = str(ipaddress.ip_network(f"{private_ip}/16", strict=False))
        parent_exists = False
        for cidr in [net_24, net_16]:
            for obj_type in ["network", "networkcontainer"]:
                parent = self._search_native(
                    obj_type,
                    {"network": cidr, "network_view": self.network_view},
                )
                if parent:
                    parent_exists = True
                    break
            if parent_exists:
                break
        if not parent_exists:
            log.warning(f"  ⚠️  ECS {vm_id} ({private_ip}): parent network not found, skipped")
            return None

        comment = f"VMID: {vm_id}" + (f" EIP: {public_ip}" if public_ip else "")

        extattr_fields = {
            "EA-AliCloudVMID":       vm_id,
            "EA-AliCloudVMName":     vm_name,
            "EA-AliCloudVMPublicIP": public_ip,
            "EA-AliCloudVMOS":       os_name,
            "EA-AliCloudVPCID":      vpc_id,
        }

        base_payload: Dict[str, Any] = {
            "ipv4addr": private_ip,
            "network_view": self.network_view,
            "name": vm_name or f"vm-{vm_id}",
        }
        if mac_address:
            base_payload["mac"] = mac_address
        else:
            base_payload["match_client"] = "RESERVED"

        return self._upsert(
            "fixedaddress",
            {"ipv4addr": private_ip, "network_view": self.network_view},
            comment, extattr_fields, base_payload,
        )
