<?php

declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/src/bootstrap.php';
require_once __DIR__ . '/src/mailer.php';

$styleNonce = base64_encode(random_bytes(18));
issue_security_headers($styleNonce);
start_secure_session();

$fatalError = null;
$settings = [];
$storagePath = '';
try {
    $settings = load_settings(dirname(__DIR__) . '/conference.yaml', __DIR__ . '/settings.yaml');
    if (!bool_setting($settings['conference']['registration_visible'] ?? true, true)) {
        header('Location: ../registration-tbd.html', true, 302);
        exit;
    }
    $storagePath = resolve_storage_path(__DIR__, (string)$settings['storage']['path']);
    ensure_storage($storagePath);
} catch (Throwable $e) {
    error_log('[MIFP registration] bootstrap failed: ' . $e->getMessage());
    $fatalError = 'Registration is temporarily unavailable because the server configuration is incomplete.';
}

$errors = [];
$values = [];
$success = $_SESSION['registration_success'] ?? null;
unset($_SESSION['registration_success']);

if (!isset($_SESSION['form_started_at'])) {
    $_SESSION['form_started_at'] = time();
}

if ($fatalError === null && ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
    try {
        if (!bool_setting($settings['conference']['registration_open'] ?? false)) {
            throw new DomainException('Online registration is currently closed.');
        }

        if (!validate_csrf($_POST['csrf_token'] ?? null)) {
            throw new DomainException('This form session has expired. Reload the page and try again.');
        }

        if (clean_text($_POST['company_website'] ?? '', 200) !== '') {
            throw new DomainException('Registration could not be submitted.');
        }

        $minimumSeconds = max(0, (int)($settings['security']['minimum_fill_seconds'] ?? 2));
        $startedAt = (int)($_SESSION['form_started_at'] ?? time());
        if ((time() - $startedAt) < $minimumSeconds) {
            throw new DomainException('Registration was submitted too quickly. Please review the form and try again.');
        }

        rate_limit_or_throw($storagePath, $settings['security']);
        list($values, $errors) = validate_form($_POST, $settings);
        if ($errors !== []) {
            throw new DomainException('Please correct the highlighted fields.');
        }

        $maxUploadMb = max(1, min(20, (int)($settings['form']['max_upload_mb'] ?? 5)));
        $proofField = form_field_definition($settings, 'proof_of_payment');
        $upload = null;
        if ($proofField !== null) {
            try {
                $upload = validate_upload($_FILES, $maxUploadMb * 1024 * 1024);
            } catch (DomainException $e) {
                $errors['proof_of_payment'] = $e->getMessage();
                throw new DomainException('Please correct the highlighted fields.');
            }
        }

        $receiptId = make_receipt_id();
        $event = (string)$settings['conference']['event'];
        $prefix = safe_header_text((string)($settings['mail']['subject_prefix'] ?? '[MIFP]'));
        $adminRecipients = configured_admin_emails($settings['mail']);

        // Store the registration and proof of payment first. Email delivery is
        // intentionally secondary: SMTP trouble must never discard a valid
        // registration that has already reached the server.
        $storedUpload = $upload;
        if (bool_setting($settings['storage']['persist_submissions'] ?? true, true)) {
            try {
                if (is_array($upload)) {
                    $storedUpload = store_proof_of_payment($storagePath, $upload, $receiptId);
                }
                $record = $values;
                $record['receipt_id'] = $receiptId;
                $record['submitted_at'] = gmdate(DATE_ATOM);
                if (is_array($storedUpload)) {
                    $record['proof_file'] = (string)$storedUpload['stored_filename'];
                    $record['proof_type'] = (string)$storedUpload['label'];
                    $record['proof_size_bytes'] = (int)$storedUpload['size'];
                }
                $record['privacy_accepted'] = !empty($values['privacy_acceptance']);
                unset($record['privacy_acceptance']);
                persist_submission_csv($storagePath, $record);
            } catch (Throwable $e) {
                if (is_array($storedUpload) && !empty($storedUpload['stored_path']) && is_file((string)$storedUpload['stored_path'])) {
                    @unlink((string)$storedUpload['stored_path']);
                }
                error_log('[MIFP registration][' . $receiptId . '] persistence failed: ' . $e->getMessage());
                throw new DomainException('Registration could not be saved. Please try again or contact the organizers.');
            }
        }

        $adminNote = is_array($storedUpload)
            ? 'Proof of payment is attached and has also been saved with the registration record.'
            : 'The registration has been saved with the registration record.';
        $adminHtml = email_html($event, 'New registration', $receiptId, $values, $adminNote);
        $adminText = email_text($event, 'New registration', $receiptId, $values, $adminNote);
        $adminMailOk = false;
        try {
            $subject = trim($prefix . ' ' . $event . ' - New registration - ' . $receiptId);
            if (is_array($storedUpload)) {
                $adminMailOk = send_admin_mail_with_attachment(
                    $adminRecipients,
                    $subject,
                    $adminHtml,
                    $adminText,
                    $settings['mail'],
                    (string)$values['email'],
                    $storedUpload,
                    $receiptId
                );
            } else {
                $adminMailOk = send_simple_html_mail(
                    $adminRecipients,
                    $subject,
                    $adminHtml,
                    $adminText,
                    $settings['mail'],
                    (string)$values['email']
                );
            }
        } catch (Throwable $e) {
            error_log('[MIFP registration][' . $receiptId . '] organizer email error: ' . $e->getMessage());
        }
        if (!$adminMailOk) {
            error_log('[MIFP registration][' . $receiptId . '] organizer email failed; registration remains stored locally');
        }

        $userMailOk = false;
        if (bool_setting($settings['mail']['send_user_confirmation'] ?? true, true)) {
            try {
                $contact = (string)$settings['conference']['contact_email'];
                $userNote = 'Your registration has been received. The organizers will contact you if any additional information is required.';
                $userHtml = confirmation_email_html($event, $values, $userNote);
                $userText = confirmation_email_text($event, $values, $userNote);
                $userMailOk = send_simple_html_mail(
                    [$values['email']],
                    trim($prefix . ' ' . $event . ' - Registration confirmation'),
                    $userHtml,
                    $userText,
                    $settings['mail'],
                    $contact
                );
            } catch (Throwable $e) {
                error_log('[MIFP registration][' . $receiptId . '] participant confirmation email error: ' . $e->getMessage());
            }
        }
        if (!$userMailOk) {
            error_log('[MIFP registration][' . $receiptId . '] participant confirmation email failed');
        }

        $_SESSION['registration_success'] = [
            'email' => $values['email'],
            'user_mail_sent' => $userMailOk,
            'summary' => build_summary_rows($values),
        ];
        reset_form_security_state();

        $self = strtok((string)($_SERVER['REQUEST_URI'] ?? 'index.php'), '?');
        header('Location: ' . ($self ?: 'index.php') . '?submitted=1', true, 303);
        exit;
    } catch (DomainException $e) {
        $errors['_form'] = $e->getMessage();
    } catch (Throwable $e) {
        error_log('[MIFP registration] submission failed: ' . $e->getMessage());
        $errors['_form'] = 'Registration could not be completed. Please try again or contact the organizers.';
    }
}

$conference = $settings['conference'] ?? [];
$form = $settings['form'] ?? [];
$content = $settings['content'] ?? [];
$registrationOpen = $fatalError === null && bool_setting($conference['registration_open'] ?? false);
$formSections = is_array($settings['form_sections'] ?? null) ? $settings['form_sections'] : [];
$tshirtEnabled = bool_setting($form['tshirt_enabled'] ?? true, true);
$maxUploadMb = max(1, min(20, (int)($form['max_upload_mb'] ?? 5)));
$event = (string)($conference['event'] ?? 'MIFP Conference');
$payment = is_array($content['payment'] ?? null) ? $content['payment'] : [];
$paymentMethodsDetail = is_array($content['payment_methods_detail'] ?? null) ? $content['payment_methods_detail'] : [];
$privacyNotice = is_array($content['privacy_notice'] ?? null) ? $content['privacy_notice'] : [];
$paymentUrl = trim((string)($payment['url'] ?? ''));
if (!filter_var($paymentUrl, FILTER_VALIDATE_URL) || strtolower((string)parse_url($paymentUrl, PHP_URL_SCHEME)) !== 'https') {
    $paymentUrl = '';
}
$branding = is_array($settings['branding'] ?? null) ? $settings['branding'] : [];
$conferenceLogo = regform_asset_url($branding['conference_logo'] ?? '', 'assets/conference-logo.svg');
$organizerLogo = regform_asset_url($branding['organizer_logo'] ?? '', 'assets/mifp-logo.png');
$clientConfig = [
    'appearance' => $settings['appearance'] ?? [],
    'runtime' => $settings['runtime'] ?? [],
    'form' => [
        'max_upload_mb' => $maxUploadMb,
        'registration_open' => $registrationOpen,
        'has_errors' => $errors !== [],
        'success' => is_array($success),
    ],
];

function old_value(string $name, array $values): string
{
    if (array_key_exists($name, $values)) {
        return h((string)$values[$name]);
    }
    return h(clean_text($_POST[$name] ?? '', 500, $name === 'address' || $name === 'dietary_notes'));
}

function selected_value(string $name, string $option, array $values): string
{
    $value = array_key_exists($name, $values) ? (string)$values[$name] : (string)($_POST[$name] ?? '');
    return hash_equals($value, $option) ? ' selected' : '';
}

function regform_asset_url($value, string $fallback): string
{
    $path = trim((string)$value);
    if ($path === '' || mifp_str_contains($path, '..') || mifp_str_contains($path, '\\') || preg_match('/^[A-Za-z][A-Za-z0-9+.-]*:/', $path) === 1 || mifp_str_starts_with($path, '/')) {
        return $fallback;
    }
    return '../' . ltrim($path, '/');
}

function css_identifier(string $value): string
{
    $safe = preg_replace('/[^A-Za-z0-9_-]/', '', $value) ?? '';
    return $safe !== '' ? $safe : 'default';
}

function css_color($value): string
{
    $color = trim((string)$value);
    return preg_match('/^#[0-9A-Fa-f]{6}$/', $color) === 1 ? $color : '';
}

function css_dimension($value, string $fallback): string
{
    $raw = trim((string)$value);
    if (preg_match('/^\d+(?:\.\d+)?(?:px|rem|em|%)$/i', $raw) === 1) return $raw;
    if (preg_match('/^\d+(?:\.\d+)?$/', $raw) === 1) return $raw . 'px';
    return $fallback;
}

function css_contrast_text(string $hex): string
{
    if (preg_match('/^#([0-9A-Fa-f]{6})$/', $hex, $match) !== 1) return '#ffffff';
    $raw = $match[1];
    $channels = [];
    foreach ([0, 2, 4] as $i) {
        $v = hexdec(substr($raw, $i, 2)) / 255;
        $channels[] = $v <= 0.03928 ? $v / 12.92 : (($v + 0.055) / 1.055) ** 2.4;
    }
    $luminance = 0.2126 * $channels[0] + 0.7152 * $channels[1] + 0.0722 * $channels[2];
    return $luminance > 0.43 ? '#111827' : '#ffffff';
}

function build_appearance_css(array $appearance): string
{
    $rules = [];
    $rules[] = 'html{--content-max:' . css_dimension($appearance['max_content_width'] ?? '1080px', '1080px') . ';--radius-sm:' . css_dimension($appearance['control_radius'] ?? '6px', '6px') . ';--radius:' . css_dimension($appearance['component_radius'] ?? '8px', '8px') . ';--radius-lg:' . css_dimension($appearance['panel_radius'] ?? '10px', '10px') . '}';
    $themeMap = [
        'bg' => '--bg', 'bg_alt' => '--bg-alt', 'bg_card' => '--card', 'nav_bg' => '--nav-bg',
        'nav_hover' => '--nav-hover', 'border' => '--border', 'border_light' => '--border-light',
        'text' => '--text', 'text_muted' => '--muted', 'text_dim' => '--dim', 'text_heading' => '--heading',
    ];
    foreach (($appearance['themes'] ?? []) as $theme) {
        if (!is_array($theme)) continue;
        $id = css_identifier((string)($theme['id'] ?? ''));
        $vars = [];
        foreach ($themeMap as $source => $target) {
            $color = css_color($theme[$source] ?? '');
            if ($color !== '') $vars[] = $target . ':' . $color;
        }
        $scheme = strtolower((string)($theme['color_scheme'] ?? 'light')) === 'dark' ? 'dark' : 'light';
        $vars[] = 'color-scheme:' . $scheme;
        // Palette secondary colors are sometimes intentionally dark (for example MIFP navy).
        // Use a theme-aware text token so those accents never become unreadable on dark surfaces.
        $vars[] = '--secondary-ink:' . ($scheme === 'dark' ? 'var(--heading)' : 'var(--secondary)');
        // Keep the brand accent unchanged for solid controls, but use a lighter accent
        // when the same palette color is rendered as small text on dark themes.
        $vars[] = '--primary-ink:' . ($scheme === 'dark' ? 'color-mix(in srgb,var(--primary) 60%,#ffffff)' : 'color-mix(in srgb,var(--primary) 52%,var(--heading))');
        $vars[] = '--danger-ink:' . ($scheme === 'dark' ? 'color-mix(in srgb,var(--danger) 60%,#ffffff)' : 'var(--danger)');
        $vars[] = '--warning-ink:' . ($scheme === 'dark' ? 'color-mix(in srgb,var(--warning) 60%,#ffffff)' : 'var(--warning)');
        $vars[] = '--success-ink:' . ($scheme === 'dark' ? 'color-mix(in srgb,var(--success) 60%,#ffffff)' : 'var(--success)');
        if ($vars !== []) $rules[] = 'html.mifp-theme-' . $id . '{' . implode(';', $vars) . '}';
    }
    foreach (($appearance['palettes'] ?? []) as $palette) {
        if (!is_array($palette)) continue;
        $id = css_identifier((string)($palette['id'] ?? ''));
        $primary = css_color($palette['primary'] ?? '');
        $secondary = css_color($palette['secondary'] ?? '');
        $vars = [];
        if ($primary !== '') { $vars[] = '--primary:' . $primary; $vars[] = '--on-primary:' . css_contrast_text($primary); }
        if ($secondary !== '') { $vars[] = '--secondary:' . $secondary; $vars[] = '--on-secondary:' . css_contrast_text($secondary); }
        if ($vars !== []) $rules[] = 'html.mifp-palette-' . $id . '{' . implode(';', $vars) . '}';
    }
    return implode("\n", $rules);
}

$appearanceCss = build_appearance_css($settings['appearance'] ?? []);
$defaultThemeClass = 'mifp-theme-' . css_identifier((string)(($settings['appearance']['default_theme'] ?? '') ?: 'paper'));
$defaultPaletteClass = 'mifp-palette-' . css_identifier((string)(($settings['appearance']['default_palette'] ?? '') ?: 'mifp'));
$clientConfigJson = json_encode($clientConfig, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) ?: '{}';
?>
<!doctype html>
<html lang="en" class="<?= h($defaultThemeClass . ' ' . $defaultPaletteClass) ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="robots" content="noindex,nofollow,noarchive">
<title><?= h($event) ?> | Registration</title>
<link rel="stylesheet" href="assets/styles.css?v=<?= h((string)(@filemtime(__DIR__ . '/assets/styles.css') ?: '1')) ?>">
<style nonce="<?= h($styleNonce) ?>"><?= $appearanceCss ?></style>
<script src="assets/form.js?v=<?= h((string)(@filemtime(__DIR__ . '/assets/form.js') ?: '1')) ?>" defer></script>
</head>
<body data-reg-config="<?= h($clientConfigJson) ?>">
<a class="skip-link" href="#main-content">Skip to main content</a>
<header class="topbar">
    <div class="topbar-inner">
        <a class="brand" href="<?= h((string)($conference['back_url'] ?? '../registration.html')) ?>" aria-label="Back to conference registration information">
            <img src="<?= h($conferenceLogo) ?>" alt="<?= h($event) ?>">
            <span><strong><?= h($event) ?></strong><small>Registration</small></span>
        </a>
        <a class="back-link" href="<?= h((string)($conference['back_url'] ?? '../registration.html')) ?>">Back to conference site</a>
    </div>
</header>

<main id="main-content" class="page-shell">
    <section class="page-head">
        <div>
            <span class="kicker">MIFP · Online registration</span>
            <h1><?= is_array($success) ? 'Registration Confirmation' : 'Registration Form' ?></h1>
            <p class="conference-name"><?= h((string)($conference['full_name'] ?? '')) ?></p>
            <div class="meta-row">
                <span><?= h((string)($conference['date_label'] ?? '')) ?></span>
                <span><?= h((string)($conference['location'] ?? '')) ?></span>
            </div>
        </div>
        <img class="mifp-mark" src="<?= h($organizerLogo) ?>" alt="MIFP">
    </section>

    <?php if ($fatalError !== null): ?>
        <section class="notice notice-danger" role="alert">
            <strong>Registration unavailable</strong>
            <p><?= h($fatalError) ?></p>
        </section>
    <?php else: ?>

    <?php if (is_array($success)): ?>
        <section class="success-card" role="status">
            <div class="success-head">
                <div>
                    <span class="kicker">Request recorded</span>
                    <h2>Registration received</h2>
                    <p>Your registration has been saved successfully.</p>
                </div>
                <span class="status-badge">Submitted</span>
            </div>
            <div class="summary-grid">
                <?php foreach (($success['summary'] ?? []) as $label => $value): ?>
                    <div><span><?= h((string)$label) ?></span><strong><?= nl2br(h((string)$value)) ?></strong></div>
                <?php endforeach; ?>
            </div>
            <p class="fine-print">
                <?php if (!empty($success['user_mail_sent'])): ?>
                    A confirmation email was sent to <?= h((string)$success['email']) ?>.
                <?php else: ?>
                    Your registration is saved, but the confirmation email could not be sent. Please contact the organizers if you need assistance.
                <?php endif; ?>
            </p>
        </section>
    <?php else: ?>

    <div class="content-grid">
        <aside class="guide-column" aria-label="Registration guidance">
            <section class="panel intro-panel">
                <span class="kicker"><?= h((string)($payment['label'] ?? 'Payment')) ?></span>
                <h2><?= h((string)($payment['title'] ?? 'Payment methods & instructions')) ?></h2>
                <p><?= h((string)($payment['intro'] ?? $content['intro'] ?? '')) ?></p>
                <?php if ($paymentUrl !== ''): ?>
                    <a class="payment-button" href="<?= h($paymentUrl) ?>" target="_blank" rel="noopener noreferrer"><?= h((string)($payment['button_label'] ?? 'Open payment website')) ?></a>
                <?php endif; ?>
            </section>

            <?php foreach ($paymentMethodsDetail as $method): ?>
                <?php if (!is_array($method)) continue; ?>
                <section class="panel payment-method-panel">
                    <h3><?= h((string)($method['title'] ?? 'Payment method')) ?></h3>
                    <?php if (!empty($method['steps']) && is_array($method['steps'])): ?>
                        <ol class="payment-steps">
                            <?php foreach ($method['steps'] as $step): ?><li><?= h((string)$step) ?></li><?php endforeach; ?>
                        </ol>
                    <?php endif; ?>
                    <?php if (!empty($method['fields']) && is_array($method['fields'])): ?>
                        <dl class="payment-data">
                            <?php foreach ($method['fields'] as $field): ?>
                                <?php if (!is_array($field)) continue; ?>
                                <div><dt><?= h((string)($field['label'] ?? '')) ?></dt><dd><?= h((string)($field['value'] ?? '')) ?></dd></div>
                            <?php endforeach; ?>
                        </dl>
                    <?php endif; ?>
                    <?php if (!empty($method['note'])): ?><p class="method-note"><?= h((string)$method['note']) ?></p><?php endif; ?>
                </section>
            <?php endforeach; ?>

            <?php if (!empty($content['provider_note'])): ?>
                <section class="panel provider-note"><p><?= h((string)$content['provider_note']) ?></p></section>
            <?php endif; ?>

            <section class="panel steps-panel">
                <h3><?= h((string)($content['guide_title'] ?? 'Registration & Payment Guide')) ?></h3>
                <ol class="step-list">
                    <?php foreach (($content['steps'] ?? []) as $step): ?>
                        <li><?= h((string)$step) ?></li>
                    <?php endforeach; ?>
                </ol>
                <?php if (!empty($payment['instructions_note'])): ?><div class="payment-note"><?= h((string)$payment['instructions_note']) ?></div><?php endif; ?>
            </section>

            <section class="panel warning-panel">
                <h3>Important conditions</h3>
                <ul>
                    <?php foreach (($content['warnings'] ?? []) as $warning): ?>
                        <li><?= h((string)$warning) ?></li>
                    <?php endforeach; ?>
                </ul>
            </section>

            <section class="security-note">
                <strong>Proof of payment</strong>
                <p>Accepted formats: PDF, JPEG or PNG · maximum <?= h((string)$maxUploadMb) ?> MB.</p>
            </section>
        </aside>

        <section class="form-column">
            <?php if (!$registrationOpen): ?>
                <div class="notice notice-warning" role="status">
                    <strong>Online registration is closed</strong>
                    <p><?= h((string)($conference['closed_message'] ?? 'Online registration is not currently available.')) ?></p>
                </div>
            <?php endif; ?>

            <?php if (isset($errors['_form'])): ?>
                <div class="notice notice-danger" role="alert" tabindex="-1">
                    <strong>Registration not submitted</strong>
                    <p><?= h($errors['_form']) ?></p>
                </div>
            <?php endif; ?>

            <form id="registrationForm" class="registration-form" method="post" enctype="multipart/form-data" action="" novalidate>
                <input type="hidden" name="csrf_token" value="<?= h(csrf_token()) ?>">
                <div class="honeypot" aria-hidden="true">
                    <label for="company_website">Company website</label>
                    <input id="company_website" type="text" name="company_website" tabindex="-1" autocomplete="off">
                </div>

                <div class="form-head">
                    <div>
                        <span class="kicker">Participant details</span>
                        <h2>Complete your registration</h2>
                    </div>
                    <p><?= h((string)($form['required_note'] ?? 'Fields marked with * are required.')) ?></p>
                </div>

                <?php foreach ($formSections as $section): ?>
                    <?php
                    if (!is_array($section) || !is_array($section['fields'] ?? null)) continue;
                    $sectionId = trim((string)($section['id'] ?? 'section'));
                    $sectionTitle = trim((string)($section['title'] ?? 'Registration details'));
                    $visibleFields = [];
                    foreach ($section['fields'] as $field) {
                        if (!is_array($field)) continue;
                        if ((string)($field['name'] ?? '') === 'tshirt_size' && !$tshirtEnabled) continue;
                        $visibleFields[] = $field;
                    }
                    if ($visibleFields === []) continue;
                    $privacySection = strtolower($sectionId) === 'privacy';
                    ?>
                    <fieldset class="<?= $privacySection ? 'privacy-fieldset' : '' ?>" <?= $registrationOpen ? '' : 'disabled' ?>>
                        <legend><?= h($sectionTitle) ?></legend>
                        <?php if ($privacySection && !empty($privacyNotice['text'])): ?>
                            <div class="privacy-notice">
                                <p><?= nl2br(h((string)$privacyNotice['text'])) ?></p>
                            </div>
                        <?php endif; ?>
                        <div class="field-grid">
                            <?php foreach ($visibleFields as $field): ?>
                                <?php
                                $name = trim((string)($field['name'] ?? ''));
                                if ($name === '' || preg_match('/^[A-Za-z][A-Za-z0-9_]*$/', $name) !== 1) continue;
                                $type = strtolower(trim((string)($field['type'] ?? 'text')));
                                if (!in_array($type, ['text', 'email', 'date', 'textarea', 'select', 'checkbox', 'file'], true)) $type = 'text';
                                $label = trim((string)($field['label'] ?? $name));
                                $required = bool_setting($field['required'] ?? false, false);
                                $wide = bool_setting($field['full_width'] ?? false, false);
                                $help = trim((string)($field['help'] ?? ''));
                                $autocomplete = trim((string)($field['autocomplete'] ?? ''));
                                $accept = trim((string)($field['accept'] ?? ''));
                                $placeholder = trim((string)($field['placeholder'] ?? ''));
                                $error = $errors[$name] ?? null;
                                $isChecked = array_key_exists($name, $values) ? !empty($values[$name]) : ((string)($_POST[$name] ?? '') === '1');
                                ?>

                                <?php if ($type === 'checkbox'): ?>
                                    <label class="privacy-check <?= $wide ? 'field-wide' : '' ?>">
                                        <input type="checkbox" name="<?= h($name) ?>" value="1" <?= $required ? 'required' : '' ?> <?= $isChecked ? 'checked' : '' ?> aria-invalid="<?= $error !== null ? 'true' : 'false' ?>">
                                        <span>
                                            <?= h($label) ?>
                                            <?php if (!empty($field['link_label'])): ?>
                                                <?php $linkHref = $name === 'privacy_acceptance' ? (string)($conference['privacy_url'] ?? '../privacy.html') : (string)($field['link_href'] ?? '#'); ?>
                                                <a href="<?= h($linkHref) ?>" target="_blank" rel="noopener noreferrer"><?= h((string)$field['link_label']) ?></a>
                                            <?php endif; ?>
                                            <?= $required ? ' <b>*</b>' : '' ?>
                                        </span>
                                    </label>
                                    <?php if ($error !== null): ?><small class="field-error privacy-error <?= $wide ? 'field-wide' : '' ?>"><?= h((string)$error) ?></small><?php endif; ?>

                                <?php else: ?>
                                    <label class="field <?= $wide ? 'field-wide' : '' ?> <?= $type === 'file' ? 'file-field' : '' ?>">
                                        <span><?= h($label) ?><?= $required ? ' <b>*</b>' : '' ?></span>

                                        <?php if ($type === 'textarea'): ?>
                                            <textarea name="<?= h($name) ?>" rows="3" <?= $required ? 'required' : '' ?> <?= $autocomplete !== '' ? 'autocomplete="' . h($autocomplete) . '"' : '' ?> <?= $placeholder !== '' ? 'placeholder="' . h($placeholder) . '"' : '' ?> aria-invalid="<?= $error !== null ? 'true' : 'false' ?>"><?= old_value($name, $values) ?></textarea>

                                        <?php elseif ($type === 'select'): ?>
                                            <select name="<?= h($name) ?>" <?= $required ? 'required' : '' ?> aria-invalid="<?= $error !== null ? 'true' : 'false' ?>">
                                                <option value="">Select option</option>
                                                <?php foreach (($field['options'] ?? []) as $option): ?>
                                                    <?php
                                                    $optionValue = is_array($option) ? (string)($option['value'] ?? $option['label'] ?? '') : (string)$option;
                                                    $optionLabel = is_array($option) ? (string)($option['label'] ?? $option['value'] ?? '') : (string)$option;
                                                    if (trim($optionValue) === '') continue;
                                                    ?>
                                                    <option value="<?= h($optionValue) ?>"<?= selected_value($name, $optionValue, $values) ?>><?= h($optionLabel) ?></option>
                                                <?php endforeach; ?>
                                            </select>

                                        <?php elseif ($type === 'file'): ?>
                                            <input type="file" name="<?= h($name) ?>" <?= $required ? 'required' : '' ?> <?= $accept !== '' ? 'accept="' . h($accept) . '"' : '' ?> aria-invalid="<?= $error !== null ? 'true' : 'false' ?>">

                                        <?php else: ?>
                                            <input type="<?= h($type) ?>" name="<?= h($name) ?>" <?= $required ? 'required' : '' ?> <?= $autocomplete !== '' ? 'autocomplete="' . h($autocomplete) . '"' : '' ?> <?= $placeholder !== '' ? 'placeholder="' . h($placeholder) . '"' : '' ?> value="<?= old_value($name, $values) ?>" aria-invalid="<?= $error !== null ? 'true' : 'false' ?>">
                                        <?php endif; ?>

                                        <?php if ($help !== ''): ?><small><?= h($help) ?></small><?php endif; ?>
                                        <?php if ($type === 'file' && $name === 'proof_of_payment' && $help === ''): ?><small>PDF, JPEG or PNG · max <?= h((string)$maxUploadMb) ?> MB.</small><?php endif; ?>
                                        <?php if ($error !== null): ?><small class="field-error"><?= h((string)$error) ?></small><?php endif; ?>
                                    </label>
                                <?php endif; ?>
                            <?php endforeach; ?>
                        </div>
                    </fieldset>
                <?php endforeach; ?>

                <div class="submit-row">
                    <div>
                        <strong>Ready to submit?</strong>
                        <span>After successful submission you will receive a confirmation email.</span>
                    </div>
                    <button id="registrationSubmit" class="btn-primary" type="submit" <?= $registrationOpen ? '' : 'disabled' ?>><?= h($registrationOpen ? (string)($form['submit_label'] ?? 'Submit registration') : 'Registration closed') ?></button>
                </div>
            </form>
        </section>
    </div>
    <?php endif; ?>
    <?php endif; ?>
</main>

<div id="submissionOverlay" class="submission-overlay" role="status" aria-live="polite" aria-hidden="true">
    <div class="submission-dialog">
        <span class="submission-spinner" aria-hidden="true"></span>
        <strong>Sending registration</strong>
        <p>Please keep this page open while your registration is being submitted.</p>
    </div>
</div>

<footer class="site-footer">
    <span>MIFP · Mediterranean Institute of Fundamental Physics</span>
    <?php if (!empty($conference['contact_email'])): ?><a href="mailto:<?= h((string)$conference['contact_email']) ?>"><?= h((string)$conference['contact_email']) ?></a><?php endif; ?>
</footer>
</body>
</html>
