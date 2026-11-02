/* Polls shared-link editor. Reuses the legacy visual question cards/preview,
   without retaining any of the old recipient or SMTP functionality. */
(function () {
  'use strict';

  document.querySelectorAll('[data-poll-share-link]').forEach(function (input) {
    input.addEventListener('click', function () { input.select(); });
  });
  document.querySelectorAll('[data-poll-delete-form], [data-poll-erase-form]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      var message = form.hasAttribute('data-poll-delete-form')
        ? 'Delete this survey and ALL its responses?' : 'Erase this email and its submission?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  var newButton = document.querySelector('[data-poll-new-toggle]');
  var newForm = document.querySelector('[data-poll-new-form]');
  newButton?.addEventListener('click', function () {
    newForm.hidden = !newForm.hidden;
    newButton.setAttribute('aria-expanded', String(!newForm.hidden));
  });
  document.querySelector('[data-poll-new-cancel]')?.addEventListener('click', function () {
    newForm.hidden = true;
    newButton?.setAttribute('aria-expanded', 'false');
  });
  var tabs = Array.from(document.querySelectorAll('[data-shared-poll-tab]'));
  var panels = Array.from(document.querySelectorAll('[data-shared-poll-panel]'));
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var selected = tab.dataset.sharedPollTab;
      tabs.forEach(function (item) { item.setAttribute('aria-selected', String(item === tab)); });
      panels.forEach(function (panel) { panel.hidden = panel.dataset.sharedPollPanel !== selected; });
    });
  });
  document.querySelector('[data-poll-copy-link]')?.addEventListener('click', async function (event) {
    var input = document.querySelector('[data-poll-share-link]');
    if (!input) return;
    var button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(input.value);
      button.textContent = 'Copied';
    } catch (error) {
      input.focus(); input.select();
    }
  });

  var form = document.querySelector('#survey-editor-form');
  var fallback = document.querySelector('#survey-admin-questions');
  var visual = document.querySelector('#survey-visual-questions');
  var preview = document.querySelector('#survey-visual-preview');
  var fields = document.querySelector('#survey-visual-fields');
  var add = document.querySelector('#survey-add-question');
  if (!form || !fallback || !visual || !preview || !fields || !add || !window.MIFPPollRenderer) return;

  var locked = form.dataset.questionsLocked === '1';
  var questions = JSON.parse(document.querySelector('#survey-question-data').textContent);
  var types = [['yes_no', 'Yes / No'], ['single_choice', 'Single choice'],
    ['multiple_choice', 'Multiple choice'], ['date', 'Date'],
    ['date_availability', 'Date availability'], ['text', 'Text']];

  function element(tag, className, text) {
    var item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function newId() {
    var bytes = new Uint8Array(8);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, function (n) { return n.toString(16).padStart(2, '0'); }).join('');
  }

  function action(label, icon, callback, disabled) {
    var item = element('button', 'btn btn-ghost btn-sm');
    item.type = 'button';
    item.title = label;
    item.setAttribute('aria-label', label);
    item.append(element('i', 'bi ' + icon));
    if (label && !['Remove option', 'Move up', 'Move down'].includes(label)) {
      item.append(document.createTextNode(' ' + label));
    }
    item.disabled = Boolean(disabled) || locked;
    item.addEventListener('click', callback);
    return item;
  }

  function labeled(label, input) {
    var wrapper = element('label', 'field');
    input.setAttribute('aria-label', label);
    wrapper.append(element('span', '', label), input);
    return wrapper;
  }

  function inputText(value, limit) {
    var item = element('input', 'form-control form-control-sm');
    item.type = 'text';
    item.value = value || '';
    item.maxLength = limit;
    item.disabled = locked;
    return item;
  }

  function move(items, index, delta) {
    var next = index + delta;
    if (next < 0 || next >= items.length) return;
    var selected = items.splice(index, 1)[0];
    items.splice(next, 0, selected);
    render();
  }

  function ensureOptions(q) {
    if (!['single_choice', 'multiple_choice'].includes(q.type)) return;
    if (!Array.isArray(q.options) || q.options.length < 2) {
      q.options = [{ id: newId(), label: 'Option 1' }, { id: newId(), label: 'Option 2' }];
    }
  }

  function drawOptions(q, container) {
    var list = element('div', 'poll-option-list');
    q.options.forEach(function (option, i) {
      var row = element('div', 'poll-option-row');
      var field = inputText(option.label, 160);
      field.setAttribute('aria-label', 'Option ' + (i + 1));
      field.required = true;
      field.addEventListener('input', function () { option.label = field.value; renderPreview(); });
      row.append(field,
        action('Move up', 'bi-arrow-up', function () { move(q.options, i, -1); }, i === 0),
        action('Move down', 'bi-arrow-down', function () { move(q.options, i, 1); }, i === q.options.length - 1),
        action('Remove option', 'bi-trash', function () {
          if (q.options.length <= 2) return;
          q.options.splice(i, 1); render();
        }, q.options.length <= 2));
      list.append(row);
    });
    container.append(list, action('Add option', 'bi-plus-lg', function () {
      if (q.options.length >= 30) return;
      q.options.push({ id: newId(), label: 'New option' });
      render();
    }, q.options.length >= 30));
  }

  function renderPreview() {
    var title = form.querySelector('[name="title"]');
    var description = form.querySelector('[name="description"]');
    var deadline = form.querySelector('[name="deadline"]');
    window.MIFPPollRenderer.render(preview, {
      title: title.value, description: description.value, deadline: deadline.value,
      questions: questions
    }, { preview: true, disabled: true });
  }

  function render() {
    visual.replaceChildren();
    questions.forEach(function (q, index) {
      ensureOptions(q);
      var card = element('article', 'poll-question-editor');
      var header = element('header');
      header.append(element('b', '', 'Question ' + (index + 1)));
      var buttons = element('div', 'poll-question-actions');
      buttons.append(
        action('Up', 'bi-arrow-up', function () { move(questions, index, -1); }, index === 0),
        action('Down', 'bi-arrow-down', function () { move(questions, index, 1); }, index === questions.length - 1),
        action('Duplicate', 'bi-copy', function () {
          if (questions.length >= 30) return;
          var clone = JSON.parse(JSON.stringify(q));
          clone.id = newId();
          (clone.options || []).forEach(function (opt) { opt.id = newId(); });
          questions.splice(index + 1, 0, clone); render();
        }, questions.length >= 30),
        action('Delete', 'bi-trash', function () {
          if (questions.length <= 1) return;
          questions.splice(index, 1); render();
        }, questions.length <= 1));
      header.append(buttons);
      var body = element('div', 'poll-question-editor-body');
      var grid = element('div', 'poll-question-grid');
      var title = inputText(q.question, 300);
      title.required = true;
      title.addEventListener('input', function () { q.question = title.value; renderPreview(); });
      var type = element('select', 'form-select form-select-sm');
      type.disabled = locked;
      types.forEach(function (choice) {
        var option = element('option', '', choice[1]);
        option.value = choice[0]; option.selected = q.type === choice[0];
        type.append(option);
      });
      type.addEventListener('change', function () { q.type = type.value; ensureOptions(q); render(); });
      var required = element('label', 'toggle-row');
      var checked = element('input');
      checked.type = 'checkbox'; checked.checked = Boolean(q.required); checked.disabled = locked;
      checked.addEventListener('change', function () { q.required = checked.checked; renderPreview(); });
      var copy = element('span');
      copy.append(element('b', '', 'Required'), element('small', '', 'Respondent must answer.'));
      required.append(checked, copy);
      grid.append(labeled('Question', title), labeled('Response type', type), required);
      body.append(grid);
      if (q.type === 'date_availability') {
        var dates = element('textarea', 'form-control form-control-sm');
        dates.rows = 4; dates.maxLength = 660; dates.disabled = locked;
        dates.placeholder = '2027-01-11\n2027-01-12';
        dates.value = (q.dates || []).join('\n');
        dates.addEventListener('input', function () {
          q.dates = dates.value.split(/\r?\n/).map(function (day) { return day.trim(); }).filter(Boolean);
          renderPreview();
        });
        body.append(labeled('Dates (YYYY-MM-DD, one per line)', dates),
          element('small', '', '1–60 distinct calendar dates. Required means every date needs an answer.'));
      } else if (q.type === 'text') {
        var style = element('select', 'form-select form-select-sm');
        style.disabled = locked;
        [['single_line', 'Single line'], ['multi_line', 'Multiple lines']].forEach(function (item) {
          var option = element('option', '', item[1]);
          option.value = item[0]; option.selected = (q.style || 'single_line') === item[0];
          style.append(option);
        });
        style.addEventListener('change', function () { q.style = style.value; renderPreview(); });
        body.append(labeled('Response style', style));
      } else if (q.type === 'yes_no') {
        var yes = inputText(q.yes_label || 'Yes', 80);
        var no = inputText(q.no_label || 'No', 80);
        yes.required = true; no.required = true;
        yes.addEventListener('input', function () { q.yes_label = yes.value; renderPreview(); });
        no.addEventListener('input', function () { q.no_label = no.value; renderPreview(); });
        var labels = element('div', 'poll-question-grid');
        labels.append(labeled('Positive label', yes), labeled('Negative label', no));
        body.append(labels);
      } else if (['single_choice', 'multiple_choice'].includes(q.type)) drawOptions(q, body);
      card.append(header, body);
      visual.append(card);
    });
    renderPreview();
  }

  function hidden(name, value) {
    var field = element('input');
    field.type = 'hidden'; field.name = name; field.value = value;
    return field;
  }

  function serialize() {
    if (!form.querySelector('[name="title"]').value.trim()) {
      window.alert('Enter the poll title.');
      return false;
    }
    if (!questions.length || questions.length > 30) return false;
    fields.replaceChildren();
    for (var i = 0; i < questions.length; i += 1) {
      var q = questions[i];
      if (!q.question.trim()) {
        window.alert('Question ' + (i + 1) + ': enter the question text.');
        return false;
      }
      if (['single_choice', 'multiple_choice'].includes(q.type)) {
        var labels = (q.options || []).map(function (opt) { return opt.label.trim(); });
        if (labels.length < 2 || labels.length > 30 || labels.some(function (v) { return !v; }) ||
            new Set(labels.map(function (v) { return v.toLocaleLowerCase(); })).size !== labels.length) {
          window.alert('Question ' + (i + 1) + ': enter between 2 and 30 different, non-empty options.');
          return false;
        }
        fields.append(hidden('q_' + i + '_options', labels.join('\n')));
      }
      if (q.type === 'date_availability') {
        var days = q.dates || [];
        if (!days.length || days.length > 60 || new Set(days).size !== days.length || days.some(function (day) {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return true;
          var parsed = new Date(day + 'T00:00:00Z');
          return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== day;
        })) {
          window.alert('Question ' + (i + 1) + ': enter 1–60 different dates in YYYY-MM-DD format.');
          return false;
        }
        fields.append(hidden('q_' + i + '_dates', days.join('\n')));
      }
      fields.append(hidden('q_' + i + '_question', q.question));
      fields.append(hidden('q_' + i + '_type', q.type));
      if (q.type === 'text') fields.append(hidden('q_' + i + '_style', q.style || 'single_line'));
      if (q.type === 'yes_no') {
        fields.append(hidden('q_' + i + '_yes_label', q.yes_label || 'Yes'));
        fields.append(hidden('q_' + i + '_no_label', q.no_label || 'No'));
      }
      if (q.required) fields.append(hidden('q_' + i + '_required', 'on'));
    }
    return true;
  }

  // The original editor uses tabs, so required fields can be in inactive panels.
  // Validate the essential fields in JS and on the server instead of having
  // native validation fail on hidden controls when saving from Privacy.
  form.noValidate = true;
  // Progressive enhancement: if the visual editor cannot initialize, the
  // server-rendered form remains usable and the survey is never silently erased.
  render();
  // Hidden fallback controls must also be disabled to avoid native validation
  // blocking the visual form (required fields in hidden elements).
  fallback.querySelectorAll('input, select, textarea').forEach(function (field) { field.disabled = true; });
  fallback.hidden = true;
  visual.hidden = false;
  add.addEventListener('click', function () {
    if (locked || questions.length >= 30) return;
    questions.push({ id: newId(), type: 'yes_no', question: 'New question', required: true });
    render();
  });
  form.querySelectorAll('[name="title"], [name="description"], [name="deadline"]').forEach(function (field) {
    field.addEventListener('input', renderPreview);
  });
  form.addEventListener('submit', function (event) {
    if (!serialize()) event.preventDefault();
  });
})();
