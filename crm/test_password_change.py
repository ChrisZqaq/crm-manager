from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


User = get_user_model()


@override_settings(ALLOWED_HOSTS=["testserver"])
class PasswordSelfChangeTests(TestCase):
    password = "BeforeChange!28"
    new_password = "VelvetMango!47"

    def setUp(self):
        self.user = User.objects.create_user(username="staff-03", password=self.password)
        self.other_user = User.objects.create_user(username="staff-04", password="OtherPassword!29")
        self.client = Client(enforce_csrf_checks=True)

    def login_and_token(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def test_anonymous_user_cannot_open_change_or_done_pages(self):
        change = self.client.get(reverse("password_change"))
        done = self.client.get(reverse("password_change_done"))
        self.assertEqual(change.status_code, 302)
        self.assertEqual(done.status_code, 302)
        self.assertIn("/accounts/login/", change["Location"])
        self.assertIn("/accounts/login/", done["Location"])

    def test_password_change_page_is_linked_for_any_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'href="/accounts/password/change/"')
        self.assertContains(response, "修改密码")

    def test_invalid_old_password_and_password_validation_do_not_change_password(self):
        csrf_token = self.login_and_token()
        url = reverse("password_change")
        invalid_old = self.client.post(url, {
            "old_password": "incorrect",
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        }, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(invalid_old.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

        invalid_new = self.client.post(url, {
            "old_password": self.password,
            "new_password1": "12345678",
            "new_password2": "12345678",
        }, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(invalid_new.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_valid_change_updates_only_own_password_and_preserves_session(self):
        csrf_token = self.login_and_token()
        response = self.client.post(reverse("password_change"), {
            "old_password": self.password,
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        }, HTTP_X_CSRFTOKEN=csrf_token, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain, [(reverse("password_change_done"), 302)])
        self.assertContains(response, "密码已修改")
        self.assertContains(response, "当前登录状态已保留")
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(self.user.pk))
        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertTrue(self.other_user.check_password("OtherPassword!29"))

    def test_change_post_requires_csrf(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("password_change"), {
            "old_password": self.password,
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        })
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
