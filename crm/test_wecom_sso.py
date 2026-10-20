from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from .models import AccountProfile
from .permissions import ROLE_OWNER, ROLE_SALES
from .views import WECOM_STATE_SESSION_KEY
from .wecom import WeComError, access_token


User = get_user_model()
HOST = "crm.example.test"
WECOM_SETTINGS = {
    "ALLOWED_HOSTS": [HOST, "testserver"],
    "WECOM_CORP_ID": "synthetic-corp",
    "WECOM_AGENT_ID": "synthetic-agent",
    "WECOM_CORP_SECRET": "synthetic-secret",
    "WECOM_CALLBACK_URL": f"https://{HOST}/accounts/wecom/callback/",
}


@override_settings(**WECOM_SETTINGS)
class WeComSSOTestBase(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)

    def start(self, next_url="/"):
        response = self.client.get(
            "/accounts/wecom/login/",
            {"next": next_url},
            HTTP_HOST=HOST,
            HTTP_X_FORWARDED_HOST="attacker.example",
            HTTP_X_FORWARDED_PROTO="https",
        )
        query = parse_qs(urlsplit(response["Location"]).query) if response.status_code == 302 else {}
        return response, query.get("state", [None])[0]

    def callback(self, state, code="synthetic-code"):
        query = urlencode({"state": state, "code": code})
        return self.client.get(
            f"/accounts/wecom/callback/?{query}",
            HTTP_HOST=HOST,
            secure=True,
        )

    @staticmethod
    def provider_result(userid=None, *, nonmember=False):
        identity = (
            {"errcode": 0, "openid": "synthetic-openid", "external_userid": "synthetic-external"}
            if nonmember else {"errcode": 0, "userid": userid}
        )

        def response_for(url):
            if "/gettoken?" in url:
                return {"errcode": 0, "access_token": "synthetic-token", "expires_in": 7200}
            return identity

        return patch("crm.wecom._request_json", side_effect=response_for)


class WeComProviderTests(SimpleTestCase):
    @override_settings(**WECOM_SETTINGS)
    def test_access_token_is_cached_using_platform_expiry(self):
        cache.clear()
        with patch("crm.wecom._request_json", return_value={
            "errcode": 0, "access_token": "synthetic-token", "expires_in": 7200,
        }) as request_json:
            self.assertEqual(access_token(), "synthetic-token")
            self.assertEqual(access_token(), "synthetic-token")
        self.assertEqual(request_json.call_count, 1)

    @override_settings(**WECOM_SETTINGS)
    def test_invalid_provider_result_is_generic_and_not_cached(self):
        cache.clear()
        with patch("crm.wecom._request_json", return_value={"errcode": 40013}):
            with self.assertRaises(WeComError):
                access_token()
        self.assertEqual(cache.get("wecom:access-token:synthetic-corp:synthetic-agent"), None)


@override_settings(**WECOM_SETTINGS)
class WeComSSOFlowTests(WeComSSOTestBase):
    def test_start_uses_fixed_https_callback_and_rejects_external_next(self):
        response, state = self.start("https://attacker.example/steal")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["redirect_uri"], [WECOM_SETTINGS["WECOM_CALLBACK_URL"]])
        self.assertEqual(query["scope"], ["snsapi_base"])
        self.assertEqual(self.client.session[WECOM_STATE_SESSION_KEY]["state"], state)
        self.assertEqual(self.client.session[WECOM_STATE_SESSION_KEY]["next"], "/")

    def test_invalid_expired_and_replayed_state_never_call_provider(self):
        _, state = self.start("/catalog/")
        with patch("crm.wecom._request_json") as request_json:
            invalid = self.callback("wrong-state")
            self.assertEqual(invalid.status_code, 400)
            request_json.assert_not_called()

            non_ascii = self.callback("非ASCII")
            self.assertEqual(non_ascii.status_code, 400)
            request_json.assert_not_called()

            session = self.client.session
            pending = session[WECOM_STATE_SESSION_KEY]
            pending["expires_at"] = 0
            session[WECOM_STATE_SESSION_KEY] = pending
            session.save()
            expired = self.callback(state)
            self.assertEqual(expired.status_code, 400)
            request_json.assert_not_called()

    def test_success_logs_in_existing_member_and_keeps_roles(self):
        user = User.objects.create_user(username="synthetic-sales", password="test-password")
        sales = Group.objects.get_or_create(name=ROLE_SALES)[0]
        user.groups.add(sales)
        AccountProfile.objects.create(user=user, wecom_userid="Sales.One")
        _, state = self.start("/catalog/")
        session_before = self.client.session.session_key
        with self.provider_result("sales.one") as request_json:
            response = self.callback(state)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/catalog/")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertNotEqual(self.client.session.session_key, session_before)
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [ROLE_SALES])
        self.assertEqual(self.client.get("/", HTTP_HOST=HOST).status_code, 200)
        with patch("crm.wecom._request_json") as request_json:
            replay = self.callback(state)
        self.assertEqual(replay.status_code, 400)
        request_json.assert_not_called()

    def test_same_user_continues_but_different_user_must_logout(self):
        current = User.objects.create_user(username="current", password="test-password")
        target = User.objects.create_user(username="target", password="test-password")
        AccountProfile.objects.create(user=current, wecom_userid="current-id")
        AccountProfile.objects.create(user=target, wecom_userid="target-id")

        self.client.force_login(current)
        _, same_state = self.start("/")
        with self.provider_result("CURRENT-ID"):
            same = self.callback(same_state)
        self.assertEqual(same.status_code, 302)
        self.assertEqual(same.wsgi_request.user.pk, current.pk)

        other_client = Client(enforce_csrf_checks=True)
        other_client.force_login(current)
        _, different_state = self.start_with(other_client)
        with self.provider_result("target-id"):
            mismatch = self.callback_with(other_client, different_state)
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.wsgi_request.user.pk, current.pk)
        self.assertContains(mismatch, "请先退出当前CRM账号", status_code=409)
        self.assertContains(mismatch, 'method="post" action="/accounts/logout/"', status_code=409)
        csrf = other_client.cookies["csrftoken"].value
        logout = other_client.post(
            "/accounts/logout/",
            HTTP_HOST=HOST,
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(other_client.get("/", HTTP_HOST=HOST).status_code, 302)

    def start_with(self, client):
        response = client.get("/accounts/wecom/login/", HTTP_HOST=HOST)
        state = parse_qs(urlsplit(response["Location"]).query)["state"][0]
        return response, state

    def callback_with(self, client, state):
        query = urlencode({"state": state, "code": "synthetic-code"})
        return client.get(f"/accounts/wecom/callback/?{query}", HTTP_HOST=HOST, secure=True)

    def test_nonmember_unbound_and_inactive_users_are_denied(self):
        inactive = User.objects.create_user(username="inactive", password="test-password", is_active=False)
        AccountProfile.objects.create(user=inactive, wecom_userid="inactive-id")
        cases = ((None, True), ("unbound-id", False), ("inactive-id", False))
        for userid, nonmember in cases:
            with self.subTest(userid=userid, nonmember=nonmember):
                _, state = self.start()
                with self.provider_result(userid, nonmember=nonmember):
                    response = self.callback(state)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(AccountProfile.objects.count(), 1)

    def test_upstream_failure_does_not_create_an_authenticated_session(self):
        _, state = self.start()
        with patch("crm.wecom._request_json", side_effect=WeComError):
            response = self.callback(state)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_loses_an_existing_session_on_next_request(self):
        user = User.objects.create_user(username="session-user", password="test-password")
        AccountProfile.objects.create(user=user, wecom_userid="session-user-id")
        _, state = self.start()
        with self.provider_result("session-user-id"):
            self.assertEqual(self.callback(state).status_code, 302)
        User.objects.filter(pk=user.pk).update(is_active=False)
        response = self.client.get("/", HTTP_HOST=HOST, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/accounts/login/"))


@override_settings(**WECOM_SETTINGS)
class WeComProfileTests(TestCase):
    def test_userid_matches_official_ascii_charset_and_case_insensitive_uniqueness(self):
        field = AccountProfile._meta.get_field("wecom_userid")
        field.clean("A" + "x" * 63, None)
        with self.assertRaises(ValidationError):
            field.clean("_invalid", None)
        with self.assertRaises(ValidationError):
            field.clean("x" * 65, None)

        first = User.objects.create_user(username="first", password="test-password")
        second = User.objects.create_user(username="second", password="test-password")
        unbound_first = User.objects.create_user(username="unbound-first", password="test-password")
        unbound_second = User.objects.create_user(username="unbound-second", password="test-password")
        AccountProfile.objects.create(user=unbound_first)
        AccountProfile.objects.create(user=unbound_second)
        AccountProfile.objects.create(user=first, wecom_userid="Member.One")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccountProfile.objects.create(user=second, wecom_userid="member.one")
        self.assertEqual(AccountProfile.objects.filter(wecom_userid__isnull=True).count(), 2)

    def test_admin_explicitly_binds_existing_employee_and_rejects_case_duplicate(self):
        owner_role = Group.objects.get_or_create(name=ROLE_OWNER)[0]
        change_user = Permission.objects.get(content_type__app_label="auth", codename="change_user")
        owner_role.permissions.add(change_user)
        owner = User.objects.create_user(username="owner", password="owner-password", is_staff=True)
        owner.groups.add(owner_role)
        employee = User.objects.create_user(username="employee", password="employee-password")
        duplicate = User.objects.create_user(username="duplicate", password="duplicate-password")
        candidate = User.objects.create_user(username="candidate", password="candidate-password")
        AccountProfile.objects.create(user=duplicate, wecom_userid="bound-id")
        self.client.force_login(owner)

        url = f"/admin/auth/user/{employee.pk}/change/"
        self.assertContains(self.client.get(url), 'name="wecom_userid"')
        data = {
            "username": employee.username,
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": "on",
            "wecom_userid": "Employee.Id",
            "_save": "保存",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(AccountProfile.objects.get(user=employee).wecom_userid, "Employee.Id")

        duplicate_url = f"/admin/auth/user/{candidate.pk}/change/"
        data["username"] = candidate.username
        data["wecom_userid"] = "BOUND-ID"
        duplicate_response = self.client.post(duplicate_url, data)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertContains(duplicate_response, "已绑定其他CRM账号")

    def test_superuser_employee_form_includes_binding_field(self):
        administrator = User.objects.create_superuser(
            username="administrator", email="admin@example.test", password="admin-password"
        )
        employee = User.objects.create_user(username="employee", password="employee-password")
        self.client.force_login(administrator)
        response = self.client.get(f"/admin/auth/user/{employee.pk}/change/")
        self.assertContains(response, 'name="wecom_userid"')


@override_settings(SECURE_PROXY_SSL_HEADER=None)
class WeComProxyBoundaryTests(SimpleTestCase):
    def test_forwarded_scheme_is_ignored_without_explicit_trusted_proxy_setting(self):
        request = RequestFactory().get("/", HTTP_HOST=HOST, HTTP_X_FORWARDED_PROTO="https")
        self.assertFalse(request.is_secure())
