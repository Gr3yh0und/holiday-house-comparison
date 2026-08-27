<?php
/**
 * Required at the top of every protected page (index.php / index-test.php).
 * Fail-closed: if auth_secret.php is missing, require() itself fatals rather
 * than silently serving the page unprotected.
 */
require __DIR__ . '/auth_secret.php';

function hhc_is_authed(): bool
{
    if (empty($_COOKIE[AUTH_COOKIE_NAME])) {
        return false;
    }
    $parts = explode('.', $_COOKIE[AUTH_COOKIE_NAME], 2);
    if (count($parts) !== 2) {
        return false;
    }
    [$expiry, $sig] = $parts;
    if (!ctype_digit($expiry) || (int)$expiry < time()) {
        return false;
    }
    $expected = hash_hmac('sha256', $expiry, AUTH_COOKIE_SECRET);
    return hash_equals($expected, $sig);
}

if (!hhc_is_authed()) {
    $dest = $_SERVER['REQUEST_URI'] ?? '/';
    header('Location: login.php?redirect=' . urlencode($dest));
    exit;
}
