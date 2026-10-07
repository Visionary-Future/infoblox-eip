"""CIDR conflict detection and Network View assignment for Infoblox IPAM.

Resolves VPC CIDR conflicts by assigning resource groups to different Network Views.
Follows "阿里云IPAM冲突对应存写方案":

- Base view: Ali-{region} — non-conflicting resources
- Conflict views: Ali-{region}002+ — each conflicting account group gets its own view
"""

import ipaddress
import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

log = logging.getLogger("infoblox-eip")

VIEW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResourceGroup(NamedTuple):
    """Resources assigned to a single Network View."""
    vpcs: Tuple[Dict[str, Any], ...]
    vswitches: Tuple[Dict[str, Any], ...]
    ecs: Tuple[Dict[str, Any], ...]


def _parse_cidr(cidr_str: str) -> Optional[ipaddress.IPv4Network]:
    """Parse an IPv4 CIDR string, returning None on failure or IPv6 input."""
    if not cidr_str:
        return None
    try:
        net = ipaddress.ip_network(cidr_str, strict=False)
    except ValueError:
        log.warning(f"Cannot parse CIDR: {cidr_str}")
        return None
    if not isinstance(net, ipaddress.IPv4Network):
        log.warning(f"Cannot use non-IPv4 CIDR: {cidr_str}")
        return None
    return net


def _cidr_overlaps_any(
    network: ipaddress.IPv4Network,
    existing: List[ipaddress.IPv4Network],
) -> bool:
    """Check if `network` overlaps with any network in `existing`."""
    for existing_net in existing:
        if network.overlaps(existing_net):
            return True
    return False


def detect_cidr_conflicts(
    vpcs: List[Dict[str, Any]],
    group_key: str = "_account",
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Detect VPC CIDR overlaps and assign conflict group numbers.

    Groups VPCs by `group_key` field value (defaults to "_account").
    The first group is treated as the base (group 0). Each subsequent group
    is checked against all VPCs already in the base group.

    If ANY VPC in a group has a CIDR that overlaps with a base-group VPC,
    the ENTIRE group is assigned to a new conflict group (1, 2, ...).

    Returns:
        (group_value → conflict_group_number, vpc_id → group_value)
        conflict_group_number: 0 = base, 1+ = conflict group
    """
    # Group VPCs by group_key
    groups: Dict[str, List[Dict[str, Any]]] = {}
    vpc_id_to_group: Dict[str, str] = {}
    for vpc in vpcs:
        vpc_id = vpc.get("VpcId", "")
        group_value = vpc.get(group_key, "default") or "default"
        groups.setdefault(group_value, []).append(vpc)
        if vpc_id:
            vpc_id_to_group[vpc_id] = group_value

    if not groups:
        return {}, vpc_id_to_group

    # Deterministic ordering: "default" group first, then alphabetical. Never
    # depend on API return order or conflict group numbers would change
    # between runs, flipping resources between views.
    group_names = sorted(groups.keys(), key=lambda g: (g != "default", g))

    base_networks: List[ipaddress.IPv4Network] = []
    conflict_groups: Dict[str, int] = {}
    next_conflict = 1

    for group_name in group_names:
        group_vpcs = groups[group_name]

        # Parse all CIDRs in this group
        group_networks: List[ipaddress.IPv4Network] = []
        for vpc in group_vpcs:
            net = _parse_cidr(vpc.get("CidrBlock", ""))
            if net:
                group_networks.append(net)

        if not group_networks:
            conflict_groups[group_name] = 0
            continue

        # Check if any VPC in this group overlaps with base
        has_conflict = False
        for net in group_networks:
            if _cidr_overlaps_any(net, base_networks):
                has_conflict = True
                break

        if has_conflict:
            conflict_groups[group_name] = next_conflict
            next_conflict += 1
            log.info(
                f"CIDR conflict detected: group '{group_name}' → "
                f"conflict group {conflict_groups[group_name]}"
            )
        else:
            conflict_groups[group_name] = 0
            base_networks.extend(group_networks)
            log.debug(f"Group '{group_name}' → base view (no conflicts)")

    return conflict_groups, vpc_id_to_group


def assign_network_view_names(
    conflict_groups: Dict[str, int],
    region: str,
    prefix: str = "Ali",
    base_view: str = "",
) -> Dict[str, str]:
    """Map each group value to a Network View name.

    Group 0 → base_view, or {prefix}-{region} if base_view is empty
    Group N → {base}{str(N+1).zfill(3)}  (002, 003, ...)

    Raises ValueError on names that are not valid WAPI view names.
    """
    base = base_view or f"{prefix}-{region}"
    if not VIEW_NAME_RE.fullmatch(base):
        raise ValueError(
            f"Invalid Network View base name: {base!r} "
            f"(allowed: [A-Za-z0-9_-], max 64 chars)"
        )

    view_names: Dict[str, str] = {}
    for group_value, group_num in conflict_groups.items():
        if group_num == 0:
            view_names[group_value] = base
        else:
            view_names[group_value] = f"{base}{str(group_num + 1).zfill(3)}"
    return view_names


def group_resources_by_view(
    vpcs: List[Dict[str, Any]],
    vswitches: List[Dict[str, Any]],
    ecs: List[Dict[str, Any]],
    group_view_map: Dict[str, str],
    vpc_id_to_group: Dict[str, str],
) -> Dict[str, ResourceGroup]:
    """Group all resources by their assigned Network View.

    Uses group_view_map (group_value → view_name) and vpc_id_to_group
    (vpc_id → group_value) to route each resource to the correct view.

    vSwitch and ECS follow their parent VPC's view assignment.
    Resources whose parent VPC cannot be determined are logged and skipped.
    """
    # Build vpc_id → view_name map
    vpc_view_map: Dict[str, str] = {}
    for vpc_id, group_value in vpc_id_to_group.items():
        view_name = group_view_map.get(group_value)
        if view_name:
            vpc_view_map[vpc_id] = view_name

    # Accumulate into plain buckets, then freeze into ResourceGroups once.
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def _bucket(name: str) -> Dict[str, List[Dict[str, Any]]]:
        return buckets.setdefault(name, {"vpcs": [], "vswitches": [], "ecs": []})

    # VPCs
    for vpc in vpcs:
        vpc_id = vpc.get("VpcId", "")
        view_name = vpc_view_map.get(vpc_id)
        if view_name is None:
            log.warning(f"VPC {vpc_id or '?'} has no Network View assigned, skipped")
            continue
        _bucket(view_name)["vpcs"].append(vpc)

    # VSwitches
    for vsw in vswitches:
        vpc_id = vsw.get("VpcId", "")
        view_name = vpc_view_map.get(vpc_id)
        if view_name is None:
            log.warning(f"VSwitch {vsw.get('VSwitchId', '?')} has no Network View assigned, skipped")
            continue
        _bucket(view_name)["vswitches"].append(vsw)

    # ECS
    for ecs in ecs:
        vpc_attrs = ecs.get("VpcAttributes") or {}
        vpc_id = vpc_attrs.get("VpcId") or ecs.get("VpcId") or ""
        view_name = vpc_view_map.get(vpc_id)
        if view_name is None:
            log.warning(f"ECS {ecs.get('InstanceId', '?')} has no Network View assigned, skipped")
            continue
        _bucket(view_name)["ecs"].append(ecs)

    return {
        name: ResourceGroup(
            vpcs=tuple(b["vpcs"]),
            vswitches=tuple(b["vswitches"]),
            ecs=tuple(b["ecs"]),
        )
        for name, b in buckets.items()
    }


def resolve_network_views(
    vpcs: List[Dict[str, Any]],
    vswitches: List[Dict[str, Any]],
    ecs: List[Dict[str, Any]],
    region: str,
    prefix: str = "Ali",
    group_key: str = "_account",
    base_view: str = "",
) -> Dict[str, ResourceGroup]:
    """Detect CIDR conflicts, assign Network Views, group resources.

    Top-level orchestrator for the conflict resolution pipeline.

    Args:
        vpcs: Raw VPC dicts from Aliyun API.
        vswitches: Raw vSwitch dicts from Aliyun API.
        ecs: Raw ECS instance dicts from Aliyun API.
        region: Aliyun region identifier (e.g. "cn-hangzhou").
        prefix: Network View name prefix (default "Ali").
        group_key: Dict key on VPC dict for grouping (default "_account").
                   Set by caller before invoking (e.g. based on OwnerId).
        base_view: Explicit base Network View name. If empty, uses
                   {prefix}-{region}. When set (e.g. from config
                   infoblox.network_view), it wins over prefix/region.

    Returns:
        {view_name: ResourceGroup} mapping, ready for per-view push.
    """
    conflict_groups, vpc_id_to_group = detect_cidr_conflicts(vpcs, group_key=group_key)
    group_view_map = assign_network_view_names(
        conflict_groups, region, prefix=prefix, base_view=base_view,
    )
    views = group_resources_by_view(vpcs, vswitches, ecs, group_view_map, vpc_id_to_group)

    log.info(f"Resolved {len(vpcs)} VPCs → {len(views)} Network Views:")
    for view_name, group in views.items():
        log.info(
            f"  {view_name}: {len(group.vpcs)} VPCs, "
            f"{len(group.vswitches)} vSwitches, {len(group.ecs)} ECS"
        )

    return views