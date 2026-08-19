// ---- searchable dropdown (.combo) --------------------------------------
// Drives the dropdowns that carry their own search box inside the open
// list - what a native <select> cannot do. The chosen value lives in a
// hidden input, so forms post exactly what a <select> would have, and a
// 'change' event fires on that input for anything listening.
//
// Expected markup (see the searchable_select macro in _macros.html):
//   .combo > hidden input
//          > button.combo-toggle > span.combo-toggle-label
//          > .combo-panel > input.combo-search
//                         > .combo-list > .combo-option[data-value]
//                         > .combo-empty
// Anything else inside .combo-panel (e.g. the product form's "+ Add a new
// HSN code" row) is left alone.
function initCombo(rootId, options) {
  var opts = options || {};
  var root = document.getElementById(rootId);
  if (!root) return null;
  var input = root.querySelector('input[type="hidden"]');
  var toggle = root.querySelector('.combo-toggle');
  var label = root.querySelector('.combo-toggle-label');
  var panel = root.querySelector('.combo-panel');
  var search = root.querySelector('.combo-search');
  var list = root.querySelector('.combo-list');
  var empty = root.querySelector('.combo-empty');
  var placeholder = toggle.dataset.placeholder || '';

  function allOptions() {
    return Array.prototype.slice.call(list.querySelectorAll('.combo-option'));
  }

  function markSelected() {
    allOptions().forEach(function (opt) {
      opt.classList.toggle('is-selected', opt.dataset.value === input.value);
    });
  }

  function filter() {
    var term = search.value.trim().toLowerCase();
    var shown = 0;
    allOptions().forEach(function (opt) {
      // data-search carries anything worth matching on beyond the value
      // itself (e.g. an HSN code's "related to products" note).
      var haystack = (opt.dataset.value + ' ' + (opt.dataset.search || '')).toLowerCase();
      // A blank entry always stays reachable, so a value can be cleared
      // without first clearing the search.
      var match = term === '' || opt.dataset.value === '' || haystack.indexOf(term) !== -1;
      opt.hidden = !match;
      opt.classList.remove('is-active');
      if (match) shown++;
    });
    if (empty) empty.hidden = shown > 0;
  }

  function open() {
    search.value = '';
    filter();
    markSelected();
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    search.focus();
  }

  function close() {
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (opts.onClose) opts.onClose();
  }

  function choose(option) {
    input.value = option.dataset.value;
    label.textContent = option.dataset.value || placeholder;
    markSelected();
    close();
    toggle.focus();
    if (opts.onChange) opts.onChange(option);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  toggle.addEventListener('click', function () {
    if (panel.hidden) { open(); } else { close(); }
  });

  list.addEventListener('click', function (e) {
    var option = e.target.closest('.combo-option');
    if (option) choose(option);
  });

  search.addEventListener('input', filter);

  // Arrow keys walk the visible matches, Enter takes the highlighted one
  // (or the first match), Escape closes. Enter must never submit the form
  // the dropdown sits in.
  search.addEventListener('keydown', function (e) {
    var visible = allOptions().filter(function (opt) { return !opt.hidden; });
    var activeIndex = visible.findIndex(function (opt) { return opt.classList.contains('is-active'); });
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!visible.length) return;
      var next = e.key === 'ArrowDown'
        ? Math.min(activeIndex + 1, visible.length - 1)
        : Math.max(activeIndex - 1, 0);
      visible.forEach(function (opt) { opt.classList.remove('is-active'); });
      visible[next].classList.add('is-active');
      visible[next].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      var pick = activeIndex >= 0 ? visible[activeIndex] : visible[0];
      if (pick) choose(pick);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
      toggle.focus();
    }
  });

  // Clicking anywhere outside closes it, like a native dropdown.
  document.addEventListener('click', function (e) {
    if (!panel.hidden && !root.contains(e.target)) close();
  });

  return { root: root, input: input, list: list, choose: choose, close: close, refresh: filter };
}
