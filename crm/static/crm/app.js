(() => {
  const sidebar = document.getElementById('primary-navigation');
  const toggle = document.querySelector('[data-menu-toggle]');
  const scrim = document.querySelector('[data-menu-close]');
  if (!sidebar || !toggle || !scrim) return;

  function setOpen(open) {
    sidebar.classList.toggle('is-open', open);
    scrim.classList.toggle('is-visible', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
    if (open) sidebar.querySelector('a, button')?.focus();
    else toggle.focus();
  }

  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  scrim.addEventListener('click', () => setOpen(false));
  sidebar.addEventListener('click', (event) => {
    if (event.target.closest('a')) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sidebar.classList.contains('is-open')) setOpen(false);
  });
})();
