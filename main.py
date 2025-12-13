import argparse
import os

from alicloud_utils import AliyunClient
from csv_utils import read_csv, write_csv, ECS_HEADER, VPC_HEADER, VSWITCHE_HEADER
from transform_fields_utils import transform_ecs_instances, transform_vpcs, transform_VSwitches

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Collect Aliyun VPCs and write to CSV")
    # parser.add_argument("--dry-run", type=bool, action="store_true", help="Run without calling Aliyun API")
    parser.add_argument("--csv-path", type=str, default="./", help="Output CSV path")
    # parser.add_argument("--export-instances", type=bool, default=True, action="store_true", help="Also export instances to CSV")
    parser.add_argument("--VM-csv-file-name", type=str, default="VM.csv", help="VM CSV output file name")
    parser.add_argument("--VPC-csv-file-name", type=str, default="VPC.csv", help="VPC CSV output file name")
    parser.add_argument("--vswitch-csv-file-name", type=str, default="vswitch.csv", help="vswitch CSV output file name")

    parser.add_argument("--access-key-id", type=str, default=os.environ.get("ALIYUN_ACCESS_KEY_ID"))
    parser.add_argument("--access-key-secret", type=str, default=os.environ.get("ALIYUN_ACCESS_KEY_SECRET"))
    parser.add_argument("--region", type=str, default=os.environ.get("ALIYUN_REGION", "cn-hangzhou"))

    args = parser.parse_args()
    region_id = args.region

    if not args.access_key_id or not args.access_key_secret:
        print("Please provide access_key_id, region and access_key_secret.")
        exit(1)
    # read exist file
    VM_csv_file_path = os.path.join(args.csv_path, args.VM_csv_file_name)
    ecs_exists_data = read_csv(VM_csv_file_path)

    VPC_csv_file_path = os.path.join(args.csv_path, args.VPC_csv_file_name)
    VPC_csv_file_name = os.path.join(args.csv_path, args.VPC_csv_file_name)
    VPC_exists_data = read_csv(VPC_csv_file_path)

    vswitch_csv_file_path = os.path.join(args.csv_path, args.vswitch_csv_file_name)
    vswitch_csv_file_name = os.path.join(args.csv_path, args.vswitch_csv_file_name)
    vswitch_exists_data = read_csv(vswitch_csv_file_path)

    # request and save aliyun data
    aliyun_client = AliyunClient(access_key_id=args.access_key_id, access_key_secret=args.access_key_secret, region_id=region_id)
    ecs_instances = aliyun_client.get_ecs_instances()

    transformed_ecs_instances = transform_ecs_instances(ecs_instances, exists_data=ecs_exists_data)
    write_csv(VM_csv_file_path, header=ECS_HEADER, rows=transformed_ecs_instances)
    print("Successfully collected Aliyun VM and write to CSV")

    vpcs = aliyun_client.get_vpcs()
    transformed_vpcs = transform_vpcs(vpcs, exists_data=VPC_exists_data)
    write_csv(VPC_csv_file_name, header=VPC_HEADER, rows=transformed_vpcs)
    print("Successfully collected Aliyun VPC and write to CSV")

    vpc_ids = [vpc.get("VpcId", '') for vpc in vpcs]
    vswitchs = aliyun_client.get_VSwitches(vpc_ids)
    transformed_vswitchs = transform_VSwitches(vswitchs, vswitch_exists_data, region_id)
    write_csv(vswitch_csv_file_name, header=VSWITCHE_HEADER, rows=transformed_vswitchs)
    print("Successfully collected Aliyun VSwitches and write to CSV")

