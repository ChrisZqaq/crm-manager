(() => {
  const form = document.getElementById('login-form');
  if (!form) return;

  const username = document.getElementById('id_username');
  const password = document.getElementById('id_password');
  const toggle = document.getElementById('toggle-password');

  form.noValidate = true;
  username.setAttribute('autocomplete', 'username');
  username.setAttribute('autocapitalize', 'none');
  username.setAttribute('spellcheck', 'false');
  password.setAttribute('autocomplete', 'current-password');
  for (const field of [username, password]) {
    const error = document.getElementById(`${field.id.replace('id_', '')}-error`);
    field.setAttribute('aria-describedby', [error.id, 'login-server-error'].filter(id => document.getElementById(id)).join(' '));
  }

  toggle.addEventListener('click', () => {
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    toggle.setAttribute('aria-pressed', String(show));
  });

  form.addEventListener('submit', event => {
    let firstInvalid;
    for (const [field, message] of [[username, '请输入账号'], [password, '请输入密码']]) {
      const invalid = field === username ? !field.value.trim() : !field.value;
      const error = document.getElementById(`${field.id.replace('id_', '')}-error`);
      field.setAttribute('aria-invalid', String(invalid));
      error.textContent = invalid ? message : '';
      if (invalid && !firstInvalid) firstInvalid = field;
    }
    if (firstInvalid) {
      event.preventDefault();
      firstInvalid.focus();
    }
  });

  for (const field of [username, password]) {
    field.addEventListener('input', () => {
      field.removeAttribute('aria-invalid');
      document.getElementById(`${field.id.replace('id_', '')}-error`).textContent = '';
    });
  }
})();
