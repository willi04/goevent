// ============================================================
//  GoEvent — bt-api.js v4
//  Design fidèle Stitch · Material Symbols · Pas d'émojis
// ============================================================

const BT_API ="https://api.goevent.africa";
// ──────────────────────────────────────────────────────────────
// SENTRY FRONTEND — Tracking erreurs JS côté browser
// ⚠️ Remplace la DSN ci-dessous par ta vraie DSN frontend Sentry
// ──────────────────────────────────────────────────────────────
const SENTRY_DSN_FRONTEND = "https://9ee6e572b3037059eeb78b655a5cb0f7@o4511610783793152.ingest.de.sentry.io/4511610811449424";
const SENTRY_ENV_FE = window.location.hostname === "goevent.africa" ? "production" : "development";

(function loadSentryFE() {
  if (!SENTRY_DSN_FRONTEND || SENTRY_DSN_FRONTEND.includes("VOTRE_DSN")) {
    console.log("[Sentry FE] Désactivé (DSN non configurée)");
    return;
  }
  const script = document.createElement("script");
  script.src = "https://browser.sentry-cdn.com/8.40.0/bundle.tracing.min.js";
  script.crossOrigin = "anonymous";
  script.onload = function () {
    Sentry.init({
      dsn: SENTRY_DSN_FRONTEND,
      environment: SENTRY_ENV_FE,
      release: "goevent-fe@1.0.0",
      tracesSampleRate: 0.1,
      ignoreErrors: [
        "Failed to fetch",
        "NetworkError",
        "Load failed",
        "Erreur serveur",
        "Erreur de chargement",
      ],
      beforeSend(event) {
        try {
          const user = btAuth.getUser();
          if (user && user.id) {
            event.user = { id: String(user.id), username: "user_" + user.id };
            event.tags = event.tags || {};
            event.tags.user_role = user.role || "guest";
          }
        } catch (e) {}
        return event;
      },
    });
    console.log("[Sentry FE] Activé — env=" + SENTRY_ENV_FE);
  };
  document.head.appendChild(script);
})();

// ── Auth ──────────────────────────────────────────────────────
const btAuth = {
  getToken: () => localStorage.getItem("bt_token"),
  getRefreshToken: () => localStorage.getItem("bt_refresh_token"),
  getUser: () => JSON.parse(localStorage.getItem("bt_user") || "null"),
  isLogged: () => !!localStorage.getItem("bt_token"),
  
  save(token, refreshToken, user) {
    localStorage.setItem("bt_token", token);
    if (refreshToken) localStorage.setItem("bt_refresh_token", refreshToken);
    if (user) localStorage.setItem("bt_user", JSON.stringify(user));
  },
  
  logout() {
    localStorage.removeItem("bt_token");
    localStorage.removeItem("bt_refresh_token");
    localStorage.removeItem("bt_user");
    window.location.href = "index.html";
  },
};

function btDashRoute(role) {
  if (role === "admin") return "admin.html";
  if (role === "staff") return "admin.html";     // ← staff partage le back-office admin
  if (role === "agent") return "scanner.html";
  if (role === "organizer") return "organisation_dashboard.html";
  return "user_dashboard.html";
}

// ── API fetch ─────────────────────────────────────────────────
async function btFetch(path, opts) {
  opts = opts || {};
  var token = btAuth.getToken();
  
  var reqOpts = Object.assign({}, opts, {
    headers: Object.assign(
      { "Content-Type": "application/json" },
      token ? { Authorization: "Bearer " + token } : {},
      opts.headers || {},
    ),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  var res = await fetch(BT_API + path, reqOpts);

  // 🔒 GESTION DU 401 : Tentative de rafraîchissement invisible
  if (res.status === 401) {
    var refreshToken = btAuth.getRefreshToken();
    var currentPage = window.location.pathname;
    var isAuthPage = currentPage.includes('login.html')
                  || currentPage.includes('signup.html')
                  || currentPage.includes('admin-login.html');

    if (refreshToken && !path.includes("/auth/refresh")) {
      try {
        var refreshRes = await fetch(BT_API + "/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken })
        });

        if (refreshRes.ok) {
          var refreshData = await refreshRes.json();
          btAuth.save(refreshData.access_token, refreshToken, null);
          reqOpts.headers.Authorization = "Bearer " + refreshData.access_token;
          res = await fetch(BT_API + path, reqOpts);
        } else {
          throw new Error("Refresh Token expiré");
        }
      } catch (e) {
        if (!isAuthPage) {
          btAuth.logout();
          return;
        }
      }
    } else if (!isAuthPage) {
      btAuth.logout();
      return;
    }
  }

  var data = await res.json().catch(function () {
    return {};
  });

  if (!res.ok) throw new Error(_btFormatApiError(data, res.status));
  return data;
}

// Normalise les erreurs FastAPI (string, array Pydantic 422, objet, ou rien)
function _btFormatApiError(data, status) {
  if (!data) return "Erreur " + status;
  var d = data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // Format Pydantic v2 : [{loc:["body","event_date"], msg:"...", type:"..."}, ...]
    return d.map(function (e) {
      var loc = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null;
      var label = _btFieldLabel(loc) || loc || "champ";
      return label + " : " + (e.msg || "invalide");
    }).join(" • ");
  }
  if (d && typeof d === "object") {
    return d.message || JSON.stringify(d);
  }
  return "Erreur " + status;
}

// Traduit les noms de champs techniques en libellés humains
function _btFieldLabel(field) {
  var map = {
    "title": "Titre",
    "description": "Description",
    "location": "Lieu",
    "category": "Catégorie",
    "event_date": "Date de l'événement",
    "price": "Prix",
    "total_seats": "Nombre de places",
    "cover_image_url": "Affiche",
    "ticket_tiers": "Catégories de billets",
    "phone_number": "Téléphone",
    "pin_code": "Code PIN",
    "email": "Email",
    "full_name": "Nom complet",
    "org_name": "Nom de l'organisation",
    "orange_money": "Numéro Orange Money",
    "amount": "Montant",
    "event_id": "Événement",
    "code": "Code",
    "discount_type": "Type de réduction",
    "discount_value": "Valeur de la réduction",
    "old_pin": "Ancien PIN",
    "new_pin": "Nouveau PIN",
    "refresh_token": "Session"
  };
  return map[field] || null;
}

var btApi = {
  get: function (p) {
    return btFetch(p);
  },
  post: function (p, b) {
    return btFetch(p, { method: "POST", body: b });
  },
  put: function (p, b) {
    return btFetch(p, { method: "PUT", body: b });
  },
  patch: function (p, b) {
    return btFetch(p, { method: "PATCH", body: b });
  },
  delete: function (p) {
    return btFetch(p, { method: "DELETE" });
  },
};

// ── Toast Stitch style ────────────────────────────────────────
function btToast(msg, type, duration ) {
  type = type || "success";
  duration = duration || 4000;
  document.querySelectorAll(".bt-toast").forEach(function (t) {
    t.remove();
  });
  var el = document.createElement("div");
  el.className = "bt-toast";
  var bg = type === "error" ? "#ba1a1a" 
         : type === "info"  ? "#3b82f6" 
         : "#00502c";
  el.style.cssText = [
    "position:fixed",
    "bottom:24px",
    "right:24px",
    "z-index:99999",
    "display:flex",
    "align-items:center",
    "gap:12px",
    "background:" + bg,
    "color:#fff",
    "padding:14px 20px",
    "border-radius:12px",
    "font-family:Manrope,sans-serif",
    "font-weight:600",
    "font-size:14px",
    "max-width:400px",
    "box-shadow:0 8px 32px rgba(0,0,0,.18)",
    "animation:btIn .3s cubic-bezier(.175,.885,.32,1.275)",
    "cursor:pointer",
  ].join(";");
 var icon = type === "error" ? "error" 
           : type === "info"  ? "info" 
           : "check_circle";
  el.innerHTML =
    '<span class="material-symbols-outlined" style="font-size:1.3rem;font-variation-settings:\'FILL\' 1">' +
    icon +
    "</span><span>" +
    msg +
    "</span>";
  el.onclick = function () {
    el.remove();
  };
  document.body.appendChild(el);
  setTimeout(function () {
    el.style.opacity = "0";
    el.style.transition = "opacity .3s";
  }, duration - 300);
  setTimeout(function () {
    el.remove();
  }, duration);
}

// ── Formatters ────────────────────────────────────────────────
function btPrice(p) {
  if (!p || p == 0) return "Gratuit";
  var n = Number(p);
  if (n >= 1000) {
    var k = Math.floor(n / 1000);
    var r = n % 1000;
    return k + (r ? "." + String(r).padStart(3, "0") : ".000") + " FCFA";
  }
  return n + " FCFA";
}

function btDate(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  var days = [
    "Dimanche",
    "Lundi",
    "Mardi",
    "Mercredi",
    "Jeudi",
    "Vendredi",
    "Samedi",
  ];
  var months = [
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
  ];
  return (
    days[d.getDay()] +
    " " +
    d.getDate() +
    " " +
    months[d.getMonth()] +
    " " +
    d.getFullYear() +
    " · " +
    String(d.getHours()).padStart(2, "0") +
    "h" +
    String(d.getMinutes()).padStart(2, "0")
  );
}

function btDateShort(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  var months = [
    "Jan",
    "Fév",
    "Mar",
    "Avr",
    "Mai",
    "Jun",
    "Jul",
    "Aoû",
    "Sep",
    "Oct",
    "Nov",
    "Déc",
  ];
  return d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear();
}

function btInitials(name) {
  return (name || "U")
    .split(" ")
    .slice(0, 2)
    .map(function (s) {
      return s[0] || "";
    })
    .join("")
    .toUpperCase();
}

// ── CSS global ────────────────────────────────────────────────
(function () {
  var s = document.createElement("style");
  s.textContent = [
    ":root{--om-primary:#FF7900;--om-dark:#E66A00;--om-light:#FFF3E6;}",
    ".bg-om-primary{background-color:var(--om-primary)!important}",
    ".bg-om-light{background-color:var(--om-light)!important}",
    ".text-om-primary{color:var(--om-primary)!important}",
    ".text-om-dark{color:var(--om-dark)!important}",
    ".border-om-primary{border-color:var(--om-primary)!important}",
    ".ring-om-primary{--tw-ring-color:var(--om-primary)!important}",
    "@keyframes btIn{from{transform:translateY(16px) scale(.95);opacity:0}to{transform:none;opacity:1}}",
    ".bt-loading{opacity:.6!important;pointer-events:none!important;cursor:not-allowed!important}",
    // Modal overlay
    ".bt-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9000;display:flex;align-items:center;justify-content:center;padding:16px;animation:btIn .2s ease}",
    ".bt-dialog{background:#fff;border-radius:1.5rem;padding:32px;width:100%;max-width:520px;max-height:90vh;overflow-y:auto;font-family:Manrope,sans-serif;box-shadow:0 24px 64px rgba(0,0,0,.15)}",
    '.bt-dialog h3{font-family:"Plus Jakarta Sans",sans-serif;font-weight:800;font-size:1.3rem;margin-bottom:18px;color:#1a1c1c}',
    // Form elements
    ".bt-field{margin-bottom:16px}",
    ".bt-label{display:block;font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#6f7a70;margin-bottom:6px}",
    ".bt-input{width:100%;padding:14px 16px;border-radius:.75rem;border:1.5px solid #e2e2e2;font-size:.9rem;font-family:Manrope,sans-serif;outline:none;transition:border-color .2s,background .2s;background:#f3f3f3;box-sizing:border-box;color:#1a1c1c}",
    ".bt-input:focus{border-color:#00502c;background:#fff}",
    ".bt-input::placeholder{color:#6f7a70}",
    // Buttons
    ".bt-btn-primary{width:100%;padding:14px;border-radius:9999px;background:linear-gradient(to right,#00502c,#006b3c);color:#fff;font-weight:700;font-size:.95rem;border:none;cursor:pointer;font-family:Manrope,sans-serif;box-shadow:0 4px 14px rgba(0,80,44,.3);transition:transform .2s,opacity .2s;margin-top:4px}",
    ".bt-btn-primary:hover{opacity:.9;transform:scale(1.02)}",
    ".bt-btn-primary:active{transform:scale(.97)}",
    ".bt-btn-outline{width:100%;padding:14px;border-radius:9999px;background:#fff;color:#1a1c1c;font-weight:700;font-size:.95rem;border:1.5px solid #e2e2e2;cursor:pointer;font-family:Manrope,sans-serif;transition:all .2s;margin-top:4px}",
    ".bt-btn-outline:hover{border-color:#00502c;color:#00502c}",
    ".bt-g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}",
    // Sidebar links
    ".sb-link{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:.75rem;font-size:.9rem;font-weight:500;color:#3f4941;cursor:pointer;transition:all .15s;background:transparent;border:none;width:100%;text-align:left;font-family:Manrope,sans-serif;text-decoration:none;margin-bottom:2px}",
    ".sb-link:hover{background:#f3f3f3;color:#00502c}",
    ".sb-link.active{background:#9df6b9;color:#00210f;font-weight:700}",
    ".sb-link .material-symbols-outlined{font-size:1.2rem;flex-shrink:0}",
  ].join("");
  document.head.appendChild(s);
})();

// ── NAVBAR — Design fidèle Stitch ─────────────────────────────
function btNavbar(activePage) {
  activePage = activePage || "";
  var user = btAuth.getUser();
  var isLogged = btAuth.isLogged();
  var role = user && user.role;

  var links = [
    { label: "Accueil", href: "index.html", key: "accueil" },
    { label: "Événements", href: "event.html", key: "evenements" },
    { label: "Follow", href: "follow.html", key: "follow" },
    { label: "Partenaire", href: "partenaire.html", key: "partenaire" },
    { label: "À propos", href: "a_propos.html", key: "apropos" },
  ];

  var navLinks = links
    .map(function (l) {
      var isActive = activePage === l.key;
      // Onglet actif = pill verte comme le bouton S'inscrire
      var cls = isActive
        ? "px-6 py-2.5 rounded-full text-sm font-bold bg-gradient-to-r from-primary to-primary-container text-white shadow-md shadow-primary/20 transition-all"
        : "text-neutral-600 hover:text-emerald-700 transition-colors font-medium text-sm";
      return '<a href="' + l.href + '" class="' + cls + '">' + l.label + "</a>";
    })
    .join("");

  var authBtns;
  if (isLogged) {
    var initials = btInitials(user && user.name);
    var dashUrl = btDashRoute(role);
    authBtns =
      '<a href="' +
      dashUrl +
      '" class="px-5 py-2.5 text-emerald-900 font-semibold hover:bg-surface-container-low rounded-full transition-all text-sm">' +
      initials +
      " — Mon espace</a>" +
      '<button onclick="btAuth.logout()" class="px-5 py-2.5 text-red-600 font-semibold hover:bg-red-50 rounded-full transition-all text-sm">Déconnexion</button>';
  } else {
    authBtns =
      '<a href="login.html" class="px-5 py-2.5 rounded-full text-sm font-bold text-emerald-900 hover:bg-surface-container-low transition-all">Se connecter</a>' +
      '<a href="signup.html" class="px-6 py-2.5 rounded-full text-sm font-bold bg-gradient-to-r from-primary to-primary-container text-white shadow-lg shadow-primary/20 hover:scale-105 active:scale-95 transition-all">S\'inscrire</a>';
  }

  return (
    '<nav class="fixed top-0 w-full z-50 bg-white/70 backdrop-blur-xl shadow-sm shadow-emerald-900/5 h-20 px-8 flex justify-between items-center max-w-full">' +
    '<div class="flex items-center gap-12">' +
    '<a href="index.html" class="flex items-center gap-2 transition-opacity">' +
    '<img src="icons/event.png" alt="Logo" class="w-10 h-10 rounded-3xl shadow-sm" />' +
    '<span class="text-2xl font-bold tracking-tight bg-gradient-to-r from-emerald-900 to-emerald-700 bg-clip-text text-transparent font-headline">GoEvent</span>' +
    "</a>" +
    '<div class="hidden md:flex items-center gap-6 font-headline">' +
    navLinks +
    "</div>" +
    "</div>" +
    '<div class="flex items-center gap-3">' +
    authBtns +
    "</div>" +
    "</nav>"
  );
}

// ── FOOTER — Design Stitch ────────────────────────────────────
function btFooter() {
  return (
    '<footer class="bg-neutral-100 mt-12">' +
    // LA CORRECTION EST ICI : lg:grid-cols-6 pour avoir la place pour tout le monde
    '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-8 px-8 md:px-16 py-16 max-w-7xl mx-auto">' +
    // Brand (Prend 2 colonnes sur tablette et grand écran)
    '<div class="sm:col-span-2 lg:col-span-2">' +
    '<a href="index.html" class="flex items-center gap-2 transition-opacity mb-4">' +
    '<img src="icons/event.png" alt="Logo" class="w-10 h-10 rounded-3xl shadow-sm" />' +
    '<span class="text-2xl font-bold tracking-tight bg-gradient-to-r from-emerald-900 to-emerald-700 bg-clip-text text-transparent font-headline">GoEvent</span>' +
    "</a>" +
    '<p class="text-neutral-500 text-sm leading-relaxed mb-6 font-body max-w-sm">La première plateforme de billetterie en ligne en République Centrafricaine. Soutenons ensemble nos artistes.</p>' +
    '<div class="flex gap-3">' +
    '<a href="#" class="text-primary hover:opacity-100 opacity-70 transition-opacity"><span class="material-symbols-outlined">social_leaderboard</span></a>' +
    '<a href="#" class="text-primary hover:opacity-100 opacity-70 transition-opacity"><span class="material-symbols-outlined">alternate_email</span></a>' +
    '<a href="#" class="text-primary hover:opacity-100 opacity-70 transition-opacity"><span class="material-symbols-outlined">phone_android</span></a>' +
    "</div>" +
    "</div>" +
    // Navigation
    "<div>" +
    '<h4 class="font-headline text-xs font-bold tracking-widest uppercase text-emerald-900 mb-6">Navigation</h4>' +
    '<ul class="space-y-3 text-sm text-neutral-500">' +
    '<li><a href="index.html" class="hover:text-primary transition-colors">Accueil</a></li>' +
    '<li><a href="event.html" class="hover:text-primary transition-colors">Événements</a></li>' +
    '<li><a href="follow.html" class="hover:text-primary transition-colors">Follow</a></li>' +
    '<li><a href="a_propos.html" class="hover:text-primary transition-colors">À propos</a></li>' +
    "</ul>" +
    "</div>" +
    // Partenaires
    "<div>" +
    '<h4 class="font-headline text-xs font-bold tracking-widest uppercase text-emerald-900 mb-6">Partenaires</h4>' +
    '<ul class="space-y-3 text-sm text-neutral-500">' +
    '<li><a href="partenaire.html" class="hover:text-primary transition-colors">Devenir partenaire</a></li>' +
    '<li><a href="partenaire.html#avantages" class="hover:text-primary transition-colors">Nos avantages</a></li>' +
    '<li><a href="partenaire.html#contact" class="hover:text-primary transition-colors">Nous contacter</a></li>' +
    "</ul>" +
    "</div>" +
    // Assistance
    "<div>" +
    '<h4 class="font-headline text-xs font-bold tracking-widest uppercase text-emerald-900 mb-6">Assistance</h4>' +
    '<ul class="space-y-3 text-sm text-neutral-500">' +
    '<li><a href="feedback.html" class="hover:text-primary transition-colors">Feedback</a></li>' +
    '<li><a href="feedback.html#noter" class="hover:text-primary transition-colors">Noter l\'application</a></li>' +
    '<li><a href="centre_aide.html" class="hover:text-primary transition-colors">Centre d\'aide</a></li>' +
    '<li><a href="centre_aide.html" class="hover:text-primary transition-colors">Support WhatsApp</a></li>' +
    "</ul>" +
    "</div>" +
    // Légal + paiement
    "<div>" +
    '<h4 class="font-headline text-xs font-bold tracking-widest uppercase text-emerald-900 mb-6">Légal</h4>' +
    '<ul class="space-y-3 text-sm text-neutral-500 mb-6">' +
    '<li><a href="cgu.html" class="hover:text-primary transition-colors">Conditions Générales</a></li>' +
    '<li><a href="confidentialite.html" class="hover:text-primary transition-colors">Confidentialité</a></li>' +
    '<li><a href="remboursements.html" class="hover:text-primary transition-colors">Remboursements</a></li>' +
    "</ul>" +
    '<div class="bg-neutral-200 p-4 rounded-xl">' +
    '<div class="flex items-center gap-2 text-emerald-900 mb-1">' +
    '<span class="material-symbols-outlined text-sm" style="font-variation-settings:\'FILL\' 1">payments</span>' +
    '<span class="text-xs font-black font-headline uppercase">Orange Money</span>' +
    "</div>" +
    '<p class="text-[11px] text-neutral-500">Payez en toute sécurité avec votre compte Orange Money local.</p>' +
    "</div>" +
    "</div>" +
    "</div>" + // Fin de la grille
    // Copyright
    '<div class="border-t border-neutral-200 py-8 px-8 md:px-16 text-center">' +
    '<p class="text-neutral-400 text-xs font-headline font-bold uppercase tracking-widest">© 2026 GoEvent | Tous Droits Réservés.</p>' +
    "</div>" +
    "</footer>"
  );
}

// ── Carte événement (style Stitch exact) ──────────────────────
function btEventCard(e) {
  var pct = e.total_seats > 0 ? Math.round((1 - e.seats_available / e.total_seats) * 100) : 0;
  var sold = e.is_sold_out;
  
  // 1. On récupère la date de l'événement et celle d'aujourd'hui
  var d = e.event_date ? new Date(e.event_date) : null;
  var isPast = d ? (d < new Date()) : false; // VRAI si l'événement est déjà passé

  var months = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"];
  var dateStr = d ? d.getDate() + " " + months[d.getMonth()] + ", " + d.getFullYear() : "";
  
  // 2. On adapte les couleurs et les textes en fonction de la date
  var barColor = isPast ? "bg-gray-400" : (sold ? "bg-error" : pct > 70 ? "bg-amber-500" : "bg-primary");
  var barLabel = isPast ? "Événement terminé" : (sold ? "Guichet fermé" : pct > 70 ? "Derniers billets ! Vendus: " + pct + "%" : "Vendus: " + pct + "%");
  var seatsLabel = isPast ? "Passé" : (sold ? "Complet" : "Il reste " + e.seats_available + " places");
  var btnLabel = isPast ? "Terminé" : (sold ? "Complet" : "Acheter");
  var btnIcon = isPast ? "history" : (sold ? "block" : "shopping_cart");
  
  // Si c'est passé OU complet, on bloque le bouton
  var disabledBtn = isPast || sold; 

  return (
    '<div class="bg-surface-container-lowest rounded-xl overflow-hidden group transition-shadow flex flex-col ' + 
    (isPast ? 'opacity-75 grayscale-[30%]' : 'hover:shadow-xl cursor-pointer') + '" ' +
    (disabledBtn ? '' : 'onclick="window.location=\'event_detail.html?id=' + e.id + '\'"') +
    '>' +
    '<div class="relative h-64">' +
    (e.cover_image_url ? '<img class="w-full h-full object-cover ' + (disabledBtn ? '' : 'group-hover:scale-105') + ' transition-transform duration-500" src="' + e.cover_image_url + '" alt="' + e.title + '" onerror="this.style.display=\'none\'">' : "") +
    '<div class="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>' +
    '<div class="absolute top-4 right-4 bg-secondary-container text-on-secondary-container px-4 py-2 rounded-lg font-black font-headline shadow-lg text-sm">' +
    btPrice(e.price) +
    "</div>" +
    "</div>" +
    '<div class="p-6 flex-1 flex flex-col">' +
    '<div class="flex items-center gap-2 text-outline text-sm mb-3">' +
    '<span class="material-symbols-outlined text-sm">calendar_today</span>' +
    "<span>" + dateStr + "</span>" +
    "</div>" +
    '<h3 class="text-xl font-bold font-headline mb-2 text-on-surface">' + e.title + "</h3>" +
    '<p class="text-on-surface-variant text-sm mb-6 flex items-center gap-1">' +
    '<span class="material-symbols-outlined text-sm">person</span>' + (e.organizer || "Organisateur") +
    "</p>" +
    '<div class="mt-auto">' +
    '<div class="mb-4">' +
    '<div class="flex justify-between text-xs font-bold text-outline mb-1">' +
    "<span>" + barLabel + "</span>" +
    "<span>" + seatsLabel + "</span>" +
    "</div>" +
    '<div class="w-full bg-surface-container-high h-2 rounded-full overflow-hidden">' +
    '<div class="' + barColor + ' h-full rounded-full transition-all duration-700" style="width:' + (isPast ? '100' : pct) + '%"></div>' +
    "</div>" +
    "</div>" +
    (disabledBtn
      ? '<button disabled class="w-full bg-surface-container text-outline py-4 rounded-xl font-bold flex items-center justify-center gap-2 cursor-not-allowed"><span class="material-symbols-outlined">' + btnIcon + '</span> ' + btnLabel + '</button>'
      : "<button onclick=\"event.stopPropagation();window.location='event_detail.html?id=" + e.id + '\'" class="w-full bg-gradient-to-r from-primary to-primary-container text-white py-4 rounded-xl font-bold flex items-center justify-center gap-2 hover:opacity-90 active:scale-95 transition-all shadow-md shadow-primary/10">' +
        '<span class="material-symbols-outlined">' + btnIcon + "</span> " + btnLabel + "</button>") +
    "</div>" +
    "</div>" +
    "</div>"
  );
}
// ── BOTTOM NAV MOBILE — Persistante sur pages publiques ───────
function btMobileBottomNav(activePage) {
  activePage = activePage || "";
  var user = btAuth.getUser();
  var isLogged = btAuth.isLogged();

  // 3 onglets fixes orientés business : Accueil, Events, Partenaires (recrutement orgas/annonceurs)
  // + 1 contextualisé selon état auth
  var tabs = [
    { key: "accueil",     href: "index.html",      icon: "home",      label: "Accueil" },
    { key: "evenements",  href: "event.html",      icon: "stadium",   label: "Events" },
    { key: "partenaire",  href: "partenaire.html", icon: "handshake", label: "Partenaires" },
  ];

  if (isLogged) {
    tabs.push({
      key: "compte",
      href: btDashRoute(user && user.role),
      icon: "person",
      label: "Espace",
    });
  } else {
    tabs.push({
      key: "login",
      href: "login.html",
      icon: "login",
      label: "Connexion",
    });
  }

  var tabsHtml = tabs
    .map(function (t) {
      var isActive = activePage === t.key;
      var colorCls = isActive ? "text-primary" : "text-gray-500";
      var fillStyle = isActive ? "font-variation-settings:'FILL' 1;" : "";
      return (
        '<a href="' +
        t.href +
        '" class="flex flex-col items-center justify-center gap-0.5 flex-1 active:scale-95 transition-transform">' +
        '<span class="material-symbols-outlined ' +
        colorCls +
        '" style="font-size:24px;' +
        fillStyle +
        '">' +
        t.icon +
        "</span>" +
        '<span class="text-[10px] font-bold ' +
        colorCls +
        '">' +
        t.label +
        "</span>" +
        "</a>"
      );
    })
    .join("");

  return (
    '<nav class="md:hidden fixed bottom-0 left-0 right-0 z-[90] bg-white/95 backdrop-blur border-t border-gray-100 flex items-stretch h-[60px]" style="padding-bottom:env(safe-area-inset-bottom)">' +
    tabsHtml +
    "</nav>"
  );
}
// ── DRAWER MOBILE : HEADER (carte user connecté ou mini logo guest) ──
// Remplace l'ancien titre "Menu" (redondant : cliquer sur hamburger implique menu).
// Optimisation : récupère 60-80px verticaux pour la nav.
function btMobileDrawerHeader() {
  var closeBtn =
    '<button onclick="toggleMobileMenu()" class="p-2 text-gray-500 hover:text-red-500 rounded-full hover:bg-red-50 transition-colors shrink-0">' +
      '<span class="material-symbols-outlined">close</span>' +
    '</button>';

  if (!btAuth.isLogged()) {
    // Guest : mini logo GoEvent + tagline court
    return (
      '<img src="icons/event.png" alt="GoEvent" class="w-9 h-9 rounded-xl shadow-sm shrink-0" />' +
      '<div class="flex-1 min-w-0">' +
        '<p class="font-black text-sm bg-gradient-to-r from-emerald-900 to-emerald-700 bg-clip-text text-transparent font-headline">GoEvent</p>' +
        '<p class="text-[11px] text-gray-500 truncate">Billetterie mobile RCA</p>' +
      '</div>' +
      closeBtn
    );
  }

  // Connecté : carte cliquable → dashboard
  var user = btAuth.getUser() || {};
  var name = _btEscapeHtml(user.full_name || user.name || "Membre GoEvent");
  var email = _btEscapeHtml(user.email || user.phone_number || "");
  var initial = (name.trim().charAt(0) || "?").toUpperCase();
  var dashUrl = (typeof btDashRoute === "function") ? btDashRoute(user.role) : "user_dashboard.html";

  return (
    '<a href="' + dashUrl + '" class="flex items-center gap-3 flex-1 min-w-0 rounded-xl -m-1 p-1 hover:bg-gray-50 active:scale-[0.98] transition-all">' +
      '<div class="w-11 h-11 rounded-full bg-primary text-white flex items-center justify-center font-bold text-base shrink-0">' + initial + '</div>' +
      '<div class="flex-1 min-w-0">' +
        '<p class="font-bold text-sm text-gray-900 truncate">' + name + '</p>' +
        (email ? '<p class="text-[11px] text-gray-500 truncate">' + email + '</p>' : '') +
      '</div>' +
    '</a>' +
    closeBtn
  );
}
// ── Modal générique ───────────────────────────────────────────
function btModal(contentHtml) {
  var ov = document.createElement("div");
  ov.className = "bt-overlay";
  ov.innerHTML = '<div class="bt-dialog">' + contentHtml + "</div>";
  ov.addEventListener("click", function (e) {
    if (e.target === ov) ov.remove();
  });
  document.body.appendChild(ov);
  return ov;
}

// Remplacé par btMobileDrawerHeader (fusion carte user + close en tête du drawer).
// Conservé vide pour rétro-compatibilité avec les pages qui l'appellent encore.
function btMobileAuthSection() {
  return "";
}

// ── DRAWER MOBILE : SECTIONS DU BAS (Aide + Support + Inviter) ─
function btMobileDrawerSections() {
  var supportPhone = "23672259398";
  var inviteText = encodeURIComponent(
    "Salut ! Je viens de découvrir GoEvent, la plateforme de billetterie en RCA. " +
    "Tu peux acheter tes billets en ligne via Orange Money. " +
    "Découvre : https://goevent.africa"
  );

  var logoutBlock = "";
  if (btAuth.isLogged()) {
    logoutBlock =
      '<div class="mt-3 pt-3 border-t border-gray-100">' +
        '<button onclick="btAuth.logout(); window.location.reload();" class="flex items-center justify-center gap-2 w-full py-3 rounded-2xl text-red-600 bg-red-50 font-bold text-sm active:scale-95 transition-all hover:bg-red-100">' +
          '<span class="material-symbols-outlined text-base">power_settings_new</span>' +
          'Déconnexion' +
        '</button>' +
      '</div>';
  }

  return (
    '<div class="space-y-1">' +
      '<a href="centre_aide.html" class="flex items-center gap-3 p-3 rounded-2xl text-gray-700 font-semibold hover:bg-primary/5 hover:text-primary transition-all text-sm">' +
        '<span class="material-symbols-outlined text-base text-gray-500">help</span>' +
        'Aide & FAQ' +
      '</a>' +
      '<a href="https://wa.me/' + supportPhone + '" target="_blank" rel="noopener" class="flex items-center gap-3 p-3 rounded-2xl text-gray-700 font-semibold hover:bg-green-50 hover:text-green-700 transition-all text-sm">' +
        '<span class="material-symbols-outlined text-base text-green-600">chat</span>' +
        'Support WhatsApp' +
      '</a>' +
      '<a href="https://wa.me/?text=' + inviteText + '" target="_blank" rel="noopener" class="flex items-center gap-3 p-3 rounded-2xl text-gray-700 font-semibold hover:bg-primary/5 hover:text-primary transition-all text-sm">' +
        '<span class="material-symbols-outlined text-base text-gray-500">person_add</span>' +
        'Inviter un ami' +
      '</a>' +
    '</div>' +
    logoutBlock
  );
}

// Helper : échappement HTML basique pour éviter injection via full_name
function _btEscapeHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Mes Billets ───────────────────────────────────────────────
// ── Affichage des billets (Dashboard) ──────────────────────────
async function btShowTickets() {
  // 1. On vérifie que la zone d'affichage existe dans le HTML
  var container = document.getElementById("tickets-container");
  if (!container) return;

  // 2. On affiche l'état de chargement
  container.innerHTML =
    '<p class="text-gray-500 font-medium py-4 text-center">Chargement de vos billets...</p>';

  try {
    // 3. On appelle notre nouvelle route FastAPI
    var tickets = await btApi.get("/tickets/my");

    // 4. Si la base de données ne renvoie aucun billet
    if (!tickets || tickets.length === 0) {
      container.innerHTML = `
        <div class="bg-white p-8 rounded-2xl text-center shadow-sm border border-gray-100 mt-4">
          <span class="material-symbols-outlined text-5xl text-gray-300 mb-3 block">confirmation_number</span>
          <p class="text-gray-600 font-bold text-lg">Vous n'avez aucun billet.</p>
          <p class="text-gray-400 text-sm mt-1">Vos futurs achats apparaîtront ici.</p>
        </div>`;
      return;
    }

    // 5. On boucle sur chaque billet (avec la fameuse variable "t" !)
    var html = tickets
      .map(function (t) {
        var ok = t.payment_status === "paye" || t.payment_status === "Valide";
        var statusLabel = ok ? "Valide" : "En attente de paiement";
        var statusCls = ok
          ? "bg-green-100 text-green-700 border border-green-200"
          : "bg-orange-100 text-orange-700 border border-orange-200";

        return (
          '<div class="bg-white border border-gray-100 shadow-sm rounded-[1.5rem] p-5 mb-4">' +
          // En-tête du billet (Titre, lieu, statut)
          '<div class="flex justify-between items-start mb-4">' +
          "<div>" +
          '<p class="font-black font-headline text-lg text-gray-900">' +
          (t.event_title || "Événement") +
          "</p>" +
          '<p class="text-sm text-gray-500 flex items-center gap-1 mt-1"><span class="material-symbols-outlined text-sm">location_on</span>' +
          (t.event_location || "-") +
          "</p>" +
          "</div>" +
          '<span class="px-3 py-1 rounded-full text-xs font-bold ' +
          statusCls +
          '">' +
          statusLabel +
          "</span>" +
          "</div>" +
          // Le QR Code (s'affiche uniquement si "ok" est vrai)
          (ok && t.qr_hash
            ? '<div class="my-6 flex flex-col items-center justify-center bg-gray-50 p-4 rounded-xl border-2 border-dashed border-gray-200">' +
              '<img src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=' +
              t.qr_hash +
              '" alt="QR Code" class="w-32 h-32 mix-blend-multiply opacity-90 mb-2">' +
              '<p class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Billet #' +
              t.ticket_id +
              "</p>" +
              "</div>"
            : "") +
          // Bas du billet (Prix et Bouton Simuler)
          '<div class="flex justify-between items-center mt-4 pt-4 border-t border-gray-100">' +
          '<p class="font-black text-primary text-xl">' +
          (t.price ? t.price.toLocaleString("fr-FR") + " FCFA" : "Gratuit") +
          "</p>" +
          // Le bouton simuler (s'affiche uniquement si "ok" est faux)
          "" +
          "</div>" +
          "</div>"
        );
      })
      .join("");

    // 6. On injecte le tout dans la page
    container.innerHTML = html;
  } catch (err) {
    console.error("Erreur chargement billets:", err);
    container.innerHTML =
      '<p class="text-red-500 font-bold p-4 bg-red-50 rounded-xl">Impossible de charger les billets. Vérifiez la connexion au serveur.</p>';
  }
}

// ── Favoris ───────────────────────────────────────────────────
async function btShowFavorites() {
  var ov = btModal(
    '<h3>Mes Favoris</h3><div id="fv-list"><p class="text-center text-outline py-8">Chargement...</p></div>',
  );
  try {
    var events = await btApi.get("/favorites");
    var list = document.getElementById("fv-list");
    if (!events.length) {
      list.innerHTML =
        '<div class="text-center py-8"><span class="material-symbols-outlined text-4xl text-outline block mb-3">favorite</span><p class="text-outline mb-4">Aucun favori</p><a href="event.html" class="text-primary font-bold hover:underline">Découvrir des événements</a></div>';
      return;
    }
    list.innerHTML = events
      .map(function (e) {
        return (
          "<div onclick=\"window.location='event_detail.html?id=" +
          e.id +
          '\'" class="flex items-center gap-4 border border-outline-variant/30 rounded-xl p-4 mb-3 cursor-pointer hover:border-primary/40 transition-colors">' +
          '<div class="w-12 h-12 rounded-xl bg-gradient-to-br from-primary to-primary-container flex items-center justify-center flex-shrink-0">' +
          '<span class="material-symbols-outlined text-white text-xl" style="font-variation-settings:\'FILL\' 1">music_note</span>' +
          "</div>" +
          '<div class="flex-1 min-w-0"><p class="font-bold text-sm text-on-surface truncate">' +
          e.title +
          "</p>" +
          '<p class="text-xs text-outline flex items-center gap-1"><span class="material-symbols-outlined text-xs">location_on</span>' +
          e.location +
          "</p></div>" +
          '<span class="font-black font-headline text-sm text-primary flex-shrink-0">' +
          btPrice(e.price) +
          "</span>" +
          "</div>"
        );
      })
      .join("");
  } catch (e) {
    document.getElementById("fv-list").innerHTML =
      '<p class="text-error text-center py-4">' + e.message + "</p>";
  }
}

// ── Paramètres ────────────────────────────────────────────────
function btShowSettings() {
  var user = btAuth.getUser() || {};
  var isOrg = user.role === "organisation";
  var orgExtra = isOrg
    ? '<div class="bt-field"><label class="bt-label">Nom de l\'organisation</label>' +
      '<input class="bt-input" id="s-org" value="' +
      (user.org_name || "") +
      '" placeholder="Nom de votre organisation"></div>'
    : "";
  var ov = btModal(
    "<h3>Paramètres</h3>" +
      '<p class="text-xs font-bold uppercase tracking-widest text-outline mb-4">Profil</p>' +
      '<div class="bt-field"><label class="bt-label">Nom complet</label><input class="bt-input" id="s-name" value="' +
      (user.name || "") +
      '"></div>' +
      '<div class="bt-field"><label class="bt-label">Email</label><input class="bt-input" id="s-email" type="email" value="' +
      (user.email || "") +
      '" placeholder="votre@email.com"></div>' +
      orgExtra +
      '<button class="bt-btn-primary" id="s-save">Sauvegarder le profil</button>' +
      '<hr class="my-6 border-outline-variant/30">' +
      '<p class="text-xs font-bold uppercase tracking-widest text-outline mb-4">Sécurité — Changer le PIN</p>' +
      '<div class="bt-field"><label class="bt-label">Ancien PIN</label><input class="bt-input" id="s-old" type="password" maxlength="6" placeholder="Votre PIN actuel"></div>' +
      '<div class="bt-g2">' +
      '<div class="bt-field"><label class="bt-label">Nouveau PIN</label><input class="bt-input" id="s-new" type="password" maxlength="6" placeholder="Nouveau"></div>' +
      '<div class="bt-field"><label class="bt-label">Confirmer</label><input class="bt-input" id="s-conf" type="password" maxlength="6" placeholder="Confirmer"></div>' +
      "</div>" +
      '<button class="bt-btn-outline" id="s-pin">Modifier le PIN</button>',
  );
  document.getElementById("s-save").onclick = async function () {
    var btn = document.getElementById("s-save");
    btn.classList.add("bt-loading");
    btn.textContent = "Sauvegarde...";
    try {
      var body = {
        full_name: document.getElementById("s-name").value.trim(),
        email: document.getElementById("s-email").value.trim() || null,
      };
      if (isOrg) body.org_name = document.getElementById("s-org").value.trim();
      await btApi.put("/auth/me", body);
      Object.assign(user, body);
      localStorage.setItem("bt_user", JSON.stringify(user));
      btToast("Profil mis à jour avec succès.");
      ov.remove();
    } catch (e) {
      btToast(e.message, "error");
      btn.classList.remove("bt-loading");
      btn.textContent = "Sauvegarder le profil";
    }
  };
  document.getElementById("s-pin").onclick = async function () {
    var n = document.getElementById("s-new").value;
    if (!/^\d{6}$/.test(n)) {
      btToast("Le PIN doit comporter exactement 6 chiffres", "error");
      return;
    }
    if (n !== document.getElementById("s-conf").value) {
      btToast("Les deux PINs ne correspondent pas", "error");
      return;
    }
    try {
      await btApi.put("/auth/pin", {
        old_pin: document.getElementById("s-old").value,
        new_pin: n,
      });
      btToast("PIN modifié avec succès.");
      ov.remove();
    } catch (e) {
      btToast(e.message, "error");
    }
  };
}

// ── Feedback ──────────────────────────────────────────────────
function btShowFeedback() {
  var ov = btModal(
    "<h3>Feedback & Note</h3>" +
      '<p class="text-on-surface-variant text-sm mb-4">Votre avis nous aide à améliorer GoEvent pour toute la communauté</p>' +
      '<div class="bt-field">' +
      '<label class="bt-label">Note de l\'application</label>' +
      '<div class="flex gap-3 mt-2" id="bt-stars">' +
      [1, 2, 3, 4, 5]
        .map(function (i) {
          return (
            '<span onclick="btSetStar(' +
            i +
            ')" data-star="' +
            i +
            '" class="material-symbols-outlined cursor-pointer transition-all" style="font-size:2rem;color:#e2e2e2;font-variation-settings:\'FILL\' 0">star</span>'
          );
        })
        .join("") +
      "</div>" +
      "</div>" +
      '<div class="bt-field"><label class="bt-label">Catégorie</label>' +
      '<select class="bt-input" id="bt-fb-cat"><option>Bug / Erreur technique</option><option>Suggestion</option><option>Félicitations</option><option>Problème de paiement</option><option>Autre</option></select>' +
      "</div>" +
      '<div class="bt-field"><label class="bt-label">Votre message</label>' +
      '<textarea class="bt-input" id="bt-fb-msg" rows="4" placeholder="Décrivez votre expérience..." style="resize:vertical"></textarea>' +
      "</div>" +
      '<button class="bt-btn-primary" id="bt-fb-send">Envoyer le feedback</button>',
  );
  window._btStar = 0;
  window.btSetStar = function (n) {
    window._btStar = n;
    document.querySelectorAll("#bt-stars span").forEach(function (s, i) {
      if (i < n) {
        s.style.color = "#fecb00";
        s.style.fontVariationSettings = "'FILL' 1";
      } else {
        s.style.color = "#e2e2e2";
        s.style.fontVariationSettings = "'FILL' 0";
      }
    });
  };
  document.getElementById("bt-fb-send").onclick = function () {
    var msg = document.getElementById("bt-fb-msg").value.trim();
    if (!msg) {
      btToast("Veuillez écrire votre message", "error");
      return;
    }
    btToast("Merci pour votre retour. Nous en prenons note.");
    ov.remove();
  };
}

// ── Sidebar wiring ────────────────────────────────────────────
function btWireSidebar(role) {
  document.querySelectorAll("[data-bt]").forEach(function (el) {
    var action = el.getAttribute("data-bt");
    el.addEventListener("click", function (e) {
      e.preventDefault();
      if (action === "tickets") btShowTickets();
      if (action === "favorites") btShowFavorites();
      if (action === "settings") btShowSettings();
      if (action === "feedback") btShowFeedback();
      if (action === "logout") btAuth.logout();
    });
  });
}

// ═══════════════════════════════════════════════════════════════
// COMPRESSEUR IMAGE — bt-api.js
// Compresse une image (File) en JPG ≤ 1 Mo, max 1920×1080.
// Qualité dynamique 85% → 60%. PNG transparent → JPG fond blanc.
// ═══════════════════════════════════════════════════════════════

async function btCompressImage(file, options) {
  options = options || {};
  var maxWidth = options.maxWidth || 1920;
  var maxHeight = options.maxHeight || 1080;
  var targetBytes = options.targetBytes || 1024 * 1024;  // 1 Mo
  var maxBytes = options.maxBytes || 10 * 1024 * 1024;   // 10 Mo brut
  var skipBelow = options.skipBelow || 1024 * 1024;      // < 1 Mo : skip

  if (!(file instanceof File) && !(file instanceof Blob)) {
    throw new Error("btCompressImage attend un File ou Blob");
  }
  if (file.size > maxBytes) {
    throw new Error("Image trop lourde (max " + Math.round(maxBytes / 1024 / 1024) + " Mo)");
  }

  // Skip compression si déjà sous la cible ET pas de redimensionnement nécessaire
  // On charge quand même l'image pour vérifier les dimensions
  var img = await _btLoadImage(file);

  var needResize = img.width > maxWidth || img.height > maxHeight;
  var needCompress = file.size > skipBelow;

  if (!needResize && !needCompress) {
    return await _btFileToBase64(file);
  }

  // Calcul des dimensions cibles (preserve ratio)
  var ratio = Math.min(maxWidth / img.width, maxHeight / img.height, 1);
  var targetW = Math.round(img.width * ratio);
  var targetH = Math.round(img.height * ratio);

  // Canvas avec fond blanc (gère la transparence PNG)
  var canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = targetH;
  var ctx = canvas.getContext("2d");
  ctx.fillStyle = "#FFFFFF";
  ctx.fillRect(0, 0, targetW, targetH);
  ctx.drawImage(img, 0, 0, targetW, targetH);

  // Compression dynamique par paliers
  var qualities = [0.85, 0.80, 0.75, 0.70, 0.65, 0.60];
  var bestBase64 = null;
  for (var i = 0; i < qualities.length; i++) {
    var base64 = canvas.toDataURL("image/jpeg", qualities[i]);
    var approxBytes = (base64.length * 3) / 4;
    bestBase64 = base64;
    if (approxBytes <= targetBytes) {
      return base64;
    }
  }

  // À 60% on est encore au-dessus : on refuse
  var finalBytes = (bestBase64.length * 3) / 4;
  if (finalBytes > targetBytes) {
    throw new Error(
      "Image trop dense même après compression maximale. " +
      "Fournissez une version plus petite ou moins détaillée."
    );
  }
  return bestBase64;
}

function _btLoadImage(file) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    var url = URL.createObjectURL(file);
    img.onload = function () {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      reject(new Error("Image illisible"));
    };
    img.src = url;
  });
}

function _btFileToBase64(file) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () { resolve(reader.result); };
    reader.onerror = function () { reject(new Error("Lecture du fichier impossible")); };
    reader.readAsDataURL(file);
  });
}

/**
 * Promo simulateur de rentabilité (aimant vers /simulateur).
 * variant : "dashboard" (carte pleine largeur) | "landing" (lien secondaire hero).
 * Statique, aucune donnée orga → pas d'échappement requis.
 */
function btSimulatorPromo(variant) {
  // Adapte si tu passes en sous-domaine (ex. "https://simulateur.goevent.africa")
  var SIMULATOR_URL = "https://goevent.africa/simulateur/";
  variant = variant || "dashboard";

  if (variant === "landing") {
    return (
      '<a href="' + SIMULATOR_URL + '" target="_blank" rel="noopener" ' +
      'class="inline-flex items-center gap-2 mt-5 text-white/90 hover:text-white font-bold font-headline underline underline-offset-4 decoration-secondary-container decoration-2 transition-colors">' +
        '<span class="material-symbols-outlined text-xl">calculate</span>' +
        "Pas sûr de ta rentabilité ? Simule ton événement d'abord" +
      "</a>"
    );
  }

  return (
    '<div class="relative overflow-hidden rounded-3xl p-6 md:p-7 text-white lush-gradient shadow-lg shadow-primary/20">' +
      '<div class="absolute -right-8 -top-8 w-40 h-40 bg-white/10 rounded-full"></div>' +
      '<div class="absolute right-16 top-12 w-24 h-24 bg-white/5 rounded-full"></div>' +
      '<div class="relative z-10 flex flex-col sm:flex-row sm:items-center gap-5">' +
        '<div class="shrink-0 w-14 h-14 rounded-2xl bg-white/15 flex items-center justify-center">' +
          '<span class="material-symbols-outlined text-3xl">calculate</span>' +
        "</div>" +
        '<div class="flex-1 min-w-0">' +
          '<h3 class="font-headline font-extrabold text-lg md:text-xl leading-tight">Ton prochain événement sera-t-il rentable ?</h3>' +
          '<p class="text-white/80 text-sm mt-1.5 max-w-xl">Simule tes coûts, tes billets et ton remplissage en 2 minutes. Découvre ton seuil de rentabilité avant de te lancer.</p>' +
        "</div>" +
        '<a href="' + SIMULATOR_URL + '" target="_blank" rel="noopener" ' +
          'class="shrink-0 inline-flex items-center justify-center gap-2 bg-white text-primary font-bold text-[15px] px-6 py-3.5 rounded-2xl shadow-sm hover:shadow-md active:scale-95 transition-all">' +
          '<span class="material-symbols-outlined">open_in_new</span>Ouvrir le simulateur' +
        "</a>" +
      "</div>" +
    "</div>"
  );
}

console.log("[BT] bt-api.js v4 — Design Stitch");
