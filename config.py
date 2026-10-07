"""Load settings from a TOML config file.

Precedence: CLI argument > config file > environment variable > built-in default.
Missing config file is not an error; everything falls back as before.
"""

import logging
import os
import tomllib
from pathlib import Path

log = logging.getLogger("infoblox-eip")

# config key "section.key" -> argparse dest
KEY_MAP = {
    "aliyun.access_key_id": "access_key_id",
    "aliyun.access_key_secret": "access_key_secret",
    "aliyun.region": "region",
    "csv.path": "csv_path",
    "csv.vm_file_name": "VM_csv_file_name",
    "csv.vpc_file_name": "VPC_csv_file_name",
    "csv.vswitch_file_name": "vswitch_csv_file_name",
    "infoblox.url": "infoblox_url",
    "infoblox.user": "infoblox_user",
    "infoblox.password": "infoblox_password",
    "infoblox.wapi_version": "wapi_version",
    "infoblox.network_view": "network_view",
    "infoblox.network_view_prefix": "network_view_prefix",
    "infoblox.verify_ssl": "infoblox_no_verify_ssl",
    "run.no_csv": "no_csv",
    "run.no_push": "no_push",
    "run.dry_run": "dry_run",
}

# dest -> (env var name or None, builtin default)
DEFAULTS = {
    "access_key_id": ("ALIYUN_ACCESS_KEY_ID", None),
    "access_key_secret": ("ALIYUN_ACCESS_KEY_SECRET", None),
    "region": ("ALIYUN_REGION", "cn-hangzhou"),
    "csv_path": (None, "./"),
    "VM_csv_file_name": (None, "VM.csv"),
    "VPC_csv_file_name": (None, "VPC.csv"),
    "vswitch_csv_file_name": (None, "vswitch.csv"),
    "infoblox_url": ("INFOBLOX_URL", None),
    "infoblox_user": ("INFOBLOX_USER", None),
    "infoblox_password": ("INFOBLOX_PASSWORD", None),
    "wapi_version": (None, "2.13.6"),
    "network_view": (None, "default"),
    "network_view_prefix": (None, "Ali"),
    "infoblox_no_verify_ssl": (None, False),
    "no_csv": (None, False),
    "no_push": (None, False),
    "dry_run": (None, False),
}


def load_config(path: str | None) -> dict:
    """Return flat {arg dest: value} from TOML file, {} if missing or empty."""
    if not path or not Path(path).exists():
        return {}
    # config.toml holds credentials — warn if it leaks to group/other
    st = os.stat(path)
    if st.st_mode & 0o077:
        log.warning(
            f"Config file {path} is readable by group/others "
            f"(mode {oct(st.st_mode & 0o777)}). Recommend: chmod 600 {path}"
        )
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    config = {}
    for section, values in raw.items():
        for key, value in values.items():
            dest = KEY_MAP.get(f"{section}.{key}")
            if dest is None:
                continue
            if dest == "infoblox_no_verify_ssl":
                # config file uses positive form "verify_ssl"
                config[dest] = not value
            else:
                config[dest] = value
    return config


def merge_cli_config(cli_args, config: dict) -> dict:
    """Resolve CLI vs config vs env vs default for every dest; return new dict."""
    merged = {}
    for dest, (env_name, default) in DEFAULTS.items():
        env_value = os.environ.get(env_name) if env_name else None
        cli_value = getattr(cli_args, dest)
        merged[dest] = _resolve(cli_value, config.get(dest), env_value, default)
    return merged


def _resolve(cli_value, config_value, env_value, default):
    """First non-None wins."""
    for value in (cli_value, config_value, env_value):
        if value is not None:
            return value
    return default