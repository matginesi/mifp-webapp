<?php
declare(strict_types=1);

function required_directory(string $name): string
{
    $value = getenv($name);
    if ($value === false || $value === '' || !is_dir($value)) {
        fwrite(STDERR, "Missing runtime directory: {$name}\n");
        exit(2);
    }
    return rtrim($value, DIRECTORY_SEPARATOR);
}

$registrationDirectory = required_directory('MIFP_REGISTRATION_DIR');
$uploadDirectory = required_directory('MIFP_UPLOAD_DIR');
$submission = $registrationDirectory . DIRECTORY_SEPARATOR . 'contract-submission.json';
$payload = json_encode(
    ['name' => 'Contract Test', 'email' => 'contract@example.invalid'],
    JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES
);
if (file_put_contents($submission, $payload . PHP_EOL, LOCK_EX) === false) {
    fwrite(STDERR, "Unable to write the private registration\n");
    exit(3);
}

$publicProbe = __DIR__ . DIRECTORY_SEPARATOR . 'must-not-be-created.txt';
if (@file_put_contents($publicProbe, "public write must fail\n") !== false) {
    fwrite(STDERR, "Public event tree is unexpectedly writable\n");
    exit(4);
}

if (getenv('MIFP_CONTRACT_WRITE_UPLOAD') === '1') {
    $upload = $uploadDirectory . DIRECTORY_SEPARATOR . 'contract-upload.txt';
    if (file_put_contents($upload, "dummy upload\n", LOCK_EX) === false) {
        fwrite(STDERR, "Unable to write the private upload\n");
        exit(5);
    }
}

echo "registration={$submission}\n";
