(function () {
  'use strict';

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render(container, poll, options) {
    options = options || {};
    container.replaceChildren();
    var header = element('header', 'poll-public-head');
    header.append(element('span', 'poll-public-brand', options.preview ? 'MIFP Poll · Preview' : 'MIFP Poll'));
    header.append(element('h1', '', poll.title || 'Poll'));
    if (poll.description) header.append(element('p', '', poll.description));
    if (poll.deadline) header.append(element('small', '', 'Deadline: ' + String(poll.deadline).slice(0, 10)));
    container.append(header);
    var answers = poll.current_answers || {};
    (poll.questions || []).forEach(function (question, index) {
      var fieldset = element('fieldset', 'poll-public-question');
      fieldset.dataset.questionId = question.id;
      fieldset.dataset.questionType = question.type;
      var legend = element('legend');
      legend.append(element('span', 'poll-question-number', String(index + 1)));
      legend.append(document.createTextNode(question.question));
      if (question.required) legend.append(element('small', 'poll-required', 'Required'));
      fieldset.append(legend);
      var name = 'poll-' + question.id;
      var current = answers[question.id];
      if (question.type === 'text') {
        var textInput = document.createElement(question.style === 'multi_line' ? 'textarea' : 'input');
        if (textInput.tagName === 'INPUT') textInput.type = 'text';
        else textInput.rows = 4;
        textInput.className = 'form-control';
        textInput.name = name;
        textInput.maxLength = 5000;
        textInput.value = typeof current === 'string' ? current : '';
        textInput.required = Boolean(question.required);
        fieldset.append(textInput);
      } else if (question.type === 'date') {
        var dateInput = element('input', 'form-control');
        dateInput.type = 'date';
        dateInput.name = name;
        dateInput.value = typeof current === 'string' ? current : '';
        dateInput.required = Boolean(question.required);
        fieldset.append(dateInput);
      } else {
        var choices = question.type === 'yes_no'
          ? [{ id: 'yes', label: question.yes_label || 'Yes' }, { id: 'no', label: question.no_label || 'No' }]
          : (question.options || []);
        var choiceList = element('div', 'poll-public-choices');
        choices.forEach(function (choice) {
          var label = element('label', 'poll-public-choice');
          var input = document.createElement('input');
          input.type = question.type === 'multiple_choice' ? 'checkbox' : 'radio';
          input.name = name;
          input.value = choice.id;
          input.required = Boolean(question.required) && input.type === 'radio';
          input.checked = Array.isArray(current) ? current.includes(choice.id) : current === choice.id;
          label.append(input, element('span', '', choice.label));
          choiceList.append(label);
        });
        fieldset.append(choiceList);
      }
      if (options.disabled) {
        fieldset.querySelectorAll('input, textarea').forEach(function (input) { input.disabled = true; });
      }
      container.append(fieldset);
    });
  }

  function answers(container) {
    var values = {};
    container.querySelectorAll('[data-question-id]').forEach(function (fieldset) {
      var questionId = fieldset.dataset.questionId;
      var type = fieldset.dataset.questionType;
      if (type === 'multiple_choice') {
        values[questionId] = Array.from(fieldset.querySelectorAll('input:checked')).map(function (input) { return input.value; });
      } else if (type === 'single_choice' || type === 'yes_no') {
        var checked = fieldset.querySelector('input:checked');
        values[questionId] = checked ? checked.value : '';
      } else {
        var input = fieldset.querySelector('input, textarea');
        values[questionId] = input ? input.value : '';
      }
    });
    return values;
  }

  window.MIFPPollRenderer = Object.freeze({ render: render, answers: answers });
})();
