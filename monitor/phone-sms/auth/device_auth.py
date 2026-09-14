from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import secrets
import hashlib
app = FastAPI()


# 模拟数据库
registration_codes = {
    "836492": {
        "used": False,
        "expires_at": "2099-01-01T00:00:00Z"
    }
}

devices = {}
device_sim_cards = {}


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SimCard(BaseModel):
    slotIndex: int
    simNumber: int
    phoneNumber: Optional[str] = None
    carrierName: Optional[str] = None
    iccid: Optional[str] = None


class DeviceRegisterRequest(BaseModel):
    name: str
    pushToken: Optional[str] = None
    simCards: List[SimCard] = []


class DeviceRegisterResponse(BaseModel):
    id: str
    token: str
    login: str
    password: Optional[str] = None


@app.post("/mobile/v1/device", response_model=DeviceRegisterResponse)
def register_device(
    body: DeviceRegisterRequest,
    authorization: Optional[str] = Header(default=None),
    user_agent: Optional[str] = Header(default=None, alias="User-Agent")
):
    # 1. 检查 Authorization
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Code "):
        raise HTTPException(status_code=401, detail="Invalid Authorization type")

    code = authorization.replace("Code ", "").strip()

    # 2. 检查注册码
    reg_code = registration_codes.get(code)

    if not reg_code:
        raise HTTPException(status_code=401, detail="Invalid registration code")

    if reg_code["used"]:
        raise HTTPException(status_code=401, detail="Registration code already used")

    # 3. 生成设备 ID 和 token
    device_id = f"dev_{secrets.token_hex(8)}"
    device_token = secrets.token_urlsafe(32)
    login = f"phone-{device_id[-6:]}"
    password = secrets.token_urlsafe(12)

    now = datetime.now(timezone.utc).isoformat()

    # 4. 保存设备
    devices[device_id] = {
        "id": device_id,
        "name": body.name,
        "push_token": body.pushToken,
        "token_hash": hash_value(device_token),
        "login": login,
        "password_hash": hash_value(password),
        "enabled": True,
        "status": "online",
        "last_seen_at": now,
        "registered_at": now,
        "user_agent": user_agent,
    }

    # 5. 保存 SIM / eSIM 信息
    device_sim_cards[device_id] = []

    for sim in body.simCards:
        device_sim_cards[device_id].append({
            "slot_index": sim.slotIndex,
            "sim_number": sim.simNumber,
            "phone_number": sim.phoneNumber,
            "carrier_name": sim.carrierName,
            "iccid_hash": hash_value(sim.iccid) if sim.iccid else None,
            "enabled": True,
            "status": "active",
        })

    # 6. 标记注册码已使用
    reg_code["used"] = True

    # 7. 返回给手机
    return {
        "id": device_id,
        "token": device_token,
        "login": login,
        "password": password,
    }