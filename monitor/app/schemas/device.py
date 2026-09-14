from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]

DeviceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


class SimCardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    slot_index: int = Field(alias="slotIndex", ge=0, strict=True)
    sim_number: int = Field(alias="simNumber", ge=1, strict=True)
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    carrier_name: str | None = Field(default=None, alias="carrierName")
    iccid: str | None = None


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: NonEmptyName
    push_token: str | None = Field(default=None, alias="pushToken")
    sim_cards: list[SimCardRequest] = Field(alias="simCards")

    @model_validator(mode="after")
    def reject_duplicate_sim_identity(self) -> "DeviceRegisterRequest":
        for attribute, alias in (
            ("slot_index", "slotIndex"),
            ("sim_number", "simNumber"),
        ):
            values = [getattr(sim, attribute) for sim in self.sim_cards]
            if len(values) != len(set(values)):
                raise ValueError(f"simCards contains duplicate {alias}")
        return self


class DeviceRegisterResponse(BaseModel):
    id: str
    token: str
    login: str
    password: str | None


class DeviceUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: DeviceId
    push_token: str | None = Field(default=None, alias="pushToken")
    sim_cards: list[SimCardRequest] | None = Field(default=None, alias="simCards")

    @model_validator(mode="after")
    def reject_duplicate_sim_identity(self) -> "DeviceUpdateRequest":
        if self.sim_cards is None:
            return self
        for attribute, alias in (
            ("slot_index", "slotIndex"),
            ("sim_number", "simNumber"),
        ):
            values = [getattr(sim, attribute) for sim in self.sim_cards]
            if len(values) != len(set(values)):
                raise ValueError(f"simCards contains duplicate {alias}")
        return self


class DeviceUpdateResponse(BaseModel):
    ok: bool = True
