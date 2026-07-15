# -*- coding: utf-8 -*-
"""Infoblox WAPI 客户端 - 将阿里云采集的 VPC / VSwitch / ECS(EIP) 数据推送到 Infoblox

支持的对象:
  - VPC       -> networkcontainer  (POST /wapi/v2.13.6/networkcontainer)
  - VSwitch   -> network            (POST /wapi/v2.13.6/network)
  - ECS/EIP   -> fixedaddress       (POST /wapi/v2.13.6/fixedaddress)

字段策略: 能用原生字段的尽量用原生, 用不了的放 extattrs。
去重: 通过原生字段 (network / ipv4addr + network_view) 查询。
"""

import logging
import urllib3
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("infoblox-wapi")


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

    # ── Extensible Attribute 定义 ────────────────

    # 仅保留无法映射到原生字段的 EA
    REQUIRED_EA_DEFS = [
        "EA-AliCloudVPCID",       # VPC ID - 无原生字段, 需结构化搜索
        "EA-AliCloudSubnetID",    # VSwitch ID - 无原生字段, 需结构化搜索
        "EA-AliCloudVMID",        # VM ID - 无原生字段, 需结构化搜索
        "EA-AliCloudVMPublicIP",  # EIP - 无原生字段
        "EA-AliCloudRegion",      # Region - network/networkcontainer 无原生字段
        "EA-AliCloudTenantID",    # Tenant ID - 无原生字段
        "EA-AliCloudZone",        # Zone - 无原生字段
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

    def _put(self, ref: str, payload: dict) -> dict:
        url = f"{self.api_base}/{ref}"
        resp = self.session.put(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

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

        原生字段: network, comment
        extattrs: EA-AliCloudVPCID, EA-AliCloudRegion, EA-AliCloudTenantID
        去重: network + network_view
        """
        # 原生字段去重
        existing = self._search_native(
            "networkcontainer",
            {"network": cidr_block, "network_view": self.network_view},
        )
        if existing:
            log.info(f"  ⏭️  VPC {vpc_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        # comment 拼接人类可读信息
        comment_parts = [f"VPC: {vpc_name} ({vpc_id})"]
        if region:
            comment_parts.append(f"Region: {region}")
        if tenant_id:
            comment_parts.append(f"Tenant: {tenant_id}")

        payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
            "comment": " | ".join(comment_parts),
            "extattrs": {
                "EA-AliCloudVPCID": {"value": vpc_id},
            },
        }
        # 无原生字段可用的放 extattrs
        if region:
            payload["extattrs"]["EA-AliCloudRegion"] = {"value": region}
        if tenant_id:
            payload["extattrs"]["EA-AliCloudTenantID"] = {"value": tenant_id}

        return self._post("networkcontainer", payload)

    # ── VSwitch -> network ─────────────────────

    def push_vswitch(
        self,
        vswitch_id: str,
        vswitch_name: str,
        cidr_block: str,
        vpc_id: str,
        region: str = "",
        zone: str = "",
        tenant_id: str = "",
    ) -> Optional[str]:
        """推送 VSwitch 为 network 对象

        原生字段: network, comment
        extattrs: EA-AliCloudSubnetID, EA-AliCloudVPCID, EA-AliCloudZone, EA-AliCloudRegion, EA-AliCloudTenantID
        去重: network + network_view
        """
        existing = self._search_native(
            "network",
            {"network": cidr_block, "network_view": self.network_view},
        )
        if existing:
            log.info(f"  ⏭️  VSwitch {vswitch_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        comment_parts = [f"VSwitch: {vswitch_name} ({vswitch_id})", f"VPC: {vpc_id}"]
        if zone:
            comment_parts.append(f"Zone: {zone}")
        if region:
            comment_parts.append(f"Region: {region}")
        if tenant_id:
            comment_parts.append(f"Tenant: {tenant_id}")

        payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
            "comment": " | ".join(comment_parts),
            "extattrs": {
                "EA-AliCloudSubnetID": {"value": vswitch_id},
                "EA-AliCloudVPCID":     {"value": vpc_id},
            },
        }
        if zone:
            payload["extattrs"]["EA-AliCloudZone"] = {"value": zone}
        if region:
            payload["extattrs"]["EA-AliCloudRegion"] = {"value": region}
        if tenant_id:
            payload["extattrs"]["EA-AliCloudTenantID"] = {"value": tenant_id}

        return self._post("network", payload)

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
        region: str = "",
    ) -> Optional[str]:
        """推送 ECS 实例为 fixedaddress 对象

        原生字段: ipv4addr, name, mac, device_type, device_vendor,
                  device_location, device_description, comment
        extattrs: EA-AliCloudVMID, EA-AliCloudVMPublicIP, EA-AliCloudVPCID
        去重: ipv4addr + network_view
        """
        if not private_ip:
            log.warning(f"  ⚠️  ECS {vm_id} has no private IP, skipped")
            return None

        # 原生字段去重
        existing = self._search_native(
            "fixedaddress",
            {"ipv4addr": private_ip, "network_view": self.network_view},
        )
        if existing:
            log.info(f"  ⏭️  ECS {vm_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        # comment 拼接
        comment_parts = [f"VMID: {vm_id}"]
        if public_ip:
            comment_parts.append(f"EIP: {public_ip}")
        if vpc_id:
            comment_parts.append(f"VPC: {vpc_id}")

        payload: Dict[str, Any] = {
            "ipv4addr": private_ip,
            "network_view": self.network_view,
            "name": vm_name or f"vm-{vm_id}",
            "comment": " | ".join(comment_parts),
            # ── 原生字段优先 ──
            "device_type": "ECS",
            "device_vendor": "Alibaba Cloud",
        }

        # mac 处理
        if mac_address:
            payload["mac"] = mac_address
        else:
            payload["match_client"] = "RESERVED"

        # 原生字段: 有对应关系的直接用
        if os_name:
            payload["device_description"] = os_name
        if region:
            payload["device_location"] = region

        # extattrs: 仅放无原生字段可用的数据
        payload["extattrs"] = {
            "EA-AliCloudVMID": {"value": vm_id},
        }
        if public_ip:
            payload["extattrs"]["EA-AliCloudVMPublicIP"] = {"value": public_ip}
        if vpc_id:
            payload["extattrs"]["EA-AliCloudVPCID"] = {"value": vpc_id}

        return self._post("fixedaddress", payload)
