from typing import Any, List, Optional, Dict

from alibabacloud_credentials.models import Config
from alibabacloud_ecs20140526.client import Client as Ecs20140526Client
from alibabacloud_vpc20160428.client import Client as Vpc20160428Client

from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526 import models as ecs_20140526_models
from alibabacloud_vpc20160428 import models as vpc_20160428_models

from alibabacloud_tea_util import models as util_models
from alibabacloud_tea_console.client import Client as ConsoleClient
from alibabacloud_tea_util.client import Client as UtilClient

ECS_MAX_RESULTS = 100 # max limit 100
VPC_PAGE_SIZE = 50 # max limit 50

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
            credential = CredentialClient(
                config=Config(
                    type='access_key',
                    access_key_id=access_key_id,
                    access_key_secret=access_key_secret),
            )
            self.EcsClient = Ecs20140526Client(config=open_api_models.Config(
                credential=credential,
                endpoint=f'ecs.{region_id}.aliyuncs.com'
            ))
            self.VpcClient = Vpc20160428Client(config=open_api_models.Config(
                credential=credential,
                endpoint=f'vpc.{region_id}.aliyuncs.com'
            ))
        else:
            self.EcsClient = None
            self.VpcClient = None

    def get_ecs_instances(self) -> List[Dict[str, Any]]:
        if self.EcsClient:
            token: Optional[str] = None
            all_instances = []
            while True:
                describe_instances_request = ecs_20140526_models.DescribeInstancesRequest(
                    region_id=self.region_id,
                    max_results=ECS_MAX_RESULTS,
                    next_token=token,
                )

                runtime = util_models.RuntimeOptions()
                try:
                    resp = self.EcsClient.describe_instances_with_options(describe_instances_request, runtime)
                    # ConsoleClient.log(UtilClient.to_jsonstring(resp))

                    body = resp.body.to_map()
                    instances = body.get('Instances', {}).get("Instance", [])
                    all_instances.extend(instances)
                    ConsoleClient.log(f"requested {len(all_instances)} ECS instances")
                    next_token = body.get('NextToken') or body.get('nextToken') or None
                    if not next_token:
                        return all_instances
                    token = next_token
                except Exception as error:
                    print(error.message)
                    print(error.data.get("Recommend"))
                    UtilClient.assert_as_string(error.message)
                    return []
            return all_instances
        else:
            return []

    def get_vpcs(self) -> List[Dict[str, Any]]:
        page_number = 1
        all_vpcs = []
        if self.VpcClient:
            while True:
                describe_vpcs_request = vpc_20160428_models.DescribeVpcsRequest(
                    region_id=self.region_id,
                    page_size=VPC_PAGE_SIZE,
                    page_number=page_number,
                )
                runtime = util_models.RuntimeOptions()
                try:
                    resp = self.VpcClient.describe_vpcs_with_options(describe_vpcs_request, runtime)
                    # ConsoleClient.log(UtilClient.to_jsonstring(resp))
                    body = resp.body.to_map()
                    vpcs = body.get('Vpcs', {}).get("Vpc", [])
                    total = body.get('TotalCount', 0)
                    all_vpcs.extend(vpcs)
                    ConsoleClient.log(f"requested {len(all_vpcs)} Vpc instances")

                    if len(all_vpcs) >= total:
                        return all_vpcs
                    else:
                        page_number = page_number + 1
                except Exception as error:
                    print(error.message)
                    print(error.data.get("Recommend"))
                    UtilClient.assert_as_string(error.message)
                    return []
        else:
            return []

    def get_VSwitches(self, vpc_ids: List[str] = []) -> List[Dict[str, Any]]:
        all_vswitches = []
        if self.VpcClient:
            if len(vpc_ids) == 0:
                return []
            for vpc_id in vpc_ids:
                page_number = 1
                vpc_vswitches = []
                while True:
                    describe_vswitches_request = vpc_20160428_models.DescribeVSwitchesRequest(
                        region_id=self.region_id,
                        vpc_id=vpc_id,
                        page_size=50, # max limit 50
                        page_number=page_number,
                    )
                    runtime = util_models.RuntimeOptions()
                    try:
                        resp = self.VpcClient.describe_vswitches_with_options(describe_vswitches_request, runtime)
                        body = resp.body.to_map()
                        # ConsoleClient.log(UtilClient.to_jsonstring(body))
                        vswitches = body.get('VSwitches', {}).get('VSwitch', [])
                        ConsoleClient.log(f"requested {len(vswitches)} VSwitchs for vpc {vpc_id}")
                        vpc_vswitches.extend(vswitches)
                        total = body.get('TotalCount', 0)
                        if len(vpc_vswitches) >= total:
                            break
                        else:
                            page_number = page_number + 1
                    except Exception as error:
                        print(error.message)
                        print(error.data.get("Recommend"))
                        UtilClient.assert_as_string(error.message)
                        return []
                all_vswitches.extend(vpc_vswitches)
            return all_vswitches
        else:
            return []
