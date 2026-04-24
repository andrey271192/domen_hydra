from pydantic import BaseModel
from typing import Literal

class DomainGroup(BaseModel):
    name: str; entries: list[str]; policy: str
    entry_type: Literal["domain","geosite"] = "domain"; enabled: bool = True

class IpGroup(BaseModel):
    name: str; entries: list[str]; policy: str
    entry_type: Literal["ip","geoip"] = "ip"; enabled: bool = True

class HydraConfig(BaseModel):
    version: str = "1.0"
    domain_groups: list[DomainGroup] = []
    ip_groups: list[IpGroup] = []
