(() => {
  const themePicker = document.querySelector('.page-theme-picker');
  if (themePicker) {
    const key = 'aluola-crm-theme';
    const choices = [...themePicker.querySelectorAll('[data-theme-choice]')];
    const applyTheme = theme => {
      const selected = theme === 'green' ? 'green' : 'cream';
      document.body.dataset.theme = selected;
      document.documentElement.dataset.theme = selected;
      choices.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === selected)));
    };
    applyTheme(localStorage.getItem(key));
    themePicker.addEventListener('click', event => {
      const choice = event.target.closest('[data-theme-choice]');
      if (!choice) return;
      applyTheme(choice.dataset.themeChoice);
      localStorage.setItem(key, choice.dataset.themeChoice);
    });
  }

  const sidebar = document.getElementById('primary-navigation');
  const toggle = document.querySelector('[data-menu-toggle]');
  const scrim = document.querySelector('[data-menu-close]');
  if (!sidebar || !toggle || !scrim) return;
  const mobile = window.matchMedia('(max-width: 760px)');

  function setOpen(open) {
    sidebar.classList.toggle('is-open', open);
    scrim.classList.toggle('is-visible', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
    if (open) sidebar.querySelector('a, button')?.focus();
    else if (mobile.matches) toggle.focus();
  }

  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  scrim.addEventListener('click', () => setOpen(false));
  sidebar.addEventListener('click', (event) => {
    if (mobile.matches && event.target.closest('a')) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sidebar.classList.contains('is-open')) setOpen(false);
  });
})();
