"""家长端登录：小程序 wx.login 的 code → 服务端换 openid → 签发令牌。

AppSecret 只在服务端（.env），不进小程序代码。令牌是 HMAC 签名的 family_id + 过期时间，不需要存库。
"""

import base64
import hashlib
import hmac
import json
import time

import httpx

from .config import Settings

TOKEN_TTL_S = 30 * 24 * 3600


class AuthError(Exception):
    pass


def _secret(settings: Settings) -> bytes:
    raw = settings.auth_secret or f"waywise-auth:{settings.agent_api_key}"
    return hashlib.sha256(raw.encode()).digest()


def issue_token(settings: Settings, family_id: str, now: float | None = None) -> str:
    payload = json.dumps({"fid": family_id, "exp": int((now or time.time()) + TOKEN_TTL_S)}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(settings: Settings, token: str, now: float | None = None) -> str:
    """返回 family_id；签名不对或过期抛 AuthError。"""
    body, _, sig = token.partition(".")
    expected = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()
    if not sig or not hmac.compare_digest(sig, expected):
        raise AuthError("invalid token")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if payload["exp"] < (now or time.time()):
        raise AuthError("token expired")
    return payload["fid"]


async def code_to_openid(settings: Settings, code: str) -> str:
    if not settings.wx_appid or not settings.wx_appsecret:
        raise AuthError("WX_APPID / WX_APPSECRET 未配置")
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.wx_appid,
                "secret": settings.wx_appsecret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        )
    data = r.json()
    if not data.get("openid"):
        raise AuthError(f"微信登录失败: {data.get('errcode')} {data.get('errmsg')}")
    return data["openid"]
