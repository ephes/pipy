// Adapt the native drawer without replacing Zensical's navigation or search.
(() => {
  const drawer = document.getElementById('__drawer');
  const sidebar = document.querySelector('.md-sidebar--primary');
  const toggle = document.querySelector('label.md-header__button[for="__drawer"]');
  if (!drawer || !sidebar || !toggle) return;
  let keyboardOpening = false;
  const compact = () => toggle.getClientRects().length > 0;
  sidebar.id ||= 'pipy-docs-navigation';
  toggle.setAttribute('role', 'button');
  toggle.tabIndex = 0;
  toggle.setAttribute('aria-controls', sidebar.id);
  function sync() {
    const closed = compact() && !drawer.checked;
    const focusInside = sidebar.contains(document.activeElement);
    sidebar.inert = closed;
    toggle.setAttribute('aria-expanded', String(compact() && drawer.checked));
    if (closed && focusInside) toggle.focus();
    if (!closed && keyboardOpening) sidebar.querySelector('a[href]')?.focus();
    keyboardOpening = false;
  }
  toggle.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    keyboardOpening = !drawer.checked;
    toggle.click();
  });
  drawer.addEventListener('change', sync);
  window.addEventListener('resize', sync);
  window.addEventListener('pageshow', sync);
  // Native search owns its own focus inside a shadow root, not __search state.
  document.addEventListener('focusin', event => {
    if (compact() && drawer.checked && event.target !== toggle &&
        !sidebar.contains(event.target)) {
      drawer.checked = false;
      drawer.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  document.addEventListener('keydown', event => {
    if (!compact() || !drawer.checked) return;
    if (event.key === 'Tab') {
      // Collapsed native groups use visibility: collapse, not display: none.
      const items = [...sidebar.querySelectorAll('a[href], [tabindex="0"]')]
        .filter(item => item.getClientRects().length &&
          getComputedStyle(item).visibility === 'visible');
      const edge = event.shiftKey ? items[0] : items.at(-1);
      if (!sidebar.contains(document.activeElement) || document.activeElement === edge) {
        event.preventDefault();
        (event.shiftKey ? items.at(-1) : items[0])?.focus();
      }
      return;
    }
    if (event.key !== 'Escape') return;
    event.preventDefault();
    drawer.checked = false;
    drawer.dispatchEvent(new Event('change', { bubbles: true }));
    toggle.focus();
  }, true);
  sync();
})();
