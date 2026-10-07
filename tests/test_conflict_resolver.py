"""Unit tests for conflict_resolver module."""

import pytest
from conflict_resolver import (
    ResourceGroup,
    _parse_cidr,
    _cidr_overlaps_any,
    detect_cidr_conflicts,
    assign_network_view_names,
    group_resources_by_view,
    resolve_network_views,
)
import ipaddress


class TestParseCidr:
    def test_valid_cidr(self):
        net = _parse_cidr("10.0.0.0/16")
        assert net is not None
        assert str(net) == "10.0.0.0/16"

    def test_valid_cidr_no_strict(self):
        net = _parse_cidr("10.0.1.5/24")
        assert net is not None
        assert str(net) == "10.0.1.0/24"

    def test_invalid_cidr(self):
        assert _parse_cidr("not-a-cidr") is None

    def test_empty_cidr(self):
        assert _parse_cidr("") is None


class TestCidrOverlapsAny:
    def test_overlap_same_network(self):
        net = ipaddress.ip_network("10.0.0.0/16")
        existing = [ipaddress.ip_network("10.0.0.0/16")]
        assert _cidr_overlaps_any(net, existing) is True

    def test_overlap_subset(self):
        net = ipaddress.ip_network("10.0.1.0/24")
        existing = [ipaddress.ip_network("10.0.0.0/16")]
        assert _cidr_overlaps_any(net, existing) is True

    def test_no_overlap(self):
        net = ipaddress.ip_network("172.16.0.0/16")
        existing = [ipaddress.ip_network("10.0.0.0/16")]
        assert _cidr_overlaps_any(net, existing) is False

    def test_empty_existing(self):
        net = ipaddress.ip_network("10.0.0.0/16")
        assert _cidr_overlaps_any(net, []) is False

    def test_multiple_existing_one_overlaps(self):
        net = ipaddress.ip_network("10.0.0.0/16")
        existing = [
            ipaddress.ip_network("172.16.0.0/16"),
            ipaddress.ip_network("10.0.0.0/16"),
            ipaddress.ip_network("192.168.0.0/16"),
        ]
        assert _cidr_overlaps_any(net, existing) is True


class TestDetectCidrConflicts:
    def test_single_group_no_conflicts(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16", "_account": "default"},
            {"VpcId": "vpc-2", "CidrBlock": "172.16.0.0/16", "_account": "default"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups == {"default": 0}

    def test_two_groups_same_cidr_conflict(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16", "_account": "default"},
            {"VpcId": "vpc-2", "CidrBlock": "10.0.0.0/16", "_account": "user-a"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups["default"] == 0
        assert groups["user-a"] == 1

    def test_two_groups_overlapping_cidr_conflict(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/8", "_account": "default"},
            {"VpcId": "vpc-2", "CidrBlock": "10.1.0.0/16", "_account": "user-a"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups["default"] == 0
        assert groups["user-a"] == 1

    def test_two_groups_non_overlapping_no_conflict(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16", "_account": "default"},
            {"VpcId": "vpc-2", "CidrBlock": "172.16.0.0/16", "_account": "user-a"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups["default"] == 0
        assert groups["user-a"] == 0

    def test_three_groups_two_conflict(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16", "_account": "default"},
            {"VpcId": "vpc-2", "CidrBlock": "10.0.0.0/16", "_account": "user-a"},
            {"VpcId": "vpc-3", "CidrBlock": "192.168.0.0/16", "_account": "user-b"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups["default"] == 0
        assert groups["user-a"] == 1
        assert groups["user-b"] == 0  # no conflict with base

    def test_default_group_implicit(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"},
            {"VpcId": "vpc-2", "CidrBlock": "10.0.0.0/16", "_account": "user-a"},
        ]
        groups, vpc_map = detect_cidr_conflicts(vpcs)
        assert groups["default"] == 0
        assert groups["user-a"] == 1

    def test_empty_vpcs(self):
        groups, vpc_map = detect_cidr_conflicts([])
        assert groups == {}


class TestAssignNetworkViewNames:
    def test_base_view(self):
        groups = {"default": 0}
        names = assign_network_view_names(groups, "cn-hangzhou")
        assert names == {"default": "Ali-cn-hangzhou"}

    def test_conflict_views(self):
        groups = {"default": 0, "user-a": 1, "user-b": 2}
        names = assign_network_view_names(groups, "cn-hangzhou")
        assert names["default"] == "Ali-cn-hangzhou"
        assert names["user-a"] == "Ali-cn-hangzhou002"
        assert names["user-b"] == "Ali-cn-hangzhou003"

    def test_custom_prefix(self):
        groups = {"default": 0, "user-a": 1}
        names = assign_network_view_names(groups, "cn-beijing", prefix="MyPrefix")
        assert names["default"] == "MyPrefix-cn-beijing"
        assert names["user-a"] == "MyPrefix-cn-beijing002"


class TestGroupResourcesByView:
    def _make_vpcs(self):
        return [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"},
            {"VpcId": "vpc-2", "CidrBlock": "172.16.0.0/16"},
        ]

    def _make_vswitches(self):
        return [
            {"VSwitchId": "vsw-1", "VpcId": "vpc-1", "CidrBlock": "10.0.1.0/24"},
            {"VSwitchId": "vsw-2", "VpcId": "vpc-2", "CidrBlock": "172.16.1.0/24"},
        ]

    def _make_ecs(self):
        return [
            {"InstanceId": "i-1", "VpcAttributes": {"VpcId": "vpc-1"}},
            {"InstanceId": "i-2", "VpcAttributes": {"VpcId": "vpc-2"}},
        ]

    def test_all_to_base(self):
        group_view_map = {"default": "Ali-cn-hangzhou"}
        vpc_id_to_group = {"vpc-1": "default", "vpc-2": "default"}
        views = group_resources_by_view(
            self._make_vpcs(), self._make_vswitches(), self._make_ecs(),
            group_view_map, vpc_id_to_group,
        )
        assert len(views) == 1
        group = views["Ali-cn-hangzhou"]
        assert len(group.vpcs) == 2
        assert len(group.vswitches) == 2
        assert len(group.ecs) == 2

    def test_sharded(self):
        group_view_map = {"default": "Ali-cn-hangzhou", "user-a": "Ali-cn-hangzhou002"}
        vpc_id_to_group = {"vpc-1": "default", "vpc-2": "user-a"}
        views = group_resources_by_view(
            self._make_vpcs(), self._make_vswitches(), self._make_ecs(),
            group_view_map, vpc_id_to_group,
        )
        assert len(views) == 2
        base = views["Ali-cn-hangzhou"]
        assert len(base.vpcs) == 1
        assert base.vpcs[0]["VpcId"] == "vpc-1"
        assert len(base.vswitches) == 1
        assert len(base.ecs) == 1

        conflict = views["Ali-cn-hangzhou002"]
        assert len(conflict.vpcs) == 1
        assert conflict.vpcs[0]["VpcId"] == "vpc-2"
        assert len(conflict.vswitches) == 1
        assert len(conflict.ecs) == 1

    def test_ecs_with_vpc_id_fallback(self):
        ecs = [{"InstanceId": "i-1", "VpcId": "vpc-1"}]
        group_view_map = {"default": "Ali-cn-hangzhou"}
        vpc_id_to_group = {"vpc-1": "default"}
        views = group_resources_by_view(
            [], [], ecs, group_view_map, vpc_id_to_group,
        )
        assert views["Ali-cn-hangzhou"].ecs[0]["InstanceId"] == "i-1"

    def test_unmatched_resource_uses_default(self):
        ecs = [{"InstanceId": "i-orphan"}]  # no VPC info
        group_view_map = {"default": "Ali-cn-hangzhou"}
        vpc_id_to_group = {}
        views = group_resources_by_view(
            [], [], ecs, group_view_map, vpc_id_to_group,
        )
        assert views["Ali-cn-hangzhou"].ecs[0]["InstanceId"] == "i-orphan"


class TestResolveNetworkViews:
    def test_single_account_all_to_base(self):
        vpcs = [
            {"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"},
            {"VpcId": "vpc-2", "CidrBlock": "172.16.0.0/16"},
        ]
        vswitches = [
            {"VSwitchId": "vsw-1", "VpcId": "vpc-1", "CidrBlock": "10.0.1.0/24"},
        ]
        ecs = [
            {"InstanceId": "i-1", "VpcAttributes": {"VpcId": "vpc-1"}},
        ]
        views = resolve_network_views(vpcs, vswitches, ecs, "cn-hangzhou")
        assert len(views) == 1
        assert "Ali-cn-hangzhou" in views
        group = views["Ali-cn-hangzhou"]
        assert len(group.vpcs) == 2
        assert len(group.vswitches) == 1
        assert len(group.ecs) == 1

    def test_multi_account_conflict(self):
        vpcs = [
            {"VpcId": "vpc-main", "CidrBlock": "10.0.0.0/16", "_account": "default"},
            {"VpcId": "vpc-ram-a", "CidrBlock": "10.0.0.0/16", "_account": "user-a"},
        ]
        vswitches = [
            {"VSwitchId": "vsw-main", "VpcId": "vpc-main", "CidrBlock": "10.0.1.0/24"},
            {"VSwitchId": "vsw-ram-a", "VpcId": "vpc-ram-a", "CidrBlock": "10.0.1.0/24"},
        ]
        ecs = [
            {"InstanceId": "i-main", "VpcAttributes": {"VpcId": "vpc-main"}},
            {"InstanceId": "i-ram-a", "VpcAttributes": {"VpcId": "vpc-ram-a"}},
        ]
        views = resolve_network_views(vpcs, vswitches, ecs, "cn-hangzhou")
        assert len(views) == 2
        base = views["Ali-cn-hangzhou"]
        assert len(base.vpcs) == 1
        assert base.vpcs[0]["VpcId"] == "vpc-main"

        conflict = views["Ali-cn-hangzhou002"]
        assert len(conflict.vpcs) == 1
        assert conflict.vpcs[0]["VpcId"] == "vpc-ram-a"

    def test_empty_input(self):
        views = resolve_network_views([], [], [], "cn-hangzhou")
        assert views == {}