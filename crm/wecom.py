import json
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .models import AccountProfile


class WeComError(Exception):
    pass


def _configuration():
    corp_id = settings.WECOM_CORP_ID
    agent_id = settings.WECOM_AGENT_ID
    corp_secret = settings.WECOM_CORP_SECRET
    callback_url = settings.WECOM_CALLBACK_URL
    if not all((corp_id, agent_id, corp_secret, callback_url)):
        raise WeComError

    allowed_hosts = set(settings.ALLOWED_HOSTS) - {"*"}
    try:
        parts = urlsplit(callback_url)
        callback_is_allowed = url_has_allowed_host_and_scheme(
            callback_url,
            allowed_hosts,
            require_https=True,
        )
    except ValueError:
        raise WeComError from None
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path != reverse("wecom-callback")
        or not callback_is_allowed
    ):
        raise WeComError
    return corp_id, agent_id, corp_secret, callback_url


def authorization_url(state):
    corp_id, agent_id, _, callback_url = _configuration()
    query = urlencode({
        "appid": corp_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "snsapi_base",
        "state": state,
        "agentid": agent_id,
    })
    return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"


def _request_json(url):
    try:
        with urlopen(url, timeout=5) as response:
            body = response.read(16_385)
        if len(body) > 16_384:
            raise WeComError
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise WeComError
        return payload
    except WeComError:
        raise
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise WeComError from None


def access_token():
    corp_id, agent_id, corp_secret, _ = _configuration()
    cache_key = f"wecom:access-token:{corp_id}:{agent_id}"
    token = cache.get(cache_key)
    if isinstance(token, str) and token:
        return token

    query = urlencode({"corpid": corp_id, "corpsecret": corp_secret})
    payload = _request_json(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}")
    token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if payload.get("errcode") != 0 or not isinstance(token, str) or not token or type(expires_in) is not int or expires_in <= 60:
        raise WeComError
    cache.set(cache_key, token, timeout=expires_in - 60)
    return token


def authorized_userid(code):
    if not isinstance(code, str) or not code or len(code.encode("utf-8")) > 512:
        raise WeComError
    query = urlencode({"access_token": access_token(), "code": code})
    payload = _request_json(f"https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo?{query}")
    if payload.get("errcode") != 0:
        raise WeComError
    userid = payload.get("userid")
    if not isinstance(userid, str) or not userid:
        return None
    try:
        AccountProfile._meta.get_field("wecom_userid").clean(userid, None)
    except ValidationError:
        return None
    return userid
