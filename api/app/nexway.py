import httpx

from app.config import settings


class NexwaySmsError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _normalize_phone_number(phone_number: str) -> str:
    digits = phone_number.replace("-", "")
    if digits.startswith("+81"):
        return "0" + digits[3:]
    return digits


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.nexway_api_token}",
        "Content-Type": "application/json",
    }


async def send_sms(to: str, text: str, user_reference: str) -> dict:
    payload = {
        "to": _normalize_phone_number(to),
        "text": text,
        "user_reference": user_reference,
    }
    try:
        async with httpx.AsyncClient(base_url=settings.nexway_api_base_url, timeout=10.0) as client:
            response = await client.post("/api/v1/short_messages", json=payload, headers=_headers())
    except httpx.HTTPError as exc:
        raise NexwaySmsError(status_code=0, code="ConnectionError", message=str(exc))

    if response.status_code != 202:
        body = response.json() if response.content else {}
        raise NexwaySmsError(
            status_code=response.status_code,
            code=body.get("code", "UnknownError"),
            message=body.get("message", "SMS送信に失敗しました"),
        )

    return response.json()


async def get_sms_delivery_status(delivery_order_id: int) -> tuple[str, str | None]:
    try:
        async with httpx.AsyncClient(base_url=settings.nexway_api_base_url, timeout=10.0) as client:
            response = await client.get("/api/v1/short_messages", headers=_headers())
    except httpx.HTTPError as exc:
        raise NexwaySmsError(status_code=0, code="ConnectionError", message=str(exc))

    if response.status_code != 200:
        body = response.json() if response.content else {}
        raise NexwaySmsError(
            status_code=response.status_code,
            code=body.get("code", "UnknownError"),
            message=body.get("message", "SMS配信結果の取得に失敗しました"),
        )

    orders = response.json().get("delivery_orders", [])
    order = next((item for item in orders if item.get("id") == delivery_order_id), None)
    if order is None:
        return "pending", None

    deliveries = order.get("deliveries", [])
    failed_delivery = next((item for item in deliveries if item.get("status") == "failed"), None)
    if failed_delivery is not None:
        error = failed_delivery.get("error") or {}
        return "failed", error.get("message") or "SMSを配信できませんでした"

    if deliveries and all(item.get("status") == "delivered" for item in deliveries):
        return "delivered", None

    return "pending", None
