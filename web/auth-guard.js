// ============================================================
//  GoEvent — auth-guard.js
//  Protection des pages par authentification et rôle
// ============================================================

/**
 * Vérifie si l'utilisateur est connecté et a le bon rôle.
 * À appeler en haut de chaque page protégée.
 *
 * @param {string[]|null} allowedRoles - Rôles autorisés. null = tous les connectés.
 * @param {string} redirectTo - Page de redirection si non autorisé.
 */
function requireAuth(allowedRoles = null, redirectTo = "login.html") {
    const token = localStorage.getItem("bt_token");
    const user  = JSON.parse(localStorage.getItem("bt_user") || "null");

    // 1. Pas de token ou pas d'utilisateur → login
    if (!token || !user) {
        _redirectWithMessage(redirectTo, "Connectez-vous pour accéder à cette page.");
        return false;
    }

    // 2. Token expiré (vérification côté client)
    if (_isTokenExpired(token)) {
        localStorage.removeItem("bt_token");
        localStorage.removeItem("bt_user");
        _redirectWithMessage(redirectTo, "Votre session a expiré. Reconnectez-vous.");
        return false;
    }

    // 3. Rôle non autorisé → accueil
    if (allowedRoles && !allowedRoles.includes(user.role)) {
        _redirectWithMessage("index.html", "Accès refusé — vous n'avez pas les droits nécessaires.");
        return false;
    }

    return true;
}

/**
 * Redirige vers login si connecté (pour login.html et signup.html).
 * Évite qu'un utilisateur déjà connecté revienne sur ces pages.
 *
 * @param {string} redirectTo - Page de redirection si déjà connecté.
 */
function requireGuest(redirectTo = null) {
    const token = localStorage.getItem("bt_token");
    const user  = JSON.parse(localStorage.getItem("bt_user") || "null");

    if (token && user && !_isTokenExpired(token)) {
        const dest = redirectTo || _getDashboardByRole(user.role);
        window.location.href = dest;
        return false;
    }
    return true;
}

/**
 * Retourne l'utilisateur connecté ou null.
 */
function getAuthUser() {
    const token = localStorage.getItem("bt_token");
    const user  = JSON.parse(localStorage.getItem("bt_user") || "null");
    if (!token || !user || _isTokenExpired(token)) return null;
    return user;
}

/**
 * Déconnecte l'utilisateur et redirige vers l'accueil.
 */
function authLogout() {
    localStorage.removeItem("bt_token");
    localStorage.removeItem("bt_user");
    sessionStorage.clear();
    window.location.href = "index.html";
}

// ── Fonctions internes ────────────────────────────────────────

function _isTokenExpired(token) {
    try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        return payload.exp && Date.now() / 1000 > payload.exp;
    } catch {
        return true; // Token malformé = expiré
    }
}

function _redirectWithMessage(page, message) {
    sessionStorage.setItem("auth_message", message);
    window.location.href = page;
}

function _getDashboardByRole(role) {
    if (role === "admin")     return "admin.html";
    if (role === "organizer") return "organisation_dashboard.html";
    if (role === "agent")     return "scanner.html";
    return "user_dashboard.html";
}

// Affiche le message d'auth si présent (à appeler sur login.html)
function showAuthMessage() {
    const msg = sessionStorage.getItem("auth_message");
    if (msg) {
        sessionStorage.removeItem("auth_message");
        if (typeof btToast === "function") btToast(msg, "error");
    }
}
