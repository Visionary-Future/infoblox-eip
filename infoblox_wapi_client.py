# -*- coding: utf-8 -*-
"""Infoblox WAPI 客户端 — 将阿里云采集的 VPC / VSwitch / ECS(EIP) 数据推送到 Infoblox

支持的对象:
  - VPC       -> networkcontainer  (POST /wapi/v2.13.6/networkcontainer)
  - VSwitch   -> network            (POST /wapi/v2.13.6/network)
  - ECS/EIP   -> fixedaddress       (POST /wapi/v2.13.6/fixedaddress)

所有云属性通过 extattrs (Extensible Attributes) 写入。
推送前自动通过 extattrs 去重，已存在则跳过。
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

    # ── 低层 HTTP ──────────────────────────────

    def _post(self, object_type: str, payload: dict) -> Optional[str]:
        url = f"{self.api_base}/{object_type}"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code == 201:
            ref = resp.text.strip().strip('"')
            log.info(f"  ✅ Created {object_type} -> {ref}")
            return ref
        # 400 + already exists
        if resp.status_code == 400 and "already exists" in resp.text.lower():
            log.info(f"  ⏭️  {object_type} already exists, skipped")
            return None
        # 400 其他错误: 打印详细错误信息, 不抛异常
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

    def _search_by_extattr(
        self, object_type: str, attr_name: str, attr_value: str,
        return_fields: List[str] = None,
    ) -> List[dict]:
        """通过扩展属性查询: GET /<obj>?*<attr>=<value>

        注意: requests 会把 * 编码成 %2A, Infoblox 需要 * 原样出现在 URL 中,
        所以这里手动拼接 query string。
        如果 extattr 未定义或查询出错, 返回空列表 (视为不存在)。
        """
        import urllib.parse
        qs_parts = [f"*{urllib.parse.quote(attr_name, safe='')}={urllib.parse.quote(attr_value, safe='')}"]
        if return_fields:
            qs_parts.append(f"_return_fields={','.join(return_fields)}")
        url = f"{self.api_base}/{object_type}?{'&'.join(qs_parts)}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 400:
                # extattr 未定义或不可搜索, 视为不存在
                log.debug(f"  extattr search 400 for {attr_name}={attr_value}: {resp.text[:200]}")
                return []
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"  extattr search failed for {attr_name}={attr_value}: {e}")
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

        WAPI 端点: POST /wapi/v2.13.6/networkcontainer
        必填: network (CIDR)
        """
        # 去重
        existing = self._search_by_extattr(
            "networkcontainer", "EA-AliCloudVPCID", vpc_id,
            return_fields=["extattrs"],
        )
        if existing:
            log.info(f"  ⏭️  VPC {vpc_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
            "comment": f"VPC: {vpc_name} ({vpc_id})",
            "extattrs": {
                "EA-AliCloudVPCID":   {"value": vpc_id},
                "EA-AliCloudVPCName": {"value": vpc_name},
            },
        }
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

        WAPI 端点: POST /wapi/v2.13.6/network
        必填: network (CIDR)
        """
        existing = self._search_by_extattr(
            "network", "EA-AliCloudSubnetID", vswitch_id,
            return_fields=["extattrs"],
        )
        if existing:
            log.info(f"  ⏭️  VSwitch {vswitch_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        payload: Dict[str, Any] = {
            "network": cidr_block,
            "network_view": self.network_view,
            "comment": f"VSwitch: {vswitch_name} ({vswitch_id})",
            "extattrs": {
                "EA-AliCloudSubnetID":   {"value": vswitch_id},
                "EA-AliCloudSubnetName": {"value": vswitch_name},
                "EA-AliCloudVPCID":      {"value": vpc_id},
            },
        }
        if region:
            payload["extattrs"]["EA-AliCloudRegion"] = {"value": region}
        if zone:
            payload["extattrs"]["EA-AliCloudZone"] = {"value": zone}
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

        WAPI 端点: POST /wapi/v2.13.6/fixedaddress
        必填: ipv4addr, mac (或 match_client=MATCH_CLIENT)
        """
        if not private_ip:
            log.warning(f"  ⚠️  ECS {vm_id} has no private IP, skipped")
            return None

        existing = self._search_by_extattr(
            "fixedaddress", "EA-AliCloudVMID", vm_id,
            return_fields=["extattrs"],
        )
        if existing:
            log.info(f"  ⏭️  ECS {vm_id} already exists -> {existing[0]['_ref']}")
            return existing[0]["_ref"]

        payload: Dict[str, Any] = {
            "ipv4addr": private_ip,
            "network_view": self.network_view,
            "name": vm_name or f"vm-{vm_id}",
            "comment": f"ECS: {vm_name} ({vm_id})" + (f" EIP: {public_ip}" if public_ip else ""),
            "extattrs": {
                "EA-AliCloudVMID":   {"value": vm_id},
                "EA-AliCloudVMName": {"value": vm_name},
            },
        }

        # mac 处理: fixedaddress 要求 mac 或 match_client
        if mac_address:
            payload["mac"] = mac_address
        else:
            payload["match_client"] = "RESERVED"

        if public_ip:
            payload["extattrs"]["EA-AliCloudVMPublicIP"] = {"value": public_ip}
        if os_name:
            payload["extattrs"]["EA-AliCloudVMOS"] = {"value": os_name}
        if vpc_id:
            payload["extattrs"]["EA-AliCloudVPCID"] = {"value": vpc_id}
        if region:
            payload["extattrs"]["EA-AliCloudRegion"] = {"value": region}

        return self._post("fixedaddress", payload)
