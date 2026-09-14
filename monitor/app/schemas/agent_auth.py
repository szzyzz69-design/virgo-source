from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


NonEmptyAgentText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class AgentLoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: NonEmptyAgentText
    password: NonEmptyAgentText


class AgentLoginResponse(BaseModel):
    token: str
    expiresAt: int


class AgentMeResponse(BaseModel):
    id: str
    username: str
    areas: str | None
