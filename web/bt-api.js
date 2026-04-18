// ============================================================
//  GoEvent — bt-api.js v4
//  Design fidèle Stitch · Material Symbols · Pas d'émojis
// ============================================================

const BT_API ="https://goevent-core.vercel.app/";

// ── Auth ──────────────────────────────────────────────────────
const btAuth = {
  getToken: () => localStorage.getItem("bt_token"),
  getUser: () => JSON.parse(localStorage.getItem("bt_user") || "null"),
  isLogged: () => !!localStorage.getItem("bt_token"),
  save(token, user) {
    localStorage.setItem("bt_token", token);
    localStorage.setItem("bt_user", JSON.stringify(user));
  },
  logout() {
    localStorage.removeItem("bt_token");
    localStorage.removeItem("bt_user");
    window.location.href = "index.html";
  },
};

function btDashRoute(role) {
  if (role === "admin") return "admin.html";
  if (role === "agent") return "scanner.html";
  if (role === "organizer") return "organisation_dashboard.html";
  return "user_dashboard.html";
}

// ── API fetch ─────────────────────────────────────────────────
async function btFetch(path, opts) {
  opts = opts || {};
  var token = btAuth.getToken();
  var res = await fetch(
    BT_API + path,
    Object.assign({}, opts, {
      headers: Object.assign(
        { "Content-Type": "application/json" },
        token ? { Authorization: "Bearer " + token } : {},
        opts.headers || {},
      ),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }),
  );
  var data = await res.json().catch(function () {
    return {};
  });
  if (!res.ok) throw new Error(data.detail || "Erreur serveur");
  return data;
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
  delete: function (p) {
    return btFetch(p, { method: "DELETE" });
  },
};

// ── Toast Stitch style ────────────────────────────────────────
function btToast(msg, type) {
  type = type || "success";
  document.querySelectorAll(".bt-toast").forEach(function (t) {
    t.remove();
  });
  var el = document.createElement("div");
  el.className = "bt-toast";
  var bg = type === "error" ? "#ba1a1a" : "#00502c";
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
  var icon = type === "error" ? "error" : "check_circle";
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
  }, 3700);
  setTimeout(function () {
    el.remove();
  }, 4000);
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
    '<li><a href="#" class="hover:text-primary transition-colors">Centre d\'aide</a></li>' +
    '<li><a href="#" class="hover:text-primary transition-colors">Support WhatsApp</a></li>' +
    "</ul>" +
    "</div>" +
    // Légal + paiement
    "<div>" +
    '<h4 class="font-headline text-xs font-bold tracking-widest uppercase text-emerald-900 mb-6">Légal</h4>' +
    '<ul class="space-y-3 text-sm text-neutral-500 mb-6">' +
    '<li><a href="#" class="hover:text-primary transition-colors">Conditions Générales</a></li>' +
    '<li><a href="#" class="hover:text-primary transition-colors">Confidentialité</a></li>' +
    '<li><a href="#" class="hover:text-primary transition-colors">Remboursements</a></li>' +
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
  var pct =
    e.total_seats > 0
      ? Math.round((1 - e.seats_available / e.total_seats) * 100)
      : 0;
  var sold = e.is_sold_out;
  var d = e.event_date ? new Date(e.event_date) : null;
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
  var dateStr = d
    ? d.getDate() + " " + months[d.getMonth()] + ", " + d.getFullYear()
    : "";
  var barColor = sold ? "bg-error" : pct > 70 ? "bg-amber-500" : "bg-primary";
  var barLabel = sold
    ? "Guichet fermé"
    : pct > 70
      ? "Derniers billets ! Vendus: " + pct + "%"
      : "Vendus: " + pct + "%";
  var seatsLabel = sold
    ? "Complet"
    : "Il reste " + e.seats_available + " places";
  var btnLabel = sold ? "Complet" : "Acheter";
  var btnIcon = sold ? "block" : "shopping_cart";

  return (
    '<div class="bg-surface-container-lowest rounded-xl overflow-hidden group hover:shadow-xl transition-shadow flex flex-col cursor-pointer" onclick="window.location=\'event_detail.html?id=' +
    e.id +
    "'\">" +
    '<div class="relative h-64">' +
    (e.cover_image_url
      ? '<img class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" src="' +
        e.cover_image_url +
        '" alt="' +
        e.title +
        '" onerror="this.style.display=\'none\'">'
      : "") +
    '<div class="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>' +
    '<div class="absolute top-4 right-4 bg-secondary-container text-on-secondary-container px-4 py-2 rounded-lg font-black font-headline shadow-lg text-sm">' +
    btPrice(e.price) +
    "</div>" +
    "</div>" +
    '<div class="p-6 flex-1 flex flex-col">' +
    '<div class="flex items-center gap-2 text-outline text-sm mb-3">' +
    '<span class="material-symbols-outlined text-sm">calendar_today</span>' +
    "<span>" +
    dateStr +
    "</span>" +
    "</div>" +
    '<h3 class="text-xl font-bold font-headline mb-2 text-on-surface">' +
    e.title +
    "</h3>" +
    '<p class="text-on-surface-variant text-sm mb-6 flex items-center gap-1">' +
    '<span class="material-symbols-outlined text-sm">person</span>' +
    (e.organizer || "Organisateur") +
    "</p>" +
    '<div class="mt-auto">' +
    '<div class="mb-4">' +
    '<div class="flex justify-between text-xs font-bold text-outline mb-1">' +
    "<span>" +
    barLabel +
    "</span>" +
    "<span>" +
    seatsLabel +
    "</span>" +
    "</div>" +
    '<div class="w-full bg-surface-container-high h-2 rounded-full overflow-hidden">' +
    '<div class="' +
    barColor +
    ' h-full rounded-full transition-all duration-700" style="width:' +
    pct +
    '%"></div>' +
    "</div>" +
    "</div>" +
    (sold
      ? '<button disabled class="w-full bg-surface-container text-outline py-4 rounded-xl font-bold flex items-center justify-center gap-2 cursor-not-allowed">Guichet fermé</button>'
      : "<button onclick=\"event.stopPropagation();window.location='event_detail.html?id=" +
        e.id +
        '\'" class="w-full bg-gradient-to-r from-primary to-primary-container text-white py-4 rounded-xl font-bold flex items-center justify-center gap-2 hover:opacity-90 active:scale-95 transition-all shadow-md shadow-primary/10">' +
        '<span class="material-symbols-outlined">' +
        btnIcon +
        "</span> " +
        btnLabel +
        "</button>") +
    "</div>" +
    "</div>" +
    "</div>"
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

// ── Paiement Mobile Money (CinetPay) ──────────────────────────
async function btInitierPaiement(eventId, e) {
  // 1. On bloque immédiatement le rechargement de la page
  if (e) e.preventDefault();

  // 2. On gère l'état visuel du bouton
  var btn = e ? e.currentTarget : document.activeElement;
  var originalText = btn.innerHTML;
  btn.classList.add("bt-loading");
  btn.innerHTML =
    '<span class="material-symbols-outlined spin">sync</span> Chargement...';

  try {
    btToast("Préparation du paiement sécurisé...");

    // On appelle notre route FastAPI
    var data = await btApi.post("/payment/init/" + eventId, {});

    // Si le backend renvoie bien l'URL de CinetPay, on redirige le fan
    if (data && data.payment_url) {
      window.location.href = data.payment_url;
    } else {
      btToast("Erreur : l'URL de paiement est introuvable.", "error");
      btn.classList.remove("bt-loading");
      btn.innerHTML = originalText;
    }
  } catch (err) {
    btToast(err.message, "error");
    btn.classList.remove("bt-loading");
    btn.innerHTML = originalText;
  }
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
          (!ok
            ? '<button onclick="btSimPay(' +
              t.ticket_id +
              ')" class="bg-yellow-400 text-yellow-900 px-5 py-2.5 rounded-xl font-bold text-sm hover:bg-yellow-500 transition-all shadow-sm flex items-center gap-2"><span class="material-symbols-outlined text-lg">payments</span> Simuler Orange Money</button>'
            : "") +
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

// ── Fonction de simulation ──────────────────────────────────────
function btSimPay(ticketId) {
  btToast("Paiement Orange Money validé !");
  // Dans la vraie vie, c'est FastAPI qui mettrait à jour le statut.
  // Ici on fait juste une petite alerte pour prouver que le bouton marche.
  alert(
    "Simulation : Ce billet va maintenant passer en statut 'payé' et afficher son QR Code !",
  );
}

async function btSimPay(id) {
  try {
    await btApi.post("/payment/simulate/" + id, {});
    btToast("Paiement confirmé. Votre billet est maintenant valide.");
    btShowTickets();
  } catch (e) {
    btToast(e.message, "error");
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
    if (n.length < 4) {
      btToast("Le PIN doit comporter au moins 4 chiffres", "error");
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

console.log("[BT] bt-api.js v4 — Design Stitch");
