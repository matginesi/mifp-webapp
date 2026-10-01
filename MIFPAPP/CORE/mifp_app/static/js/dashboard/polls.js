(function () {
  'use strict';

  var editor = document.querySelector('[data-poll-editor]');
  document.querySelector('[data-poll-new-toggle]')?.addEventListener('click', function () {
    document.querySelector('[data-poll-new-form]').hidden = false;
  });
  document.querySelector('[data-poll-new-cancel]')?.addEventListener('click', function () {
    document.querySelector('[data-poll-new-form]').hidden = true;
  });
  if (!editor) return;

  var state = JSON.parse(editor.querySelector('[data-poll-json]').textContent);
  var analysis = JSON.parse(editor.querySelector('[data-analysis-json]').textContent);
  var smtpReady = editor.dataset.smtpReady === '1';
  var questionList = editor.querySelector('[data-question-list]');
  var preview = editor.querySelector('[data-poll-preview]');
  var feedback = editor.querySelector('[data-poll-feedback]');
  var recipientTable = null;
  var recipientResult = null;
  var batch = { running: false, paused: false, recipients: [], failed: [], index: 0, accepted: 0, controller: null, started: 0 };

  function uid() {
    var bytes = new Uint8Array(8);
    crypto.getRandomValues(bytes);
    return Array.from(bytes).map(function (value) { return value.toString(16).padStart(2, '0'); }).join('');
  }

  function node(tag, className, text) {
    var item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function dashboardColor(name, fallback) {
    var value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function button(label, action, icon) {
    var item = node('button', 'btn btn-ghost btn-sm');
    item.type = 'button';
    item.dataset.questionAction = action;
    if (icon) item.append(node('i', 'bi ' + icon));
    item.append(document.createTextNode(label));
    return item;
  }

  function field(label, input) {
    var wrapper = node('label', 'field');
    wrapper.append(node('span', '', label), input);
    return wrapper;
  }

  function textInput(value, maxLength) {
    var input = node('input', 'form-control form-control-sm');
    input.type = 'text';
    input.value = value || '';
    input.maxLength = maxLength;
    return input;
  }

  function optionEditor(question, body) {
    var list = node('div', 'poll-option-list');
    (question.options || []).forEach(function (option, optionIndex) {
      var row = node('div', 'poll-option-row');
      var input = textInput(option.label, 160);
      input.addEventListener('input', function () { option.label = input.value; renderPreview(); });
      var up = button('', 'option-up', 'bi-arrow-up');
      var down = button('', 'option-down', 'bi-arrow-down');
      var remove = button('', 'option-delete', 'bi-trash');
      up.disabled = optionIndex === 0;
      down.disabled = optionIndex === question.options.length - 1;
      up.addEventListener('click', function () { move(question.options, optionIndex, -1); renderQuestions(); });
      down.addEventListener('click', function () { move(question.options, optionIndex, 1); renderQuestions(); });
      remove.addEventListener('click', function () {
        if (question.options.length <= 2) return show('Choice questions need at least two options.', true);
        question.options.splice(optionIndex, 1); renderQuestions();
      });
      row.append(input, up, down, remove);
      list.append(row);
    });
    var add = button('Add option', 'option-add', 'bi-plus-lg');
    add.addEventListener('click', function () {
      if (question.options.length >= 50) return show('A question can have at most 50 options.', true);
      question.options.push({ id: uid(), label: 'New option' }); renderQuestions();
    });
    body.append(list, add);
  }

  function normalizeQuestion(question, previousType) {
    if (question.type === 'text') question.style = question.style || 'single_line';
    if (question.type === 'yes_no') {
      question.yes_label = question.yes_label || 'Yes';
      question.no_label = question.no_label || 'No';
    }
    if (['single_choice', 'multiple_choice'].includes(question.type) && !Array.isArray(question.options)) {
      question.options = [{ id: uid(), label: 'Option 1' }, { id: uid(), label: 'Option 2' }];
    }
    if (previousType && previousType !== question.type) renderQuestions();
  }

  function renderQuestions() {
    questionList.replaceChildren();
    state.questions.forEach(function (question, index) {
      normalizeQuestion(question);
      var card = node('article', 'poll-question-editor');
      var header = node('header');
      header.append(node('b', '', 'Question ' + (index + 1)));
      var actions = node('div', 'poll-question-actions');
      var up = button('Up', 'up', 'bi-arrow-up');
      var down = button('Down', 'down', 'bi-arrow-down');
      var duplicate = button('Duplicate', 'duplicate', 'bi-copy');
      var remove = button('Delete', 'delete', 'bi-trash');
      up.disabled = index === 0;
      down.disabled = index === state.questions.length - 1;
      up.addEventListener('click', function () { move(state.questions, index, -1); renderQuestions(); });
      down.addEventListener('click', function () { move(state.questions, index, 1); renderQuestions(); });
      duplicate.addEventListener('click', function () {
        if (state.questions.length >= 50) return show('A poll can have at most 50 questions.', true);
        var copy = JSON.parse(JSON.stringify(question));
        copy.id = uid();
        (copy.options || []).forEach(function (option) { option.id = uid(); });
        state.questions.splice(index + 1, 0, copy); renderQuestions();
      });
      remove.addEventListener('click', function () {
        if (state.questions.length <= 1) return show('A poll needs at least one question.', true);
        state.questions.splice(index, 1); renderQuestions();
      });
      actions.append(up, down, duplicate, remove);
      header.append(actions);
      var body = node('div', 'poll-question-editor-body');
      var questionInput = textInput(question.question, 300);
      questionInput.addEventListener('input', function () { question.question = questionInput.value; renderPreview(); });
      var typeSelect = node('select', 'form-select form-select-sm');
      [['text', 'Text response'], ['yes_no', 'Yes / No'], ['single_choice', 'Single choice'], ['multiple_choice', 'Multiple choice'], ['date', 'Date']].forEach(function (choice) {
        var option = node('option', '', choice[1]); option.value = choice[0]; option.selected = question.type === choice[0]; typeSelect.append(option);
      });
      typeSelect.addEventListener('change', function () { var old = question.type; question.type = typeSelect.value; normalizeQuestion(question, old); });
      var required = node('label', 'toggle-row');
      var checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = Boolean(question.required);
      checkbox.addEventListener('change', function () { question.required = checkbox.checked; renderPreview(); });
      var requiredCopy = node('span'); requiredCopy.append(node('b', '', 'Required'), node('small', '', 'Respondent must answer before submitting.'));
      required.append(checkbox, requiredCopy);
      var grid = node('div', 'poll-question-grid');
      grid.append(field('Question', questionInput), field('Response type', typeSelect), required);
      body.append(grid);
      if (question.type === 'text') {
        var style = node('select', 'form-select form-select-sm');
        [['single_line', 'Single line'], ['multi_line', 'Multiple lines']].forEach(function (choice) { var option = node('option', '', choice[1]); option.value = choice[0]; option.selected = question.style === choice[0]; style.append(option); });
        style.addEventListener('change', function () { question.style = style.value; renderPreview(); });
        body.append(field('Response style', style));
      } else if (question.type === 'yes_no') {
        var yes = textInput(question.yes_label, 80); var no = textInput(question.no_label, 80);
        yes.addEventListener('input', function () { question.yes_label = yes.value; renderPreview(); });
        no.addEventListener('input', function () { question.no_label = no.value; renderPreview(); });
        var labels = node('div', 'poll-question-grid'); labels.append(field('Positive label', yes), field('Negative label', no)); body.append(labels);
      } else if (['single_choice', 'multiple_choice'].includes(question.type)) {
        optionEditor(question, body);
      }
      card.append(header, body); questionList.append(card);
    });
    renderPreview();
  }

  function move(items, index, delta) {
    var target = index + delta;
    if (target < 0 || target >= items.length) return;
    var item = items.splice(index, 1)[0]; items.splice(target, 0, item);
  }

  function syncFields() {
    editor.querySelectorAll('[data-poll-field]').forEach(function (input) {
      state[input.dataset.pollField] = input.type === 'checkbox' ? input.checked : input.value;
    });
  }

  function renderPreview() {
    syncFields();
    window.MIFPPollRenderer.render(preview, state, { preview: true, disabled: true });
  }

  function show(message, error) {
    feedback.textContent = message;
    feedback.classList.toggle('is-error', Boolean(error));
    feedback.classList.add('is-visible');
    window.setTimeout(function () { feedback.classList.remove('is-visible'); }, 5000);
  }

  async function save() {
    syncFields();
    var result = await window.MIFP.request(editor.dataset.saveUrl, { method: 'POST', json: state });
    state = result.data.poll;
    show('Poll saved.');
    return state;
  }

  editor.querySelector('[data-poll-save]').addEventListener('click', function () {
    save().catch(function (error) { show(error.message, true); });
  });
  editor.querySelectorAll('[data-poll-field]').forEach(function (input) { input.addEventListener('input', renderPreview); });
  editor.querySelector('[data-question-add]').addEventListener('click', function () {
    if (state.questions.length >= 50) return show('A poll can have at most 50 questions.', true);
    state.questions.push({ id: uid(), type: 'text', question: 'New question', required: false, style: 'single_line' });
    renderQuestions();
  });

  editor.querySelectorAll('[data-poll-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      editor.querySelectorAll('[data-poll-tab]').forEach(function (item) { item.setAttribute('aria-selected', String(item === tab)); });
      editor.querySelectorAll('[data-poll-panel]').forEach(function (panel) { panel.hidden = panel.dataset.pollPanel !== tab.dataset.pollTab; panel.classList.toggle('is-active', !panel.hidden); });
    });
  });

  function fillSelect(select, headers, optional) {
    select.replaceChildren();
    if (optional) { var none = node('option', '', 'Not mapped'); none.value = ''; select.append(none); }
    headers.forEach(function (header, index) { var option = node('option', '', header); option.value = String(index); select.append(option); });
  }

  function refreshRecipients() {
    if (!recipientTable) return;
    var selected = {};
    editor.querySelectorAll('[data-map]').forEach(function (select) { selected[select.dataset.map] = select.value; });
    if (selected.email === '') { recipientResult = null; return; }
    recipientResult = window.MIFPRecipientFiles.recipients(recipientTable, selected);
    Object.keys(recipientResult).forEach(function (key) {
      var count = editor.querySelector('[data-count="' + key + '"]');
      if (count) count.textContent = key === 'valid' ? recipientResult.valid.length : recipientResult[key];
    });
    editor.querySelector('[data-recipient-summary]').hidden = false;
    editor.querySelector('[data-poll-send]').disabled = !smtpReady || recipientResult.valid.length === 0 || batch.running;
  }

  editor.querySelector('[data-recipient-file]').addEventListener('change', async function (event) {
    try {
      recipientTable = await window.MIFPRecipientFiles.parse(event.target.files[0]);
      var guessed = window.MIFPRecipientFiles.mapping(recipientTable);
      editor.querySelectorAll('[data-map]').forEach(function (select) {
        fillSelect(select, recipientTable.headers, select.dataset.map !== 'email');
        select.value = guessed[select.dataset.map];
        select.addEventListener('change', refreshRecipients);
      });
      editor.querySelector('[data-recipient-mapping]').hidden = false;
      refreshRecipients();
    } catch (error) { recipientTable = null; recipientResult = null; show(error.message, true); }
  });

  function progress() {
    var panel = editor.querySelector('[data-batch-progress]');
    panel.hidden = false;
    var total = batch.recipients.length;
    var complete = batch.index;
    panel.querySelector('[data-progress-title]').textContent = (batch.paused ? 'Paused ' : batch.running ? 'Sending ' : 'Finished ') + complete + ' / ' + total;
    var bar = panel.querySelector('[data-progress-bar]'); bar.max = Math.max(1, total); bar.value = complete;
    panel.querySelector('[data-progress="accepted"]').textContent = batch.accepted;
    panel.querySelector('[data-progress="failed"]').textContent = batch.failed.length;
    panel.querySelector('[data-progress="pending"]').textContent = Math.max(0, total - complete);
    panel.querySelector('[data-batch-pause]').disabled = !batch.running || batch.paused;
    panel.querySelector('[data-batch-resume]').disabled = !batch.running || !batch.paused;
    panel.querySelector('[data-batch-retry]').disabled = batch.running || batch.failed.length === 0;
  }

  function delay(milliseconds) { return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); }); }

  async function runBatch(recipients) {
    if (batch.running) return;
    var password = await window.MIFPConfirmSend();
    if (!password) return;
    try {
      batch = { running: true, paused: false, recipients: recipients.slice(), failed: [], index: 0, accepted: 0, controller: new AbortController(), started: Date.now() };
      await save();
      editor.querySelector('[data-poll-send]').disabled = true;
      progress();
      while (batch.index < batch.recipients.length && !batch.controller.signal.aborted) {
        while (batch.paused && !batch.controller.signal.aborted) await delay(200);
        if (batch.controller.signal.aborted) break;
        var recipient = batch.recipients[batch.index];
        try {
          await window.MIFP.request(editor.dataset.inviteUrl, { method: 'POST', json: Object.assign({}, recipient, { password: password }), signal: batch.controller.signal, timeout: 30000 });
          batch.accepted += 1;
        } catch (error) {
          if (error.name === 'AbortError') break;
          if (error.status === 403) {
            batch.failed.push.apply(batch.failed, batch.recipients.slice(batch.index));
            batch.index = batch.recipients.length;
            show(error.message, true);
            break;
          }
          batch.failed.push(recipient);
        }
        batch.index += 1;
        progress();
        if (batch.index < batch.recipients.length) await delay(350);
      }
      batch.running = false;
      progress();
      editor.querySelector('[data-poll-send]').disabled = !smtpReady || !recipientResult || recipientResult.valid.length === 0;
      show('Invitation batch finished: ' + batch.accepted + ' accepted, ' + batch.failed.length + ' failed.', batch.failed.length > 0);
    } finally { password = ''; }
  }

  editor.querySelector('[data-poll-send]').addEventListener('click', function () {
    if (!recipientResult || !recipientResult.valid.length || batch.running) return;
    runBatch(recipientResult.valid).catch(function (error) { batch.running = false; progress(); show(error.message, true); });
  });
  editor.querySelector('[data-batch-pause]').addEventListener('click', function () { batch.paused = true; progress(); });
  editor.querySelector('[data-batch-resume]').addEventListener('click', function () { batch.paused = false; progress(); });
  editor.querySelector('[data-batch-retry]').addEventListener('click', function () { if (batch.failed.length) runBatch(batch.failed).catch(function (error) { show(error.message, true); }); });
  window.addEventListener('pagehide', function () { if (batch.controller) batch.controller.abort(); }, { once: true });

  editor.querySelector('[data-poll-send-one]').addEventListener('click', async function () {
    var email = editor.querySelector('[data-single-email]').value.trim();
    if (!email) return show('Enter the recipient email.', true);
    var password = await window.MIFPConfirmSend();
    if (!password) return;
    var payload = { email: email, password: password };
    var firstName = editor.querySelector('[data-single-first-name]');
    var lastName = editor.querySelector('[data-single-last-name]');
    if (firstName) payload.first_name = firstName.value.trim();
    if (lastName) payload.last_name = lastName.value.trim();
    try {
      await save();
      await window.MIFP.request(editor.dataset.inviteUrl, { method: 'POST', json: payload, timeout: 30000 });
      show('Poll invitation accepted by SMTP. This is a real invitation with a secure respondent link.');
    } catch (error) { show(error.message, true); }
    finally { password = ''; delete payload.password; }
  });

  function renderAnalysis() {
    var grid = editor.querySelector('[data-analysis-grid]');
    grid.replaceChildren();

    var activity = Array.isArray(analysis.activity) ? analysis.activity : [];
    var activityCard = node('article', 'control-panel poll-analysis-card poll-analysis-activity');
    var activityHeading = node('div', 'panel-head');
    var activityCopy = node('div');
    activityCopy.append(
      node('h3', '', 'Response activity'),
      node('p', '', 'Daily first submissions and later response changes. Dates are grouped in UTC.')
    );
    activityHeading.append(activityCopy);
    activityCard.append(activityHeading);

    var activityBody = node('div', 'poll-activity-body');
    var activityKpis = node('div', 'poll-activity-kpis');
    var newTotal = activity.reduce(function (total, item) { return total + Number(item.new || 0); }, 0);
    var modifiedTotal = activity.reduce(function (total, item) { return total + Number(item.modified || 0); }, 0);
    var lastActivity = activity.length ? activity[activity.length - 1].date : '—';
    [
      ['First submissions', newTotal],
      ['Later changes', modifiedTotal],
      ['Active days', activity.length],
      ['Last activity', lastActivity]
    ].forEach(function (item) {
      var stat = node('span', 'poll-activity-kpi');
      stat.append(node('small', '', item[0]), node('b', '', String(item[1])));
      activityKpis.append(stat);
    });
    activityBody.append(activityKpis);

    if (activity.length) {
      var activityLayout = node('div', 'poll-activity-layout');
      var chartWrap = node('div', 'poll-activity-chart');
      var activityCanvas = document.createElement('canvas');
      activityCanvas.height = 210;
      chartWrap.append(activityCanvas);

      var tableWrap = node('div', 'poll-activity-table-wrap');
      var table = node('table', 'poll-activity-table');
      var thead = document.createElement('thead');
      var headRow = document.createElement('tr');
      ['Date', 'New', 'Changes', 'Events'].forEach(function (label) { headRow.append(node('th', '', label)); });
      thead.append(headRow);
      var tbody = document.createElement('tbody');
      activity.slice().reverse().forEach(function (item) {
        var row = document.createElement('tr');
        var total = Number(item.new || 0) + Number(item.modified || 0);
        [item.date, item.new, item.modified, total].forEach(function (value, index) {
          var cell = node('td', index === 0 ? 'poll-activity-date' : '', String(value));
          row.append(cell);
        });
        tbody.append(row);
      });
      table.append(thead, tbody);
      tableWrap.append(table);
      activityLayout.append(chartWrap, tableWrap);
      activityBody.append(activityLayout);

      window.setTimeout(function () {
        new Chart(activityCanvas, {
          type: 'bar',
          data: {
            labels: activity.map(function (item) { return item.date; }),
            datasets: [
              { label: 'First submissions', data: activity.map(function (item) { return item.new; }), backgroundColor: dashboardColor('--accent', '#a72b31'), borderRadius: 3 },
              { label: 'Later changes', data: activity.map(function (item) { return item.modified; }), backgroundColor: dashboardColor('--text-3', '#626b75'), borderRadius: 3 }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } } },
            scales: {
              x: { stacked: false, grid: { display: false } },
              y: { beginAtZero: true, ticks: { precision: 0 } }
            }
          }
        });
      }, 0);
    } else {
      activityBody.append(node('p', 'poll-activity-empty', 'No response activity yet. Daily activity will appear after the first submitted response.'));
    }
    activityCard.append(activityBody);
    grid.append(activityCard);

    analysis.questions.forEach(function (question, index) {
      var card = node('article', 'control-panel poll-analysis-card');
      var heading = node('div', 'panel-head');
      var copy = node('div'); copy.append(node('h3', '', (index + 1) + '. ' + question.question), node('p', '', question.answered + ' answered · ' + question.empty + ' empty')); heading.append(copy); card.append(heading);
      if (question.type === 'text') {
        var list = node('div', 'poll-text-responses');
        (question.responses || []).forEach(function (response) { list.append(node('p', '', response)); });
        if (!question.responses || !question.responses.length) list.append(node('p', '', 'No text responses yet.'));
        card.append(list);
      } else {
        var canvas = document.createElement('canvas'); canvas.height = 210; card.append(canvas);
        if (question.type === 'multiple_choice') card.append(node('small', 'poll-analysis-note', 'Percentages use respondents as the denominator and may total more than 100%.'));
        window.setTimeout(function () {
          new Chart(canvas, { type: 'bar', data: { labels: question.options.map(function (item) { return item.label; }), datasets: [{ data: question.options.map(function (item) { return item.count; }), backgroundColor: dashboardColor('--accent', '#a72b31'), borderRadius: 3 }] }, options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { afterLabel: function (context) { return question.options[context.dataIndex].percentage + '%'; } } } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } } });
        }, 0);
      }
      grid.append(card);
    });
  }

  function formatTimestamp(value) {
    if (!value) return '—';
    return String(value).replace('T', ' ').slice(0, 19) + ' UTC';
  }

  function renderResponseHistory(container, history) {
    container.replaceChildren();
    var summary = node('div', 'poll-history-summary');
    summary.append(
      node('span', '', 'First submitted: ' + formatTimestamp(history.first_submitted_at)),
      node('span', '', 'Last modified: ' + formatTimestamp(history.last_modified_at)),
      node('span', '', 'Revisions: ' + history.revision_count)
    );
    container.append(summary);
    if (history.history_compacted) {
      container.append(node('div', 'poll-history-compacted', 'Older revision contents were compacted; activity metadata is preserved.'));
      if (history.revision_timestamps && history.revision_timestamps.length > 1) {
        var timeline = node('div', 'poll-history-timeline');
        timeline.append(node('b', '', 'Retained revision activity'));
        history.revision_timestamps.forEach(function (value, index) {
          timeline.append(node('span', '', 'Revision ' + (index + 1) + ' · ' + formatTimestamp(value)));
        });
        container.append(timeline);
      }
    }
    var questions = {};
    (history.questions || []).forEach(function (item) { questions[item.id] = item.question; });
    (history.revisions || []).forEach(function (revision) {
      var card = node('article', 'poll-history-revision');
      var head = node('header');
      head.append(node('b', '', 'Revision ' + revision.revision), node('small', '', formatTimestamp(revision.submitted_at)));
      card.append(head);
      if (revision.changed_questions && revision.changed_questions.length) {
        card.append(node('p', 'poll-history-changed', 'Changed: ' + revision.changed_questions.join(', ')));
      } else if (revision.revision === 1) {
        card.append(node('p', 'poll-history-changed', 'Initial submission'));
      }
      var list = node('dl', 'poll-history-answers');
      Object.keys(questions).forEach(function (qid) {
        list.append(node('dt', '', questions[qid]), node('dd', '', revision.display_answers[qid] || '—'));
      });
      card.append(list);
      container.append(card);
    });
  }

  editor.querySelectorAll('[data-response-history]').forEach(function (control) {
    control.addEventListener('click', async function () {
      var row = document.getElementById(control.dataset.target);
      if (!row) return;
      if (!row.hidden) { row.hidden = true; return; }
      var container = row.querySelector('[data-response-history-content]');
      row.hidden = false;
      if (row.dataset.loaded === '1') return;
      container.replaceChildren(node('p', '', 'Loading response history…'));
      control.disabled = true;
      try {
        var result = await window.MIFP.request(control.dataset.url, { method: 'GET', timeout: 15000 });
        renderResponseHistory(container, result.data);
        row.dataset.loaded = '1';
      } catch (error) {
        container.replaceChildren(node('p', 'poll-history-error', error.message));
      } finally { control.disabled = false; }
    });
  });


  editor.querySelector('[data-response-search]')?.addEventListener('input', function (event) {
    var query = event.target.value.trim().toLowerCase();
    editor.querySelectorAll('[data-response-main-row]').forEach(function (row) {
      var hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
      row.hidden = hidden;
      var history = row.nextElementSibling;
      if (hidden && history && history.matches('[data-response-history-row]')) history.hidden = true;
    });
  });
  editor.querySelectorAll('[data-response-sort]').forEach(function (control) {
    control.addEventListener('click', function () {
      var body = editor.querySelector('[data-response-rows]');
      var index = Number(control.dataset.responseSort);
      var direction = control.dataset.direction === 'asc' ? 'desc' : 'asc';
      control.dataset.direction = direction;
      var pairs = Array.from(body.querySelectorAll('[data-response-main-row]')).map(function (row) {
        return { row: row, history: row.nextElementSibling && row.nextElementSibling.matches('[data-response-history-row]') ? row.nextElementSibling : null };
      });
      pairs.sort(function (left, right) {
        var leftCell = left.row.children[index];
        var rightCell = right.row.children[index];
        var leftValue = leftCell ? (leftCell.dataset.sort || leftCell.textContent).trim() : '';
        var rightValue = rightCell ? (rightCell.dataset.sort || rightCell.textContent).trim() : '';
        return leftValue.localeCompare(rightValue, undefined, { numeric: true }) * (direction === 'asc' ? 1 : -1);
      }).forEach(function (pair) {
        body.append(pair.row);
        if (pair.history) body.append(pair.history);
      });
    });
  });


  editor.querySelectorAll('[data-lifecycle]').forEach(function (control) {
    control.addEventListener('click', async function () {
      var action = control.dataset.lifecycle;
      if (action === 'clean') {
        if (!window.confirm('Remove obsolete technical invitation metadata? Response history will not be changed.')) return;
      } else {
        var typed = window.prompt('This action is irreversible. Type the poll title to continue:\n\n' + state.title);
        if (typed !== state.title) return show('Confirmation did not match the poll title.', true);
      }
      control.disabled = true;
      try {
        var result = await window.MIFP.request(control.dataset.url, { method: 'POST', json: {} });
        if (result.data.redirect) window.location.assign(result.data.redirect);
        else window.location.reload();
      } catch (error) { control.disabled = false; show(error.message, true); }
    });
  });

  renderQuestions();
  renderAnalysis();
})();
