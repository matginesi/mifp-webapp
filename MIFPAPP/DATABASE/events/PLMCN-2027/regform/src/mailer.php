<?php

declare(strict_types=1);

if (isset($_SERVER['SCRIPT_FILENAME']) && realpath((string)$_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    http_response_code(404);
    exit;
}

function safe_header_text(string $value): string
{
    return trim(str_replace(["\r", "\n"], '', $value));
}

function validate_mailbox(string $email): string
{
    $email = safe_header_text($email);
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        throw new RuntimeException('Invalid configured email address.');
    }
    return $email;
}

function encoded_subject(string $subject): string
{
    return '=?UTF-8?B?' . base64_encode(safe_header_text($subject)) . '?=';
}

function mailbox_header(string $email, string $name = ''): string
{
    $email = validate_mailbox($email);
    $name = safe_header_text($name);
    if ($name === '') {
        return '<' . $email . '>';
    }
    return encoded_subject($name) . ' <' . $email . '>';
}

function configured_admin_emails(array $mailSettings): array
{
    $emails = $mailSettings['admin_emails'] ?? [];
    if (!is_array($emails)) {
        $emails = [$emails];
    }
    $valid = [];
    foreach ($emails as $email) {
        if (is_string($email) && $email !== '') {
            $valid[] = validate_mailbox($email);
        }
    }
    $valid = array_values(array_unique($valid));
    if ($valid === []) {
        throw new RuntimeException('No organizer email addresses are configured.');
    }
    return $valid;
}

function send_simple_html_mail(array $recipients, string $subject, string $html, string $text, array $mailSettings, $replyTo = null): bool
{
    $fromEmail = validate_mailbox((string)($mailSettings['from_email'] ?? ''));
    $fromName = (string)($mailSettings['from_name'] ?? 'MIFP');
    $to = implode(', ', array_map('validate_mailbox', $recipients));

    $boundary = 'alt_' . bin2hex(random_bytes(18));
    $headers = [
        'MIME-Version: 1.0',
        'From: ' . mailbox_header($fromEmail, $fromName),
        'Content-Type: multipart/alternative; boundary="' . $boundary . '"',
        'X-Mailer: MIFP Registration',
    ];
    if ($replyTo !== null && $replyTo !== '') {
        $headers[] = 'Reply-To: ' . mailbox_header($replyTo);
    }

    $message = '--' . $boundary . "\r\n"
        . "Content-Type: text/plain; charset=UTF-8\r\n"
        . "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        . quoted_printable_encode($text) . "\r\n"
        . '--' . $boundary . "\r\n"
        . "Content-Type: text/html; charset=UTF-8\r\n"
        . "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        . quoted_printable_encode($html) . "\r\n"
        . '--' . $boundary . "--\r\n";

    return mail($to, encoded_subject($subject), $message, implode("\r\n", $headers));
}

function send_admin_mail_with_attachment(array $recipients, string $subject, string $html, string $text, array $mailSettings, string $replyTo, array $upload, string $receiptId): bool
{
    $fromEmail = validate_mailbox((string)($mailSettings['from_email'] ?? ''));
    $fromName = (string)($mailSettings['from_name'] ?? 'MIFP');
    $to = implode(', ', array_map('validate_mailbox', $recipients));

    $mixed = 'mixed_' . bin2hex(random_bytes(18));
    $alt = 'alt_' . bin2hex(random_bytes(18));
    $attachmentName = 'proof-' . preg_replace('/[^A-Z0-9-]/', '', strtoupper($receiptId)) . '.' . $upload['extension'];

    $headers = [
        'MIME-Version: 1.0',
        'From: ' . mailbox_header($fromEmail, $fromName),
        'Reply-To: ' . mailbox_header($replyTo),
        'Content-Type: multipart/mixed; boundary="' . $mixed . '"',
        'X-Mailer: MIFP Registration',
    ];

    $fileData = file_get_contents($upload['tmp_path']);
    if ($fileData === false) {
        throw new RuntimeException('Proof of payment could not be read for email delivery.');
    }

    $message = '--' . $mixed . "\r\n"
        . 'Content-Type: multipart/alternative; boundary="' . $alt . "\"\r\n\r\n"
        . '--' . $alt . "\r\n"
        . "Content-Type: text/plain; charset=UTF-8\r\n"
        . "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        . quoted_printable_encode($text) . "\r\n"
        . '--' . $alt . "\r\n"
        . "Content-Type: text/html; charset=UTF-8\r\n"
        . "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        . quoted_printable_encode($html) . "\r\n"
        . '--' . $alt . "--\r\n"
        . '--' . $mixed . "\r\n"
        . 'Content-Type: ' . $upload['mime'] . '; name="' . $attachmentName . "\"\r\n"
        . "Content-Transfer-Encoding: base64\r\n"
        . 'Content-Disposition: attachment; filename="' . $attachmentName . "\"\r\n\r\n"
        . chunk_split(base64_encode($fileData), 76, "\r\n")
        . '--' . $mixed . "--\r\n";

    return mail($to, encoded_subject($subject), $message, implode("\r\n", $headers));
}

function build_summary_rows(array $data): array
{
    $labels = [
        'first_name' => 'First name',
        'last_name' => 'Last name',
        'email' => 'Email',
        'affiliation' => 'Affiliation / Institution / Company',
        'country' => 'Country',
        'address' => 'Full address',
        'arrival_date' => 'Arrival date',
        'departure_date' => 'Departure date',
        'tshirt_size' => 'T-shirt size',
        'dietary_choice' => 'Dietary choice',
        'dietary_notes' => 'Dietary notes',
        'registration_type' => 'Registration type',
        'payment_method' => 'Payment method',
    ];
    $rows = [];
    foreach ($labels as $key => $label) {
        if (!array_key_exists($key, $data)) continue;
        $value = is_bool($data[$key]) ? ($data[$key] ? 'Yes' : 'No') : trim((string)$data[$key]);
        if ($value === '' && in_array($key, ['tshirt_size', 'dietary_choice', 'dietary_notes'], true)) continue;
        $rows[$label] = $value !== '' ? $value : '—';
    }
    return $rows;
}

function email_html(string $event, string $heading, string $receiptId, array $data, string $note): string
{
    $rows = '';
    foreach (build_summary_rows($data) as $label => $value) {
        $rows .= '<tr><th style="text-align:left;padding:8px 10px;border-bottom:1px solid #dde3ec;color:#13213c;vertical-align:top">'
            . h($label)
            . '</th><td style="padding:8px 10px;border-bottom:1px solid #dde3ec;color:#263246;white-space:pre-line">'
            . h((string)$value)
            . '</td></tr>';
    }

    return '<!doctype html><html><body style="margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#263246">'
        . '<div style="max-width:680px;margin:24px auto;background:#fff;border:1px solid #dde3ec">'
        . '<div style="padding:22px;border-top:4px solid #b5122b"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#b5122b;font-weight:700">MIFP Registration</div>'
        . '<h1 style="margin:6px 0 4px;font-size:24px;color:#111827">' . h($event) . '</h1>'
        . '<h2 style="margin:0 0 12px;font-size:18px;color:#13213c">' . h($heading) . '</h2>'
        . '<p style="margin:0;color:#637086">Receipt ID: <strong>' . h($receiptId) . '</strong></p></div>'
        . '<table style="width:100%;border-collapse:collapse;font-size:14px">' . $rows . '</table>'
        . '<div style="padding:18px 22px;color:#637086;font-size:13px"><p style="margin:0">' . h($note) . '</p></div>'
        . '</div></body></html>';
}

function email_text(string $event, string $heading, string $receiptId, array $data, string $note): string
{
    $lines = [$event, $heading, 'Receipt ID: ' . $receiptId, ''];
    foreach (build_summary_rows($data) as $label => $value) {
        $lines[] = $label . ': ' . str_replace("\n", ' / ', (string)$value);
    }
    $lines[] = '';
    $lines[] = $note;
    return implode("\n", $lines);
}

function confirmation_email_html(string $event, array $data, string $note): string
{
    $rows = '';
    foreach (build_summary_rows($data) as $label => $value) {
        $rows .= '<tr><th style="text-align:left;padding:8px 10px;border-bottom:1px solid #dde3ec;color:#13213c;vertical-align:top">'
            . h($label)
            . '</th><td style="padding:8px 10px;border-bottom:1px solid #dde3ec;color:#263246;white-space:pre-line">'
            . h((string)$value)
            . '</td></tr>';
    }

    return '<!doctype html><html><body style="margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#263246">'
        . '<div style="max-width:680px;margin:24px auto;background:#fff;border:1px solid #dde3ec">'
        . '<div style="padding:22px;border-top:4px solid #b5122b"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#b5122b;font-weight:700">MIFP Registration</div>'
        . '<h1 style="margin:6px 0 4px;font-size:24px;color:#111827">' . h($event) . '</h1>'
        . '<h2 style="margin:0 0 12px;font-size:18px;color:#13213c">Registration confirmation</h2>'
        . '<p style="margin:0;color:#637086">Your registration has been received.</p></div>'
        . '<table style="width:100%;border-collapse:collapse;font-size:14px">' . $rows . '</table>'
        . '<div style="padding:18px 22px;color:#637086;font-size:13px"><p style="margin:0">' . h($note) . '</p></div>'
        . '</div></body></html>';
}

function confirmation_email_text(string $event, array $data, string $note): string
{
    $lines = [$event, 'Registration confirmation', 'Your registration has been received.', ''];
    foreach (build_summary_rows($data) as $label => $value) {
        $lines[] = $label . ': ' . str_replace("\n", ' / ', (string)$value);
    }
    $lines[] = '';
    $lines[] = $note;
    return implode("\n", $lines);
}
