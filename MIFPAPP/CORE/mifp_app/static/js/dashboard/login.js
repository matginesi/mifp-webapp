/* Login enhancement. The form remains fully functional without JavaScript. */
document.addEventListener('click', function (event) {
  var toggle = event.target.closest('[data-password-toggle]');
  if (!toggle) return;
  var field = document.getElementById(toggle.getAttribute('aria-controls'));
  if (!field) return;
  var show = field.type === 'password';
  field.type = show ? 'text' : 'password';
  toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  var icon = toggle.querySelector('i');
  if (icon) icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
});
