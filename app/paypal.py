import os
import httpx
from fastapi import APIRouter

router = APIRouter()


def _paypal_base_url() -> str:
    mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
    if mode == "live":
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


async def test_paypal_credentials():
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()

    if not client_id or not client_secret:
        return {
            "ok": False,
            "mode": mode,
            "message": "ยังไม่ได้ตั้งค่า PAYPAL_CLIENT_ID และ PAYPAL_CLIENT_SECRET ครบ",
        }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{_paypal_base_url()}/v1/oauth2/token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json", "Accept-Language": "en_US"},
            )

        if response.status_code == 200:
            data = response.json()
            return {
                "ok": True,
                "mode": mode,
                "token_type": data.get("token_type"),
                "expires_in": data.get("expires_in"),
                "message": "เชื่อมต่อ PayPal Sandbox สำเร็จ" if mode != "live" else "เชื่อมต่อ PayPal Live สำเร็จ",
            }

        return {
            "ok": False,
            "mode": mode,
            "status_code": response.status_code,
            "message": "PayPal ปฏิเสธ Client ID หรือ Secret กรุณาตรวจสอบ credentials",
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": mode,
            "message": f"เชื่อมต่อ PayPal ไม่สำเร็จ: {type(exc).__name__}",
        }


@router.on_event("startup")
async def paypal_startup_check():
    result = await test_paypal_credentials()
    # Intentionally log only a safe result; never log credentials or access tokens.
    print(f"PAYPAL_STARTUP_CHECK ok={result.get('ok')} mode={result.get('mode')} status={result.get('status_code', 'oauth-ok' if result.get('ok') else 'n/a')}")


@router.get("/api/paypal/status")
def paypal_status():
    client_id = bool(os.getenv("PAYPAL_CLIENT_ID"))
    client_secret = bool(os.getenv("PAYPAL_CLIENT_SECRET"))
    mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
    return {
        "configured": client_id and client_secret,
        "mode": mode,
        "live_money_actions": os.getenv("ALLOW_LIVE_MONEY_ACTIONS", "false").lower() == "true",
        "live_payouts": os.getenv("ALLOW_LIVE_PAYOUTS", "false").lower() == "true",
    }


@router.get("/api/paypal/test")
async def paypal_test():
    return await test_paypal_credentials()
