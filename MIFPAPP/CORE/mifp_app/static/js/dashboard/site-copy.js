(() => {
  'use strict';

  const root = document.querySelector('[data-site-copy]');
  if (!root || root.dataset.copyReady === '1') return;
  root.dataset.copyReady = '1';

  const fields = Array.from(root.querySelectorAll('[data-copy-field]'));
  const groups = Array.from(root.querySelectorAll('[data-copy-group]'));
  const search = root.querySelector('[data-copy-search]');
  const count = root.querySelector('[data-copy-count]');

  function updateField(field) {
    const input = field.querySelector('[data-copy-input]');
    const length = field.querySelector('[data-copy-length]');
    const state = field.querySelector('[data-copy-state]');
    if (!input) return;
    if (length) length.textContent = String(input.value.length);
    if (state) {
      const custom = input.value.trim().length > 0;
      state.textContent = custom ? 'Custom' : 'Default';
      state.classList.toggle('is-custom', custom);
    }
  }

  function filterFields() {
    const query = (search?.value || '').trim().toLocaleLowerCase();
    let visible = 0;
    fields.forEach((field) => {
      const match = !query || (field.dataset.searchText || '').includes(query);
      field.hidden = !match;
      if (match) visible += 1;
    });
    groups.forEach((group) => {
      group.hidden = !group.querySelector('[data-copy-field]:not([hidden])');
    });
    if (count) count.textContent = `${visible} of ${fields.length} fields`;
  }

  fields.forEach((field) => {
    updateField(field);
    field.querySelector('[data-copy-input]')?.addEventListener('input', () => updateField(field));
  });
  search?.addEventListener('input', filterFields);
  filterFields();
})();
