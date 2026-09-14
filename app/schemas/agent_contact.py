from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


AgentRemarkText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=1000),
]


class AgentContactItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str | None = Field(alias="displayName")
    phone_number: str = Field(alias="phoneNumber")
    normalized_phone_number: str = Field(alias="normalizedPhoneNumber")
    remark: str | None
    status: str
    source: str
    last_contact_at: int | None = Field(alias="lastContactAt")
    areas: str
    updated_at: int = Field(alias="updatedAt")


class AgentContactRemarkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    remark: AgentRemarkText


class AgentMenuItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    menu: str
    update_time: int = Field(alias="updateTime")
    update_by: str | None = Field(alias="updateBy")
    areas: str


class AgentSimCardItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    phone_number: str | None = Field(alias="phoneNumber")
    carrier_name: str | None = Field(alias="carrierName")
    areas: str | None
    customer_remark: str | None = Field(alias="customerRemark")
