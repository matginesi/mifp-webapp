"""Shared-link poll reports: one locked snapshot, existing export libraries."""
from __future__ import annotations

import csv
import io

from . import simple_polls as store
from .exporters import MIFP_EXPORT_COLORS, rows_to_pdf


def _analysis_rows(analysis):
    rows = []
    for index, q in enumerate(analysis['questions'], 1):
        def add(metric, count):
            percentage = round(count * 100 / analysis['responses'], 1) if analysis['responses'] else 0
            rows.append({'Question': f'{index}. {q["question"]}', 'Metric': metric,
                         'Count': count, 'Percent of submissions': percentage})
        add('Answered', q['answered'])
        add('Unanswered', q['unanswered'])
        if q['type'] == 'date_availability':
            for day, totals in q['date_counts'].items():
                for category, count in totals.items():
                    add(f'{day}: {category.title()}', count)
        else:
            for option in q['distribution']:
                add(option['label'], option['count'])
    return rows


def export_xlsx(config_dir, poll_id):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    poll, rows = store.results(config_dir, poll_id)
    analysis = store.summary(config_dir, poll_id)
    book = Workbook()
    overview = book.active
    overview.title = 'Overview'
    for row in [('MIFP Poll report', ''), ('Title', poll['title']), ('Mode', poll['response_mode']),
                ('Status', poll['status']), ('Deadline', poll['deadline']), ('Responses', analysis['responses']),
                ('Percentages', 'Denominator: all submissions. Multiple-choice percentages may total more than 100%.'),
                ('Activity', 'Not stored in aggregate-only mode.' if poll.get('aggregate_only') else 'Daily UTC totals; no additional respondent tracking.')]:
        overview.append([store._csv_cell(v) if isinstance(v, str) else v for v in row])
    responses = book.create_sheet('Counts' if poll.get('aggregate_only') else 'Responses')
    for row in csv.reader(io.StringIO(store.export_csv(config_dir, poll_id).decode('utf-8-sig'))):
        responses.append(row)
    sheet = book.create_sheet('Analysis')
    headers = ['Question', 'Metric', 'Count', 'Percent of submissions']
    sheet.append(headers)
    for row in _analysis_rows(analysis):
        sheet.append([store._csv_cell(row[key]) if isinstance(row[key], str) else row[key] for key in headers])
    sheet.append([])
    sheet.append(['Response activity (UTC)', 'Submissions'])
    for bucket in analysis['activity']:
        sheet.append([bucket['date'], bucket['count']])
    for sheet in book:
        sheet.freeze_panes = 'A2'
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor=MIFP_EXPORT_COLORS['red'])
        for col in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col)].width = 30 if col < 3 else 20
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def export_pdf(config_dir, poll_id):
    poll, rows = store.results(config_dir, poll_id)
    analysis = store.summary(config_dir, poll_id)
    report = [{'Question': 'Overview', 'Metric': 'Total submissions', 'Count': analysis['responses'], 'Percent of submissions': ''},
              {'Question': 'Percentages', 'Metric': 'All submissions are the denominator; multiple-choice percentages may total more than 100%.', 'Count': '', 'Percent of submissions': ''}]
    report.extend(_analysis_rows(analysis))
    report.extend({'Question': 'Daily activity (UTC)', 'Metric': item['date'], 'Count': item['count'],
                   'Percent of submissions': ''} for item in analysis['activity'])
    return rows_to_pdf(report, title=f'MIFP Poll analytics — {poll["title"]}')
