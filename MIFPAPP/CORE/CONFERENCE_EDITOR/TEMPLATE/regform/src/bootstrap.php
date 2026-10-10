<?php

declare(strict_types=1);

if (isset($_SERVER['SCRIPT_FILENAME']) && realpath((string)$_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    http_response_code(404);
    exit;
}

const MIFP_REG_GUARD = '<?php http_response_code(404); exit; ?>';


/**
 * PHP 7.0 compatibility helpers for the small subset of PHP 8 string helpers
 * used by the registration module. Namespaced with the project prefix so they
 * remain harmless when the site later runs on PHP 8.x.
 */
function mifp_str_contains(string $haystack, string $needle): bool
{
    return $needle === '' || strpos($haystack, $needle) !== false;
}

function mifp_str_starts_with(string $haystack, string $needle): bool
{
    return $needle === '' || strncmp($haystack, $needle, strlen($needle)) === 0;
}

/**
 * Small dependency-free YAML reader matching the limited YAML subset used by
 * the MIFP YAML settings files. If ext-yaml is installed, PHP uses it instead.
 */
function yaml_scalar_value(string $raw)
{
    $value = trim($raw);
    if ($value === '') {
        return '';
    }

    $first = $value[0] ?? '';
    $last = $value[strlen($value) - 1] ?? '';
    if (($first === '"' && $last === '"') || ($first === "'" && $last === "'")) {
        if ($first === '"') {
            $decoded = json_decode($value, true);
            return json_last_error() === JSON_ERROR_NONE ? $decoded : substr($value, 1, -1);
        }
        return str_replace("''", "'", substr($value, 1, -1));
    }

    if (preg_match('/^(true|false)$/i', $value) === 1) {
        return strtolower($value) === 'true';
    }
    if (preg_match('/^(null|~)$/i', $value) === 1) {
        return null;
    }
    if (preg_match('/^-?(?:\d+\.?\d*|\.\d+)$/', $value) === 1) {
        return mifp_str_contains($value, '.') ? (float)$value : (int)$value;
    }
    return $value;
}

function yaml_strip_comment(string $line): string
{
    $quote = null;
    $length = strlen($line);
    for ($i = 0; $i < $length; $i++) {
        $char = $line[$i];
        if (($char === '"' || $char === "'") && ($i === 0 || $line[$i - 1] !== '\\')) {
            $quote = $quote === $char ? null : ($quote ?? $char);
        }
        if ($char === '#' && $quote === null && ($i === 0 || ctype_space($line[$i - 1]))) {
            return rtrim(substr($line, 0, $i));
        }
    }
    return rtrim($line);
}

function yaml_mapping_entry(string $text)
{
    $quote = null;
    $length = strlen($text);
    for ($i = 0; $i < $length; $i++) {
        $char = $text[$i];
        if (($char === '"' || $char === "'") && ($i === 0 || $text[$i - 1] !== '\\')) {
            $quote = $quote === $char ? null : ($quote ?? $char);
            continue;
        }
        if ($char === ':' && $quote === null) {
            $key = trim(substr($text, 0, $i));
            if ($key === '' || in_array($key, ['__proto__', 'prototype', 'constructor'], true)) {
                throw new RuntimeException('Invalid YAML key.');
            }
            return [$key, trim(substr($text, $i + 1))];
        }
    }
    return null;
}

function yaml_tokenize(string $raw): array
{
    if (strlen($raw) > 2000000) {
        throw new RuntimeException('YAML settings file is unexpectedly large.');
    }

    $tokens = [];
    $lines = preg_split('/\r?\n/', ltrim($raw, "\xEF\xBB\xBF")) ?: [];
    foreach ($lines as $index => $rawLine) {
        $line = str_replace("\t", '  ', $rawLine);
        $clean = yaml_strip_comment($line);
        if (trim($clean) === '') {
            continue;
        }
        preg_match('/^\s*/', $clean, $match);
        $tokens[] = [
            'indent' => strlen($match[0] ?? ''),
            'text' => trim($clean),
            'line' => $index + 1,
        ];
    }
    return $tokens;
}

function yaml_read_multiline(array $tokens, int $position, int $parentIndent, bool $fold): array
{
    $parts = [];
    $count = count($tokens);
    while ($position < $count && $tokens[$position]['indent'] > $parentIndent) {
        $parts[] = $tokens[$position]['text'];
        $position++;
    }
    return [$fold ? implode(' ', $parts) : implode("\n", $parts), $position];
}

function yaml_parse_block(array $tokens, int $position, int $indent, int $depth = 0): array
{
    if ($depth > 40) {
        throw new RuntimeException('YAML settings nesting is too deep.');
    }
    $count = count($tokens);
    if ($position >= $count) {
        return [[], $position];
    }

    $isArray = $tokens[$position]['indent'] === $indent && mifp_str_starts_with($tokens[$position]['text'], '- ');
    $container = [];

    while ($position < $count) {
        $token = $tokens[$position];
        if ($token['indent'] < $indent) {
            break;
        }
        if ($token['indent'] > $indent) {
            throw new RuntimeException('Unexpected YAML indentation at line ' . $token['line'] . '.');
        }

        if ($isArray) {
            if (!mifp_str_starts_with($token['text'], '- ')) {
                break;
            }
            $body = trim(substr($token['text'], 2));
            if ($body === '') {
                if ($position + 1 < $count && $tokens[$position + 1]['indent'] > $indent) {
                    list($value, $position) = yaml_parse_block($tokens, $position + 1, $tokens[$position + 1]['indent'], $depth + 1);
                    $container[] = $value;
                } else {
                    $container[] = null;
                    $position++;
                }
                continue;
            }

            $pair = yaml_mapping_entry($body);
            if ($pair === null) {
                $container[] = yaml_scalar_value($body);
                $position++;
                continue;
            }

            $object = [];
            list($key, $valueText) = $pair;
            if ($valueText === '|' || $valueText === '>') {
                list($value, $position) = yaml_read_multiline($tokens, $position + 1, $indent, $valueText === '>');
                $object[$key] = $value;
            } elseif ($valueText !== '') {
                $object[$key] = yaml_scalar_value($valueText);
                $position++;
            } elseif ($position + 1 < $count && $tokens[$position + 1]['indent'] > $indent) {
                list($value, $position) = yaml_parse_block($tokens, $position + 1, $tokens[$position + 1]['indent'], $depth + 1);
                $object[$key] = $value;
            } else {
                $object[$key] = [];
                $position++;
            }

            while ($position < $count && $tokens[$position]['indent'] > $indent) {
                $child = $tokens[$position];
                $childIndent = $child['indent'];
                if (mifp_str_starts_with($child['text'], '- ')) {
                    break;
                }
                $childPair = yaml_mapping_entry($child['text']);
                if ($childPair === null) {
                    break;
                }
                list($childKey, $childValueText) = $childPair;
                if ($childValueText === '|' || $childValueText === '>') {
                    list($childValue, $position) = yaml_read_multiline($tokens, $position + 1, $childIndent, $childValueText === '>');
                    $object[$childKey] = $childValue;
                } elseif ($childValueText !== '') {
                    $object[$childKey] = yaml_scalar_value($childValueText);
                    $position++;
                } elseif ($position + 1 < $count && $tokens[$position + 1]['indent'] > $childIndent) {
                    list($childValue, $position) = yaml_parse_block($tokens, $position + 1, $tokens[$position + 1]['indent'], $depth + 1);
                    $object[$childKey] = $childValue;
                } else {
                    $object[$childKey] = [];
                    $position++;
                }
            }

            $container[] = $object;
            continue;
        }

        $pair = yaml_mapping_entry($token['text']);
        if ($pair === null) {
            throw new RuntimeException('Expected YAML key/value at line ' . $token['line'] . '.');
        }
        list($key, $valueText) = $pair;
        if ($valueText === '|' || $valueText === '>') {
            list($value, $position) = yaml_read_multiline($tokens, $position + 1, $indent, $valueText === '>');
            $container[$key] = $value;
        } elseif ($valueText !== '') {
            $container[$key] = yaml_scalar_value($valueText);
            $position++;
        } elseif ($position + 1 < $count && $tokens[$position + 1]['indent'] > $indent) {
            list($value, $position) = yaml_parse_block($tokens, $position + 1, $tokens[$position + 1]['indent'], $depth + 1);
            $container[$key] = $value;
        } else {
            $container[$key] = [];
            $position++;
        }
    }

    return [$container, $position];
}

function parse_conference_yaml(string $file): array
{
    $label = basename($file);
    if (!is_file($file) || !is_readable($file)) {
        throw new RuntimeException($label . ' is unavailable.');
    }

    if (function_exists('yaml_parse_file')) {
        $parsed = @yaml_parse_file($file);
        if (is_array($parsed)) {
            return $parsed;
        }
    }

    $raw = file_get_contents($file);
    if (!is_string($raw)) {
        throw new RuntimeException($label . ' could not be read.');
    }
    $tokens = yaml_tokenize($raw);
    if ($tokens === []) {
        throw new RuntimeException($label . ' is empty.');
    }
    list($parsed) = yaml_parse_block($tokens, 0, $tokens[0]['indent']);
    return $parsed;
}

function yaml_path(array $config, array $path, $default = null)
{
    $value = $config;
    foreach ($path as $key) {
        if (!is_array($value) || !array_key_exists($key, $value)) {
            return $default;
        }
        $value = $value[$key];
    }
    return $value;
}

function registration_field_options(array $config, string $fieldName): array
{
    $sections = yaml_path($config, ['registration', 'form', 'sections'], []);
    if (!is_array($sections)) {
        return [];
    }
    foreach ($sections as $section) {
        if (!is_array($section)) {
            continue;
        }
        $fields = $section['fields'] ?? [];
        if (!is_array($fields)) {
            continue;
        }
        foreach ($fields as $field) {
            if (!is_array($field) || (string)($field['name'] ?? '') !== $fieldName) {
                continue;
            }
            $options = $field['options'] ?? [];
            if (!is_array($options)) {
                return [];
            }
            $values = [];
            foreach ($options as $option) {
                if (is_array($option)) {
                    $candidate = $option['value'] ?? $option['label'] ?? null;
                } else {
                    $candidate = $option;
                }
                if (is_scalar($candidate) && trim((string)$candidate) !== '') {
                    $values[] = (string)$candidate;
                }
            }
            return array_values(array_unique($values));
        }
    }
    return [];
}

function registration_field_config(array $config, string $fieldName)
{
    $sections = yaml_path($config, ['registration', 'form', 'sections'], []);
    if (!is_array($sections)) return null;
    foreach ($sections as $section) {
        if (!is_array($section) || !is_array($section['fields'] ?? null)) continue;
        foreach ($section['fields'] as $field) {
            if (is_array($field) && (string)($field['name'] ?? '') === $fieldName) return $field;
        }
    }
    return null;
}

function form_field_definition(array $settings, string $fieldName)
{
    $sections = $settings['form_sections'] ?? [];
    if (!is_array($sections)) return null;
    foreach ($sections as $section) {
        if (!is_array($section) || !is_array($section['fields'] ?? null)) continue;
        foreach ($section['fields'] as $field) {
            if (is_array($field) && (string)($field['name'] ?? '') === $fieldName) return $field;
        }
    }
    return null;
}

function yaml_string_list($value): array
{
    if (!is_array($value)) {
        return is_scalar($value) && trim((string)$value) !== '' ? [(string)$value] : [];
    }
    $result = [];
    foreach ($value as $item) {
        if (is_scalar($item) && trim((string)$item) !== '') {
            $result[] = (string)$item;
        }
    }
    return $result;
}

function load_settings(string $conferenceFile, string $regformFile): array
{
    $config = parse_conference_yaml($conferenceFile);
    $regformDocument = parse_conference_yaml($regformFile);
    $conference = yaml_path($config, ['conference'], []);
    $registration = yaml_path($config, ['registration'], []);
    $form = yaml_path($regformDocument, ['regform'], []);
    $backend = is_array($form) ? ($form['backend'] ?? []) : [];
    $mail = is_array($form) ? ($form['mail'] ?? []) : [];
    $appearance = yaml_path($config, ['appearance'], []);
    $runtime = yaml_path($config, ['runtime'], []);
    $assets = yaml_path($config, ['assets'], []);

    if (!is_array($conference) || !is_array($registration) || !is_array($form) || !is_array($backend) || !is_array($mail) || !is_array($appearance) || !is_array($runtime) || !is_array($assets)) {
        throw new RuntimeException('Registration configuration is invalid. Check conference.yaml and regform/settings.yaml.');
    }

    // Merge only in memory so existing validation/helpers can work with one
    // structure. regform/settings.yaml remains the sole source of form/backend/mail settings.
    if (!isset($config['registration']) || !is_array($config['registration'])) {
        $config['registration'] = [];
    }
    $config['registration']['form'] = $form;

    $contactEmail = trim((string)($mail['reply_to_email'] ?? ($conference['email'] ?? '')));
    if ($contactEmail === '') {
        throw new RuntimeException('regform.mail.reply_to_email must be configured.');
    }

    $mailRequired = (bool)($registration['enabled'] ?? true)
        && (bool)($form['enabled'] ?? true)
        && (bool)($form['submit_enabled'] ?? false);
    if ($mailRequired && !filter_var($contactEmail, FILTER_VALIDATE_EMAIL)) {
        throw new RuntimeException('regform.mail.reply_to_email must be a valid email address while registration submissions are enabled.');
    }

    $fromEmail = trim((string)($mail['from_email'] ?? $contactEmail));
    if ($mailRequired && !filter_var($fromEmail, FILTER_VALIDATE_EMAIL)) {
        throw new RuntimeException('regform.mail.from_email must be a valid email address while registration submissions are enabled.');
    }

    if ($mailRequired && registration_field_config($config, 'email') === null) {
        throw new RuntimeException('An email field is required while registration submissions are enabled.');
    }

    $adminEmails = yaml_string_list($mail['admin_emails'] ?? []);
    if ($adminEmails === []) {
        $adminEmails = [$contactEmail];
    }
    $adminEmails = array_values(array_unique(array_map('trim', $adminEmails)));
    if ($mailRequired) {
        foreach ($adminEmails as $adminEmail) {
            if (!filter_var($adminEmail, FILTER_VALIDATE_EMAIL)) {
                throw new RuntimeException('regform.mail.admin_emails contains an invalid email address.');
            }
        }
    }

    $city = trim((string)($conference['city'] ?? ''));
    $country = trim((string)($conference['country'] ?? ''));
    $location = trim($city . ($city !== '' && $country !== '' ? ', ' : '') . $country);

    $categories = registration_field_options($config, 'registration_type');
    if ($categories === []) {
        foreach (($registration['plans'] ?? []) as $plan) {
            if (is_array($plan) && trim((string)($plan['name'] ?? '')) !== '') {
                $categories[] = (string)$plan['name'];
            }
        }
    }

    $warnings = array_merge(
        yaml_string_list($registration['warnings'] ?? []),
        yaml_string_list($registration['important_notes'] ?? [])
    );

    $settings = [
        'conference' => [
            'event' => (string)(yaml_path($config, ['site', 'title'], '') ?: ($conference['acronym'] ?? 'MIFP Conference')),
            'full_name' => (string)($conference['full_name'] ?? ''),
            'date_label' => (string)($conference['date_label'] ?? ''),
            'location' => $location,
            'back_url' => (string)($form['back_url'] ?? '../registration.html'),
            'privacy_url' => (string)($form['privacy_url'] ?? '../privacy.html'),
            'contact_email' => $contactEmail,
            'registration_visible' => (bool)($registration['enabled'] ?? true) && (bool)($form['enabled'] ?? true),
            'registration_open' => (bool)($form['enabled'] ?? true) && (bool)($form['submit_enabled'] ?? false),
            'closed_message' => (string)($form['closed_message'] ?? $form['unavailable_label'] ?? 'Online registration is not currently available.'),
        ],
        'mail' => [
            'from_email' => $fromEmail,
            'from_name' => (string)($mail['from_name'] ?? ($conference['contact_name'] ?? 'MIFP')),
            'admin_emails' => $adminEmails,
            'subject_prefix' => (string)($mail['subject_prefix'] ?? '[MIFP]'),
            'send_user_confirmation' => (bool)($mail['send_user_confirmation'] ?? true),
        ],
        'form' => [
            'max_upload_mb' => (int)($backend['max_upload_mb'] ?? 5),
            'tshirt_enabled' => (bool)($backend['tshirt_enabled'] ?? true),
            'required_note' => (string)($form['required_note'] ?? 'Fields marked with * are required.'),
            'submit_label' => (string)($form['submit_label'] ?? 'Submit registration'),
        ],
        'form_sections' => is_array($form['sections'] ?? null) ? $form['sections'] : [],
        'categories' => $categories,
        'payment_methods' => registration_field_options($config, 'payment_method'),
        'tshirt_sizes' => registration_field_options($config, 'tshirt_size'),
        'dietary_choices' => registration_field_options($config, 'dietary_choice'),
        'content' => [
            'intro' => (string)($form['intro'] ?? ''),
            'payment' => is_array($registration['payment'] ?? null) ? $registration['payment'] : [],
            'payment_methods_detail' => is_array($registration['payment_methods'] ?? null) ? $registration['payment_methods'] : [],
            'provider' => (string)($registration['provider'] ?? ''),
            'provider_note' => (string)($registration['provider_note'] ?? ''),
            'guide_title' => (string)($registration['guide_title'] ?? 'Registration & Payment Guide'),
            'steps' => yaml_string_list($registration['guide_steps'] ?? []),
            'warnings' => $warnings,
            'privacy_notice' => is_array($form['privacy_notice'] ?? null) ? $form['privacy_notice'] : [],
        ],
        'appearance' => [
            'default_theme' => (string)($appearance['default_theme'] ?? ''),
            'default_palette' => (string)($appearance['default_palette'] ?? ''),
            'remember_preferences' => (bool)($appearance['remember_preferences'] ?? true),
            'max_content_width' => (string)($appearance['max_content_width'] ?? '1080px'),
            'component_radius' => (string)($appearance['component_radius'] ?? '8px'),
            'panel_radius' => (string)($appearance['panel_radius'] ?? '10px'),
            'control_radius' => (string)($appearance['control_radius'] ?? '6px'),
            'themes' => is_array($appearance['themes'] ?? null) ? $appearance['themes'] : [],
            'palettes' => is_array($appearance['palettes'] ?? null) ? $appearance['palettes'] : [],
        ],
        'branding' => [
            'conference_logo' => (string)($assets['logo'] ?? ''),
            'conference_logo_large' => (string)($assets['logo_large'] ?? ($assets['logo'] ?? '')),
            'organizer_logo' => (string)($assets['organizer_logo'] ?? ''),
        ],
        'runtime' => [
            'debug' => (bool)($runtime['debug'] ?? false),
            'log_level' => (string)($runtime['log_level'] ?? 'info'),
            'debug_log_level' => (string)($runtime['debug_log_level'] ?? 'debug'),
            'log_prefix' => (string)($runtime['log_prefix'] ?? 'MIFP'),
        ],
        'security' => [
            'rate_limit_requests' => (int)($backend['rate_limit_requests'] ?? 5),
            'rate_limit_window_seconds' => (int)($backend['rate_limit_window_seconds'] ?? 900),
            'minimum_fill_seconds' => (int)($backend['minimum_fill_seconds'] ?? 2),
            'trust_proxy' => (bool)($backend['trust_proxy'] ?? false),
            'trusted_proxies' => yaml_string_list($backend['trusted_proxies'] ?? []),
        ],
        'storage' => [
            'path' => (string)($backend['storage_path'] ?? 'storage'),
            'persist_submissions' => (bool)($backend['persist_submissions'] ?? true),
        ],
    ];

    foreach ([
        'registration_type' => 'categories',
        'payment_method' => 'payment_methods',
        'tshirt_size' => 'tshirt_sizes',
        'dietary_choice' => 'dietary_choices',
    ] as $fieldName => $listName) {
        $field = registration_field_config($config, $fieldName);
        if ($field !== null && strtolower((string)($field['type'] ?? '')) === 'select' && $settings[$listName] === []) {
            throw new RuntimeException('Missing options for registration field: ' . $fieldName);
        }
    }

    return $settings;
}

function h($value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function text_length(string $value): int
{
    return function_exists('mb_strlen') ? mb_strlen($value, 'UTF-8') : strlen($value);
}

function clean_text($value, int $maxLength, bool $multiline = false): string
{
    $text = is_string($value) ? trim($value) : '';
    $text = str_replace("\0", '', $text);
    if (!$multiline) {
        $text = preg_replace('/[\r\n\t]+/u', ' ', $text) ?? $text;
    } else {
        $text = preg_replace("/\r\n?|\n/u", "\n", $text) ?? $text;
    }
    $text = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', $text) ?? $text;

    if (text_length($text) > $maxLength) {
        return function_exists('mb_substr') ? mb_substr($text, 0, $maxLength, 'UTF-8') : substr($text, 0, $maxLength);
    }
    return $text;
}

function bool_setting($value, bool $default = false): bool
{
    if (is_bool($value)) {
        return $value;
    }
    if (is_int($value)) {
        return $value !== 0;
    }
    if (is_string($value)) {
        return filter_var($value, FILTER_VALIDATE_BOOLEAN, FILTER_NULL_ON_FAILURE) ?? $default;
    }
    return $default;
}

function event_storage_key(string $baseDir): string
{
    $eventDirectory = basename(dirname($baseDir));
    $eventKey = preg_replace('/[^A-Za-z0-9._~-]+/', '-', $eventDirectory);
    $eventKey = trim((string)$eventKey, '-');
    if ($eventKey === '' || $eventKey === '.' || $eventKey === '..') {
        throw new RuntimeException('Event storage key could not be derived safely.');
    }
    return $eventKey;
}

function is_absolute_storage_path(string $value): bool
{
    return mifp_str_starts_with($value, '/')
        || (strlen($value) >= 3
            && ctype_alpha($value[0])
            && $value[1] === ':'
            && ($value[2] === '\\' || $value[2] === '/'));
}

function private_runtime_directory(string $name): ?string
{
    $value = getenv($name);
    if ($value === false || trim((string)$value) === '') {
        return null;
    }

    $value = trim((string)$value);
    $isAbsolute = is_absolute_storage_path($value);
    if (!$isAbsolute) {
        throw new RuntimeException($name . ' must be an absolute directory.');
    }
    return rtrim($value, '/\\');
}

function resolve_storage_path(string $baseDir, string $configured): string
{
    // MIFP local-vps PHP-FPM exposes authoritative private registration storage
    // outside the public document tree. The package remains portable to shared
    // hosting by falling back to the configured local directory when the host
    // environment variable is absent.
    $privateRoot = private_runtime_directory('MIFP_REGISTRATION_DIR');
    if ($privateRoot !== null) {
        return $privateRoot . DIRECTORY_SEPARATOR . event_storage_key($baseDir);
    }

    $configured = trim($configured);
    if ($configured === '') {
        throw new RuntimeException('Storage path is not configured.');
    }

    $isAbsolute = is_absolute_storage_path($configured);
    return $isAbsolute ? rtrim($configured, DIRECTORY_SEPARATOR) : $baseDir . DIRECTORY_SEPARATOR . trim($configured, '/\\');
}

function resolve_upload_storage_path(string $baseDir, string $registrationStoragePath): string
{
    // Persistent uploads have their own private VPS root. On shared hosting keep
    // the historical protected registrations/proofs layout for compatibility.
    $privateRoot = private_runtime_directory('MIFP_UPLOAD_DIR');
    if ($privateRoot !== null) {
        return $privateRoot . DIRECTORY_SEPARATOR . event_storage_key($baseDir);
    }
    return $registrationStoragePath . DIRECTORY_SEPARATOR . 'proofs';
}
function ensure_storage(string $path)
{
    if (!is_dir($path) && !mkdir($path, 0700, true) && !is_dir($path)) {
        throw new RuntimeException('Registration storage could not be created.');
    }
    apply_registration_mode($path, getenv('MIFP_REGISTRATION_WRITE_GROUP') === '1' ? 02770 : (getenv('MIFP_REGISTRATION_READ_GROUP') === '1' ? 0750 : 0700));
    if (!is_writable($path)) {
        throw new RuntimeException('Registration storage is not writable.');
    }
}

function start_secure_session()
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }

    ini_set('session.use_strict_mode', '1');
    ini_set('session.use_only_cookies', '1');
    ini_set('session.cookie_httponly', '1');
    ini_set('session.cookie_samesite', 'Strict');

    $secure = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off')
        || ((string)($_SERVER['SERVER_PORT'] ?? '') === '443');

    session_name('MIFPREG');
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => rtrim(dirname((string)($_SERVER['SCRIPT_NAME'] ?? '/')), '/') . '/',
        'secure' => $secure,
        'httponly' => true,
        'samesite' => 'Strict',
    ]);
    session_start();
}

function issue_security_headers($styleNonce = null)
{
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: strict-origin-when-cross-origin');
    header('X-Frame-Options: SAMEORIGIN');
    header("Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()");
    $styleSrc = "'self'" . ($styleNonce !== null && $styleNonce !== '' ? " 'nonce-" . preg_replace('/[^A-Za-z0-9+\/=]/', '', $styleNonce) . "'" : '');
    header("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src " . $styleSrc . "; img-src 'self' data:; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'self'");
    header('X-Robots-Tag: noindex, nofollow, noarchive');
    header('Cache-Control: no-store, max-age=0');
    header('Pragma: no-cache');

    $secure = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off')
        || ((string)($_SERVER['SERVER_PORT'] ?? '') === '443');
    if ($secure) {
        header('Strict-Transport-Security: max-age=31536000');
    }
}

function csrf_token(): string
{
    if (empty($_SESSION['csrf_token']) || !is_string($_SESSION['csrf_token'])) {
        $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf_token'];
}

function validate_csrf($token): bool
{
    return is_string($token)
        && isset($_SESSION['csrf_token'])
        && is_string($_SESSION['csrf_token'])
        && hash_equals($_SESSION['csrf_token'], $token);
}

function reset_form_security_state()
{
    $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
    $_SESSION['form_started_at'] = time();
}

function current_client_ip(array $security): string
{
    $remote = filter_var($_SERVER['REMOTE_ADDR'] ?? '', FILTER_VALIDATE_IP) ?: '0.0.0.0';
    $trustProxy = bool_setting($security['trust_proxy'] ?? false);
    $trusted = $security['trusted_proxies'] ?? [];
    if (!is_array($trusted)) {
        $trusted = [$trusted];
    }

    if ($trustProxy && in_array($remote, $trusted, true)) {
        $forwarded = (string)($_SERVER['HTTP_X_FORWARDED_FOR'] ?? '');
        if ($forwarded !== '') {
            // Walk from the proxy nearest PHP; clients may supply a fake leftmost IP.
            $chain = array_map('trim', explode(',', $forwarded));
            for ($i = count($chain) - 1; $i >= 0; $i--) {
                $candidate = $chain[$i];
                if (!filter_var($candidate, FILTER_VALIDATE_IP)) return $remote;
                if (!in_array($candidate, $trusted, true)) return $candidate;
            }
        }
    }

    return $remote;
}


function storage_secret(string $storagePath): string
{
    $path = $storagePath . DIRECTORY_SEPARATOR . '.secret.php';
    if (!is_file($path)) {
        $secret = bin2hex(random_bytes(32));
        $fp = @fopen($path, 'x');
        if ($fp !== false) {
            fwrite($fp, MIFP_REG_GUARD . "\n" . $secret . "\n");
            fflush($fp);
            fclose($fp);
            @chmod($path, 0600);
            return $secret;
        }
    }

    $raw = @file_get_contents($path);
    if (!is_string($raw)) {
        throw new RuntimeException('Security secret storage is unavailable.');
    }
    $newline = strpos($raw, "\n");
    if ($newline === false || trim(substr($raw, 0, $newline)) !== MIFP_REG_GUARD) {
        throw new RuntimeException('Security secret storage is invalid.');
    }
    $secret = trim(substr($raw, $newline + 1));
    if (!preg_match('/^[a-f0-9]{64}$/', $secret)) {
        throw new RuntimeException('Security secret storage is invalid.');
    }
    return $secret;
}

function rate_limit_or_throw(string $storagePath, array $security)
{
    $limit = max(1, (int)($security['rate_limit_requests'] ?? 5));
    $window = max(60, (int)($security['rate_limit_window_seconds'] ?? 900));
    $secret = storage_secret($storagePath);
    $key = hash_hmac('sha256', current_client_ip($security), $secret);
    $path = $storagePath . DIRECTORY_SEPARATOR . '.rate-' . $key . '.php';
    $now = time();

    $fp = fopen($path, 'c+');
    if ($fp === false) {
        throw new RuntimeException('Rate limit storage is unavailable.');
    }

    try {
        if (!flock($fp, LOCK_EX)) {
            throw new RuntimeException('Rate limit lock failed.');
        }
        rewind($fp);
        $raw = stream_get_contents($fp) ?: '';
        $state = [];
        if ($raw !== '') {
            $newline = strpos($raw, "\n");
            if ($newline !== false && trim(substr($raw, 0, $newline)) === MIFP_REG_GUARD) {
                $parsed = json_decode(substr($raw, $newline + 1), true);
                $state = is_array($parsed) ? $parsed : [];
            }
        }

        $started = (int)($state['started'] ?? $now);
        $count = (int)($state['count'] ?? 0);
        if (($now - $started) >= $window) {
            $started = $now;
            $count = 0;
        }
        if ($count >= $limit) {
            throw new DomainException('Too many registration attempts. Please try again later.');
        }

        $state = ['started' => $started, 'count' => $count + 1, 'updated' => $now];
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, MIFP_REG_GUARD . "\n" . json_encode($state, JSON_UNESCAPED_SLASHES));
        fflush($fp);
        @chmod($path, 0600);
        flock($fp, LOCK_UN);
    } finally {
        fclose($fp);
    }
}

function validate_date_value($value)
{
    if (!is_string($value) || $value === '') {
        return null;
    }
    $date = DateTimeImmutable::createFromFormat('!Y-m-d', $value);
    return $date && $date->format('Y-m-d') === $value ? $value : null;
}

function validate_form(array $post, array $settings): array
{
    $errors = [];
    $data = [];
    $sections = is_array($settings['form_sections'] ?? null) ? $settings['form_sections'] : [];

    foreach ($sections as $section) {
        if (!is_array($section) || !is_array($section['fields'] ?? null)) continue;
        foreach ($section['fields'] as $field) {
            if (!is_array($field)) continue;
            $name = trim((string)($field['name'] ?? ''));
            if ($name === '' || preg_match('/^[A-Za-z][A-Za-z0-9_]*$/', $name) !== 1) continue;
            $label = trim((string)($field['label'] ?? $name));
            $type = strtolower(trim((string)($field['type'] ?? 'text')));
            $required = bool_setting($field['required'] ?? false, false);

            if ($type === 'file') continue;

            if ($type === 'checkbox') {
                $checked = isset($post[$name]) && is_string($post[$name]) && $post[$name] === '1';
                if ($required && !$checked) $errors[$name] = $label . ' is required.';
                $data[$name] = $checked;
                continue;
            }

            $raw = $post[$name] ?? '';
            if ($type === 'date') {
                $clean = clean_text($raw, 20);
                if ($clean === '') {
                    if ($required) $errors[$name] = $label . ' is required.';
                    $data[$name] = '';
                    continue;
                }
                $valid = validate_date_value($clean);
                if ($valid === null) $errors[$name] = 'Enter a valid ' . strtolower($label) . '.';
                $data[$name] = $valid ?? '';
                continue;
            }

            if ($type === 'email') {
                $value = clean_text($raw, 254);
                if ($value === '') {
                    if ($required) $errors[$name] = $label . ' is required.';
                } elseif (!filter_var($value, FILTER_VALIDATE_EMAIL)) {
                    $errors[$name] = 'Enter a valid email address.';
                }
                $data[$name] = $value;
                continue;
            }

            if ($type === 'select') {
                $value = clean_text($raw, 180);
                $options = [];
                foreach (($field['options'] ?? []) as $option) {
                    $candidate = is_array($option) ? ($option['value'] ?? $option['label'] ?? '') : $option;
                    if (is_scalar($candidate) && trim((string)$candidate) !== '') $options[] = (string)$candidate;
                }
                if ($value === '') {
                    if ($required) $errors[$name] = $label . ' is required.';
                } elseif (!in_array($value, $options, true)) {
                    $errors[$name] = 'Choose a valid ' . strtolower($label) . '.';
                }
                $data[$name] = $value;
                continue;
            }

            $maxDefault = $type === 'textarea' ? 1000 : 250;
            $max = (int)($field['maxlength'] ?? $maxDefault);
            $max = max(1, min($max, 4000));
            $value = clean_text($raw, $max, $type === 'textarea');
            if ($required && $value === '') $errors[$name] = $label . ' is required.';
            $data[$name] = $value;
        }
    }

    if (($data['arrival_date'] ?? '') !== '' && ($data['departure_date'] ?? '') !== '' && $data['departure_date'] < $data['arrival_date']) {
        $errors['departure_date'] = 'Departure date cannot be before arrival date.';
    }

    return [$data, $errors];
}

// PHP accepts K/M/G suffixes in these system-level limits. Keep this
// parser compatible with shared hosts running PHP before ini_parse_quantity.
function php_ini_size_bytes(string $value): int
{
    if (preg_match('/^([0-9]+(?:\.[0-9]+)?)\s*([KMG]?)$/i', trim($value), $match) !== 1) {
        return 0;
    }
    $power = strpos('KMG', strtoupper($match[2] ?? ''));
    $multiplier = empty($match[2]) ? 1 : 1024 ** ($power + 1);
    return (int)((float)$match[1] * $multiplier);
}

function effective_upload_limit_mb(int $configured): int
{
    $limit = max(1, min(20, $configured));
    $upload = php_ini_size_bytes((string)ini_get('upload_max_filesize'));
    $limit = min($limit, (int)floor($upload / (1024 * 1024)));
    $post = php_ini_size_bytes((string)ini_get('post_max_size'));
    if ($post > 0) {
        // Leave one MiB for multipart framing and the other form fields.
        $limit = min($limit, max(0, (int)floor($post / (1024 * 1024)) - 1));
    }
    return $limit;
}

function validate_upload(array $files, int $maxBytes): array
{
    if (!isset($files['proof_of_payment']) || !is_array($files['proof_of_payment'])) {
        throw new DomainException('Proof of payment is required.');
    }

    $file = $files['proof_of_payment'];
    $error = (int)($file['error'] ?? UPLOAD_ERR_NO_FILE);
    if ($error !== UPLOAD_ERR_OK) {
        $messages = [
            UPLOAD_ERR_INI_SIZE => 'The uploaded file exceeds the server upload limit.',
            UPLOAD_ERR_FORM_SIZE => 'The uploaded file is too large.',
            UPLOAD_ERR_PARTIAL => 'The file upload was incomplete. Please try again.',
            UPLOAD_ERR_NO_FILE => 'Proof of payment is required.',
        ];
        throw new DomainException($messages[$error] ?? 'The file upload failed.');
    }

    $size = (int)($file['size'] ?? 0);
    $tmp = (string)($file['tmp_name'] ?? '');
    if ($size < 1 || $size > $maxBytes) {
        throw new DomainException('Proof of payment must be smaller than the allowed upload size.');
    }
    if ($tmp === '' || !is_uploaded_file($tmp)) {
        throw new DomainException('The uploaded file could not be verified.');
    }

    $finfo = new finfo(FILEINFO_MIME_TYPE);
    $mime = (string)$finfo->file($tmp);
    $allowed = [
        'application/pdf' => ['extension' => 'pdf', 'label' => 'PDF'],
        'image/jpeg' => ['extension' => 'jpg', 'label' => 'JPEG'],
        'image/png' => ['extension' => 'png', 'label' => 'PNG'],
    ];
    if (!isset($allowed[$mime])) {
        throw new DomainException('Only PDF, JPEG and PNG files are accepted.');
    }

    if ($mime === 'application/pdf') {
        $fh = fopen($tmp, 'rb');
        $signature = $fh ? fread($fh, 5) : false;
        if (is_resource($fh)) {
            fclose($fh);
        }
        if ($signature !== '%PDF-') {
            throw new DomainException('The uploaded PDF is not valid.');
        }
    } else {
        $imageInfo = @getimagesize($tmp);
        $expectedType = $mime === 'image/jpeg' ? IMAGETYPE_JPEG : IMAGETYPE_PNG;
        if ($imageInfo === false || (int)($imageInfo[2] ?? 0) !== $expectedType) {
            throw new DomainException('The uploaded image is not valid.');
        }
    }

    return [
        'tmp_path' => $tmp,
        'mime' => $mime,
        'extension' => $allowed[$mime]['extension'],
        'label' => $allowed[$mime]['label'],
        'size' => $size,
    ];
}

function registrations_csv_path(string $storagePath): string
{
    return $storagePath . DIRECTORY_SEPARATOR . 'registrations.csv';
}

// Verify the effective mode: failed chmod must never advertise shared access.
function apply_registration_mode(string $path, int $mode): void
{
    clearstatcache(true, $path);
    if (is_link($path)) throw new RuntimeException('Unsafe registration resource.');
    if ((fileperms($path) & 07777) === $mode) return;
    if (!@chmod($path, $mode)) throw new RuntimeException('Registration permissions could not be applied.');
    clearstatcache(true, $path);
    if ((fileperms($path) & 07777) !== $mode) throw new RuntimeException('Registration permissions could not be verified.');
}

function registration_file_mode(): int
{
    return getenv('MIFP_REGISTRATION_WRITE_GROUP') === '1' ? 0660
        : (getenv('MIFP_REGISTRATION_READ_GROUP') === '1' ? 0640 : 0600);
}


function ensure_registration_repository(string $storagePath): void
{
    ensure_storage($storagePath);
}

function store_proof_of_payment(string $uploadStoragePath, array $upload, string $receiptId): array
{
    ensure_storage($uploadStoragePath);
    $safeId = preg_replace('/[^A-Z0-9-]/', '', strtoupper($receiptId));
    $filename = 'proof-' . $safeId . '.' . (string)$upload['extension'];
    $destination = $uploadStoragePath . DIRECTORY_SEPARATOR . $filename;
    if (file_exists($destination)) {
        throw new RuntimeException('Proof-of-payment filename collision.');
    }
    if (!move_uploaded_file((string)$upload['tmp_path'], $destination)) {
        throw new RuntimeException('Proof of payment could not be stored.');
    }
    try { apply_registration_mode($destination, registration_file_mode()); }
    catch (Throwable $e) { @unlink($destination); throw $e; }

    $stored = $upload;
    $stored['tmp_path'] = $destination;
    $stored['stored_filename'] = $filename;
    $stored['stored_path'] = $destination;
    return $stored;
}

function csv_safe_value($value): string
{
    $text = is_bool($value) ? ($value ? 'true' : 'false') : (string)($value ?? '');
    $text = str_replace("\0", '', $text);
    if (preg_match('/^[\x00-\x20]*[=+@-]/', $text)) {
        $text = "'" . $text;
    }
    return $text;
}

function persist_submission_csv(string $storagePath, array $record)
{
    ensure_registration_repository($storagePath);
    $path = registrations_csv_path($storagePath);
    $headers = [
        'receipt_id', 'submitted_at', 'first_name', 'last_name', 'email', 'affiliation', 'country', 'address',
        'arrival_date', 'departure_date', 'tshirt_size', 'dietary_choice', 'dietary_notes', 'registration_type',
        'payment_method', 'proof_file', 'proof_type', 'proof_size_bytes', 'privacy_accepted'
    ];

    $fp = fopen($path, 'c+b');
    if ($fp === false) {
        throw new RuntimeException('Registration CSV could not be opened.');
    }
    try {
        apply_registration_mode($path, registration_file_mode());
        if (!flock($fp, LOCK_EX)) {
            throw new RuntimeException('Registration CSV lock failed.');
        }
        $stat = fstat($fp);
        $size = is_array($stat) ? (int)($stat['size'] ?? 0) : 0;
        fseek($fp, 0, SEEK_END);
        if ($size === 0) {
            fputcsv($fp, $headers, ',', '"', '');
        }
        $row = [];
        foreach ($headers as $header) {
            $row[] = csv_safe_value($record[$header] ?? '');
        }
        if (fputcsv($fp, $row, ',', '"', '') === false) {
            throw new RuntimeException('Registration CSV could not be written.');
        }
        if (!fflush($fp) || (function_exists('fsync') && !fsync($fp))) {
            throw new RuntimeException('Registration CSV could not be synchronized.');
        }
        flock($fp, LOCK_UN);
    } finally {
        fclose($fp);
    }
}

function make_receipt_id(): string
{
    return 'REG-' . gmdate('Ymd') . '-' . strtoupper(bin2hex(random_bytes(4)));
}


// Durable transport outcomes, separate from the immutable submission CSV.
// A transport acceptance never proves inbox delivery. Ambiguous failures must
// be investigated by an operator before another attempt.
function registration_notification(string $storagePath, string $receiptId, string $channel, callable $send): bool
{
    if (!preg_match('/^REG-[0-9]{8}-[A-F0-9]{8}$/D', $receiptId)
        || !in_array($channel, ['organizers', 'participant'], true)) {
        throw new RuntimeException('Invalid notification identifier.');
    }
    $path = $storagePath . DIRECTORY_SEPARATOR . $receiptId . '.notifications.json';
    $lockPath = $storagePath . DIRECTORY_SEPARATOR . $receiptId . '.notifications.lock';
    if (is_link($path) || is_link($lockPath)) {
        throw new RuntimeException('Unsafe notification state.');
    }
    $lock = fopen($lockPath, 'c+b');

    if ($lock === false || !flock($lock, LOCK_EX)) {
        throw new RuntimeException('Notification lock unavailable.');
    }
    try {
        apply_registration_mode($lockPath, registration_file_mode());
        $state = is_file($path) ? json_decode((string)file_get_contents($path), true, 16, JSON_THROW_ON_ERROR) : [];
        $previous = $state[$channel]['status'] ?? 'pending';
        if (!in_array($previous, ['pending', 'failed'], true)) {
            return $previous === 'accepted';
        }
        $save = static function (array $value) use ($storagePath, $path): void {
            $temp = tempnam($storagePath, '.notification-');
            if ($temp === false) throw new RuntimeException('Notification state unavailable.');
            try {
                $fp = fopen($temp, 'wb');
                if ($fp === false) throw new RuntimeException('Notification state unavailable.');
                try {
                    $payload = json_encode($value, JSON_THROW_ON_ERROR);
                    if (fwrite($fp, $payload) !== strlen($payload) || !fflush($fp) || (function_exists('fsync') && !fsync($fp))) {
                        throw new RuntimeException('Notification state could not be saved.');
                    }
                } finally { fclose($fp); }
                apply_registration_mode($temp, registration_file_mode());
                if (!rename($temp, $path)) throw new RuntimeException('Notification state could not be installed.');
                sync_registration_directory($storagePath);
            } finally { if (is_file($temp)) unlink($temp); }
        };
        $state[$channel] = ['status' => 'sending', 'updated_at' => gmdate(DATE_ATOM)];
        $save($state);
        $accepted = false;
        try { $accepted = (bool)$send(); }
        finally {
            $state[$channel] = ['status' => $accepted ? 'accepted' : 'unknown', 'updated_at' => gmdate(DATE_ATOM)];
            $save($state);
        }
        return $accepted;
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}


function initialize_registration_notifications(string $storagePath, string $receiptId, bool $participant): void
{
    if (!preg_match('/^REG-[0-9]{8}-[A-F0-9]{8}$/D', $receiptId)) {
        throw new RuntimeException('Invalid receipt ID.');
    }
    $path = $storagePath . DIRECTORY_SEPARATOR . $receiptId . '.notifications.json';
    $fp = fopen($path, 'xb');
    if ($fp === false) throw new RuntimeException('Notification initialization failed.');
    try {
        $state = ['organizers' => ['status' => 'pending'],
                  'participant' => ['status' => $participant ? 'pending' : 'not_requested']];
        $payload = json_encode($state, JSON_THROW_ON_ERROR);
        if (fwrite($fp, $payload) !== strlen($payload) || !fflush($fp) || (function_exists('fsync') && !fsync($fp))) {
            throw new RuntimeException('Notification initialization failed.');
        }
        apply_registration_mode($path, registration_file_mode());
    } finally { fclose($fp); }
    sync_registration_directory($storagePath);
}


function sync_registration_directory(string $path): void
{
    // Keep PHP 7.4 shared hosting compatible; fsync exists from PHP 8.1.
    // On the VPS, persist directory entries before attempting external mail.
    if (!function_exists('fsync') || !getenv('MIFP_REGISTRATION_DIR')) return;
    $fp = fopen($path, 'r');
    if ($fp === false) throw new RuntimeException('Storage directory could not be synchronized.');
    try {
        if (!fsync($fp)) throw new RuntimeException('Storage directory could not be synchronized.');
    } finally { fclose($fp); }
}
