<?php
/**
 * Shared password gate. Sets a signed, stateless auth cookie on success (no
 * server-side session storage, so it isn't at the mercy of PHP's
 * session.gc_maxlifetime -- typically ~24 min on shared hosting, far short
 * of the persistent login this is meant to provide).
 */
require __DIR__ . '/auth_secret.php';

function hhc_safe_redirect(string $raw): string
{
    // Only ever allow a same-site relative path -- never let ?redirect= be
    // used as an open redirect to an external URL.
    if ($raw === '' || $raw[0] !== '/' || (isset($raw[1]) && $raw[1] === '/')) {
        return '/';
    }
    return $raw;
}

$redirect = hhc_safe_redirect($_GET['redirect'] ?? $_POST['redirect'] ?? '/');
$error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $submitted = (string)($_POST['password'] ?? '');
    $hash = hash('sha256', SITE_PASSWORD_SALT . $submitted);
    if (hash_equals(SITE_PASSWORD_HASH, $hash)) {
        $expiry = time() + AUTH_COOKIE_SECONDS;
        $sig = hash_hmac('sha256', (string)$expiry, AUTH_COOKIE_SECRET);
        setcookie(AUTH_COOKIE_NAME, $expiry . '.' . $sig, [
            'expires' => $expiry,
            'path' => '/',
            'httponly' => true,
            'samesite' => 'Lax',
            'secure' => !empty($_SERVER['HTTPS']),
        ]);
        header('Location: ' . $redirect);
        exit;
    }
    sleep(1); // cheap brute-force throttle
    $error = 'Falsches Passwort.';
}
?>
<!doctype html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>Anmeldung</title>
<style>
  :root { --blue:#2563eb; --bg:#f1f5f9; --card:#ffffff; --text:#1e293b; --muted:#64748b; --err:#dc2626; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:var(--bg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .card { background:var(--card); padding:2.5rem 2rem; border-radius:12px; box-shadow:0 4px 16px rgba(0,0,0,.08);
          width:100%; max-width:320px; }
  h1 { font-size:1.15rem; margin:0 0 1.25rem; color:var(--text); text-align:center; }
  input[type=password] { width:100%; padding:.65rem .75rem; border:1px solid #cbd5e1; border-radius:8px;
                          font-size:1rem; margin-bottom:1rem; }
  button { width:100%; padding:.65rem; border:none; border-radius:8px; background:var(--blue); color:#fff;
           font-size:1rem; cursor:pointer; }
  button:hover { filter:brightness(0.95); }
  .err { color:var(--err); font-size:.9rem; margin:-.5rem 0 1rem; text-align:center; }
</style>
</head>
<body>
  <form class="card" method="post" autocomplete="off">
    <h1>Passwort erforderlich</h1>
    <?php if ($error): ?><p class="err"><?= htmlspecialchars($error, ENT_QUOTES) ?></p><?php endif; ?>
    <input type="hidden" name="redirect" value="<?= htmlspecialchars($redirect, ENT_QUOTES) ?>">
    <input type="password" name="password" autofocus required>
    <button type="submit">Weiter</button>
  </form>
</body>
</html>
