/* ════════════════════════════════════════════════════════════════
 * admin-invitations.js — Onglet « Invitations » du back-office (admin.html)
 *
 * Offre accompagnée : l'équipe GoEvent crée l'événement privé, importe la
 * liste, envoie les pass par WhatsApp (lien api.whatsapp.com, un clic par invité),
 * suit les arrivées et remet au client un lien de suivi (chiffres seuls).
 * Accès : capability invitations_manage (admin + commercial). Activation d'un
 * pack : admin uniquement (invitations_activate), après paiement reçu sur un
 * moyen AU NOM DE GoEvent SARLU, référence obligatoire (garde serveur).
 * Catalogue (formules, packs, options, codes, réglages, historique) : lecture
 * admin + commercial, modification admin seul. Devis = même calcul serveur que
 * l'activation (POST …/packs/quote) : affiché = facturé.
 * Lot 4.5 — équipe commerciale : portefeuilles (un commercial ne voit que ses
 * événements et devis ; l'admin voit tout, filtre et réaffecte), devis numérotés
 * DEV-AAAA-NNNN (création, PDF, WhatsApp, statuts, conversion en événement,
 * activation au même montant), Ventes & commissions (clôture mensuelle, versement).
 * Dépend de bt-api.js (btApi, btToast, _btEscapeHtml) et de hasCapability().
 * ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var S = { meta: null, view: "list", eventId: null, ev: null, guests: null, q: "", status: "", page: 1,
            queue: [], timer: null, importRows: null };
  function formulaName(code) {
    var f = S.meta && (S.meta.formulas || []).filter(function (x) { return x.code === code; })[0];
    return f ? f.name : (code || "—");
  }
  var TZ = "Africa/Bangui";
  // Dates de validité : saisies au jour (heure de Bangui), stockées en UTC
  function dateInput(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return d.toLocaleDateString("fr-CA", { timeZone: TZ });
  }
  function dateIso(v, end) { return v ? v + (end ? "T23:59:59+01:00" : "T00:00:00+01:00") : null; }
  function dayLabel(iso) { return iso ? new Date(/[zZ]$/.test(iso) ? iso : iso + "Z").toLocaleDateString("fr-FR", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" }) : ""; }
  var STATUS = { pending: ["À envoyer", "bg-gray-100 text-gray-700"], sent: ["Envoyé", "bg-blue-100 text-blue-800"],
                 partial: ["Entré en partie", "bg-amber-100 text-amber-800"], complete: ["Entré", "bg-green-100 text-green-800"],
                 revoked: ["Annulé", "bg-red-100 text-red-700"], to_print: ["À imprimer", "bg-orange-100 text-orange-800"],
                 printed: ["Imprimé", "bg-blue-100 text-blue-800"] };

  var toast = function (m, t) { if (typeof btToast === "function") btToast(m, t || "info"); };
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k.indexOf("on") === 0 && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function fcfa(v) { return v == null ? "—" : Math.round(v).toLocaleString("fr-FR") + " FCFA"; }
  function dt(iso) {
    if (!iso) return "—";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function toLocalInput(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z"), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function copy(text, label) {
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { toast((label || "Lien") + " copié.", "success"); })
      .catch(function () { window.prompt("Copiez :", text); });
  }
  function canActivate() { return S.meta && S.meta.can_activate; }
  function sec() { return document.getElementById("section-invitations"); }
  function lbl(t) { return el("label", { class: "block text-[11px] font-bold text-gray-500 uppercase tracking-widest mb-1", text: t }); }
  function field(label, node, hint) {
    return el("div", {}, [lbl(label), node, hint ? el("p", { class: "text-[11px] text-gray-400 mt-1", text: hint }) : null]);
  }
  function modal(title, body, actions) {
    var overlay = el("div", { class: "modal-overlay inv-modal" });
    var close = function () { overlay.remove(); };
    overlay.appendChild(el("div", { class: "modal-box", style: "max-width:640px;max-height:92vh;overflow:auto" }, [
      el("div", { class: "flex justify-between items-center mb-4" }, [
        el("h3", { class: "text-lg font-black text-gray-900", text: title }),
        el("button", { class: "text-gray-400 hover:text-red-500 text-xl font-bold", text: "×", onclick: close }),
      ]),
      body,
      el("div", { class: "flex justify-end gap-2 pt-4" }, [el("button", { class: "action-btn", style: "background:#f3f4f6;color:#374151", text: "Annuler", onclick: close })].concat(actions(close))),
    ]));
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    return close;
  }
  function stopTimer() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }

  // ════════════════════════════════════════════════════════════════
  // LISTE DES ÉVÉNEMENTS PRIVÉS
  // Rangée par urgence : Aujourd'hui · À venir (du plus proche au plus lointain) · Passés (du plus
  // récent) · Archivés (purgés). Recherche et filtre par type dans le navigateur (300 dossiers max).
  // ════════════════════════════════════════════════════════════════
  var EVF = { owner: "", q: "", type: "" };
  function loadList() {
    stopTimer();
    S.view = "list";
    var root = sec();
    root.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadSellers().then(function () {
      return btApi.get("/admin/invitations/events" + (EVF.owner !== "" ? "?owner_id=" + EVF.owner : ""));
    }).then(function (d) {
      S.list = d.events;
      root.innerHTML = "";
      root.appendChild(navTabs("events"));
      root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-4" }, [
        el("div", {}, [
          el("h2", { class: "text-xl font-black text-gray-900", text: "Invitations digitales" }),
          el("p", { class: "text-xs text-gray-500 mt-1", text: "Événements privés (mariages, dots, galas…) : liste d'invités, pass QR nominatifs envoyés par WhatsApp, contrôle à l'entrée, suivi des arrivées." }),
        ]),
        el("button", { class: "action-btn btn-primary", text: "+ Nouvel événement privé", onclick: eventModal }),
      ]));
      if (d.summary) {
        var sm = d.summary;
        root.appendChild(el("p", { class: "text-xs text-gray-600 mb-3", text: "Encaissé (packs actifs payés) : " + fcfa(sm.paid_total_fcfa) + " · " +
          sm.paid_count + " pack(s) payé(s)" + (sm.offered_count ? " · " + sm.offered_count + " pack(s) offert(s), non comptés en recette" : "") }));
      }
      if (!d.events.length) {
        root.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucun événement privé pour l'instant." }));
        return;
      }
      var q = el("input", { class: "input-field", style: "flex:1;min-width:200px", placeholder: "Rechercher : titre, client, lieu", value: EVF.q });
      var ty = el("select", { class: "input-field", style: "max-width:200px" }, [["", "Tous les types"], ["familial", "Célébrations familiales"],
        ["corporate", "Professionnel"], ["none", "Type non renseigné"]].map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
      ty.value = EVF.type;
      var box = el("div");
      q.addEventListener("input", function () { EVF.q = q.value; clearTimeout(S._lt); S._lt = setTimeout(function () { drawList(box); }, 150); });
      ty.addEventListener("change", function () { EVF.type = ty.value; drawList(box); });
      var filters = [q, ty];
      if (canActivate()) {
        var ow = el("select", { class: "input-field", style: "max-width:220px" }, [el("option", { value: "", text: "Tous les dossiers" }), el("option", { value: "0", text: "Administration (maison)" })]
          .concat((S.sellers || []).map(function (x) { return el("option", { value: String(x.id), text: x.full_name }); })));
        ow.value = EVF.owner;
        ow.addEventListener("change", function () { EVF.owner = ow.value; loadList(); });
        filters.push(ow);
      }
      root.appendChild(el("div", { class: "flex flex-wrap gap-2 mb-4" }, filters));
      root.appendChild(box);
      drawList(box);
    }).catch(function (e) { root.innerHTML = ""; root.appendChild(el("div", { class: "card p-6 text-red-600 text-sm", text: e.message || "Erreur" })); });
  }

  // Jours calendaires à l'heure de Bangui (pas d'heure UTC : un événement à 23 h reste « aujourd'hui »)
  function bgDay(iso) { return iso ? bgParts(iso).date : null; }
  function daysBetween(a, b) { return Math.round((new Date(b + "T12:00:00Z") - new Date(a + "T12:00:00Z")) / 86400000); }
  function shortDay(ymd, withYear) {
    return new Date(ymd + "T12:00:00Z").toLocaleDateString("fr-FR", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: withYear ? "numeric" : undefined });
  }
  function norm(v) { return String(v || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  // Fin réelle (dernière séance comprise) = date de purge − durée de conservation
  function endDay(e) {
    if (!e.purge_after) return bgDay(e.event_date);
    var d = new Date((/[zZ]|[+-]\d\d:?\d\d$/.test(e.purge_after) ? e.purge_after : e.purge_after + "Z"));
    d.setUTCDate(d.getUTCDate() - (S.meta.retention_days || 30));
    return bgParts(d.toISOString()).date;
  }
  // Une seule info utile par dossier (même logique que « Prochaine étape » de la fiche)
  function listAction(e) {
    var s = e.stats, t = typeOf(e.event_type);
    if (!e.quota) return ["Aucun pack activé", "#92400e", "#fef3c7"];
    if (!s.passes) return [t && t.acces === "ouvert" ? "Inscriptions à ouvrir" : "Liste à importer", "#1e40af", "#dbeafe"];
    if (s.passes_sent < s.passes) return [(s.passes - s.passes_sent) + " pass à envoyer", "#1e40af", "#dbeafe"];
    return ["Prêt · " + e.quota + " pers.", "#166534", "#dcfce7"];
  }
  function drawList(box) {
    var today = bgDay(new Date().toISOString()), year = today.slice(0, 4), f = norm(EVF.q);
    var rows = (S.list || []).filter(function (e) {
      var t = typeOf(e.event_type);
      if (EVF.type === "none" && t) return false;
      if (EVF.type && EVF.type !== "none" && (!t || t.famille !== EVF.type)) return false;
      return !f || norm([e.title, e.client_name, e.location, e.owner_name, typeLabel(e.event_type)].join(" ")).indexOf(f) >= 0;
    });
    var now = [], next = [], past = [], arch = [];
    rows.forEach(function (e) {
      var a = bgDay(e.event_date), z = endDay(e);
      e._a = a; e._z = z;
      if (e.purged) arch.push(e);
      else if (a > today) next.push(e);
      else if (z >= today) now.push(e);
      else past.push(e);
    });
    next.sort(function (x, y) { return x.event_date < y.event_date ? -1 : 1; });
    now.sort(function (x, y) { return x.event_date < y.event_date ? -1 : 1; });
    past.sort(function (x, y) { return x._z > y._z ? -1 : 1; });
    arch.sort(function (x, y) { return x._z > y._z ? -1 : 1; });
    box.innerHTML = "";
    if (!rows.length) { box.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucun dossier ne correspond." })); return; }
    function head(t, n) { return el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400 mt-2 mb-2", text: t + " · " + n }); }
    function chip(txt, fg, bg) { return el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full whitespace-nowrap", style: "color:" + fg + ";background:" + bg, text: txt }); }
    function meta(e) {
      return (typeLabel(e.event_type) ? typeLabel(e.event_type) + " · " : "") + shortDay(e._a, e._a.slice(0, 4) !== year) +
        (e._z !== e._a ? " → " + shortDay(e._z, false) : "") + (e.location ? " · " + e.location : "");
    }
    function owner(e) { return canActivate() ? " · " + (e.owner_name ? e.owner_name + (e.owner_code ? " (" + e.owner_code + ")" : "") : "Maison") : ""; }
    function card(e, live) {
      var n = daysBetween(today, e._a), act = listAction(e), s = e.stats;
      var when = live ? chip("Aujourd'hui", "#991b1b", "#fee2e2") : n === 1 ? chip("Demain", "#92400e", "#fef3c7")
        : n <= 30 ? chip("J-" + n, n <= 7 ? "#92400e" : "#374151", n <= 7 ? "#fef3c7" : "#f3f4f6")
        : chip(n < 60 ? "dans " + Math.round(n / 7) + " semaines" : "dans " + Math.round(n / 30) + " mois", "#374151", "#f3f4f6");
      return el("button", { class: "card p-4 text-left hover:shadow-md transition-all", onclick: function () { openEvent(e.id); } }, [
        el("div", { class: "flex justify-between items-start gap-2" }, [el("p", { class: "font-black text-gray-900", text: e.title }), when]),
        el("p", { class: "text-xs text-gray-500 mt-1", text: meta(e) }),
        el("p", { class: "text-xs text-gray-500", text: "Client : " + e.client_name + owner(e) }),
        el("div", { class: "flex flex-wrap justify-between items-center gap-2 mt-2" }, [
          el("span", { class: "text-sm font-bold text-gray-800", text: live ? s.entered + " / " + s.expected + " entrés" : s.passes_sent + " / " + s.passes + " pass envoyés" }),
          chip(act[0], act[1], act[2]),
        ]),
      ]);
    }
    function line(e) {
      var s = e.stats, until = e.purge_after ? new Date(bgDay(e.purge_after) + "T12:00:00Z").toLocaleDateString("fr-FR", { timeZone: "UTC", day: "numeric", month: "short" }) : "";
      var right = e.purged ? chip("Données supprimées", "#6b7280", "#f3f4f6") : !e.quota ? chip("Aucun pack", "#6b7280", "#f3f4f6")
        : chip((canActivate() ? "Bilan avant le " : "Suppression le ") + until, "#92400e", "#fef3c7");
      // Téléphone : empilé ; à partir de sm : 3 colonnes alignées
      return el("button", { class: "w-full text-left flex flex-col gap-1 sm:grid sm:gap-2 sm:items-center px-4 py-3 hover:bg-gray-50", style: "grid-template-columns:minmax(0,2fr) minmax(0,1.3fr) 11rem",
        onclick: function () { openEvent(e.id); } }, [
        el("div", { class: "min-w-0" }, [el("p", { class: "font-bold text-sm text-gray-900 truncate", text: e.title }),
          el("p", { class: "text-[11px] text-gray-500 truncate", text: (typeLabel(e.event_type) ? typeLabel(e.event_type) + " · " : "") + "Client : " + e.client_name + owner(e) })]),
        el("p", { class: "text-xs text-gray-600", text: shortDay(e._a, e._a.slice(0, 4) !== year) + (e._z !== e._a ? " → " + shortDay(e._z, false) : "") +
          " · " + s.entered + " / " + s.expected + " entrés" }),
        el("div", { class: "sm:text-right" }, [right]),
      ]);
    }
    function table(list, max) {
      var t = el("div", { class: "card divide-y divide-gray-100 mb-4 overflow-hidden" }), shown = max ? list.slice(0, max) : list;
      shown.forEach(function (e) { t.appendChild(line(e)); });
      if (max && list.length > max) t.appendChild(el("button", { class: "w-full text-center text-xs font-bold text-gray-500 py-3 hover:bg-gray-50",
        text: "Voir les " + (list.length - max) + " autres", onclick: function () { t.replaceWith(table(list, 0)); } }));
      return t;
    }
    if (now.length) {
      box.appendChild(head("Aujourd'hui", now.length));
      box.appendChild(el("div", { class: "grid gap-3 md:grid-cols-2 mb-4" }, now.map(function (e) { return card(e, true); })));
    }
    box.appendChild(head("À venir", next.length));
    box.appendChild(next.length ? el("div", { class: "grid gap-3 md:grid-cols-2 mb-4" }, next.map(function (e) { return card(e, false); }))
      : el("p", { class: "text-sm text-gray-400 mb-4", text: "Aucun événement à venir." }));
    if (past.length) { box.appendChild(head("Passés", past.length)); box.appendChild(table(past, 8)); }
    if (arch.length) {
      box.appendChild(el("details", { class: "mb-4" }, [el("summary", { class: "cursor-pointer text-[11px] font-black uppercase tracking-widest text-gray-400 mb-2", text: "Archivés (données supprimées) · " + arch.length }), table(arch, 0)]));
    }
  }

  function eventModal(ev) {
    ev = ev && ev.id ? ev : null;
    var f = {
      title: el("input", { class: "input-field", maxlength: "150", placeholder: "Ex. Mariage de Paul & Marie" }),
      date: el("input", { class: "input-field", type: "datetime-local" }),
      location: el("input", { class: "input-field", maxlength: "200", placeholder: "Salle, quartier, ville" }),
      client: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Famille Ngaba" }),
      phone: el("input", { class: "input-field", maxlength: "30", placeholder: "+236 …" }),
      host: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Paul & Marie (affiché « … vous invite »)" }),
      service: el("select", { class: "input-field" }, (S.meta.formulas || []).map(function (fo) {
        return el("option", { value: fo.code, text: fo.name + (fo.description ? " — " + fo.description : "") });
      })),
      desc: el("textarea", { class: "input-field", rows: "2", maxlength: "2000", placeholder: "Note interne (facultatif)" }),
      type: el("select", { class: "input-field" }, [el("option", { value: "", text: "— Choisir —" })].concat((S.meta.event_types || []).map(function (t) {
        return el("option", { value: t.code, text: t.label });
      }))),
    };
    var ownerSel = canActivate() && !ev ? ownerSelect(null, true) : null;
    if (ev) {
      f.title.value = ev.title; f.date.value = toLocalInput(ev.event_date); f.location.value = ev.location || "";
      f.client.value = ev.client_name; f.phone.value = ev.client_phone || ""; f.host.value = ev.host_label || "";
      if (!(S.meta.formulas || []).some(function (fo) { return fo.code === ev.service_level; }))
        f.service.appendChild(el("option", { value: ev.service_level, text: (ev.formula_name || ev.service_level) + " (archivée)" }));
      f.service.value = ev.service_level; f.desc.value = ev.description || ""; f.type.value = ev.event_type || "";
    } else {
      var acc = (S.meta.formulas || []).filter(function (fo) { return fo.code === "accompagne"; })[0];
      if (acc) f.service.value = acc.code;
    }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Type d'événement", f.type, "Adapte la fiche : liste d'invités, inscription en ligne, modèles conseillés."), field("Titre", f.title)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Date et heure", f.date), field("Lieu", f.location)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Client", f.client), field("Téléphone du client", f.phone)]),
      field("Hôte affiché sur le pass", f.host, "Vide = nom du client."), field("Formule prévue", f.service, "Le pack réellement payé (activation) fait foi."), field("Description", f.desc),
      ownerSel ? field("Commercial propriétaire", ownerSel, "Le commercial voit et gère ce dossier ; la vente lui est attribuée.") : null,
    ]);
    modal(ev ? "Modifier l'événement" : "Nouvel événement privé", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: ev ? "Enregistrer" : "Créer", onclick: function () {
        if (!f.date.value) { toast("Indiquez la date.", "error"); return; }
        if (!ev && !f.type.value) { toast("Choisissez le type d'événement.", "error"); return; }
        var payload = { title: f.title.value.trim(), event_date: new Date(f.date.value).toISOString(), location: f.location.value.trim(),
          client_name: f.client.value.trim(), client_phone: f.phone.value.trim() || null, host_label: f.host.value.trim() || null,
          service_level: f.service.value, description: f.desc.value.trim() || null, event_type: f.type.value || null };
        if (ownerSel && ownerSel.value) payload.owner_id = parseInt(ownerSel.value, 10);
        b.disabled = true;
        (ev ? btApi.patch("/admin/invitations/events/" + ev.id, payload) : btApi.post("/admin/invitations/events", payload))
          .then(function (r) { close(); toast(ev ? "Événement enregistré." : "Événement privé créé.", "success"); openEvent(r.id); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ════════════════════════════════════════════════════════════════
  // FICHE ÉVÉNEMENT
  // ════════════════════════════════════════════════════════════════
  function openEvent(id) {
    stopTimer();
    S.view = "event"; S.eventId = id; S.q = ""; S.status = ""; S.page = 1; S.importRows = null; S.fold = null; S.guests = null; S.designDirty = false; S.dstep = null;
    S.tab = "apercu"; try { S.tab = sessionStorage.getItem(tabKey()) || "apercu"; } catch (e) {}
    if (!TAB_BUILD[S.tab]) S.tab = "apercu";
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/events/" + id).then(function (ev) { S.ev = ev; renderEvent(); loadGuests(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); loadList(); });
    S.timer = setInterval(function () { if (S.view === "event" && !document.hidden) refreshLive(); }, 30000);
  }
  function reloadEvent() {
    return btApi.get("/admin/invitations/events/" + S.eventId).then(function (ev) { S.ev = ev; renderEvent(); renderGuests(); });
  }

  function kpi(label, value, sub, cls) {
    return el("div", { class: "card p-4" }, [
      el("p", { class: "text-[10px] font-black uppercase tracking-widest text-gray-400", text: label }),
      el("p", { class: "text-2xl font-black " + (cls || "text-gray-900"), text: value }),
      sub ? el("p", { class: "text-[11px] text-gray-500", text: sub }) : null,
    ]);
  }
  function entLabel(s) { return s && s.session ? "Entrés · " + s.session.label + (s.session.current ? "" : " (terminée)") : "Entrés"; }
  function block(title, children, id) {
    return el("div", { class: "card p-5 mb-4", id: id || null }, [el("h3", { class: "font-black text-gray-900 mb-3", text: title })].concat(children));
  }

  function renderEvent() {
    var ev = S.ev, root = sec(), s = ev.stats;
    root.innerHTML = "";
    root.appendChild(el("button", { class: "text-sm font-bold text-primary mb-3", text: "← Tous les événements privés", onclick: function () { if (confirmLeave()) loadList(); } }));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-4" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: ev.title }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: ev.event_date_label + " · " + (ev.location || "") + " · Client : " + ev.client_name +
          (ev.client_phone ? " (" + ev.client_phone + ")" : "") + " · " + (ev.formula_name || ev.service_level) }),
        canActivate() ? el("p", { class: "text-xs text-gray-500", text: "Dossier : " + (ev.owner_name ? ev.owner_name + (ev.owner_code ? " (" + ev.owner_code + ")" : "") : "Administration (maison)") }) : null,
      ]),
      el("div", { class: "flex flex-wrap gap-2" }, [
        canActivate() ? el("button", { class: "action-btn text-xs", style: "background:#e0f2fe;color:#075985", text: "Réaffecter", onclick: function () {
          loadSellers().then(function () {
            var sel = ownerSelect(ev.owner_id, true);
            modal("Réaffecter l'événement", el("div", { class: "space-y-2" }, [field("Commercial propriétaire", sel, "Les ventes et commissions déjà acquises restent au vendeur d'origine.")]), function (close) {
              var b = el("button", { class: "action-btn btn-primary", text: "Réaffecter", onclick: function () {
                b.disabled = true;
                btApi.post("/admin/invitations/events/" + ev.id + "/owner", { owner_id: sel.value ? parseInt(sel.value, 10) : null })
                  .then(function () { close(); toast("Événement réaffecté.", "success"); reloadEvent(); })
                  .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
              } });
              return [b];
            });
          });
        } }) : null,
        el("button", { class: "action-btn text-xs", style: "background:#ede9fe;color:#5b21b6", text: "Modifier", onclick: function () { eventModal(ev); } }),
      ]),
    ]));
    if (ev.purged) {
      root.appendChild(el("div", { class: "card p-4 mb-4 bg-gray-50 text-sm text-gray-600", text: "Liste d'invités supprimée (conservation de " + S.meta.retention_days + " jours dépassée) : seuls les chiffres restent." }));
    }
    root.appendChild(el("div", { id: "inv-top", class: "card p-3 mb-3 sticky top-0 z-20" }));
    renderTop();
    var body = el("div", { id: "inv-tab-body" });
    root.appendChild(body);
    S.folds = {};
    TAB_BUILD[S.tab](body, ev);
    if (S.tab === "invites" && S.guests) renderGuests();
  }

  // ── Fiche en onglets : étapes, prochaine action, cartes repliées ──────
  var TABS = [["apercu", "Aperçu"], ["invites", "Invités"], ["invitation", "Invitation"], ["jourj", "Jour J"], ["donnees", "Données"]];
  function typeOf(code) { return ((S.meta && S.meta.event_types) || []).filter(function (t) { return t.code === code; })[0] || null; }
  function typeLabel(code) { var t = typeOf(code); return t ? t.label : ""; }
  function tabKey() { return "inv_tab:" + S.eventId; }
  function goTab(tab, focus) {
    if (S.tab === "invitation" && tab !== "invitation" && !confirmLeave()) return;
    S.tab = tab; S.fold = focus || null;
    try { sessionStorage.setItem(tabKey(), tab); } catch (e) {}
    renderEvent();
    if (focus === "import") { var d = document.getElementById("inv-import"); if (d) { d.open = true; d.scrollIntoView({ block: "start" }); } }
    else { var top = document.getElementById("inv-top"); if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView({ block: "start" }); }
  }
  // Inscription en ligne : utile pour un événement « ouvert » (conférence, atelier…). Toujours visible si elle
  // est déjà configurée ou si le type n'est pas renseigné (dossiers créés avant).
  function wantsSignup(ev) {
    var t = typeOf(ev.event_type), su = ev.signup || {};
    if (su.slug || su.open || su.count || ev.cover || ev.public_description || S.showSignup === ev.id) return true;
    return !t || !t.acces || t.acces === "ouvert";
  }
  function steps(ev) {
    var s = ev.stats, sent = s.passes_sent, all = s.passes, agents = ev.agents.length;
    return [
      { tab: "apercu", label: ev.quota ? "Pack " + ev.quota + " pers." : "Pack", st: ev.quota ? "done" : "todo" },
      { tab: "invites", label: all ? "Invités · " + all + " pass" : "Invités", st: all ? "done" : "todo" },
      { tab: "invitation", label: ev.design_status === "pret" ? "Invitation publiée" : "Invitation", st: ev.design_status === "pret" ? "done" : (ev.design_status ? "doing" : "todo") },
      { tab: "invites", label: all ? "Envoi " + sent + " / " + all : "Envoi", st: !all ? "todo" : (sent >= all ? "done" : (sent ? "doing" : "todo")) },
      { tab: "jourj", label: s.entered ? "Jour J · " + s.entered + " entrés" : "Jour J" + (agents ? " · " + agents + " agent(s)" : ""), st: s.entered ? "done" : (agents ? "doing" : "todo") },
    ];
  }
  // Prochaine action utile selon l'état du dossier : un seul bouton
  function nextAction(ev) {
    var s = ev.stats, q = (S.queue || []).length;
    if (ev.purged) return null;
    if (!ev.quota) return canActivate() ? ["Activer le pack", function () { orderModal(true); }]
                                         : ["Préparer un devis", function () { quoteModal(null, { event: S.ev }); }];
    if (!s.passes && !(ev.signup && ev.signup.open)) {
      var t = typeOf(ev.event_type);
      return t && t.acces === "ouvert" ? ["Ouvrir les inscriptions", function () { goTab("invites", "signup"); }]
                                       : ["Importer la liste", function () { goTab("invites", "import"); }];
    }
    if (ev.design_status !== "pret" && !s.passes_sent) return ["Préparer l'invitation", function () { goTab("invitation"); }];
    if (q) return ["Envoyer au suivant (" + q + ")", function () { if (S.tab !== "invites") goTab("invites"); sendNext(); }];
    if (!ev.agents.length) return ["Ajouter un agent", function () { goTab("jourj", "agents"); }];
    return ["Suivre les arrivées", function () { goTab("jourj"); }];
  }
  var STEP_STYLE = { done: "background:#dcfce7;color:#166534", doing: "background:#fef3c7;color:#92400e", todo: "background:#f3f4f6;color:#6b7280" };
  function renderTop() {
    var top = document.getElementById("inv-top"), ev = S.ev;
    if (!top || !ev) return;
    top.innerHTML = "";
    var na = nextAction(ev);
    top.appendChild(el("div", { class: "flex flex-wrap items-center gap-2" }, steps(ev).map(function (x) {
      return el("button", { class: "text-[11px] font-bold px-2.5 py-1 rounded-full whitespace-nowrap", style: STEP_STYLE[x.st],
        text: (x.st === "done" ? "✓ " : "") + x.label, onclick: function () { goTab(x.tab); } });
    }).concat([el("span", { class: "flex-1" }), na ? el("span", { class: "text-[11px] text-gray-400 hidden sm:inline", text: "Prochaine étape" }) : null,
      na ? el("button", { class: "action-btn btn-primary text-xs", id: "inv-next", text: na[0] + " →", onclick: na[1] }) : null])));
    // Même style que les onglets du module (navTabs) ; défile horizontalement sur téléphone
    top.appendChild(el("div", { class: "mt-3 overflow-x-auto" }, [el("div", { class: "flex gap-1 p-1 bg-gray-100 rounded-2xl w-max", role: "tablist" }, TABS.map(function (t) {
      var on = t[0] === S.tab;
      return el("button", { role: "tab", "aria-selected": on ? "true" : "false",
        class: "px-4 py-2 rounded-xl text-sm font-bold whitespace-nowrap transition-colors " + (on ? "bg-white shadow-sm" : "text-gray-600 hover:text-gray-900"),
        style: on ? "color:#00502c" : null, text: t[1], onclick: on ? null : function () { goTab(t[0]); } });
    }))]));
  }
  // Carte repliée : une ligne de résumé ; ouverte : le contenu du bloc. Une seule ouverte à la fois.
  function inner(card) { var out = []; card.removeChild(card.firstChild); while (card.firstChild) out.push(card.removeChild(card.firstChild)); return out; }
  function fold(key, title, summary, build, cta) {
    var open = S.fold === key, card = el("div", { class: "card mb-3 " + (open ? "p-5" : "px-5 py-3"), id: "fold-" + key });
    card.appendChild(el("div", { class: "flex flex-wrap items-center gap-2" + (open ? " mb-3" : "") }, [
      el("h3", { class: "font-black text-gray-900", text: title }),
      el("span", { class: "text-sm text-gray-500 flex-1 min-w-0", text: open ? "" : summary || "" }),
      el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", text: open ? "Fermer" : (cta || "Modifier"), onclick: function () {
        var prev = S.fold;
        S.fold = open ? null : key;
        var p = prev && prev !== key && S.folds[prev] && document.getElementById("fold-" + prev);
        if (p) p.replaceWith(S.folds[prev]());
        card.replaceWith(S.folds[key]());
      } }),
    ]));
    if (open) inner(build()).forEach(function (n) { card.appendChild(n); });
    return card;
  }
  function addFold(parent, key, title, summary, build, cta) {
    S.folds[key] = function () { return fold(key, title, summary, build, cta); };
    parent.appendChild(S.folds[key]());
  }
  function purgedNote(body) {
    body.appendChild(el("div", { class: "card p-5 text-sm text-gray-500", text: "Données supprimées après la purge : seuls les chiffres restent (onglet Aperçu)." }));
  }

  var TAB_BUILD = {
    apercu: function (body, ev) {
      var s = ev.stats, packTxt = ev.quota ? ev.quota + " personnes" : "Aucun";
      body.appendChild(el("div", { class: "grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4", id: "inv-kpis" }, [
        kpi(entLabel(s), s.entered + " / " + s.expected, s.rate + " % · " + s.remaining + " restant(s)", "text-primary"),
        kpi("Pass envoyés", s.passes_sent + " / " + s.passes, s.passes_arrived + " pass utilisés"),
        kpi("Pack payé", packTxt, ev.quota ? ev.listed + " personne(s) dans la liste" : "Envoi et scan bloqués", ev.quota ? "text-gray-900" : "text-amber-600"),
        kpi("Conflits hors ligne", String(s.conflicts), s.conflicts ? "Pass accepté(s) sur 2 téléphones" : "Aucun", s.conflicts ? "text-red-600" : "text-gray-900"),
      ]));
      body.appendChild(packBlock());
    },
    invites: function (body, ev) {
      if (ev.purged) return purgedNote(body);
      if (wantsSignup(ev)) {
        var su = ev.signup || {}, st = (SIGNUP_STATE[su.state] || SIGNUP_STATE.closed)[0];
        addFold(body, "signup", "Inscription en ligne", st + " · " + (su.count || 0) + " inscrit(s)" + (ev.cover ? " · affiche ✓" : ""), function () {
          var a = signupBlock(), b = posterBlock();
          return block("", inner(a).concat([el("h4", { class: "font-black text-gray-900 mt-6 mb-2", text: "Page publique : affiche et description" })], inner(b)));
        }, su.slug ? "Modifier" : "Configurer");
      }
      addFold(body, "message", "Message WhatsApp", ev.message_is_default ? "message par défaut" : "message personnalisé", messageBlock);
      body.appendChild(guestsBlock());
      if (!wantsSignup(ev)) body.appendChild(el("button", { class: "text-xs text-gray-500 underline", text: "Ouvrir aussi une inscription en ligne (lien public)",
        onclick: function () { S.showSignup = ev.id; S.fold = "signup"; renderEvent(); } }));
    },
    invitation: function (body, ev) {
      if (ev.purged) return purgedNote(body);
      body.appendChild(designBlock());
    },
    jourj: function (body, ev) {
      if (!ev.purged) {
        var n = (ev.sessions || []).length;
        addFold(body, "sessions", "Séances", n ? n + " séance(s) · " + (ev.dates_label || "") : "aucune : une seule entrée pour tout l'événement",
          sessionsBlock, n ? "Modifier" : "Ajouter");
      }
      var onsite = ev.features && ev.features.onsite_agents;
      addFold(body, "agents", "Agents de contrôle", ev.agents.length ? ev.agents.map(function (a) { return a.full_name; }).join(", ") +
        (onsite && ev.quota ? " · " + ev.agents.length + " / " + (ev.agents_included || 0) + " inclus" : "") : "aucun agent", agentsBlock, ev.agents.length ? "Gérer" : "Ajouter");
      body.appendChild(liveBlock());
    },
    donnees: function (body) { body.appendChild(dataBlock()); },
  };

  // ── Design visuel (lot 6) ───────────────────────────────────────────
  // Trois étapes (Modèle · Contenu · Photos) sur un BROUILLON local ; un seul enregistrement (barre
  // fixe en bas du bloc). Rien n'est enregistré tout seul, sauf les fichiers téléversés (photo, logo,
  // photos d'intervenants), qui le sont au moment du choix.
  function designBlock() {
    var box = el("div", { id: "inv-design-body" }, [el("p", { class: "text-sm text-gray-400", text: "Chargement…" })]);
    loadDesign(box);
    return block("Design de l'invitation", [box], "inv-design");
  }

  function loadDesign(box) {
    Promise.all([
      btApi.get("/admin/invitations/events/" + S.eventId + "/design"),
      btApi.get("/admin/invitations/cadres" + ((typeOf(S.ev.event_type) || {}).suggere ? "?event_type=" + encodeURIComponent(typeOf(S.ev.event_type).suggere) : ""))
    ]).then(function (r) {
      S.design = r[0]; S.cadres = r[1].cadres || [];
      if (!S.design.fiche) S.design.fiche = {};
      renderDesign(box);
    }).catch(function (e) {
      box.innerHTML = ""; box.appendChild(el("div", { class: "text-sm text-red-600", text: e.message || "Erreur" }));
    });
  }

  // Modifications non enregistrées : on prévient avant de quitter l'onglet, la fiche ou la page
  function confirmLeave() {
    if (!S.designDirty) return true;
    if (!window.confirm("Le design a des modifications non enregistrées. Quitter sans enregistrer ?")) return false;
    S.designDirty = false;
    return true;
  }
  window.addEventListener("beforeunload", function (e) { if (S.designDirty) { e.preventDefault(); e.returnValue = ""; } });

  // Colonnes d'image (photo, logo) exclues : pas d'URL externe servie aux invités (filtrées aussi côté serveur)
  function _listKeys(type) {
    var m = /^list\[(.+)\]$/.exec(type);
    return m ? m[1].split(",").filter(function (k) { return k !== "photo" && k !== "logo"; }) : null;
  }
  function _listToText(arr, keys) {
    return (arr || []).map(function (o) { return keys.map(function (k) { return o[k] || ""; }).join(" | "); }).join("\n");
  }
  // Détection auto des colonnes : l'organisateur écrit librement, pas besoin de « | ».
  //  • Si la ligne contient « | » ou une tabulation → colonnes explicites (respectées telles quelles).
  //  • Sinon : l'heure en tête est reconnue (18h, 9h30, 18:30, 9.00…), puis le reste est
  //    découpé sur les séparateurs forts (tiret entouré d'espaces, tabulation, « · », 2+ espaces).
  //    Sans séparateur fort, tout le reste va dans la 1re colonne restante (jamais coupé sur un simple espace).
  function _smartLine(line, keys) {
    var l = line.trim(); if (!l) return null;
    var o = {};
    if (/[|\t]/.test(l)) {                                   // colonnes explicites
      var pe = l.split(/\s*[|\t]\s*/);
      keys.forEach(function (k, i) { if (pe[i] && pe[i].trim()) o[k] = pe[i].trim(); });
      return Object.keys(o).length ? o : null;
    }
    var rest = l, ki = 0;
    var timeKey = /heure|hour|time/i.test(keys[0] || "") ? keys[0] : null;
    if (timeKey) {
      var m = /^\s*(\d{1,2})\s*[h:.]\s*(\d{2})?\b/i.exec(l);  // 18h / 9h30 / 18:30 / 9.00
      if (m) {
        o[timeKey] = m[2] ? (m[1] + "h" + m[2]) : (m[1] + "h");
        rest = l.slice(m[0].length).replace(/^\s*[-–—:·.)\]]+\s*/, "").trim();
        ki = 1;
      }
    }
    var remKeys = keys.slice(ki);
    var parts = rest.split(/\s+[-–—]\s+|\s{2,}|\s*·\s*/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (parts.length <= 1) {
      if (remKeys[0] && rest) o[remKeys[0]] = rest;
    } else {
      parts.forEach(function (pv, i) {
        if (remKeys[i]) o[remKeys[i]] = pv;
        else if (remKeys.length) o[remKeys[remKeys.length - 1]] += " · " + pv;   // surplus → dernière colonne
      });
    }
    return Object.keys(o).length ? o : null;
  }
  function _textToList(txt, keys) {
    return (txt || "").split(/\r?\n/).map(function (l) { return _smartLine(l, keys); }).filter(Boolean);
  }

  // Ordre + libellés FR + exemples des champs de la fiche (indépendant de l'ordre JSONB)
  var FIELD_SPECS = {
    familial: [
      { k: "display_names", l: "Noms affichés", ph: "Awa & Léon" },
      { k: "eyebrow", l: "Accroche (en-tête)", ph: "Nous nous marions" },
      { k: "date_label", l: "Date (en toutes lettres)", ph: "Samedi 14 juin 2026" },
      { k: "lieu", l: "Lieu", ph: "Cathédrale de Bangui" },
      { k: "verset", l: "Verset / citation", t: "textarea", ph: "L'amour est patient, il est plein de bonté" },
      { k: "reference", l: "Référence du verset", ph: "1 Corinthiens 13" },
      { k: "intro", l: "Introduction", t: "textarea", ph: "C'est avec joie que nous vous convions à célébrer notre union." },
      { k: "body", l: "Texte principal", t: "textarea" },
      { k: "closing", l: "Formule de fin", ph: "Votre présence est notre plus beau cadeau." },
      { k: "programme", l: "Programme", t: "list", ph: "15h00 Cérémonie à la Cathédrale\n18h00 Vin d'honneur\n20h00 Dîner dansant" }
    ],
    corporate: [
      { k: "organisateur", l: "Organisateur", ph: "AXION Group" },
      { k: "titre", l: "Titre de l'événement", ph: "Tech Summit 2026" },
      { k: "sous_titre", l: "Sous-titre", ph: "L'IA au service de l'Afrique centrale" },
      { k: "lieu", l: "Lieu", ph: "Ledger Plaza, Bangui" },
      { k: "intervenants", l: "Intervenants", t: "list", ph: "David Koyt — CEO — PayGo Africa\nMarie Nzé — Directrice — Orange CA" },
      { k: "sessions", l: "Programme / sessions", t: "list", ph: "09h00 Ouverture par D. Koyt\n10h30 Panel IA & Afrique" },
      { k: "sponsors", l: "Sponsors", t: "list", ph: "Orange CA\nEcobank" }
    ]
  };

  // Photos des intervenants : rattachées au NOM (survit au réordonnancement des lignes)
  function _spkKey(nom) { return String(nom || "").trim().toLowerCase().replace(/\s+/g, " "); }
  function _mediaBlob(id, img) {
    S.mediaBlobs = S.mediaBlobs || {};
    if (S.mediaBlobs[id]) { img.src = S.mediaBlobs[id]; return; }
    fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/design/media/" + id, { headers: { Authorization: "Bearer " + btAuth.getToken() } })
      .then(function (r) { return r.ok ? r.blob() : Promise.reject(); })
      .then(function (b) { S.mediaBlobs[id] = URL.createObjectURL(b); img.src = S.mediaBlobs[id]; })
      .catch(function () {});
  }

  // Forme de la photo dans chaque cadre familial (aperçu du recadrage et vignettes)
  var PHOTO_SHAPES = {
    b1: { w: 220, ar: "4/3" }, b2: { w: 220, ar: "3/2" }, b3: { w: 170, ar: "3/4", r: "90px 90px 10px 10px" },
    b4: { w: 170, ar: "3/4" }, b5: { w: 180, ar: "1/1" }, b6: { w: 180, ar: "1/1", r: "50%" }, b7: { w: 180, ar: "1/1" },
    b8: { w: 150, ar: "1/1", r: "50%" }, b9: { w: 240, ar: "16/10" },
    b10: { w: 180, ar: "1/1", clip: "polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)" }
  };

  // Vignette d'un cadre : croquis de la mise en page aux couleurs de son premier skin
  function cadreThumb(code, skin) {
    var c = (skin && skin.couleurs) || {}, fond = c.fond || "#f3f4f1", acc = c.accent || "#00502c", tx = c.texte || "#111827", cl = c.clair || "#fff";
    function bx(st) { return el("div", { style: "position:absolute;" + st }); }
    function line(top, w, col, h) { return bx("left:50%;transform:translateX(-50%);top:" + top + "px;width:" + w + "%;height:" + (h || 3) + "px;border-radius:2px;background:" + (col || tx)); }
    var ph = "background:" + acc + ";opacity:.35;";
    var parts = {
      b1: [bx("left:0;right:0;top:0;height:46px;" + ph), bx("left:0;right:0;top:46px;height:4px;background:" + acc), line(64, 60), line(72, 40, acc, 2), line(80, 50, tx, 2)],
      b2: [bx("left:14px;right:14px;top:12px;height:38px;border:1px solid " + acc + ";" + ph), line(62, 64), line(72, 44, acc, 2), line(80, 50, tx, 2)],
      b3: [bx("left:22px;right:22px;top:10px;height:46px;border-radius:22px 22px 3px 3px;" + ph), bx("inset:5px;border:1px solid " + acc + ";border-radius:6px"), line(66, 56), line(76, 40, acc, 2)],
      b4: [bx("inset:0;" + ph + "opacity:.6"), line(80, 64, "#fff"), line(90, 40, "#fff", 2)],
      b5: [bx("left:0;top:0;bottom:0;width:42%;" + ph), bx("left:46%;right:6px;top:30px;height:3px;background:" + tx), bx("left:46%;right:14px;top:38px;height:2px;background:" + acc)],
      b6: [bx("left:50%;top:10px;width:44px;height:44px;margin-left:-22px;border-radius:50%;" + ph), bx("left:50%;top:6px;width:52px;height:52px;margin-left:-26px;border-radius:50%;border:1px dashed " + acc), line(68, 60), line(78, 40, acc, 2)],
      b7: [bx("left:22px;right:22px;top:10px;height:52px;background:#fff;transform:rotate(-4deg);box-shadow:0 1px 3px #0003"), bx("left:28px;right:28px;top:15px;height:36px;transform:rotate(-4deg);" + ph), line(76, 50), line(86, 40, acc, 2)],
      b8: [line(16, 70, tx, 8), line(28, 56, tx, 8), bx("left:50%;top:44px;width:28px;height:28px;margin-left:-14px;border-radius:50%;" + ph), line(82, 40, acc, 2)],
      b9: [bx("left:0;right:0;top:0;height:10px;background:" + tx), bx("left:0;right:0;top:10px;height:36px;" + ph), line(56, 64), line(66, 44, acc, 2), line(74, 50, tx, 2)],
      b10: [bx("left:50%;top:10px;width:40px;height:46px;margin-left:-20px;clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);background:" + acc), line(64, 56, acc), line(74, 40, acc, 2)],
      c1: [bx("left:0;right:0;top:0;height:6px;background:" + acc), line(18, 70, tx, 5), line(28, 50, acc, 2), bx("left:14px;top:46px;width:16px;height:16px;border-radius:50%;" + ph), bx("left:38px;top:46px;width:16px;height:16px;border-radius:50%;" + ph), bx("left:62px;top:46px;width:16px;height:16px;border-radius:50%;" + ph)],
      c2: [bx("left:14px;right:14px;top:10px;bottom:16px;border:1px solid " + acc + ";border-radius:6px;background:" + cl), bx("left:50%;top:14px;width:16px;height:3px;margin-left:-8px;border-radius:2px;background:" + acc), line(30, 50, tx, 4), line(40, 36, acc, 2)],
      c3: [bx("left:0;right:0;top:0;height:50px;background:" + acc), line(18, 64, "#fff", 5), line(28, 44, "#fff", 2), line(62, 50), line(72, 36, acc, 2)],
      c4: [bx("inset:0;background:" + cl), line(18, 30, acc, 2), line(30, 70, acc, 5), line(42, 50, tx, 2), line(52, 30, acc, 1)],
      c5: [bx("left:10px;right:10px;top:10px;height:34px;border-radius:5px;background:" + acc), bx("left:16px;top:15px;width:18px;height:4px;border-radius:2px;background:#e11d48"), line(54, 64), line(64, 44, acc, 2)],
      c6: [bx("left:0;right:0;top:0;height:30px;background:" + acc), line(12, 60, "#fff", 4), bx("left:14px;right:14px;top:40px;height:2px;background:" + acc), bx("left:14px;right:14px;top:52px;height:2px;background:" + acc), bx("left:14px;right:14px;top:64px;height:2px;background:" + acc), bx("left:14px;right:14px;top:76px;height:2px;background:" + acc)],
      c7: [line(12, 64, tx, 4), [0, 1, 2].map(function (i) { return bx("left:" + (12 + i * 24) + "px;top:30px;width:16px;height:16px;border-radius:50%;" + ph); }), [0, 1, 2].map(function (i) { return bx("left:" + (12 + i * 24) + "px;top:54px;width:16px;height:16px;border-radius:50%;" + ph); })],
      c8: [line(30, 30, acc, 1), line(40, 64, tx, 4), line(50, 40, tx, 2), line(60, 30, acc, 1)],
      c9: [bx("left:0;right:0;top:0;height:42px;background:" + tx), line(14, 50, acc, 3), line(24, 64, "#fff", 4), line(56, 56), line(66, 40, acc, 2)],
      c10: [line(12, 40, acc, 4), line(22, 64, tx, 4), [0, 1, 2, 3].map(function (i) { return bx("left:14px;top:" + (38 + i * 12) + "px;width:8px;height:8px;border-radius:50%;background:" + acc); }), [0, 1, 2, 3].map(function (i) { return bx("left:28px;right:14px;top:" + (41 + i * 12) + "px;height:2px;background:" + tx); })]
    };
    var kids = [].concat.apply([], (parts[code] || [line(30, 60), line(42, 40, acc, 2)]).map(function (x) { return Array.isArray(x) ? x : [x]; }));
    return el("div", { style: "position:relative;width:92px;height:104px;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;background:" + fond }, kids);
  }
  function swatches(skin) {
    var c = (skin && skin.couleurs) || {};
    return el("span", { class: "inline-flex gap-1" }, ["fond", "accent", "texte"].map(function (k) {
      return el("span", { style: "width:14px;height:14px;border-radius:4px;border:1px solid #0002;background:" + (c[k] || "#fff") });
    }));
  }

  function renderDesign(box) {
    var d = S.design;
    // Brouillon : ce que l'admin modifie ; envoyé en UNE fois par « Enregistrer » / « Publier »
    var W = {
      fam: (d.cadre && d.cadre.famille) || (typeOf(S.ev.event_type) || {}).famille || "familial",
      cadre: d.cadre, modele_id: d.modele_id, modele: d.modele, theme_override: d.theme_override,
      photo_crop: { x: (d.photo_crop || {}).x == null ? 50 : d.photo_crop.x, y: (d.photo_crop || {}).y == null ? 28 : d.photo_crop.y,
                    zoom: Math.min(3, Math.max(1, (d.photo_crop || {}).zoom || 1)) },
      fiche: JSON.parse(JSON.stringify(d.fiche || {})), photoMap: {}
    };
    (W.fiche.intervenants || []).forEach(function (it) { if (it && it.photo && it.nom) W.photoMap[_spkKey(it.nom)] = it.photo; });
    S.designDirty = false;
    box.innerHTML = "";
    var inputs = {}, panels = {}, stepBtns = {};
    var status = el("span", { class: "text-xs" });
    function dirty() { S.designDirty = true; drawStatus(); drawSteps(); }
    function drawStatus(msg) {
      status.className = "text-xs font-semibold";
      if (msg) { status.style.color = "#166534"; status.textContent = msg; return; }
      if (S.designDirty) { status.style.color = "#92400e"; status.textContent = "● Modifications non enregistrées"; return; }
      status.style.color = d.status === "pret" ? "#166534" : "#6b7280";
      status.textContent = d.status === "pret" ? "Publié : visible par les invités" : "Brouillon : invisible pour les invités";
    }
    function cadreInfo() { return W.cadre ? S.cadres.filter(function (c) { return c.code === W.cadre.code; })[0] : null; }
    function skinOf() {
      var base = (W.modele && W.modele.skin) || {};
      return W.theme_override && W.theme_override.couleurs ? Object.assign({}, base, { couleurs: Object.assign({}, base.couleurs, W.theme_override.couleurs) }) : base;
    }
    function ficheFilled() { return Object.keys(inputs).some(function (k) { return (inputs[k].value || "").trim(); }) || Object.keys(W.fiche).some(function (k) { var v = W.fiche[k]; return Array.isArray(v) ? v.length : !!v; }); }

    // ── Étapes
    var STEPS = [["modele", "Modèle"], ["contenu", "Contenu"], ["photos", "Photos"]];
    if (!S.dstep || (!W.cadre && S.dstep !== "modele")) S.dstep = W.cadre ? "contenu" : "modele";
    var stepBar = el("div", { class: "flex flex-wrap gap-2 mb-4" });
    function done(k) {
      if (k === "modele") return !!W.modele_id;
      if (k === "contenu") return W.modele_id && ficheFilled();
      return W.fam === "familial" ? !!d.has_photo : !!d.has_logo;
    }
    function drawSteps() {
      stepBar.innerHTML = "";
      STEPS.forEach(function (s, i) {
        var on = S.dstep === s[0], ok = done(s[0]);
        stepBtns[s[0]] = el("button", { type: "button", class: "text-xs font-bold px-3 py-1.5 rounded-full",
          style: on ? "background:#00502c;color:#fff" : ok ? "background:#dcfce7;color:#166534" : "background:#f3f4f6;color:#6b7280",
          text: (ok && !on ? "✓ " : (i + 1) + " ") + s[1], onclick: function () { go(s[0]); } });
        stepBar.appendChild(stepBtns[s[0]]);
      });
    }
    function go(k) {
      if (k !== "modele" && !W.cadre) { toast("Choisissez d'abord un cadre.", "error"); return; }
      S.dstep = k;
      Object.keys(panels).forEach(function (p) { panels[p].style.display = p === k ? "" : "none"; });
      if (k === "modele") drawModele();
      if (k === "photos") drawPhotos();
      drawSteps();
    }

    // ── 1. Modèle : famille, cadre (vignettes), skin, couleurs
    panels.modele = el("div");
    function drawModele() {
      var p = panels.modele;
      p.innerHTML = "";
      var famSel = el("select", { class: "input-field", style: "max-width:240px", onchange: function () {
        W.fam = famSel.value; W.cadre = null; W.modele_id = null; W.modele = null; W.theme_override = null; dirty(); drawModele();
      } }, [["familial", "Célébrations familiales"], ["corporate", "Professionnel / corporate"]].map(function (o) {
        return el("option", { value: o[0], text: o[1], selected: o[0] === W.fam ? "selected" : null });
      }));
      p.appendChild(field("Famille", famSel));
      // ★ seulement s'il distingue quelque chose (tous les cadres familiaux conviennent à un mariage, par ex.)
      var famCadres = S.cadres.filter(function (c) { return c.famille === W.fam; });
      var star = famCadres.some(function (c) { return c.suggested; }) && !famCadres.every(function (c) { return c.suggested; });
      p.appendChild(lbl("Cadre" + (star ? " (★ conseillé pour ce type d'événement)" : "")));
      p.appendChild(el("div", { class: "flex flex-wrap gap-3 mb-4" }, famCadres.map(function (c) {
        var on = W.cadre && W.cadre.code === c.code, m0 = (c.modeles || [])[0];
        return el("button", { type: "button", class: "text-left rounded-xl p-1.5 hover:bg-gray-50", "data-cadre": c.code,
          style: "width:108px;border:2px solid " + (on ? "#00502c" : "transparent"), onclick: function () {
            if (on) return;
            W.cadre = { code: c.code, famille: c.famille, scenes: c.scenes, champs: c.champs };
            W.modele = m0 || null; W.modele_id = m0 ? m0.id : null;   // couleurs de l'affiche gardées (propres à l'événement)
            dirty(); buildContenu(); drawModele();
          } }, [cadreThumb(c.code, m0 && m0.skin),
          el("p", { class: "text-[11px] font-bold text-gray-800 mt-1 leading-tight", text: (star && c.suggested ? "★ " : "") + c.name })]);
      })));
      var ci = cadreInfo();
      if (!ci) return;
      p.appendChild(lbl("Couleurs"));
      var row = el("div", { class: "flex flex-wrap gap-2 items-center mb-2" });
      (ci.modeles || []).forEach(function (m) {
        var on = W.modele_id === m.id && !(W.theme_override && W.theme_override.couleurs);
        row.appendChild(el("button", { type: "button", class: "flex items-center gap-2 text-xs font-semibold px-3 py-2 rounded-xl",
          style: "border:1px solid " + (on ? "#00502c" : "#e5e7eb") + ";background:" + (on ? "#f0fdf4" : "#fff"),
          onclick: function () { W.modele_id = m.id; W.modele = m; W.theme_override = null; dirty(); drawModele(); } }, [swatches(m.skin), el("span", { text: m.name.split(" — ").pop() })]));
      });
      if (W.theme_override && W.theme_override.couleurs) {
        row.appendChild(el("span", { class: "flex items-center gap-2 text-xs font-semibold px-3 py-2 rounded-xl", style: "border:1px solid #00502c;background:#f0fdf4" },
          [swatches(W.theme_override), el("span", { text: "Couleurs de l'affiche" })]));
      }
      p.appendChild(row);
      var fin = el("input", { type: "file", accept: "image/jpeg,image/png,image/webp", style: "display:none" });
      function applyPalette(res) { W.theme_override = Object.assign({}, W.theme_override, { couleurs: res.couleurs }); dirty(); drawModele(); }
      fin.addEventListener("change", function () { if (fin.files[0]) _upload("/design/palette", fin.files[0], applyPalette); });
      var acts = [el("button", { type: "button", class: "text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50",
        text: "🎨 Couleurs d'une affiche…", onclick: function () { fin.value = ""; fin.click(); } }), fin];
      if (S.ev.cover) acts.push(el("button", { type: "button", class: "text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50",
        text: "🎨 Couleurs de l'affiche de la page d'inscription", onclick: function () {
          fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/cover.jpg", { headers: { Authorization: "Bearer " + btAuth.getToken() } })
            .then(function (r) { return r.ok ? r.blob() : Promise.reject(new Error("Affiche indisponible")); })
            .then(function (b) { _upload("/design/palette", new File([b], "affiche.jpg", { type: "image/jpeg" }), applyPalette); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }));
      p.appendChild(el("div", { class: "flex flex-wrap gap-2" }, acts));
      p.appendChild(el("p", { class: "text-[11px] text-gray-400 mt-1", text: "L'affiche n'est pas conservée : seules les couleurs sont reprises (la police du modèle ne change pas)." }));
    }

    // ── 2. Contenu : fiche typée du cadre (reconstruite au changement de cadre, saisie conservée)
    panels.contenu = el("div", { class: "space-y-2" });
    panels.contenu.addEventListener("input", dirty);
    function snapshotFiche() {
      Object.keys(inputs).forEach(function (k) { var n = inputs[k]; W.fiche[k] = n._keys ? _textToList(n.value, n._keys) : (n.value || "").trim(); });
    }
    function buildContenu() {
      snapshotFiche();
      inputs = {};
      var p = panels.contenu;
      p.innerHTML = "";
      if (!W.cadre || !W.cadre.champs) return;
      (FIELD_SPECS[W.fam] || []).forEach(function (sp) {
        var type = W.cadre.champs[sp.k];
        if (type == null || type === "image") return;
        var keys = _listKeys(type), val = W.fiche[sp.k], node, help = null;
        if (keys) {
          node = el("textarea", { class: "input-field", rows: String(Math.max(3, (val || []).length + 1)), placeholder: sp.ph || keys.join(" | "),
            text: Array.isArray(val) ? _listToText(val, keys) : (val || "") });
          node._keys = keys;
          help = "Une ligne par entrée (" + keys.join(", ") + "). Écrivez librement, ex. « 18h00 Accueil des invités » : l'heure et les colonnes sont reconnues ; « | » ou une tabulation force la séparation.";
        } else if (type === "textarea") {
          node = el("textarea", { class: "input-field", rows: "2", placeholder: sp.ph || "", text: typeof val === "string" ? val : "" });
        } else {
          node = el("input", { class: "input-field", placeholder: sp.ph || "", value: typeof val === "string" ? val : "" });
        }
        inputs[sp.k] = node;
        p.appendChild(field(sp.l, node, help));
        if (keys) p.appendChild(importText(node));
      });
      if (inputs.intervenants) p.appendChild(el("p", { class: "text-[11px] text-gray-500", text: "Photos des intervenants : étape 3 « Photos »." }));
    }

    // ── 3. Photos : photo (familial) avec recadrage direct, logo (corporate), intervenants
    panels.photos = el("div", { class: "space-y-5" });
    function fileButton(label, accept, onfile) {
      var fin = el("input", { type: "file", accept: accept, style: "display:none" });
      fin.addEventListener("change", function () { if (fin.files[0]) onfile(fin.files[0]); });
      return [el("button", { type: "button", class: "text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50", text: label,
        onclick: function () { fin.value = ""; fin.click(); } }), fin];
    }
    function adminImg(which, img) {
      fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/design/photo?which=" + which, { headers: { Authorization: "Bearer " + btAuth.getToken() } })
        .then(function (r) { return r.ok ? r.blob() : Promise.reject(); })
        .then(function (b) { img.src = URL.createObjectURL(b); }).catch(function () {});
    }
    function delImage(which, after) {
      btApi.delete("/admin/invitations/events/" + S.eventId + "/design/" + which)
        .then(function () { d["has_" + which] = false; after(); }).catch(function (e) { toast(e.message || "Erreur", "error"); });
    }
    function cropEditor() {
      var sh = PHOTO_SHAPES[W.cadre && W.cadre.code] || { w: 180, ar: "1/1" }, c = W.photo_crop;
      var img = el("img", { alt: "", draggable: "false", style: "width:100%;height:100%;object-fit:cover;pointer-events:none;user-select:none" });
      var frame = el("div", { id: "inv-crop-frame", title: "Glisser pour placer les visages ; molette pour zoomer",
        style: "position:relative;width:" + sh.w + "px;max-width:100%;aspect-ratio:" + sh.ar + ";overflow:hidden;background:#f3f4f6;border:1px solid #d1d5db;cursor:grab;touch-action:none;" +
          (sh.r ? "border-radius:" + sh.r + ";" : "border-radius:10px;") + (sh.clip ? "clip-path:" + sh.clip + ";" : "") }, [img]);
      var zl = el("span", { class: "text-sm font-bold text-gray-700 text-center", style: "min-width:46px" });
      function paint() {
        img.style.objectPosition = c.x + "% " + c.y + "%";
        img.style.transformOrigin = c.x + "% " + c.y + "%";
        img.style.transform = "scale(" + c.zoom + ")";
        zl.textContent = c.zoom.toFixed(1).replace(".", ",") + "×";
      }
      function setZoom(z) { c.zoom = Math.round(Math.min(3, Math.max(1, z)) * 10) / 10; paint(); dirty(); }
      var drag = null;
      frame.addEventListener("pointerdown", function (e) { drag = { x: e.clientX, y: e.clientY, cx: c.x, cy: c.y }; frame.setPointerCapture(e.pointerId); frame.style.cursor = "grabbing"; });
      frame.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var r = frame.getBoundingClientRect();
        // Glisser vers la droite montre la partie gauche de la photo : le point focal recule
        c.x = Math.round(Math.min(100, Math.max(0, drag.cx - (e.clientX - drag.x) / r.width * 100 / c.zoom)));
        c.y = Math.round(Math.min(100, Math.max(0, drag.cy - (e.clientY - drag.y) / r.height * 100 / c.zoom)));
        paint();
      });
      function end() { if (drag) { if (drag.cx !== c.x || drag.cy !== c.y) dirty(); drag = null; frame.style.cursor = "grab"; } }
      frame.addEventListener("pointerup", end); frame.addEventListener("pointercancel", end);
      frame.addEventListener("wheel", function (e) { e.preventDefault(); setZoom(c.zoom + (e.deltaY < 0 ? 0.1 : -0.1)); }, { passive: false });
      paint();
      adminImg("photo", img);
      return el("div", { class: "flex flex-wrap items-center gap-4" }, [frame, el("div", { class: "flex flex-col gap-2" }, [
        el("div", { class: "flex items-center gap-1" }, [
          el("button", { type: "button", class: "action-btn text-sm", style: "background:#f3f4f6;color:#111;width:36px", "aria-label": "Zoom arrière", text: "−", onclick: function () { setZoom(c.zoom - 0.1); } }),
          zl,
          el("button", { type: "button", class: "action-btn text-sm", style: "background:#f3f4f6;color:#111;width:36px", "aria-label": "Zoom avant", text: "+", onclick: function () { setZoom(c.zoom + 0.1); } }),
          el("button", { type: "button", class: "text-xs px-2 py-1 ml-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50", text: "Recentrer",
            onclick: function () { c.x = 50; c.y = 28; c.zoom = 1; paint(); dirty(); } })]),
        el("p", { class: "text-[11px] text-gray-500", text: "Glissez la photo pour placer les visages. Aperçu à la forme du cadre." }),
      ])]);
    }
    function drawPhotos() {
      var p = panels.photos;
      p.innerHTML = "";
      if (W.fam === "familial") {
        var st = el("span", { class: "text-xs text-gray-500" });
        var head = el("div", { class: "flex flex-wrap items-center gap-2 mb-2" }, [el("h4", { class: "font-black text-gray-900 text-sm", text: "Photo du couple / du client" })]
          .concat(fileButton(d.has_photo ? "Remplacer" : "📷 Choisir une photo", "image/jpeg,image/png,image/webp", function (f) {
            st.textContent = "Envoi…";
            _upload("/design/photo", f, function () { d.has_photo = true; drawPhotos(); drawSteps(); });
          })).concat(d.has_photo ? [el("button", { type: "button", class: "text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50", text: "Retirer",
            onclick: function () { if (window.confirm("Retirer la photo ?")) delImage("photo", function () { drawPhotos(); drawSteps(); }); } })] : []).concat([st]));
        p.appendChild(el("div", {}, [head, d.has_photo ? cropEditor() : el("p", { class: "text-xs text-gray-400", text: "Aucune photo : le cadre affiche un emplacement aux couleurs du modèle." })]));
      } else {
        var lg = el("img", { alt: "", style: "max-width:140px;max-height:60px;object-fit:contain" + (d.has_logo ? "" : ";display:none") });
        if (d.has_logo) adminImg("logo", lg);
        p.appendChild(el("div", {}, [el("div", { class: "flex flex-wrap items-center gap-2 mb-2" }, [el("h4", { class: "font-black text-gray-900 text-sm", text: "Logo" })]
          .concat(fileButton(d.has_logo ? "Remplacer" : "Choisir le logo", "image/jpeg,image/png,image/webp", function (f) {
            _upload("/design/logo", f, function () { d.has_logo = true; drawPhotos(); drawSteps(); });
          })).concat(d.has_logo ? [el("button", { type: "button", class: "text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50", text: "Retirer",
            onclick: function () { delImage("logo", function () { drawPhotos(); drawSteps(); }); } })] : [])), lg]));
        if (inputs.intervenants) p.appendChild(speakerPhotos(inputs.intervenants));
      }
    }
    // Une ligne par intervenant saisi à l'étape 2 ; photo rattachée au nom
    function speakerPhotos(node) {
      var wrap = el("div", { class: "space-y-2" });
      wrap.appendChild(el("h4", { class: "font-black text-gray-900 text-sm", text: "Photos des intervenants" }));
      var rows = _textToList(node.value, node._keys).filter(function (r) { return r.nom; });
      if (!rows.length) { wrap.appendChild(el("p", { class: "text-xs text-gray-400", text: "Ajoutez les intervenants à l'étape 2 « Contenu » : une ligne apparaîtra ici pour chacun." })); return wrap; }
      rows.forEach(function (r) {
        var key = _spkKey(r.nom), ref = W.photoMap[key];
        var img = el("img", { alt: "", style: "width:100%;height:100%;object-fit:cover" + (ref ? "" : ";display:none") });
        if (ref) _mediaBlob(ref.slice(1), img);
        var av = el("div", { style: "width:44px;height:44px;flex:0 0 44px;border-radius:50%;overflow:hidden;background:#f3f4f6;border:1px solid #e5e7eb;display:flex;align-items:center;justify-content:center" },
          [img, el("span", { class: "text-xs font-bold text-gray-500", text: ref ? "" : r.nom.split(/\s+/).map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase() })]);
        var acts = fileButton(ref ? "Changer" : "📷 Ajouter une photo", "image/jpeg,image/png,image/webp", function (f) {
          _upload("/design/media", f, function (res) { W.photoMap[key] = res.ref; dirty(); drawPhotos(); });
        });
        if (ref) acts.push(el("button", { type: "button", class: "text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50", text: "Retirer", onclick: function () {
          btApi.delete("/admin/invitations/events/" + S.eventId + "/design/media/" + ref.slice(1))
            .then(function () { delete W.photoMap[key]; drawPhotos(); }).catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }));
        wrap.appendChild(el("div", { class: "flex items-center gap-3" }, [av,
          el("div", { class: "flex-1 min-w-0" }, [el("div", { class: "text-sm font-semibold text-gray-800 truncate", text: r.nom }),
            el("div", { class: "text-[11px] text-gray-400 truncate", text: [r.role, r.societe].filter(Boolean).join(" · ") })]),
          el("div", { class: "flex items-center gap-1" }, acts)]));
      });
      wrap.appendChild(el("p", { class: "text-[11px] text-gray-400", text: "Photo rattachée au nom : si vous renommez un intervenant, rechoisissez sa photo." }));
      return wrap;
    }

    // ── Enregistrement unique (barre fixe en bas du bloc)
    function collectFiche() {
      snapshotFiche();
      var out = {};
      Object.keys(inputs).forEach(function (k) { out[k] = W.fiche[k]; });
      (out.intervenants || []).forEach(function (r) { var ref = W.photoMap[_spkKey(r.nom)]; if (ref) r.photo = ref; });
      return out;
    }
    function save(newStatus, btn) {
      if (newStatus === "pret" && !W.modele_id) { toast("Choisissez un cadre avant de publier.", "error"); go("modele"); return; }
      var body = { modele_id: W.modele_id, fiche: collectFiche(), theme_override: W.theme_override || null, photo_crop: W.photo_crop };
      if (newStatus) body.status = newStatus;
      btn.disabled = true;
      btApi.put("/admin/invitations/events/" + S.eventId + "/design", body).then(function (res) {
        S.design = res; if (!S.design.fiche) S.design.fiche = {};
        S.ev.design_status = res.status; renderTop();
        if (newStatus === "pret") toast("Invitation publiée : visible par les invités.", "success");
        else if (newStatus === "brouillon") toast("Invitation retirée : les invités voient le pass classique.", "success");
        renderDesign(box);
        if (!newStatus) {          // confirmation discrète (pas de toast), puis retour à l'état
          var s2 = document.querySelector("#inv-design-body [data-status]");
          if (s2) {
            var prev = [s2.textContent, s2.style.color];
            s2.textContent = "✓ Enregistré"; s2.style.color = "#166534";
            setTimeout(function () { if (s2.textContent === "✓ Enregistré") { s2.textContent = prev[0]; s2.style.color = prev[1]; } }, 2500);
          }
        }
      }).catch(function (e) { btn.disabled = false; toast(e.message || "Erreur", "error"); });
    }
    status.setAttribute("data-status", "1");
    var bSave = el("button", { class: "action-btn text-sm", style: "background:#f3f4f6;color:#111", text: "Enregistrer", onclick: function () { save(null, bSave); } });
    var bPub = d.status === "pret"
      ? el("button", { class: "action-btn text-sm", style: "background:#fee2e2;color:#991b1b", text: "Dépublier", onclick: function () {
          if (window.confirm("Retirer l'invitation ? Les invités verront le pass classique.")) save("brouillon", bPub); } })
      : el("button", { class: "action-btn btn-primary text-sm", text: "Publier", onclick: function () { save("pret", bPub); } });
    var bar = el("div", { class: "sticky bottom-0 z-10 -mx-5 -mb-5 mt-5 px-5 py-3 flex flex-wrap items-center gap-2 border-t border-gray-100 rounded-b-2xl",
      style: "background:#fff" }, [status, el("span", { class: "flex-1" }), bSave, bPub]);

    box.appendChild(stepBar);
    box.appendChild(panels.modele); box.appendChild(panels.contenu); box.appendChild(panels.photos);
    box.appendChild(bar);
    buildContenu();
    drawStatus();
    go(S.dstep);
  }

  function importText(node) {
    var fin = el("input", { type: "file", accept: ".docx,.xlsx,.txt,text/plain", style: "display:none" });
    var btn = el("button", {
      type: "button", class: "text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50",
      text: "⬆ Importer .docx / .xlsx / .txt",
      onclick: function () { fin.value = ""; fin.click(); }
    });
    fin.addEventListener("change", function () {
      if (!fin.files[0]) return;
      _upload("/design/import-text", fin.files[0], function (res) {
        var t = res.text || "";
        node.value = node.value && node.value.trim() ? (node.value.replace(/\s+$/, "") + "\n" + t) : t;
        node.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    return el("div", { class: "mt-1" }, [btn, fin]);
  }

  function _upload(suffix, file, onok) {
    var fd = new FormData(); fd.append("file", file);
    fetch(BT_API + "/admin/invitations/events/" + S.eventId + suffix, {
      method: "POST", headers: { Authorization: "Bearer " + btAuth.getToken() }, body: fd
    }).then(function (r) { return r.ok ? r.json() : r.json().catch(function () { return {}; }).then(function (j) { throw new Error(j.detail || "Erreur"); }); })
      .then(onok).catch(function (e) { toast(e.message || "Envoi impossible", "error"); });
  }

  function refreshLive() {
    btApi.get("/admin/invitations/events/" + S.eventId + "/arrivals").then(function (d) {
      S.ev.stats = d.stats;
      var k = document.getElementById("inv-kpis");
      if (k) {
        var s = d.stats;
        k.replaceWith(el("div", { class: "grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4", id: "inv-kpis" }, [
          kpi(entLabel(s), s.entered + " / " + s.expected, s.rate + " % · " + s.remaining + " restant(s)", "text-primary"),
          kpi("Pass envoyés", s.passes_sent + " / " + s.passes, s.passes_arrived + " pass utilisés"),
          kpi("Pack payé", S.ev.quota ? S.ev.quota + " personnes" : "Aucun", S.ev.quota ? S.ev.listed + " personne(s) dans la liste" : "Envoi et scan bloqués",
              S.ev.quota ? "text-gray-900" : "text-amber-600"),
          kpi("Conflits hors ligne", String(s.conflicts), s.conflicts ? "Pass accepté(s) sur 2 téléphones" : "Aucun", s.conflicts ? "text-red-600" : "text-gray-900"),
        ]));
      }
      renderArrivals(d.arrivals, d.stats, d.breakdown);
      renderTop();
    }).catch(function () {});
  }

  // ── Pack & paiement (commandes figées) ────────────────────────────
  function orderLines(p) {
    var lines = [];
    if (p.legacy) return [fcfa(p.amount_fcfa) + " (pack activé avant le catalogue)"];
    lines.push("Pack " + (p.pack_name ? "« " + p.pack_name + " » " : "") + p.size + " pers. : " + fcfa(p.unit_price) + (p.custom ? " (sur mesure : " + (p.custom_reason || "") + ")" : ""));
    (p.options || []).forEach(function (o) { lines.push(o.name + (o.unit === "flat" ? "" : " × " + o.qty) + " : " + fcfa(o.total)); });
    if (p.discount) lines.push("Remise " + (p.discount.kind === "code" ? "code " + p.discount.code : "manuelle (" + (p.discount.reason || "") + ")") + " : −" + fcfa(p.discount.amount));
    if (p.vat && p.vat.enabled) lines.push("HT " + fcfa(p.vat.ht) + " · TVA " + (p.vat.rate_bp / 100).toString().replace(".", ",") + " % " + fcfa(p.vat.vat));
    return lines;
  }
  function packBlock() {
    var ev = S.ev, items = [];
    if (!ev.packs.length) items.push(el("p", { class: "text-sm text-amber-700 font-semibold mb-3", text: "Aucun pack activé : vous pouvez préparer la liste, mais l'envoi des pass, le scan et le lien de suivi restent bloqués." }));
    ev.packs.forEach(function (p) {
      var ch = p.offered ? "Pack offert" : ((S.meta.channels.filter(function (c) { return c.code === p.payment_channel; })[0] || {}).label || p.payment_channel);
      items.push(el("div", { class: "border border-gray-100 rounded-xl p-3 mb-2 flex flex-wrap items-start gap-3 " + (p.status === "actif" ? "" : "opacity-60") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("p", { class: "font-bold text-sm", text: (p.formula_name || formulaName(p.service)) + " · " + p.size + " personnes · " + (p.offered ? "OFFERT" : fcfa(p.amount_fcfa)) + (p.status === "actif" ? "" : " · ANNULÉ") }),
            p.offered ? el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-purple-100 text-purple-800", text: "Pack offert" }) : null,
          ]),
          el("ul", { class: "text-[11px] text-gray-600 mt-1 list-disc pl-4" }, orderLines(p).map(function (t) { return el("li", { text: t }); })),
          el("p", { class: "text-[11px] text-gray-500 mt-1", text: p.offered ? "Motif : " + (p.offered_reason || "—") + " · " + dt(p.activated_at)
            : ch + " · réf. " + p.payment_ref + " · reçu " + fcfa(p.amount_received) + " · " + dt(p.activated_at) }),
          p.agents_included ? el("p", { class: "text-[11px] text-gray-500", text: p.agents_included + " agent(s) GoEvent inclus" }) : null,
          p.cancel_reason ? el("p", { class: "text-[11px] text-red-600", text: "Annulé : " + p.cancel_reason }) : null,
        ]),
        p.status === "actif" && canActivate() ? el("button", { class: "action-btn btn-danger text-xs", text: "Annuler le pack", onclick: function () {
          var reason = window.prompt("Motif de l'annulation (obligatoire) :");
          if (!reason || reason.trim().length < 3) return;
          btApi.post("/admin/invitations/packs/" + p.id + "/cancel", { reason: reason.trim() }).then(function () { toast("Pack annulé.", "success"); reloadEvent(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }) : null,
      ]));
    });
    var actions = el("div", { class: "flex flex-wrap gap-2" });
    if (canActivate()) actions.appendChild(el("button", { class: "action-btn btn-primary text-xs", text: "Activer un pack (paiement reçu)", onclick: function () { orderModal(true); } }));
    actions.appendChild(el("button", { class: "action-btn text-xs", style: "background:#ede9fe;color:#5b21b6", text: "Préparer un devis numéroté", onclick: function () { quoteModal(null, { event: S.ev }); } }));
    items.push(actions);
    // Devis numérotés de cet événement (ouvrir, activer au même montant)
    var qbox = el("div", { class: "mt-3" });
    items.push(qbox);
    btApi.get("/admin/invitations/quotes?event_id=" + ev.id + "&limit=20").then(function (d) {
      if (!d.quotes.length) return;
      qbox.appendChild(lbl("Devis de cet événement"));
      d.quotes.forEach(function (q) {
        qbox.appendChild(el("button", { class: "w-full text-left flex flex-wrap items-center gap-2 p-2 rounded-xl hover:bg-gray-50 text-sm", onclick: function () { openQuote(q.id); } }, [
          el("span", { class: "font-mono font-bold", text: q.number }), qChip(q.status),
          el("span", { class: "text-gray-600", text: fcfa(q.total_fcfa) + " · valable jusqu'au " + isoDay(q.valid_until) }),
        ]));
      });
    }).catch(function () {});
    if (!canActivate()) items.push(el("p", { class: "text-[11px] text-gray-500 mt-2", text: "L'activation d'un pack est réservée à l'administrateur, après réception du paiement." }));
    return block("Pack & paiement", items);
  }

  // ── Sélecteur formule / pack / options / remise : UN composant pour l'activation, le devis
  // d'un événement et les devis numérotés. Le récapitulatif vient TOUJOURS du serveur (même calcul).
  //   o.priceUrl   : POST de calcul (…/packs/quote ou /admin/invitations/quotes/price)
  //   o.draft      : devis préparé par un commercial (sur mesure / remise manuelle « à valider »)
  //   o.serviceCode, o.initial (saisie d'un devis existant), o.activate (pré-remplit le montant reçu)
  function orderForm(o) {
    var m = S.meta, admin = canActivate(), advanced = admin || !!o.draft, init = o.initial || {};
    var formula = el("select", { class: "input-field" }), pack = el("select", { class: "input-field" });
    (m.formulas || []).forEach(function (fo) { formula.appendChild(el("option", { value: String(fo.id), text: fo.name })); });
    var cur = init.formula_id ? (m.formulas || []).filter(function (fo) { return fo.id === init.formula_id; })[0]
      : (m.formulas || []).filter(function (fo) { return fo.code === (o.serviceCode || "accompagne"); })[0];
    if (cur) formula.value = String(cur.id);
    var cSize = el("input", { class: "input-field", type: "number", min: "1", max: "5000", placeholder: "Personnes" });
    var cPrice = el("input", { class: "input-field", type: "number", min: "0", placeholder: "FCFA" });
    var cReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. mariage 1 200 invités, tarif négocié" });
    var customBox = el("div", { class: "hidden grid sm:grid-cols-3 gap-3 p-3 rounded-xl bg-gray-50" }, [field("Taille", cSize), field("Prix du pack", cPrice), field("Motif (obligatoire)", cReason)]);
    var optBox = el("div", { class: "space-y-2" }), optInputs = [];
    var dMode = el("select", { class: "input-field" }, [el("option", { value: "", text: "Aucune remise" }), el("option", { value: "code", text: "Code de remise" })]
      .concat(advanced ? [el("option", { value: "manual", text: admin ? "Remise manuelle (admin)" : "Remise manuelle (à valider par l'admin)" })] : []));
    var dCode = el("input", { class: "input-field uppercase", maxlength: "40", placeholder: "Ex. PIONNIER" });
    var mKind = el("select", { class: "input-field" }, [el("option", { value: "pct", text: "%" }), el("option", { value: "fixed", text: "FCFA" })]);
    var mValue = el("input", { class: "input-field", type: "number", min: "1" }), mReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Motif (obligatoire)" });
    var codeBox = el("div", { class: "hidden" }, [field("Code", dCode, "Remise sur le pack uniquement (jamais sur les options).")]);
    var manualBox = el("div", { class: "hidden grid sm:grid-cols-3 gap-3" }, [field("Type", mKind), field("Valeur", mValue), field("Motif", mReason)]);
    var offered = el("input", { type: "checkbox" }), offeredReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. partenariat, mariage du personnel" });
    var offeredBox = el("div", { class: "hidden" }, [field("Motif du pack offert (obligatoire)", offeredReason)]);
    var recap = el("div", { class: "p-3 rounded-xl bg-gray-50 text-sm" }, [el("p", { class: "text-gray-400", text: "Choisissez un pack…" })]);
    var lastQuote = null, timer = null, sizeHint = 0;
    // Pack proposé d'après le nombre d'invités : le plus petit pack qui couvre la liste (tant que
    // l'utilisateur n'a pas choisi lui-même)
    function suggest(n) {
      sizeHint = n || 0;
      if (!n || pack.dataset.touched || pack.value === "custom") return;
      var fid = parseInt(formula.value, 10);
      var list = (m.packs || []).filter(function (p) { return p.formula_id === fid && !p.on_quote; }).sort(function (a, b) { return a.size - b.size; });
      if (!list.length) return;
      var pick = list.filter(function (p) { return p.size >= n; })[0] || list[list.length - 1];
      if (String(pick.id) !== pack.value) { pack.value = String(pick.id); sync(); schedule(); }
    }

    function fillPacks() {
      var fid = parseInt(formula.value, 10);
      pack.innerHTML = "";
      (m.packs || []).filter(function (p) { return p.formula_id === fid; }).forEach(function (p) {
        pack.appendChild(el("option", { value: String(p.id), text: p.size + " pers." + (p.name ? " · " + p.name : "") + " · " + (p.on_quote ? "sur devis" : fcfa(p.price_fcfa)) }));
      });
      if (advanced) pack.appendChild(el("option", { value: "custom", text: admin ? "Sur mesure (taille et prix libres)…" : "Sur mesure (à valider par l'admin)…" }));
      optBox.innerHTML = ""; optInputs = [];
      var opts = (m.options || []).filter(function (op) { return op.formula_ids.indexOf(fid) >= 0; });
      if (!opts.length) optBox.appendChild(el("p", { class: "text-[11px] text-gray-400", text: "Aucune option pour cette formule." }));
      opts.forEach(function (op) {
        var input = op.unit === "flat" ? el("input", { type: "checkbox" }) : el("input", { class: "input-field", type: "number", min: "0", max: String(op.max_qty), value: "0", style: "max-width:90px" });
        input.addEventListener("input", schedule); input.addEventListener("change", schedule);
        optInputs.push({ o: op, input: input });
        optBox.appendChild(el("label", { class: "flex items-center gap-2 text-sm" }, [input,
          el("span", { text: op.name + " — " + fcfa(op.price_fcfa) + (op.unit === "flat" ? " (forfait)" : " / " + (op.unit_label || (op.unit === "per_page" ? "page" : "unité")) + ", max " + op.max_qty) })]));
      });
    }
    function payload() {
      var body = { formula_id: parseInt(formula.value, 10), options: [], offered: offered.checked };
      if (pack.value === "custom") body.custom = { size: parseInt(cSize.value, 10) || 0, price_fcfa: cPrice.value === "" ? -1 : parseInt(cPrice.value, 10), reason: cReason.value.trim() };
      else body.pack_id = parseInt(pack.value, 10) || null;
      optInputs.forEach(function (x) {
        var q = x.o.unit === "flat" ? (x.input.checked ? 1 : 0) : (parseInt(x.input.value, 10) || 0);
        if (q > 0) body.options.push({ option_id: x.o.id, qty: q });
      });
      if (offered.checked) body.offered_reason = offeredReason.value.trim();
      else if (dMode.value === "code" && dCode.value.trim()) body.discount_code = dCode.value.trim();
      else if (dMode.value === "manual") body.manual_discount = { kind: mKind.value, value: parseInt(mValue.value, 10) || 0, reason: mReason.value.trim() };
      return body;
    }
    function renderQuote(q) {
      lastQuote = q;
      recap.innerHTML = "";
      quoteLines(q).forEach(function (n) { recap.appendChild(n); });
      if (q.needs_admin) recap.appendChild(el("p", { class: "text-xs font-bold text-purple-800 bg-purple-50 rounded-lg p-2 mt-2", text: "Sur mesure ou remise manuelle : l'administrateur validera ce devis avant son envoi." }));
      (q.warnings || []).forEach(function (w) { recap.appendChild(el("p", { class: "text-xs font-bold text-amber-800 bg-amber-50 rounded-lg p-2 mt-2", text: "⚠ " + w })); });
      (q.blocking || []).forEach(function (w) { recap.appendChild(el("p", { class: "text-xs font-bold text-red-700 bg-red-50 rounded-lg p-2 mt-2", text: "⛔ " + w })); });
      if (o.onQuote) o.onQuote(q);
    }
    function refresh() {
      if (!pack.value) { lastQuote = null; recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-gray-400", text: "Aucun pack disponible pour cette formule." })); return; }
      if (pack.value === "custom" && (!cSize.value || cPrice.value === "")) { recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-gray-400", text: "Indiquez la taille et le prix du pack sur mesure." })); lastQuote = null; return; }
      btApi.post(o.priceUrl, payload()).then(renderQuote).catch(function (e) {
        lastQuote = null; recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-sm font-bold text-red-600", text: e.message || "Calcul impossible" }));
      });
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 300); }
    function sync() {
      customBox.classList.toggle("hidden", pack.value !== "custom");
      codeBox.classList.toggle("hidden", offered.checked || dMode.value !== "code");
      manualBox.classList.toggle("hidden", offered.checked || dMode.value !== "manual");
      dMode.disabled = offered.checked;
      offeredBox.classList.toggle("hidden", !offered.checked);
      if (o.onSync) o.onSync(offered.checked);
    }
    formula.addEventListener("change", function () { fillPacks(); if (sizeHint) suggest(sizeHint); sync(); schedule(); });
    pack.addEventListener("change", function () { pack.dataset.touched = "1"; });
    [pack, dMode, offered].forEach(function (x) { x.addEventListener("change", function () { sync(); schedule(); }); });
    [cSize, cPrice, cReason, dCode, mValue, mReason, offeredReason].forEach(function (x) { x.addEventListener("input", schedule); });
    mKind.addEventListener("change", schedule);
    fillPacks();
    // Reprise d'une saisie existante (modification d'un brouillon)
    if (init.custom) { pack.value = "custom"; cSize.value = init.custom.size; cPrice.value = init.custom.price_fcfa; cReason.value = init.custom.reason || ""; }
    else if (init.pack_id) pack.value = String(init.pack_id);
    (init.options || []).forEach(function (l) {
      optInputs.forEach(function (x) { if (x.o.id === l.option_id) { if (x.o.unit === "flat") x.input.checked = true; else x.input.value = String(l.qty); } });
    });
    if (init.discount_code) { dMode.value = "code"; dCode.value = init.discount_code; }
    else if (init.manual_discount) { dMode.value = "manual"; mKind.value = init.manual_discount.kind; mValue.value = init.manual_discount.value; mReason.value = init.manual_discount.reason || ""; }
    if (init.offered && admin) { offered.checked = true; offeredReason.value = init.offered_reason || ""; }
    sync(); refresh();

    var node = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Formule", formula), field("Pack", pack)]),
      customBox,
      el("div", {}, [lbl("Options"), optBox]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Remise (une seule par commande)", dMode), codeBox]),
      manualBox,
      admin ? el("label", { class: "flex items-center gap-2 text-sm font-semibold text-purple-800" }, [offered, "Pack offert (total 0, sans paiement — admin, motif obligatoire)"]) : null,
      offeredBox,
      el("div", {}, [lbl("Récapitulatif"), recap]),
    ]);
    return { node: node, payload: payload, last: function () { return lastQuote; }, offered: function () { return offered.checked; }, suggest: suggest };
  }

  // Lignes d'un calcul (aperçu, devis enregistré, commande) — même rendu partout
  function quoteLines(q) {
    function row(label, value, cls) { return el("div", { class: "flex justify-between gap-3 " + (cls || "") }, [el("span", { text: label }), el("span", { class: "font-bold whitespace-nowrap", text: value })]); }
    var out = [row(q.formula.name + " · " + (q.pack.name && q.pack.name !== "Sur mesure" ? "« " + q.pack.name + " » " : "") + q.pack.size + " pers." + (q.pack.custom ? " (sur mesure)" : ""), fcfa(q.pack.unit_price))];
    (q.options || []).forEach(function (op) { out.push(row(op.name + (op.unit === "flat" ? "" : " × " + op.qty), fcfa(op.total), "text-gray-600")); });
    if (q.discount) out.push(row("Remise " + (q.discount.kind === "code" ? q.discount.code : "manuelle") + (q.discount.type === "pct" ? " (−" + q.discount.value + " % du pack)" : ""), "−" + fcfa(q.discount.amount), "text-green-700"));
    if (q.offered) out.push(row("Pack offert", "−" + fcfa(q.subtotal), "text-purple-700"));
    if (q.vat && q.vat.enabled) { out.push(row("Total HT", fcfa(q.vat.ht), "text-gray-600")); out.push(row("TVA " + (q.vat.rate_bp / 100).toString().replace(".", ",") + " %", fcfa(q.vat.vat), "text-gray-600")); }
    out.push(row(q.vat && q.vat.enabled ? "Total TTC à payer" : "Total à payer", fcfa(q.amount_due), "text-base border-t border-gray-200 pt-2 mt-2"));
    if (q.agents_included) out.push(el("p", { class: "text-[11px] text-gray-600 mt-2", text: q.agents_included + " agent(s) GoEvent inclus dans ce pack" + (q.agents_assigned != null ? " · " + q.agents_assigned + " déjà assigné(s) à l'événement" : "") }));
    return out;
  }

  // Activation (admin) : depuis le sélecteur, ou depuis un devis numéroté (instantané rejoué, non modifiable)
  function orderModal(activate, fromQuote) {
    if (!activate) { quoteModal(null, { event: S.ev }); return; }
    var m = S.meta, form = null, quoteSnap = fromQuote ? fromQuote.snapshot : null;
    var channel = el("select", { class: "input-field" }), ref = el("input", { class: "input-field", maxlength: "80" }), refLbl = lbl("Référence");
    var amount = el("input", { class: "input-field", type: "number", min: "0" }), note = el("input", { class: "input-field", maxlength: "300", placeholder: "Facultatif" });
    var confirm = el("input", { type: "checkbox" });
    var active = (m.channels || []).filter(function (c) { return c.active; });
    active.forEach(function (c) { channel.appendChild(el("option", { value: c.code, text: c.label })); });
    var payBox = el("div", { class: "space-y-3" }, [
      el("div", { class: "p-3 rounded-xl bg-amber-50 text-xs text-amber-900", text: "N'activez un pack qu'après avoir reçu le paiement sur un moyen AU NOM DE GoEvent SARLU. La référence ne pourra plus servir à activer un autre pack." }),
      field("Moyen de paiement", channel), el("div", {}, [refLbl, ref]), field("Montant reçu (FCFA)", amount, "Au moins le total à payer."), field("Note", note),
      el("label", { class: "flex items-start gap-2 text-sm font-semibold text-gray-800" }, [confirm, "Je confirme que ce paiement a été reçu sur un moyen de paiement au nom de GoEvent SARLU."]),
    ]);
    function syncRef() { var c = active.filter(function (x) { return x.code === channel.value; })[0]; refLbl.textContent = c ? c.ref_label + " (obligatoire)" : "Référence"; }
    channel.addEventListener("change", syncRef); syncRef();
    amount.addEventListener("input", function () { amount.dataset.touched = "1"; });
    var top;
    if (quoteSnap) {
      var recap = el("div", { class: "p-3 rounded-xl bg-gray-50 text-sm" }, quoteLines(quoteSnap));
      top = el("div", { class: "space-y-2" }, [
        el("p", { class: "text-xs font-bold text-primary", text: "Devis " + fromQuote.number + " — montant figé (le catalogue actuel ne s'applique pas)." }), recap]);
      amount.value = String(quoteSnap.amount_due);
      payBox.classList.toggle("hidden", !!quoteSnap.offered);
    } else {
      form = orderForm({ priceUrl: "/admin/invitations/events/" + S.eventId + "/packs/quote", serviceCode: S.ev.service_level, activate: true,
        onQuote: function (q) { if (!amount.dataset.touched) amount.value = String(q.amount_due); },
        onSync: function (off) { payBox.classList.toggle("hidden", off); } });
      top = form.node;
    }
    var body = el("div", { class: "space-y-3" }, [top, payBox]);
    modal(fromQuote ? "Activer le pack du devis " + fromQuote.number : "Activer un pack", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Activer", onclick: function () {
        var body2, isOffered;
        if (quoteSnap) {
          body2 = Object.assign({}, (quoteSnap.input || { formula_id: quoteSnap.formula.id, pack_id: quoteSnap.pack.id, options: [] }), { quote_id: fromQuote.id });
          isOffered = !!quoteSnap.offered;
        } else {
          var lq = form.last();
          if (!lq) { toast("Le récapitulatif doit être valide avant l'activation.", "error"); return; }
          if (lq.blocking.length) { toast(lq.blocking[0], "error"); return; }
          body2 = form.payload(); isOffered = form.offered();
        }
        if (!isOffered) {
          if (!confirm.checked) { toast("Cochez la confirmation de réception du paiement.", "error"); return; }
          if (ref.value.trim().length < 3) { toast("Référence du paiement obligatoire.", "error"); return; }
          body2.payment_channel = channel.value; body2.payment_ref = ref.value.trim();
          body2.amount_received = parseInt(amount.value, 10) || 0; body2.confirmed_goevent_account = true;
        }
        body2.note = note.value.trim() || null;
        if (isOffered && !window.confirm("Activer ce pack OFFERT (total 0, aucune recette) ?")) return;
        b.disabled = true;
        btApi.post("/admin/invitations/events/" + S.eventId + "/packs", body2)
          .then(function () { close(); toast(isOffered ? "Pack offert activé." : "Pack activé.", "success"); reloadEvent(); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ── Invités : import, liste, envoi WhatsApp ──────────────────────
  function guestsBlock() {
    var paste = el("textarea", { class: "input-field font-mono text-xs", rows: "5", id: "inv-paste",
      placeholder: "Collez ici les colonnes depuis Excel (ou un CSV) :\nNom ⇥ Téléphone ⇥ Places ⇥ Table ⇥ Catégorie ⇥ Papier\nJean Kossi ⇥ 75 00 00 01 ⇥ 2 ⇥ T1 ⇥ Famille ⇥ non" });
    var file = el("input", { type: "file", accept: ".xlsx,.docx,.csv,.txt,.doc,.xls", class: "text-xs", onchange: function (e) {
      var f = e.target.files[0];
      if (!f) return;
      var name = f.name.toLowerCase();
      if (/\.(csv|txt)$/.test(name)) {
        var r = new FileReader();
        r.onload = function () { paste.value = String(r.result || ""); runImport(paste.value, true); };
        r.readAsText(f, "utf-8");
        return;
      }
      if (/\.(doc|xls)$/.test(name)) {
        toast("Ancien format (.doc / .xls) : ouvrez le fichier et enregistrez-le en .docx ou .xlsx, ou copiez-collez la liste.", "error");
        e.target.value = "";
        return;
      }
      if (f.size > 2 * 1024 * 1024) { toast("Fichier trop lourd (2 Mo maximum).", "error"); e.target.value = ""; return; }
      // .xlsx / .docx : lus par le serveur (contenu renvoyé, rien n'est stocké), puis vérification habituelle
      var fd = new FormData();
      fd.append("file", f);
      fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/guests/read-file", {
        method: "POST", body: fd, headers: { Authorization: "Bearer " + btAuth.getToken() },
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (d) {
          if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Lecture du fichier impossible.");
          return d;
        });
      }).then(function (d) {
        paste.value = d.text;
        toast(d.rows + " ligne(s) lue(s) dans le fichier.", "success");
        runImport(paste.value, true);
      }).catch(function (err) { toast(err.message || "Lecture du fichier impossible.", "error"); })
        .then(function () { e.target.value = ""; });
    } });
    var report = el("div", { id: "inv-import-report" });
    var importBox = el("details", { class: "mb-4", id: "inv-import", open: S.ev.listed ? null : "open" }, [
      el("summary", { class: "cursor-pointer font-bold text-sm text-primary mb-2", text: "Importer des invités" }),
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Fichier Excel (.xlsx), Word (.docx, tableau), CSV, ou copier-coller. Colonnes dans cet ordre : Nom, Téléphone (8 chiffres = +236, ou format international), Places (1 à 20, défaut 1), Table, Catégorie, Papier (« oui » = pass imprimé ; vide = papier seulement si pas de numéro) — ou une ligne de titres (« Nom », « Téléphone »…). Anciens formats .doc / .xls : enregistrez-les d'abord en .docx / .xlsx." }),
      paste,
      el("div", { class: "flex flex-wrap items-center gap-3 mt-2" }, [file,
        el("button", { class: "action-btn btn-primary text-xs", text: "Vérifier la liste", onclick: function () { runImport(paste.value, true); } })]),
      report,
    ]);
    var search = el("input", { class: "input-field", style: "max-width:260px", placeholder: "Rechercher (nom, numéro, table)", value: S.q,
      oninput: function (e) { S.q = e.target.value; S.page = 1; clearTimeout(S._t); S._t = setTimeout(loadGuests, 300); } });
    var filter = el("select", { class: "input-field", style: "max-width:200px", onchange: function (e) { S.status = e.target.value; S.page = 1; loadGuests(); } },
      [["", "Tous"], ["pending", "À envoyer"], ["sent", "Envoyés"], ["paper", "Pass papier"], ["arrived", "Entrés"], ["not_arrived", "Pas encore arrivés"], ["revoked", "Annulés"]].map(function (o) {
        return el("option", { value: o[0], text: o[1], selected: S.status === o[0] ? "selected" : null });
      }));
    var sendBtn = el("button", { class: "action-btn text-xs", id: "inv-send-next", style: "background:#25d366;color:#fff", onclick: sendNext });
    return block("Invités", [importBox,
      el("div", { class: "flex flex-wrap items-center gap-2 mb-3" }, [search, filter, el("span", { class: "flex-1" }), sendBtn]),
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "« Envoyer au suivant » ouvre WhatsApp avec le message prêt pour le prochain invité non servi : appuyez sur Envoyer dans WhatsApp, revenez ici, recommencez." }),
      el("p", { id: "inv-nophone", class: "hidden text-xs font-semibold text-amber-800 bg-amber-50 rounded-xl p-3 mb-2" }),
      el("div", { id: "inv-paper", class: "hidden flex flex-wrap items-center gap-2 text-xs font-semibold text-orange-900 bg-orange-50 rounded-xl p-3 mb-2" }),
      el("div", { id: "inv-guests" })]);
  }

  // Analyse du texte collé : tabulations (Excel), point-virgule ou virgule (CSV), guillemets
  function parseRows(text) {
    var lines = String(text || "").replace(/\r/g, "").split("\n").filter(function (l) { return l.trim(); });
    if (!lines.length) return [];
    var sep = lines[0].indexOf("\t") >= 0 ? "\t" : (lines[0].split(";").length > lines[0].split(",").length ? ";" : ",");
    function split(line) {
      if (sep === "\t") return line.split("\t");
      var out = [], cur = "", q = false;
      for (var i = 0; i < line.length; i++) {
        var c = line[i];
        if (c === '"') { if (q && line[i + 1] === '"') { cur += '"'; i++; } else q = !q; }
        else if (c === sep && !q) { out.push(cur); cur = ""; }
        else cur += c;
      }
      out.push(cur);
      return out;
    }
    var cols = { full_name: 0, phone: 1, seats: 2, table_label: 3, category: 4, paper: 5 }, start = 0;
    var head = split(lines[0]).map(function (h) { return h.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); });
    if (head.some(function (h) { return /^(nom|name|invite)/.test(h); })) {
      var find = function (re) { for (var i = 0; i < head.length; i++) if (re.test(head[i])) return i; return -1; };
      cols = { full_name: find(/^(nom|name|invite)/), phone: find(/^(tel|phone|numero|n°|no |whatsapp|gsm|portable|mobile|contact|cel)/), seats: find(/^(place|nb|nombre|pers)/),
               table_label: find(/^table/), category: find(/^(cat|groupe|type)/), paper: find(/^(papier|paper|imprim)/) };
      start = 1;
    }
    return lines.slice(start).map(function (l, i) {
      var c = split(l), get = function (k) { return cols[k] >= 0 && c[cols[k]] != null ? String(c[cols[k]]).trim() : null; };
      return { line: i + start + 1, full_name: get("full_name"), phone: get("phone"), seats: get("seats"), table_label: get("table_label"), category: get("category"), paper: get("paper") || null };
    });
  }

  function runImport(text, dry) {
    var rows = dry ? parseRows(text) : S.importRows;
    if (!rows || !rows.length) { toast("Aucune ligne à importer.", "error"); return; }
    var box = document.getElementById("inv-import-report");
    btApi.post("/admin/invitations/events/" + S.eventId + "/guests/import", { rows: rows, dry_run: dry }).then(function (r) {
      if (!dry) {
        toast(r.created + " invité(s) importé(s).", "success");
        S.importRows = null;
        var p = document.getElementById("inv-paste"); if (p) p.value = "";
        reloadEvent().then(loadGuests);
        return;
      }
      S.importRows = rows;
      box.innerHTML = "";
      var q = r.quota;
      box.appendChild(el("div", { class: "mt-3 p-3 rounded-xl " + (r.errors.length || q.exceeds ? "bg-amber-50" : "bg-green-50") }, [
        el("p", { class: "text-sm font-bold text-gray-900", text: r.valid.length + " invité(s) valide(s), " + q.adding + " personne(s) · " + r.errors.length + " ligne(s) en erreur" }),
        el("p", { class: "text-xs text-gray-600", text: q.pack ? "Pack : " + q.pack + " personnes · liste après import : " + q.after + (q.exceeds ? " → DÉPASSE LE PACK" : "")
                                                               : "Aucun pack payé : l'import est possible, l'envoi restera bloqué jusqu'à l'activation." }),
        (function () {
          var paper = r.valid.filter(function (v) { return v.paper; }).length;
          var noph = r.valid.filter(function (v) { return !v.phone && !v.paper; }).length;
          return el("div", {}, [
            paper ? el("p", { class: "text-xs font-bold text-orange-800 mt-1", text: "🖨 " + paper + " pass papier (à imprimer, remis en main propre) — " +
              "par défaut pour les invités sans numéro. Si c'est une erreur, vérifiez que la colonne Téléphone est bien la 2e (ou titrée « Téléphone »)." }) : null,
            noph ? el("p", { class: "text-xs font-bold text-amber-800 mt-1", text: "⚠ " + noph + " invité(s) sans numéro et « papier = non » : leur pass ne partira ni par WhatsApp ni à l'impression." }) : null,
          ]);
        })(),
        r.errors.length ? el("ul", { class: "text-xs text-red-700 mt-2 list-disc pl-5 max-h-40 overflow-auto" }, r.errors.map(function (e) {
          return el("li", { text: "Ligne " + e.line + (e.name ? " (" + e.name + ")" : "") + " : " + e.message });
        })) : null,
        r.valid.length && !q.exceeds ? el("button", { class: "action-btn btn-primary text-xs mt-3", text: "Importer les " + r.valid.length + " invité(s) valides",
          onclick: function () { runImport(null, false); } }) : null,
      ]));
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function loadGuests() {
    if (!S.eventId) return;
    var qs = "?page=" + S.page + "&limit=50" + (S.q ? "&q=" + encodeURIComponent(S.q) : "") + (S.status ? "&status=" + S.status : "");
    btApi.get("/admin/invitations/events/" + S.eventId + "/guests" + qs).then(function (d) { S.guests = d; S.queue = d.send_queue; S.noPhone = d.pending_without_phone || 0;
      S.paperPending = d.paper_pending || 0; S.paperTotal = d.paper_total || 0; renderGuests(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function renderGuests() {
    var box = document.getElementById("inv-guests"), d = S.guests;
    var btn = document.getElementById("inv-send-next");
    renderTop();
    if (btn && S.queue) {
      // « Tous les pass sont envoyés » seulement s'il ne reste vraiment rien à envoyer
      btn.textContent = S.queue.length ? "Envoyer au suivant sur WhatsApp (" + S.queue.length + ")"
        : S.noPhone ? "Aucun invité avec numéro à envoyer" : "Tous les pass sont envoyés";
      btn.disabled = !S.queue.length || !S.ev.quota;
      btn.title = S.ev.quota ? "" : "Activez un pack pour envoyer les pass";
    }
    var warn = document.getElementById("inv-nophone");
    if (warn) {
      warn.textContent = S.noPhone ? "⚠ " + S.noPhone + " invité(s) sans numéro : leur pass n'est PAS envoyé. Ajoutez le numéro (« Modifier ») " +
        "pour l'envoyer par WhatsApp, ou transmettez le lien vous-même (« Copier le lien »)." : "";
      warn.classList.toggle("hidden", !S.noPhone);
    }
    var paper = document.getElementById("inv-paper");
    if (paper) {
      paper.innerHTML = "";
      paper.classList.toggle("hidden", !S.paperTotal);
      if (S.paperTotal) {
        paper.appendChild(el("span", { class: "flex-1 min-w-[200px]", text: "🖨 " + S.paperTotal + " pass papier" +
          (S.paperPending ? " dont " + S.paperPending + " à imprimer" : ", tous imprimés") + " (A4, 8 pass découpables par page, QR générés chez GoEvent)." }));
        if (S.paperPending) paper.appendChild(act("Imprimer les " + S.paperPending + " nouveaux", "background:#ea580c;color:#fff", function () { downloadPaper("pending", this); }));
        paper.appendChild(act("Réimprimer tous les pass papier", "background:#fff;color:#9a3412", function () { downloadPaper("paper", this); }));
      }
    }
    if (!box || !d) return;
    box.innerHTML = "";
    if (!d.guests.length) { box.appendChild(el("p", { class: "text-sm text-gray-400 py-4 text-center", text: "Aucun invité." })); return; }
    var table = el("div", { class: "divide-y divide-gray-100" });
    d.guests.forEach(function (g) { table.appendChild(guestRow(g)); });
    box.appendChild(table);
    var pages = Math.ceil(d.total / d.limit);
    if (pages > 1) {
      box.appendChild(el("div", { class: "flex justify-center items-center gap-3 mt-3 text-sm" }, [
        el("button", { class: "action-btn text-xs", text: "←", disabled: S.page <= 1 ? "disabled" : null, onclick: function () { S.page--; loadGuests(); } }),
        el("span", { text: "Page " + S.page + " / " + pages + " · " + d.total + " invités" }),
        el("button", { class: "action-btn text-xs", text: "→", disabled: S.page >= pages ? "disabled" : null, onclick: function () { S.page++; loadGuests(); } }),
      ]));
    }
  }

  // PDF des pass papier (fetch authentifié → téléchargement). Les pass papier inclus passent à « Imprimé ».
  function downloadPaper(scope, btn) {
    if (!S.ev.quota) { toast("Activez un pack avant d'imprimer les pass.", "error"); return; }
    if (btn) btn.disabled = true;
    fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/paper.pdf?scope=" + scope, {
      headers: { Authorization: "Bearer " + btAuth.getToken() },
    }).then(function (res) {
      if (!res.ok) return res.json().catch(function () { return {}; }).then(function (d) {
        throw new Error(typeof d.detail === "string" ? d.detail : "PDF indisponible.");
      });
      return res.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = el("a", { href: url, download: "pass-papier-" + S.eventId + ".pdf" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
      toast("PDF prêt : imprimez en A4 (taille réelle), puis découpez le long des pointillés.", "success");
      reloadEvent().then(loadGuests);
    }).catch(function (e) { toast(e.message || "PDF indisponible.", "error"); })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function act(label, style, fn) { return el("button", { class: "action-btn text-[11px]", style: style, text: label, onclick: fn }); }
  function guestRow(g) {
    var st = STATUS[g.status] || STATUS.pending;
    var actions = el("div", { class: "flex flex-wrap gap-1" });
    if (g.status !== "revoked") {
      if (g.wa_url) actions.appendChild(act("WhatsApp", "background:#dcfce7;color:#166534", function () { sendGuest(g); }));
      if (g.pass_url) actions.appendChild(act("Copier le lien", "background:#f3f4f6;color:#374151", function () { copy(g.pass_url, "Lien du pass"); }));
      if (g.seats_used < g.seats) actions.appendChild(act("Entrée manuelle", "background:#fef3c7;color:#92400e", function () { manualModal(g); }));
      actions.appendChild(act("Modifier", "background:#ede9fe;color:#5b21b6", function () { guestModal(g); }));
      actions.appendChild(act("Nouveau lien", "background:#f3f4f6;color:#374151", function () {
        if (!window.confirm("Générer un nouveau lien pour " + g.full_name + " ? L'ancien pass ne fonctionnera plus : il faudra le renvoyer.")) return;
        btApi.post("/admin/invitations/guests/" + g.id + "/regenerate").then(function () { toast("Nouveau lien généré : pensez à le renvoyer.", "success"); loadGuests(); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
      actions.appendChild(act("Annuler le pass", "background:#fee2e2;color:#991b1b", function () {
        if (!window.confirm("Annuler le pass de " + g.full_name + " ? Il sera refusé à l'entrée.")) return;
        btApi.post("/admin/invitations/guests/" + g.id + "/revoke").then(function () { toast("Pass annulé.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
    } else {
      actions.appendChild(act("Restaurer", "background:#dcfce7;color:#166534", function () {
        btApi.post("/admin/invitations/guests/" + g.id + "/restore").then(function () { toast("Pass restauré.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
    }
    if (!g.seats_used) actions.appendChild(act("Supprimer", "background:#fee2e2;color:#991b1b", function () {
      if (!window.confirm("Supprimer définitivement " + g.full_name + " de la liste ?")) return;
      btApi.delete("/admin/invitations/guests/" + g.id).then(function () { toast("Invité supprimé.", "success"); reloadEvent().then(loadGuests); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    }));
    return el("div", { class: "py-3 flex flex-wrap items-center gap-3" }, [
      el("div", { class: "flex-1 min-w-[200px]" }, [
        el("div", { class: "flex flex-wrap items-center gap-2" }, [
          el("span", { class: "font-bold text-sm text-gray-900", text: g.full_name }),
          el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + st[1], text: st[0] }),
        ]),
        el("p", { class: "text-[11px] text-gray-500", text: [g.paper ? "pass papier" : null, g.phone || "sans numéro", g.seats + " place(s)" + (g.seats_used ? " · " + g.seats_used + " entrée(s)" : ""),
          g.table_label ? "table " + g.table_label : null, g.category].filter(Boolean).join(" · ") }),
      ]),
      actions,
    ]);
  }

  // Ouvre WhatsApp (clic utilisateur → pas de blocage de fenêtre) puis marque « envoyé »
  function sendGuest(g) {
    if (!S.ev.quota) { toast("Activez un pack avant d'envoyer les pass.", "error"); return; }
    var w = window.open(g.wa_url, "_blank");
    if (w) w.opener = null;
    else { toast("Le navigateur a bloqué l'ouverture de WhatsApp : autorisez les fenêtres pour ce site.", "error"); return; }
    btApi.post("/admin/invitations/guests/" + g.id + "/sent").then(function () {
      S.queue = S.queue.filter(function (id) { return id !== g.id; });
      loadGuests();
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function sendNext() {
    if (!S.queue.length) return;
    btApi.get("/admin/invitations/guests/" + S.queue[0]).then(sendGuest).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function guestModal(g) {
    var f = { name: el("input", { class: "input-field", maxlength: "120" }), phone: el("input", { class: "input-field", maxlength: "40" }),
              seats: el("input", { class: "input-field", type: "number", min: String(Math.max(1, g.seats_used)), max: "20" }),
              table: el("input", { class: "input-field", maxlength: "40" }), cat: el("input", { class: "input-field", maxlength: "40" }),
              paper: el("input", { type: "checkbox" }) };
    f.paper.checked = !!g.paper;
    f.name.value = g.full_name; f.phone.value = g.phone || ""; f.seats.value = g.seats; f.table.value = g.table_label || ""; f.cat.value = g.category || "";
    var body = el("div", { class: "space-y-3" }, [field("Nom", f.name), field("Téléphone", f.phone, "Le changer ne renvoie pas le pass : utilisez WhatsApp après."),
      el("div", { class: "grid grid-cols-3 gap-3" }, [field("Places", f.seats), field("Table", f.table), field("Catégorie", f.cat)]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [f.paper, "Pass papier (imprimé et remis en main propre, pas d'envoi WhatsApp)"])]);
    modal("Modifier l'invité", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        btApi.patch("/admin/invitations/guests/" + g.id, { full_name: f.name.value.trim(), phone: f.phone.value.trim() || null,
          seats: parseInt(f.seats.value, 10) || 1, table_label: f.table.value.trim() || null, category: f.cat.value.trim() || null,
          paper: f.paper.checked })
          .then(function () { close(); toast("Invité enregistré.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  function manualModal(g) {
    var remaining = g.seats - g.seats_used;
    var count = el("input", { class: "input-field", type: "number", min: "1", max: String(remaining), value: String(remaining) });
    var reason = el("input", { class: "input-field", maxlength: "200", placeholder: "Ex. téléphone déchargé, identité vérifiée" });
    modal("Entrée manuelle : " + g.full_name, el("div", { class: "space-y-3" }, [field("Personnes qui entrent (" + remaining + " restante(s))", count), field("Motif (obligatoire)", reason)]), function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Valider l'entrée", onclick: function () {
        var n = parseInt(count.value, 10);
        if (!(n >= 1 && n <= remaining)) { toast("Nombre de personnes : entre 1 et " + remaining + ".", "error"); return; }
        if (reason.value.trim().length < 3) { toast("Indiquez le motif de l'entrée manuelle (3 caractères minimum).", "error"); reason.focus(); return; }
        b.disabled = true;
        btApi.post("/admin/invitations/guests/" + g.id + "/checkin", { count: n, reason: reason.value.trim() })
          .then(function () { close(); toast("Entrée enregistrée.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ── Suivi en direct + lien de suivi client ────────────────────────
  function liveBlock() {
    var ev = S.ev, items = [];
    var track = el("div", { class: "p-3 rounded-xl bg-gray-50 mb-3" });
    if (ev.quota && ev.features && ev.features.track_link) {
      track.appendChild(el("p", { class: "text-sm font-bold text-gray-900", text: "Lien de suivi pour le client" }));
      track.appendChild(el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Page publique : titre, date et chiffres (attendus, entrés, restants, taux) actualisés toutes les 30 s. Aucun nom, aucun numéro, pas le lieu." }));
      if (ev.track_url) {
        track.appendChild(el("div", { class: "flex flex-wrap gap-2 items-center" }, [
          el("code", { class: "text-xs bg-white px-2 py-1 rounded border border-gray-200 break-all", text: ev.track_url }),
          act("Copier", "background:#f3f4f6;color:#374151", function () { copy(ev.track_url, "Lien de suivi"); }),
          act("Régénérer", "background:#ede9fe;color:#5b21b6", function () {
            if (!window.confirm("Régénérer le lien ? L'ancien ne fonctionnera plus.")) return;
            btApi.post("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Nouveau lien de suivi.", "success"); reloadEvent(); });
          }),
          act("Révoquer", "background:#fee2e2;color:#991b1b", function () {
            btApi.delete("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Lien de suivi révoqué.", "success"); reloadEvent(); });
          }),
        ]));
      } else {
        track.appendChild(act("Créer le lien de suivi", "background:#dcfce7;color:#166534", function () {
          btApi.post("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Lien de suivi créé.", "success"); reloadEvent(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        }));
      }
    } else {
      track.appendChild(el("p", { class: "text-[11px] text-gray-500", text: "Lien de suivi client : inclus dans les formules qui le prévoient (aucun pack actif ne l'inclut)." }));
    }
    items.push(track);
    items.push(el("div", { id: "inv-arrivals" }, [el("p", { class: "text-xs text-gray-400", text: "Chargement des arrivées…" })]));
    setTimeout(refreshLive, 0);
    return block("Suivi en direct", items);
  }
  // Bilan : téléchargé avec le jeton (pas de lien direct : la route exige l'authentification)
  function downloadReport(ev) {
    fetch(BT_API + "/admin/invitations/events/" + ev.id + "/report.csv", { headers: { Authorization: "Bearer " + btAuth.getToken() } })
      .then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.detail || "Bilan indisponible"); });
        // Nom construit ici : Content-Disposition n'est pas lisible en cross-origin (API sur un autre domaine)
        var name = "bilan-" + (String(ev.title || "evenement").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
          .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "evenement") + ".csv";
        return r.blob().then(function (b) {
          var a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name;
          document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        });
      }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function renderArrivals(list, stats, bd) {
    var box = document.getElementById("inv-arrivals");
    if (!box) return;
    box.innerHTML = "";
    if (bd && bd.categories && bd.categories.length > 1) {
      var curCol = (bd.columns || []).filter(function (c) { return c.key === bd.current; })[0];
      box.appendChild(el("p", { class: "text-xs text-gray-700 mb-1", text: "Par catégorie" + (curCol && bd.columns.length > 1 ? " (" + curCol.label + ")" : "") + " : " +
        bd.categories.map(function (c) { return c.category + " " + c.by[bd.current].entered + "/" + c.expected; }).join(" · ") }));
    }
    if (stats && stats.sessions && stats.sessions.length) {
      box.appendChild(el("p", { class: "text-xs font-bold text-gray-700 mb-2", text: stats.sessions.map(function (x) {
        return x.label + " : " + x.entered + " entrée(s)" + (x.reentries ? " + " + x.reentries + " retour(s)" : "");
      }).join(" · ") }));
    }
    if (!list.length) { box.appendChild(el("p", { class: "text-xs text-gray-400", text: "Aucune arrivée pour l'instant." })); return; }
    box.appendChild(el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400 mb-1", text: "Dernières arrivées" }));
    list.forEach(function (a) {
      box.appendChild(el("p", { class: "text-xs " + (a.conflict ? "text-red-700 font-bold" : a.reentry ? "text-gray-400" : "text-gray-700"), text: dt(a.at) + " · " + a.name +
        (a.table ? " (table " + a.table + ")" : "") + " · " + (a.reentry ? "retour" : a.count + " pers.") + " · " + ({ online: "scan", offline: "scan hors ligne", manual: "manuel" }[a.source] || a.source) +
        (a.session ? " · " + a.session : "") + (a.conflict ? " · CONFLIT" : "") }));
    });
  }

  // ── Séances (événement sur plusieurs jours) ───────────────────────
  function bgParts(iso) {   // ISO UTC → { date: "2026-11-20", time: "07:00" } à l'heure de Bangui
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z"), g = {};
    new Intl.DateTimeFormat("fr-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit",
      minute: "2-digit", hour12: false }).formatToParts(d).forEach(function (p) { g[p.type] = p.value; });
    return { date: g.year + "-" + g.month + "-" + g.day, time: (g.hour === "24" ? "00" : g.hour) + ":" + g.minute };
  }
  function addDays(ymd, n) {
    var d = new Date(ymd + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function sessionsBlock() {
    var ev = S.ev, rows = el("div", { class: "space-y-2 mb-3" });
    var list = (ev.sessions || []).map(function (x) {
      var a = bgParts(x.starts_at), b = bgParts(x.ends_at);
      return { id: x.id, label: x.label, date: a.date, start: a.time, end: b.time, locked: x.locked };
    });
    var doors = el("input", { class: "input-field", type: "number", min: "0", max: "240" });
    doors.value = String(ev.sessions && ev.sessions.length ? ev.sessions[0].doors_before_min : 60);
    function draw() {
      rows.innerHTML = "";
      if (!list.length) rows.appendChild(el("p", { class: "text-xs text-gray-400", text: "Aucune séance : chaque pass donne une seule entrée pour tout l'événement." }));
      list.forEach(function (r, i) {
        var lab = el("input", { class: "input-field", maxlength: "60", value: r.label });
        var day = el("input", { class: "input-field", type: "date", value: r.date });
        var a = el("input", { class: "input-field", type: "time", value: r.start });
        var b = el("input", { class: "input-field", type: "time", value: r.end });
        lab.addEventListener("input", function () { r.label = lab.value; });
        day.addEventListener("input", function () { r.date = day.value; });
        a.addEventListener("input", function () { r.start = a.value; });
        b.addEventListener("input", function () { r.end = b.value; });
        if (r.locked) { day.disabled = a.disabled = b.disabled = true; }
        rows.appendChild(el("div", { class: "grid grid-cols-2 sm:grid-cols-5 gap-2 items-center" }, [lab, day, a, b,
          r.locked ? el("span", { class: "text-[11px] text-gray-500", text: "Entrées enregistrées : horaires figés" })
            : act("Retirer", "background:#fee2e2;color:#991b1b", function () { list.splice(i, 1); draw(); })]));
      });
    }
    function add() {
      var last = list[list.length - 1], base = last ? addDays(last.date, 1) : bgParts(ev.event_date || new Date().toISOString()).date;
      list.push({ id: null, label: "Jour " + (list.length + 1), date: base, start: last ? last.start : "07:00", end: last ? last.end : "16:00" });
      draw();
    }
    var bSave = el("button", { class: "action-btn btn-primary text-xs", text: "Enregistrer les séances", onclick: function () {
      var body = { sessions: list.map(function (r) {
        // Fin avant (ou égale à) l'heure de début = séance qui passe minuit (ex. 20h → 2h)
        var endDate = r.end <= r.start ? addDays(r.date, 1) : r.date;
        return { id: r.id, label: (r.label || "").trim(), starts_at: r.date + "T" + r.start + ":00+01:00",
                 ends_at: endDate + "T" + r.end + ":00+01:00", doors_before_min: doors.value === "" ? 60 : parseInt(doors.value, 10) };
      }) };
      if (list.some(function (r) { return !r.date || !r.start || !r.end; })) return toast("Indiquez la date et les heures de chaque séance.", "error");
      bSave.disabled = true;
      btApi.put("/admin/invitations/events/" + ev.id + "/sessions", body).then(function () { toast("Séances enregistrées.", "success"); reloadEvent(); })
        .catch(function (e) { bSave.disabled = false; toast(e.message || "Erreur", "error"); });
    } });
    draw();
    return block("Séances (événement sur plusieurs jours)", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Avec des séances, chaque pass donne ses places à CHAQUE séance (ex. forum les 20 et 21). " +
        "Un invité déjà entré pendant la séance : l'agent décide s'il s'agit d'un retour (journalisé, jamais compté). " +
        "Sans séance : une seule entrée pour tout l'événement. Heures de Bangui ; fin avant le début = séance qui passe minuit." }),
      ev.dates_label ? el("p", { class: "text-sm font-bold text-gray-800 mb-2", text: ev.dates_label }) : null,
      rows,
      el("div", { class: "grid sm:grid-cols-3 gap-3 items-end" }, [
        field("Scan accepté … minutes avant le début", doors, "Ouverture des portes (toutes les séances)."),
        el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", text: "+ Ajouter une séance", onclick: add }),
        bSave,
      ]),
    ]);
  }

  // ── Affiche (page d'inscription + aperçu de partage WhatsApp / Facebook) ─
  // Compressée dans le navigateur (btCompressImage), puis contrôlée et ré-encodée par le serveur.
  function posterBlock() {
    var ev = S.ev, cv = ev.cover;
    var img = el("img", { alt: "Affiche", style: "width:120px;height:150px;object-fit:cover;object-position:top;border-radius:12px;background:#f3f4f6" });
    var input = el("input", { type: "file", accept: "image/jpeg,image/png,image/webp", class: "hidden" });
    if (cv) {
      fetch(BT_API + "/admin/invitations/events/" + ev.id + "/cover.jpg", { headers: { Authorization: "Bearer " + btAuth.getToken() } })
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { if (b) img.src = URL.createObjectURL(b); }).catch(function () {});
    }
    input.addEventListener("change", function () {
      var f = input.files && input.files[0];
      if (!f) return;
      toast("Préparation de l'affiche…", "info");
      btCompressImage(f, { maxWidth: 1600, maxHeight: 1600, targetBytes: 1200 * 1024, skipBelow: 900 * 1024 })
        .then(function (dataUrl) { return btApi.put("/admin/invitations/events/" + ev.id + "/cover", { image: dataUrl }); })
        .then(function () { toast("Affiche enregistrée.", "success"); reloadEvent(); })
        .catch(function (e) { toast(e.message || "Affiche refusée", "error"); })
        .then(function () { input.value = ""; });
    });
    var desc = el("textarea", { class: "input-field text-sm", rows: "6", maxlength: "2000", placeholder: "Présentation de l'événement pour le public (programme, à qui il s'adresse…)" });
    desc.value = ev.public_description || "";
    var bDesc = el("button", { class: "action-btn btn-primary text-xs", text: "Enregistrer la description", onclick: function () {
      bDesc.disabled = true;
      btApi.put("/admin/invitations/events/" + ev.id + "/page", { description: desc.value })
        .then(function () { toast("Description enregistrée.", "success"); reloadEvent(); })
        .catch(function (e) { bDesc.disabled = false; toast(e.message || "Erreur", "error"); });
    } });
    return block("Page d'inscription : affiche et description", [
      el("p", { class: "text-[11px] text-gray-500 mb-3", text: "Visibles par le public : page d'inscription (affiche en grand sur ordinateur, en vignette sur téléphone) et aperçu du lien partagé sur WhatsApp ou Facebook. " +
        "Affiche : JPEG, PNG ou WebP, portrait ou carré conseillé. La note interne de l'événement (« Modifier ») n'est jamais publiée." }),
      el("div", { class: "flex flex-wrap items-end gap-4" }, [
        cv ? img : el("div", { class: "w-[120px] h-[150px] rounded-xl bg-gray-100 flex items-center justify-center text-[11px] text-gray-400 text-center p-2", text: "Aucune affiche" }),
        el("div", { class: "flex flex-col gap-2" }, [
          cv ? el("p", { class: "text-[11px] text-gray-500", text: cv.width + " × " + cv.height + " px · " + dt(cv.updated_at) }) : null,
          act(cv ? "Remplacer l'affiche" : "Choisir une affiche", "background:#dcfce7;color:#166534", function () { input.click(); }),
          cv ? act("Retirer", "background:#fee2e2;color:#991b1b", function () {
            if (!window.confirm("Retirer l'affiche ? La page d'inscription et l'aperçu WhatsApp n'auront plus d'image.")) return;
            btApi.delete("/admin/invitations/events/" + ev.id + "/cover").then(function () { toast("Affiche retirée.", "success"); reloadEvent(); });
          }) : null,
          input,
        ]),
      ]),
      el("div", { class: "mt-4" }, [field("Description publique", desc, "Un paragraphe par ligne. 2 000 caractères maximum."), el("div", { class: "mt-2" }, [bDesc])]),
    ]);
  }

  // ── Inscription libre (page publique /r/<lien>, sans compte) ──────
  var SIGNUP_STATE = { open: ["Ouvertes", "#dcfce7", "#166534"], closed: ["Fermées", "#f3f4f6", "#374151"],
                       ended: ["Terminées (date limite)", "#f3f4f6", "#374151"], inactive: ["Aucun pack actif", "#fef3c7", "#92400e"],
                       full: ["Complet", "#fee2e2", "#991b1b"] };
  function signupBlock() {
    var ev = S.ev, su = ev.signup || {}, stt = SIGNUP_STATE[su.state] || SIGNUP_STATE.closed;
    var slug = el("input", { class: "input-field", placeholder: "ex. fonej-2026", maxlength: "40" });
    var cat = el("input", { class: "input-field", placeholder: su.default_category || "Participant", maxlength: "40" });
    var max = el("input", { class: "input-field", type: "number", min: "1", max: "5000", placeholder: "Limite du pack" });
    var until = el("input", { class: "input-field", type: "datetime-local" });
    slug.value = su.slug || ""; cat.value = su.category || ""; max.value = su.max ? String(su.max) : ""; until.value = toLocalInput(su.until);
    slug.addEventListener("input", function () { slug.value = slug.value.toLowerCase().replace(/[^a-z0-9-]/g, ""); });
    function save(open, btn) {
      var body = { open: open, slug: slug.value.trim() || null, category: cat.value.trim() || null,
                   max: max.value ? parseInt(max.value, 10) : null, until: until.value ? new Date(until.value).toISOString() : null };
      if (su.open && su.slug && body.slug !== su.slug && !window.confirm("Changer le lien ? L'ancien lien (" + su.url + ") ne fonctionnera plus.")) return;
      btn.disabled = true;
      btApi.put("/admin/invitations/events/" + ev.id + "/signup", body)
        .then(function () { toast(open ? "Inscriptions ouvertes." : (su.open ? "Inscriptions fermées." : "Réglages enregistrés."), "success"); reloadEvent(); })
        .catch(function (e) { btn.disabled = false; toast(e.message || "Erreur", "error"); });
    }
    var items = [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Page publique sans compte : chaque inscrit reçoit aussitôt un pass d'1 place à son nom (catégorie ci-dessous), " +
        "sans passer par l'envoi WhatsApp. Plafond : le pack actif (toujours) et la limite facultative. Inscription possible sur place pendant l'événement si la page reste ouverte." }),
      el("div", { class: "flex flex-wrap items-center gap-2 mb-3" }, [
        el("span", { class: "text-xs font-bold px-2 py-1 rounded-full", style: "background:" + stt[1] + ";color:" + stt[2], text: stt[0] }),
        el("span", { class: "text-sm text-gray-700", text: su.count + " inscrit(s) en ligne" + (su.max ? " / " + su.max : "") }),
      ]),
    ];
    if (su.url) items.push(el("div", { class: "flex flex-wrap gap-2 items-center mb-3" }, [
      el("code", { class: "text-xs bg-white px-2 py-1 rounded border border-gray-200 break-all", text: su.url }),
      act("Copier", "background:#f3f4f6;color:#374151", function () { copy(su.url, "Lien d'inscription"); }),
      el("a", { class: "action-btn text-[11px]", style: "background:#f3f4f6;color:#374151", href: su.url, target: "_blank", rel: "noopener", text: "Ouvrir" }),
    ]));
    items.push(el("div", { class: "grid sm:grid-cols-2 gap-3" }, [
      field("Lien : goevent.africa/r/…", slug, "3 à 40 caractères : minuscules, chiffres, tirets."),
      field("Catégorie des inscrits", cat, "Affichée en tête du pass (vide = " + (su.default_category || "Participant") + ")."),
      field("Limite d'inscriptions en ligne", max, "Vide = jusqu'à la limite du pack."),
      field("Fermeture automatique", until, "Vide = fermeture manuelle."),
    ]));
    var bOpen = el("button", { class: "action-btn btn-primary text-xs", text: su.open ? "Enregistrer" : "Enregistrer et ouvrir les inscriptions", onclick: function () { save(true, bOpen); } });
    var bClose = el("button", { class: su.open ? "action-btn btn-danger text-xs" : "action-btn text-xs", style: su.open ? null : "background:#f3f4f6;color:#374151",
      text: su.open ? "Fermer les inscriptions" : "Enregistrer sans ouvrir", onclick: function () { save(false, bClose); } });
    items.push(el("div", { class: "flex flex-wrap gap-2 mt-3" }, [bOpen, bClose]));
    return block("Inscription libre (lien public)", items);
  }

  // ── Message WhatsApp ──────────────────────────────────────────────
  function messageBlock() {
    var ev = S.ev, m = S.meta;
    var ta = el("textarea", { class: "input-field text-sm", rows: "6", maxlength: "1000" });
    ta.value = ev.message_template || m.default_message;
    var save = el("button", { class: "action-btn btn-primary text-xs", text: "Enregistrer le message", onclick: function () {
      btApi.patch("/admin/invitations/events/" + ev.id, { message_template: ta.value }).then(function () { toast("Message enregistré.", "success"); reloadEvent().then(loadGuests); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    } });
    var reset = el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", text: "Revenir au message par défaut", onclick: function () {
      btApi.patch("/admin/invitations/events/" + ev.id, { reset_message: true }).then(function () { toast("Message par défaut rétabli.", "success"); reloadEvent().then(loadGuests); });
    } });
    return block("Message WhatsApp", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Variables : " + m.message_vars.map(function (v) { return "{" + v + "}"; }).join(" ") + " — {lien} est obligatoire." + (ev.message_is_default ? " (message par défaut)" : "") }),
      ta, el("div", { class: "flex flex-wrap gap-2 mt-2" }, [save, ev.message_is_default ? null : reset]),
    ]);
  }

  // ── Agents de contrôle ────────────────────────────────────────────
  function agentsBlock() {
    var ev = S.ev, list = el("div", { class: "space-y-1 mb-3" });
    if (!ev.agents.length) list.appendChild(el("p", { class: "text-xs text-gray-400", text: "Aucun agent assigné." }));
    ev.agents.forEach(function (a) {
      list.appendChild(el("div", { class: "flex items-center gap-2 text-sm" }, [
        el("span", { class: "flex-1", text: a.full_name + " · " + a.phone_number }),
        act("Retirer", "background:#fee2e2;color:#991b1b", function () {
          btApi.delete("/admin/invitations/events/" + ev.id + "/agents/" + a.id).then(function () { toast("Agent retiré.", "success"); reloadEvent(); });
        }),
      ]));
    });
    var name = el("input", { class: "input-field", placeholder: "Nom", maxlength: "120" }), phone = el("input", { class: "input-field", placeholder: "Téléphone (+236…)", maxlength: "30" });
    var pin = el("input", { class: "input-field", placeholder: "PIN (6 chiffres)", maxlength: "6", inputmode: "numeric" });
    var onsite = ev.features && ev.features.onsite_agents;
    var over = onsite && ev.quota && ev.agents.length > (ev.agents_included || 0);
    return block("Agents de contrôle", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: onsite ? "Agents GoEvent sur place. Ils se connectent sur goevent.africa avec ce numéro et ce PIN, puis scannent avec « Scanner »." :
        "Formule sans agents GoEvent : créez les comptes des personnes du client qui scanneront à l'entrée (numéro + PIN à leur transmettre)." }),
      onsite && ev.quota ? el("p", { class: "text-xs mb-2 " + (over ? "font-bold text-amber-800 bg-amber-50 rounded-lg p-2" : "text-gray-600"),
        text: (ev.agents_included || 0) + " agent(s) inclus · " + ev.agents.length + " assigné(s)" + (over ? " : au-delà de l'inclus, proposez l'option « agent supplémentaire »." : "") }) : null,
      list,
      el("div", { class: "grid sm:grid-cols-4 gap-2" }, [name, phone, pin, el("button", { class: "action-btn btn-primary text-xs", text: "Ajouter l'agent", onclick: function () {
        btApi.post("/admin/invitations/events/" + ev.id + "/agents", { full_name: name.value.trim(), phone_number: phone.value.trim(), pin_code: pin.value.trim() })
          .then(function () { toast("Agent ajouté.", "success"); reloadEvent(); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      } })]),
    ]);
  }

  // ── Données personnelles ──────────────────────────────────────────
  function dataBlock() {
    var ev = S.ev, items = [el("p", { class: "text-xs text-gray-600", text: ev.purged ? "Liste anonymisée." :
      "Noms et numéros des invités supprimés automatiquement le " + dt(ev.purge_after) + " (" + S.meta.retention_days + " jours après l'événement). Ils ne servent qu'à cet événement : jamais d'export d'audience ni de publicité." })];
    if (canActivate()) items.push(el("div", { class: "flex flex-wrap items-center gap-2 my-3" }, [
      act("Télécharger le bilan (CSV)", "background:#dcfce7;color:#166534", function () { downloadReport(ev); }),
      el("span", { class: "text-[11px] text-gray-500", text: "Synthèse par catégorie et par séance + invités présents hors inscription en ligne." +
        (ev.purged ? "" : " À télécharger avant la suppression des données (" + dt(ev.purge_after) + ").") }),
    ]));
    if (!ev.purged && canActivate()) items.push(el("button", { class: "action-btn btn-danger text-xs mt-2", text: "Supprimer la liste maintenant", onclick: function () {
      if (!window.confirm("Anonymiser dès maintenant tous les invités de cet événement ? Les pass ne fonctionneront plus. Action irréversible.")) return;
      btApi.post("/admin/invitations/purge?event_id=" + ev.id).then(function (r) { toast(r.anonymized + " invité(s) anonymisé(s).", "success"); openEvent(ev.id); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    } }));
    return block("Données personnelles", items);
  }

  // ════════════════════════════════════════════════════════════════
  // NAVIGATION INTERNE (Événements · Devis · Ventes & commissions · Catalogue)
  // ════════════════════════════════════════════════════════════════
  function navTabs(active) {
    var tabs = [["events", "Événements", loadList], ["quotes", "Devis", openQuotes], ["sales", "Ventes & commissions", openSales],
                ["catalog", "Catalogue", function () { openCatalog("formulas"); }]];
    return el("div", { class: "flex flex-wrap gap-1 mb-5 p-1 bg-gray-100 rounded-2xl w-fit", role: "tablist" }, tabs.map(function (t) {
      var on = t[0] === active;
      return el("button", { role: "tab", "aria-selected": on ? "true" : "false",
        class: "px-4 py-2 rounded-xl text-sm font-bold transition-colors " + (on ? "bg-white text-primary shadow-sm" : "text-gray-600 hover:text-primary"),
        text: t[1], onclick: on ? null : t[2] });
    }));
  }
  function loadSellers() {
    if (!canActivate()) return Promise.resolve([]);
    if (S.sellers) return Promise.resolve(S.sellers);
    return btApi.get("/admin/sales/sellers").then(function (d) { S.sellers = d.sellers; return S.sellers; }).catch(function () { return []; });
  }
  function ownerSelect(current, allowHouse, houseLabel) {
    var sel = el("select", { class: "input-field" });
    sel.appendChild(el("option", { value: "", text: houseLabel || (allowHouse ? "Administration (maison)" : "Moi (administrateur)") }));
    (S.sellers || []).filter(function (x) { return x.is_active || x.id === current; }).forEach(function (x) {
      sel.appendChild(el("option", { value: String(x.id), text: x.full_name + (x.sales_code ? " · " + x.sales_code : "") + (x.is_active ? "" : " (désactivé)") }));
    });
    sel.value = current ? String(current) : "";
    return sel;
  }
  var QSTATUS = { brouillon: ["Brouillon", "bg-gray-100 text-gray-700"], envoye: ["Envoyé", "bg-blue-100 text-blue-800"],
                  accepte: ["Accepté", "bg-green-100 text-green-800"], refuse: ["Refusé", "bg-red-100 text-red-700"],
                  converti: ["Converti", "bg-purple-100 text-purple-800"], expire: ["Expiré", "bg-amber-100 text-amber-800"] };
  function qChip(st) { var x = QSTATUS[st] || [st, "bg-gray-100 text-gray-700"]; return el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full whitespace-nowrap " + x[1], text: x[0] }); }
  function isoDay(v) { return v ? new Date(v + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "—"; }

  // ════════════════════════════════════════════════════════════════
  // DEVIS NUMÉROTÉS
  // ════════════════════════════════════════════════════════════════
  var QS = { status: "", owner: "", flag: "", q: "", page: 1 };
  function openQuotes() {
    stopTimer();
    S.view = "quotes";
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadSellers().then(loadQuotes);
  }
  function loadQuotes() {
    var qs = "?page=" + QS.page + "&limit=50" + (QS.status ? "&status=" + QS.status : "") + (QS.owner ? "&owner_id=" + QS.owner : "") +
             (QS.flag ? "&flag=" + QS.flag : "") + (QS.q ? "&q=" + encodeURIComponent(QS.q) : "");
    btApi.get("/admin/invitations/quotes" + qs).then(renderQuotes)
      .catch(function (e) { sec().innerHTML = ""; sec().appendChild(el("div", { class: "card p-6 text-red-600 text-sm", text: e.message || "Erreur" })); });
  }
  function renderQuotes(d) {
    var root = sec(), admin = canActivate();
    root.innerHTML = "";
    root.appendChild(navTabs("quotes"));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-4" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: "Devis" }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: admin ? "Tous les devis de l'équipe. Numéro attribué automatiquement, prix figé, valable 15 jours."
          : "Vos devis. Numéro attribué automatiquement, prix figé, valable 15 jours." }),
      ]),
      el("button", { class: "action-btn btn-primary", text: "+ Nouveau devis", onclick: function () { quoteModal(null, {}); } }),
    ]));
    if (admin && d.counts) {
      var chips = [["", "Tous"], ["to_validate", "À valider (" + d.counts.to_validate + ")"], ["to_cash", "Acceptés — à encaisser (" + d.counts.to_cash + ")"],
                   ["duplicate", "Conflits / doublons (" + d.counts.duplicate + ")"]];
      root.appendChild(el("div", { class: "flex flex-wrap gap-2 mb-3" }, chips.map(function (c) {
        var on = QS.flag === c[0];
        return el("button", { class: "px-3 py-1.5 rounded-full text-xs font-bold border " + (on ? "bg-primary text-white border-primary" : "bg-white text-gray-700 border-gray-200 hover:border-primary"),
          text: c[1], onclick: function () { QS.flag = c[0]; QS.page = 1; loadQuotes(); } });
      })));
    }
    var search = el("input", { class: "input-field", placeholder: "N° de devis, nom, entreprise ou téléphone", value: QS.q });
    var stSel = el("select", { class: "input-field" }, [["", "Tous les statuts"], ["brouillon", "Brouillon"], ["envoye", "Envoyé"], ["accepte", "Accepté"],
      ["refuse", "Refusé"], ["converti", "Converti"], ["expire", "Expiré"]].map(function (x) { return el("option", { value: x[0], text: x[1] }); }));
    stSel.value = QS.status;
    var filters = [el("div", { class: "flex-1 min-w-[220px]" }, [search]), el("div", { class: "min-w-[160px]" }, [stSel])];
    var owSel = null;
    if (admin) {
      owSel = el("select", { class: "input-field" }, [el("option", { value: "", text: "Tous les commerciaux" })].concat((S.sellers || []).map(function (x) {
        return el("option", { value: String(x.id), text: x.full_name + (x.sales_code ? " · " + x.sales_code : "") });
      })));
      owSel.value = QS.owner;
      filters.push(el("div", { class: "min-w-[180px]" }, [owSel]));
    }
    var t = null;
    search.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () { QS.q = search.value.trim(); QS.page = 1; loadQuotes(); }, 350); });
    stSel.addEventListener("change", function () { QS.status = stSel.value; QS.page = 1; loadQuotes(); });
    if (owSel) owSel.addEventListener("change", function () { QS.owner = owSel.value; QS.page = 1; loadQuotes(); });
    root.appendChild(el("div", { class: "flex flex-wrap gap-2 mb-4" }, filters));
    if (!d.quotes.length) {
      root.appendChild(el("div", { class: "card p-6 empty-state", text: QS.q || QS.status || QS.flag ? "Aucun devis ne correspond." : "Aucun devis pour l'instant : « + Nouveau devis » pour chiffrer un prospect." }));
      return;
    }
    var list = el("div", { class: "card divide-y divide-gray-100" });
    d.quotes.forEach(function (q) {
      var flags = [];
      if (q.needs_admin) flags.push(el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-purple-100 text-purple-800", text: "À valider" }));
      if (admin && (q.possible_duplicate_of || q.conflict_with_quote_id)) flags.push(el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-orange-100 text-orange-800", text: q.conflict_with_quote_id ? "Conflit forcé" : "Doublon possible" }));
      list.appendChild(el("button", { class: "w-full text-left p-4 hover:bg-gray-50 flex flex-wrap items-center gap-x-4 gap-y-1", onclick: function () { openQuote(q.id); } }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [el("span", { class: "font-mono font-black text-sm text-gray-900", text: q.number }), qChip(q.status)].concat(flags)),
          el("p", { class: "text-sm font-bold text-gray-800 mt-1", text: q.client_name + (q.client_company ? " · " + q.client_company : "") }),
          el("p", { class: "text-xs text-gray-500", text: [q.event_type, q.event_date ? isoDay(q.event_date) : null, q.formula_name ? q.formula_name + " " + (q.pack_size || "") + " pers." : null].filter(Boolean).join(" · ") }),
        ]),
        el("div", { class: "text-right" }, [
          el("p", { class: "font-black text-gray-900", text: fcfa(q.total_fcfa) }),
          el("p", { class: "text-[11px] text-gray-500", text: (admin ? (q.owner_name || "—") + " · " : "") + "valable jusqu'au " + isoDay(q.valid_until) }),
        ]),
      ]));
    });
    root.appendChild(list);
    var pages = Math.ceil(d.total / d.limit);
    if (pages > 1) root.appendChild(el("div", { class: "flex justify-center items-center gap-3 mt-4 text-sm" }, [
      el("button", { class: "action-btn text-xs", text: "← Précédents", disabled: QS.page <= 1 ? "disabled" : null, onclick: function () { QS.page--; loadQuotes(); } }),
      el("span", { class: "text-gray-500", text: "Page " + QS.page + " / " + pages }),
      el("button", { class: "action-btn text-xs", text: "Suivants →", disabled: QS.page >= pages ? "disabled" : null, onclick: function () { QS.page++; loadQuotes(); } }),
    ]));
  }

  // Création / modification d'un devis (brouillon). o.event : devis rattaché à un événement.
  function quoteModal(q, o) {
    o = o || {};
    var admin = canActivate(), ev = o.event || null;
    var input = q ? ((q.snapshot || {}).input || null) : null;
    var f = {
      name: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Famille Ngaba, Mme Kossi" }),
      company: el("input", { class: "input-field", maxlength: "120", placeholder: "Facultatif (gala, conférence…)" }),
      phone: el("input", { class: "input-field", maxlength: "30", placeholder: "+236 …", type: "tel" }),
      type: el("input", { class: "input-field", maxlength: "60", placeholder: "Mariage, dot, gala…", list: "inv-event-types" }),
      date: el("input", { class: "input-field", type: "date" }),
      location: el("input", { class: "input-field", maxlength: "200", placeholder: "Salle, quartier (facultatif)" }),
      guests: el("input", { class: "input-field", type: "number", min: "1", placeholder: "Ex. 280" }),
    };
    var types = el("datalist", { id: "inv-event-types" }, ["Mariage", "Dot", "Anniversaire", "Baptême", "Gala", "Conférence", "Soirée d'entreprise"].map(function (x) { return el("option", { value: x }); }));
    if (q) {
      f.name.value = q.client_name; f.company.value = q.client_company || ""; f.phone.value = q.client_phone || "";
      f.type.value = q.event_type || ""; f.date.value = q.event_date || ""; f.location.value = q.location || ""; f.guests.value = q.guests_count || "";
    } else if (ev) {
      f.name.value = ev.client_name || ""; f.phone.value = ev.client_phone || ""; f.location.value = ev.location || "";
      f.date.value = ev.event_date ? dateInput(ev.event_date) : ""; f.type.value = "";
    }
    var owner = null;
    if (admin && !q && !ev) owner = ownerSelect(null, false);
    var form = orderForm({ priceUrl: "/admin/invitations/quotes/price", draft: !admin, initial: input, serviceCode: ev ? ev.service_level : null });
    if (!input) f.guests.addEventListener("input", function () { form.suggest(parseInt(f.guests.value, 10) || 0); });
    var body = el("div", { class: "space-y-3" }, [
      types,
      ev ? el("p", { class: "text-xs font-bold text-primary", text: "Devis rattaché à l'événement « " + ev.title + " »" }) : null,
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Client (obligatoire)", f.name), field("Entreprise / organisation", f.company)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Téléphone du client", f.phone, "Sert au contrôle des doublons entre commerciaux."), field("Type d'événement", f.type)]),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Date prévue", f.date), field("Lieu", f.location), field("Nombre d'invités", f.guests)]),
      owner ? field("Commercial propriétaire", owner, "Le devis et la vente seront attribués à ce commercial.") : null,
      el("hr", { class: "border-gray-100" }),
      form.node,
    ]);
    modal(q ? "Modifier le devis " + q.number : "Nouveau devis", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: q ? "Enregistrer" : "Créer le devis", onclick: function () { submit(null); } });
      function submit(force) {
        if (f.name.value.trim().length < 2) { toast("Nom du client obligatoire.", "error"); return; }
        if (!form.last()) { toast("Le récapitulatif doit être valide.", "error"); return; }
        var payload = { client_name: f.name.value.trim(), client_company: f.company.value.trim() || null, client_phone: f.phone.value.trim() || null,
          event_type: f.type.value.trim() || null, event_date: f.date.value || null, location: f.location.value.trim() || null,
          guests_count: parseInt(f.guests.value, 10) || null, order: form.payload() };
        if (force) payload.force_reason = force;
        if (!q) { if (ev) payload.event_id = ev.id; if (owner && owner.value) payload.owner_id = parseInt(owner.value, 10); }
        b.disabled = true;
        (q ? btApi.patch("/admin/invitations/quotes/" + q.id, payload) : btApi.post("/admin/invitations/quotes", payload))
          .then(function (r) {
            close();
            toast(q ? "Devis enregistré." : "Devis " + r.number + " créé.", "success");
            if (S.view === "quotes") loadQuotes();
            openQuote(r.id);
          })
          .catch(function (e) {
            b.disabled = false;
            var msg = e.message || "Erreur";
            if (admin && /motif pour passer outre/.test(msg)) {
              var reason = window.prompt(msg + "\n\nMotif (obligatoire, journalisé) :");
              if (reason && reason.trim().length >= 3) submit(reason.trim());
              return;
            }
            toast(msg, "error");
          });
      }
      return [b];
    });
  }

  function openQuote(id) {
    btApi.get("/admin/invitations/quotes/" + id).then(quoteDetail).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function quoteDetail(q) {
    var admin = canActivate(), snap = q.snapshot || {}, closeFn = null;
    function act(label, style, fn) { return el("button", { class: "action-btn text-xs", style: style, text: label, onclick: fn }); }
    function after(r, msg) { if (msg) toast(msg, "success"); if (closeFn) closeFn(); if (S.view === "quotes") loadQuotes(); if (S.view === "event") reloadEvent(); if (r) quoteDetail(r); }
    function call(promise, msg) { return promise.then(function (r) { after(r, msg); }).catch(function (e) { toast(e.message || "Erreur", "error"); }); }
    var st = q.status, open = st === "brouillon" || st === "envoye" || st === "accepte", sendable = !q.needs_admin && !q.purged;
    var info = el("div", { class: "space-y-1 text-sm" }, [
      el("div", { class: "flex flex-wrap items-center gap-2" }, [qChip(st), q.needs_admin ? el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-purple-100 text-purple-800", text: "À valider par l'admin" }) : null]),
      el("p", { class: "font-bold text-gray-900 mt-2", text: q.client_name + (q.client_company ? " · " + q.client_company : "") }),
      el("p", { class: "text-xs text-gray-600", text: [q.client_phone, q.event_type, q.event_date ? isoDay(q.event_date) : null, q.location, q.guests_count ? q.guests_count + " invités" : null].filter(Boolean).join(" · ") || "—" }),
      el("p", { class: "text-xs text-gray-500", text: "Créé le " + dt(q.created_at) + " · valable jusqu'au " + isoDay(q.valid_until) + (admin ? " · " + (q.owner_name || "—") + (q.sales_code ? " (" + q.sales_code + ")" : "") : "") }),
      q.refused_reason ? el("p", { class: "text-xs text-red-600", text: "Refusé : " + q.refused_reason }) : null,
      admin && q.possible_duplicate_of ? el("p", { class: "text-xs font-bold text-orange-700 bg-orange-50 rounded-lg p-2", text: "Doublon possible avec un devis d'un autre commercial (nom proche, date à ± 30 jours) : vérifiez avant de convertir." }) : null,
      admin && q.conflict_with_quote_id ? el("p", { class: "text-xs font-bold text-orange-700 bg-orange-50 rounded-lg p-2", text: "Créé malgré un conflit de téléphone (motif journalisé)." }) : null,
    ]);
    var recap = snap.formula ? el("div", { class: "p-3 rounded-xl bg-gray-50 text-sm mt-3" }, quoteLines(snap)) : null;
    var acts = el("div", { class: "flex flex-wrap gap-2 mt-4" });
    if (sendable) {
      acts.appendChild(act("Télécharger le PDF", "background:#ede9fe;color:#5b21b6", function () { downloadQuotePdf(q); }));
      acts.appendChild(act("Copier le texte WhatsApp", "background:#dcfce7;color:#166534", function () {
        btApi.get("/admin/invitations/quotes/" + q.id + "/whatsapp").then(function (r) {
          copy(r.text, "Devis");
          if (r.wa_url && window.confirm("Texte copié. Ouvrir WhatsApp vers " + q.client_phone + " ?")) window.open(r.wa_url, "_blank", "noopener");
        }).catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
    }
    if (st === "brouillon") acts.appendChild(act("Modifier", "background:#f3f4f6;color:#374151", function () { closeFn(); quoteModal(q, {}); }));
    if (admin && q.needs_admin && st === "brouillon") acts.appendChild(act("Valider le devis", "background:#7c3aed;color:#fff", function () { call(btApi.post("/admin/invitations/quotes/" + q.id + "/validate"), "Devis validé."); }));
    if (st === "brouillon" && sendable) acts.appendChild(act("Marquer envoyé", "background:#dbeafe;color:#1e40af", function () { call(btApi.post("/admin/invitations/quotes/" + q.id + "/sent"), "Devis marqué envoyé."); }));
    if ((st === "envoye" || st === "brouillon") && sendable) acts.appendChild(act("Marquer accepté", "background:#dcfce7;color:#166534", function () { call(btApi.post("/admin/invitations/quotes/" + q.id + "/accepted"), "Devis accepté."); }));
    if (open) acts.appendChild(act("Marquer refusé", "background:#fee2e2;color:#991b1b", function () {
      var reason = window.prompt("Motif du refus (obligatoire) :");
      if (!reason || reason.trim().length < 3) return;
      call(btApi.post("/admin/invitations/quotes/" + q.id + "/refused", { reason: reason.trim() }), "Devis marqué refusé.");
    }));
    if (!q.purged) acts.appendChild(act("Dupliquer (prix du jour)", "background:#f3f4f6;color:#374151", function () {
      btApi.post("/admin/invitations/quotes/" + q.id + "/duplicate").then(function (r) { toast("Nouveau devis " + r.number + ".", "success"); after(r); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    }));
    if (admin && st !== "converti") acts.appendChild(act("Réaffecter", "background:#e0f2fe;color:#075985", function () {
      var choices = (S.sellers || []).filter(function (x) { return x.is_active; });
      var pick = window.prompt("Réaffecter le devis " + q.number + " à :\n0 — Moi (administrateur)\n" + choices.map(function (x) { return x.id + " — " + x.full_name; }).join("\n") + "\n\nTapez le numéro :", "0");
      if (pick === null) return;
      var id = parseInt(pick, 10);
      if (isNaN(id) || (id && !choices.some(function (x) { return x.id === id; }))) { toast("Choix invalide.", "error"); return; }
      call(btApi.post("/admin/invitations/quotes/" + q.id + "/owner", { owner_id: id || btAuth.getUser().id }), "Devis réaffecté.");
    }));
    if (q.event_id) acts.appendChild(act("Ouvrir l'événement", "background:#00502c;color:#fff", function () { closeFn(); openEvent(q.event_id); }));
    else if (admin && (st === "envoye" || st === "accepte") && sendable) acts.appendChild(act("Créer l'événement depuis ce devis", "background:#00502c;color:#fff", function () { closeFn(); convertModal(q); }));
    if (admin && q.event_id && !q.pack_id && (st === "envoye" || st === "accepte") && sendable) acts.appendChild(act("Activer le pack (paiement reçu)", "background:#047857;color:#fff", function () {
      closeFn(); openEventAndActivate(q.event_id, q);
    }));
    var body = el("div", {}, [info, recap, acts,
      !admin ? el("p", { class: "text-[11px] text-gray-500 mt-3", text: "Le client paie GoEvent SARLU (espèces contre reçu ou Orange Money GoEvent). L'activation est faite par l'administrateur à réception du paiement." }) : null]);
    closeFn = modal("Devis " + q.number, body, function () { return []; });
  }
  function downloadQuotePdf(q) {
    fetch(BT_API + "/admin/invitations/quotes/" + q.id + "/pdf", { headers: { Authorization: "Bearer " + btAuth.getToken() } })
      .then(function (res) { if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "Erreur " + res.status); }); return res.blob(); })
      .then(function (b) {
        var url = URL.createObjectURL(b), a = el("a", { href: url, download: q.number + ".pdf" });
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      }).catch(function (e) { toast(e.message || "PDF indisponible", "error"); });
  }
  function convertModal(q) {
    var f = {
      title: el("input", { class: "input-field", maxlength: "150", value: (q.event_type ? q.event_type + " — " : "") + q.client_name }),
      date: el("input", { class: "input-field", type: "datetime-local", value: q.event_date ? q.event_date + "T18:00" : "" }),
      location: el("input", { class: "input-field", maxlength: "200", value: q.location || "" }),
      host: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Paul & Marie (affiché « … vous invite »)" }),
      type: el("select", { class: "input-field" }, [el("option", { value: "", text: "— Choisir —" })].concat((S.meta.event_types || []).map(function (t) {
        return el("option", { value: t.code, text: t.label });
      }))),
    };
    var guess = String(q.event_type || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    (S.meta.event_types || []).some(function (t) {
      var lab = t.label.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      if (guess && (guess.indexOf(t.code) >= 0 || guess.indexOf(lab) >= 0)) { f.type.value = t.code; return true; }
    });
    var body = el("div", { class: "space-y-3" }, [
      el("p", { class: "text-xs text-gray-600", text: "L'événement privé est créé au nom du client du devis " + q.number + (q.owner_name ? " et attribué à " + q.owner_name : "") + ". Vous activerez ensuite le pack au même montant (" + fcfa(q.total_fcfa) + "), après réception du paiement." }),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Type d'événement", f.type, "Adapte la fiche : liste d'invités, inscription en ligne, modèles conseillés."), field("Titre", f.title)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Date et heure", f.date), field("Lieu", f.location)]),
      field("Hôte affiché sur le pass", f.host, "Vide = nom du client."),
    ]);
    modal("Créer l'événement depuis " + q.number, body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Créer l'événement", onclick: function () {
        if (!f.date.value || f.title.value.trim().length < 3 || f.location.value.trim().length < 2) { toast("Titre, date et lieu obligatoires.", "error"); return; }
        if (!f.type.value) { toast("Choisissez le type d'événement.", "error"); return; }
        b.disabled = true;
        btApi.post("/admin/invitations/quotes/" + q.id + "/convert", { title: f.title.value.trim(), event_date: new Date(f.date.value).toISOString(),
          location: f.location.value.trim(), host_label: f.host.value.trim() || null, event_type: f.type.value })
          .then(function (r) { close(); toast("Événement créé : activez le pack à réception du paiement.", "success"); openEventAndActivate(r.event_id, r.quote); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }
  function openEventAndActivate(eventId, q) {
    stopTimer();
    S.view = "event"; S.eventId = eventId; S.q = ""; S.status = ""; S.page = 1; S.importRows = null; S.fold = null; S.guests = null; S.designDirty = false; S.dstep = null;
    S.tab = "apercu"; try { S.tab = sessionStorage.getItem(tabKey()) || "apercu"; } catch (e) {}
    if (!TAB_BUILD[S.tab]) S.tab = "apercu";
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/events/" + eventId).then(function (ev) {
      S.ev = ev; renderEvent(); loadGuests();
      return btApi.get("/admin/invitations/quotes/" + q.id);
    }).then(function (full) { orderModal(true, full); }).catch(function (e) { toast(e.message || "Erreur", "error"); });
    S.timer = setInterval(function () { if (S.view === "event" && !document.hidden) refreshLive(); }, 30000);
  }

  // ════════════════════════════════════════════════════════════════
  // VENTES & COMMISSIONS
  // ════════════════════════════════════════════════════════════════
  var SALES = { period: null, owner: "" };
  var CSTATUS = { a_venir: ["À venir", "bg-gray-100 text-gray-700"], a_cloturer: ["À clôturer", "bg-amber-100 text-amber-800"],
                  due: ["Due", "bg-blue-100 text-blue-800"], versee: ["Versée", "bg-green-100 text-green-800"], annulee: ["Annulée", "bg-red-100 text-red-700"] };
  function thisPeriod() { return new Date().toLocaleDateString("fr-CA", { timeZone: TZ }).slice(0, 7); }
  function openSales() {
    stopTimer();
    S.view = "sales";
    SALES.period = SALES.period || thisPeriod();
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadSellers().then(loadSales);
  }
  function loadSales() {
    btApi.get("/admin/sales/commissions?period=" + SALES.period + (SALES.owner ? "&owner_id=" + SALES.owner : "")).then(renderSales)
      .catch(function (e) { sec().innerHTML = ""; sec().appendChild(navTabs("sales")); sec().appendChild(el("div", { class: "card p-6 text-red-600 text-sm", text: e.message || "Erreur" })); });
  }
  function renderSales(d) {
    var root = sec(), admin = d.can_manage;
    root.innerHTML = "";
    root.appendChild(navTabs("sales"));
    var month = el("input", { class: "input-field", type: "month", value: SALES.period, style: "max-width:180px" });
    month.addEventListener("change", function () { if (month.value) { SALES.period = month.value; loadSales(); } });
    var head = [month];
    if (admin) {
      var ow = el("select", { class: "input-field", style: "max-width:220px" }, [el("option", { value: "", text: "Tous les commerciaux" })].concat((S.sellers || []).map(function (x) {
        return el("option", { value: String(x.id), text: x.full_name });
      })));
      ow.value = SALES.owner;
      ow.addEventListener("change", function () { SALES.owner = ow.value; loadSales(); });
      head.push(ow);
    }
    head.push(el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", text: "Exporter (CSV)", onclick: function () {
      fetch(BT_API + "/admin/sales/commissions/statement.csv?period=" + SALES.period + (SALES.owner ? "&owner_id=" + SALES.owner : ""), { headers: { Authorization: "Bearer " + btAuth.getToken() } })
        .then(function (r) { if (!r.ok) throw new Error("Export impossible"); return r.blob(); })
        .then(function (b) { var url = URL.createObjectURL(b), a = el("a", { href: url, download: "commissions-" + SALES.period + ".csv" }); document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 2000); })
        .catch(function (e) { toast(e.message, "error"); });
    } }));
    if (admin) head.push(el("button", { class: "action-btn btn-primary text-xs", text: "Clôturer " + SALES.period, onclick: function () {
      if (!window.confirm("Clôturer " + SALES.period + " ? Les commissions des packs actifs dont l'événement a eu lieu ce mois-ci deviennent dues.")) return;
      btApi.post("/admin/sales/commissions/close?period=" + SALES.period).then(function (r) { toast(r.closed + " commission(s) clôturée(s) · " + fcfa(r.total_fcfa), "success"); loadSales(); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    } }));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-4" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: admin ? "Ventes & commissions" : "Mes ventes & commissions" }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: "Commission = taux × prix du pack après remise (options exclues), pour les événements du mois. Versement au plus tard le " + isoDay(d.deadline) + "." }),
      ]),
      el("div", { class: "flex flex-wrap gap-2 items-center" }, head),
    ]));
    if (!d.totals.length) { root.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucune vente avec commission pour ce mois." })); return; }
    var cards = el("div", { class: "grid gap-3 md:grid-cols-2 xl:grid-cols-3 mb-4" });
    d.totals.forEach(function (t) {
      var items = [
        el("div", { class: "flex justify-between items-start gap-2" }, [
          el("div", {}, [el("p", { class: "font-black text-gray-900", text: t.owner_name }), el("p", { class: "text-[11px] text-gray-500", text: (t.sales_code || "") + " · " + t.sales + " vente(s)" })]),
        ]),
        el("div", { class: "grid grid-cols-2 gap-2 mt-3 text-xs" }, [
          el("div", {}, [el("p", { class: "text-gray-500", text: "Due" }), el("p", { class: "font-black text-blue-800 text-base", text: fcfa(t.due) })]),
          el("div", {}, [el("p", { class: "text-gray-500", text: "Versée" }), el("p", { class: "font-black text-green-800 text-base", text: fcfa(t.paid) })]),
          el("div", {}, [el("p", { class: "text-gray-500", text: "À clôturer" }), el("p", { class: "font-bold text-amber-800", text: fcfa(t.to_close) })]),
          el("div", {}, [el("p", { class: "text-gray-500", text: "À venir" }), el("p", { class: "font-bold text-gray-700", text: fcfa(t.upcoming) })]),
        ]),
      ];
      if (admin && t.due > 0) items.push(el("button", { class: "action-btn btn-primary text-xs mt-3 w-full", text: "Marquer comme versée (" + fcfa(t.due) + ")", onclick: function () { payModal(t); } }));
      cards.appendChild(el("div", { class: "card p-4" }, items));
    });
    root.appendChild(cards);
    var tbl = el("div", { class: "card divide-y divide-gray-100" });
    d.lines.forEach(function (l) {
      var c = CSTATUS[l.status] || [l.status, "bg-gray-100 text-gray-700"];
      tbl.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("p", { class: "font-bold text-gray-900", text: l.event_title }),
          el("p", { class: "text-[11px] text-gray-500", text: dt(l.event_date) + (l.quote_number ? " · " + l.quote_number : "") + (admin ? " · " + l.owner_name : "") + (l.paid_ref ? " · versée le " + isoDay((l.paid_at || "").slice(0, 10)) + ", réf. " + l.paid_ref : "") }),
        ]),
        el("span", { class: "text-xs text-gray-600", text: fcfa(l.base_fcfa) + " × " + String(l.rate_pct).replace(".", ",") + " %" }),
        el("span", { class: "font-black text-gray-900 min-w-[7.5rem] text-right whitespace-nowrap", text: fcfa(l.amount_fcfa) }),
        el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + c[1], text: c[0] }),
      ]));
    });
    root.appendChild(tbl);
  }
  function payModal(t) {
    var day = el("input", { class: "input-field", type: "date", value: new Date().toLocaleDateString("fr-CA", { timeZone: TZ }) });
    var ref = el("input", { class: "input-field", maxlength: "80", placeholder: "ID de transaction Orange Money, n° de reçu…" });
    var body = el("div", { class: "space-y-3" }, [
      el("p", { class: "text-sm text-gray-700", text: t.owner_name + " · " + SALES.period + " · " + fcfa(t.due) }),
      field("Date du versement", day), field("Référence (obligatoire)", ref),
    ]);
    modal("Commission versée", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer le versement", onclick: function () {
        if (ref.value.trim().length < 3) { toast("Référence obligatoire.", "error"); return; }
        b.disabled = true;
        btApi.post("/admin/sales/commissions/pay?period=" + SALES.period + "&owner_id=" + t.owner_id, { paid_on: day.value, reference: ref.value.trim() })
          .then(function (r) { close(); toast(r.paid + " commission(s) marquée(s) versée(s) · " + fcfa(r.total_fcfa), "success"); loadSales(); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ════════════════════════════════════════════════════════════════
  // CATALOGUE DES PACKS (lecture admin + commercial, modification admin)
  // ════════════════════════════════════════════════════════════════
  var CAT = { tab: "formulas", data: null, archived: false, packFormula: "" };
  var UNIT_TXT = { flat: "forfait", per_unit: "par unité", per_page: "par page" };
  var ACTION_TXT = { create: "Création", update: "Modification", archive: "Archivage", restore: "Restauration", "delete": "Suppression", seed: "Reprise" };
  var ENTITY_TXT = { formula: "Formule", pack: "Pack", option: "Option", discount: "Code", settings: "Réglages" };
  var FIELD_TXT = { name: "nom", description: "description", included: "inclus", badge: "badge", public: "visible", onsite_agents: "agents sur place",
    track_link: "lien de suivi", team_sends: "envoi par l'équipe", agents_step: "invités par agent", sort_order: "ordre", status: "statut",
    size: "taille", price_fcfa: "prix", on_quote: "sur devis", valid_from: "début", valid_until: "fin", unit: "unité", unit_label: "libellé d'unité",
    max_qty: "quantité max", agents_per_unit: "agents par unité", formula_ids: "formules", pack_ids: "packs", kind: "type", value: "valeur",
    max_uses: "utilisations max", note: "note", code: "code", formula_id: "formule", manual_discount_max_pct: "plafond remise manuelle (%)",
    vat_enabled: "TVA activée", vat_rate_bp: "taux TVA (pb)", prices_include_vat: "prix TTC" };

  function canEditCat() { return CAT.data && CAT.data.can_edit; }
  function fname(id) { var f = CAT.data.formulas.filter(function (x) { return x.id === id; })[0]; return f ? f.name : "?"; }
  function chip(text, cls) { return el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + cls, text: text }); }
  function statusChip(o) { return o.status === "archived" ? chip("Archivé", "bg-gray-200 text-gray-600") : null; }
  function catCall(promise, okMsg) {
    return promise.then(function () { toast(okMsg, "success"); return loadCatalog(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); throw e; });
  }
  function rowActions(kind, o, editFn) {
    if (!canEditCat()) return null;
    var base = "/admin/invitations/catalog/" + kind + "/" + o.id;
    var box = el("div", { class: "flex flex-wrap gap-1" });
    box.appendChild(act("Modifier", "background:#ede9fe;color:#5b21b6", function () { editFn(o); }));
    if (o.status === "active") box.appendChild(act("Archiver", "background:#f3f4f6;color:#374151", function () {
      if (window.confirm("Archiver ? L'élément sera masqué partout mais gardé dans l'historique des commandes.")) catCall(btApi.post(base + "/archive"), "Archivé.").catch(function () {});
    }));
    else box.appendChild(act("Restaurer", "background:#dcfce7;color:#166534", function () { catCall(btApi.post(base + "/restore"), "Restauré.").catch(function () {}); }));
    if (!o.used) box.appendChild(act("Supprimer", "background:#fee2e2;color:#991b1b", function () {
      if (window.confirm("Supprimer définitivement (jamais utilisé dans une commande) ?")) catCall(btApi.delete(base), "Supprimé.").catch(function () {});
    }));
    return box;
  }
  function checks(items, selected) {
    var box = el("div", { class: "flex flex-wrap gap-3" }), inputs = [];
    items.forEach(function (it) {
      var c = el("input", { type: "checkbox", value: String(it.id) });
      c.checked = selected.indexOf(it.id) >= 0;
      inputs.push(c);
      box.appendChild(el("label", { class: "flex items-center gap-1 text-sm" }, [c, it.label]));
    });
    box.values = function () { return inputs.filter(function (c) { return c.checked; }).map(function (c) { return parseInt(c.value, 10); }); };
    return box;
  }
  function numOrNull(input) { return input.value === "" ? null : parseInt(input.value, 10); }

  function openCatalog(tab) {
    stopTimer();
    S.view = "catalog"; CAT.tab = tab || CAT.tab;
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadCatalog();
  }
  function loadCatalog() {
    return btApi.get("/admin/invitations/catalog").then(function (d) {
      CAT.data = d;
      return btApi.get("/admin/invitations/meta").then(function (m) { S.meta = m; renderCatalog(); });
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function renderCatalog() {
    var root = sec(), d = CAT.data;
    root.innerHTML = "";
    root.appendChild(navTabs("catalog"));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-3" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: "Catalogue des packs" }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: (d.can_edit ? "" : "Lecture seule (modification : administrateur). ") +
          "Ce catalogue alimente les devis, l'activation et la future page publique : prix affiché = prix facturé. Un pack déjà activé garde son prix et son contenu." }),
      ]),
      el("label", { class: "flex items-center gap-2 text-xs font-bold text-gray-600" }, [
        (function () { var c = el("input", { type: "checkbox", onchange: function (e) { CAT.archived = e.target.checked; renderCatalog(); } }); c.checked = CAT.archived; return c; })(),
        "Afficher les éléments archivés"]),
    ]));
    var tabs = [["formulas", "Formules"], ["packs", "Packs"], ["options", "Options"], ["discounts", "Codes de remise"], ["settings", "Réglages"], ["history", "Historique"]];
    root.appendChild(el("div", { class: "flex flex-wrap gap-2 mb-4" }, tabs.map(function (t) {
      return el("button", { class: "action-btn text-xs", style: CAT.tab === t[0] ? "background:#00502c;color:#fff" : "background:#f3f4f6;color:#374151",
        text: t[1], onclick: function () { CAT.tab = t[0]; renderCatalog(); } });
    })));
    var body = el("div", { id: "inv-cat-body" });
    root.appendChild(body);
    ({ formulas: catFormulas, packs: catPacks, options: catOptions, discounts: catDiscounts, settings: catSettings, history: catHistory })[CAT.tab](body);
  }
  function visible(list) { return list.filter(function (o) { return CAT.archived || o.status === "active"; }); }

  // ── Formules
  function catFormulas(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouvelle formule", onclick: function () { formulaModal(null); } }));
    var grid = el("div", { class: "grid gap-3 md:grid-cols-2" });
    visible(CAT.data.formulas).forEach(function (f) {
      var feats = [f.onsite_agents ? "Agents GoEvent sur place" + (f.agents_step ? " (1 / " + f.agents_step + " invités)" : "") : null,
                   f.track_link ? "Lien de suivi client" : null, f.team_sends ? "Envoi des pass par l'équipe" : null].filter(Boolean);
      grid.appendChild(el("div", { class: "card p-4 " + (f.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex flex-wrap items-center gap-2" }, [
          el("p", { class: "font-black text-gray-900", text: f.name }), el("code", { class: "text-[10px] text-gray-400", text: f.code }),
          f.badge ? chip(f.badge, "bg-amber-100 text-amber-800") : null, f.public ? null : chip("Masquée au public", "bg-gray-100 text-gray-600"), statusChip(f),
        ]),
        f.description ? el("p", { class: "text-xs text-gray-600 mt-1", text: f.description }) : null,
        f.included.length ? el("ul", { class: "text-xs text-gray-700 mt-2 list-disc pl-4" }, f.included.map(function (t) { return el("li", { text: t }); })) : null,
        el("p", { class: "text-[11px] text-gray-500 mt-2", text: feats.length ? feats.join(" · ") : "Aucun service GoEvent sur place (le client gère)" }),
        el("div", { class: "mt-3" }, [rowActions("formulas", f, formulaModal)]),
      ]));
    });
    box.appendChild(grid);
  }
  function formulaModal(f) {
    var x = {
      code: el("input", { class: "input-field", maxlength: "20", placeholder: "ex. prestige (minuscules, immuable)" }),
      name: el("input", { class: "input-field", maxlength: "60" }), desc: el("input", { class: "input-field", maxlength: "300" }),
      included: el("textarea", { class: "input-field text-sm", rows: "4", placeholder: "Une puce par ligne (12 max)" }),
      badge: el("input", { class: "input-field", maxlength: "30", placeholder: "Ex. Populaire" }), order: el("input", { class: "input-field", type: "number", min: "0" }),
      pub: el("input", { type: "checkbox" }), onsite: el("input", { type: "checkbox" }), track: el("input", { type: "checkbox" }), team: el("input", { type: "checkbox" }),
      step: el("input", { class: "input-field", type: "number", min: "1", max: "5000", placeholder: "ex. 300" }),
    };
    if (f) {
      x.code.value = f.code; x.code.disabled = true; x.name.value = f.name; x.desc.value = f.description || ""; x.included.value = f.included.join("\n");
      x.badge.value = f.badge || ""; x.order.value = f.sort_order; x.pub.checked = f.public; x.onsite.checked = f.onsite_agents; x.track.checked = f.track_link;
      x.team.checked = f.team_sends; x.step.value = f.agents_step || "";
    } else { x.pub.checked = true; x.order.value = String(CAT.data.formulas.length + 1); }
    var cb = function (c, t) { return el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [c, t]); };
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Code technique", x.code, "Immuable après création."), field("Nom affiché", x.name)]),
      field("Description courte", x.desc), field("Inclus (puces)", x.included),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Badge (facultatif)", x.badge), field("Ordre d'affichage", x.order)]),
      cb(x.pub, "Visible sur la page publique"),
      el("div", { class: "p-3 rounded-xl bg-gray-50 space-y-2" }, [el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400", text: "Comportements techniques" }),
        cb(x.onsite, "Agents GoEvent sur place"), field("1 agent inclus par tranche de … invités", x.step), cb(x.track, "Lien de suivi client"),
        cb(x.team, "Envoi des pass par l'équipe GoEvent")]),
    ]);
    modal(f ? "Modifier la formule" : "Nouvelle formule", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { name: x.name.value.trim(), description: x.desc.value.trim() || null,
          included: x.included.value.split("\n").map(function (t) { return t.trim(); }).filter(Boolean),
          badge: x.badge.value.trim() || null, sort_order: numOrNull(x.order) || 0, public: x.pub.checked,
          onsite_agents: x.onsite.checked, track_link: x.track.checked, team_sends: x.team.checked, agents_step: numOrNull(x.step) };
        if (!f) payload.code = x.code.value.trim();
        b.disabled = true;
        catCall(f ? btApi.patch("/admin/invitations/catalog/formulas/" + f.id, payload) : btApi.post("/admin/invitations/catalog/formulas", payload), "Formule enregistrée.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Packs
  function catPacks(box) {
    var bar = el("div", { class: "flex flex-wrap items-center gap-2 mb-3" });
    var sel = el("select", { class: "input-field", style: "max-width:240px", onchange: function (e) { CAT.packFormula = e.target.value; renderCatalog(); } },
      [el("option", { value: "", text: "Toutes les formules" })].concat(CAT.data.formulas.map(function (f) {
        return el("option", { value: String(f.id), text: f.name, selected: CAT.packFormula === String(f.id) ? "selected" : null });
      })));
    bar.appendChild(sel);
    if (canEditCat()) bar.appendChild(el("button", { class: "action-btn btn-primary text-xs", text: "+ Nouveau pack", onclick: function () { packCatModal(null); } }));
    box.appendChild(bar);
    var list = visible(CAT.data.packs).filter(function (p) { return !CAT.packFormula || String(p.formula_id) === CAT.packFormula; });
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucun pack." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (p) {
      var period = p.valid_from || p.valid_until ? (p.valid_from ? "du " + dayLabel(p.valid_from) : "") + (p.valid_until ? " au " + dayLabel(p.valid_until) : "") : "";
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (p.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("span", { class: "font-bold text-sm", text: fname(p.formula_id) + " · " + p.size + " personnes" + (p.name ? " · « " + p.name + " »" : "") }),
            el("span", { class: "text-sm font-black text-primary", text: p.on_quote ? "Sur devis" : fcfa(p.price_fcfa) }),
            p.public ? null : chip("Masqué au public", "bg-gray-100 text-gray-600"),
            p.status === "active" && !p.available ? chip("Hors période", "bg-amber-100 text-amber-800") : null, statusChip(p),
            p.used ? chip("Déjà vendu", "bg-blue-50 text-blue-700") : null,
          ]),
          period ? el("p", { class: "text-[11px] text-gray-500", text: "Offre valable " + period }) : null,
        ]),
        rowActions("packs", p, packCatModal),
      ]));
    });
    box.appendChild(table);
  }
  function packCatModal(p) {
    var formula = el("select", { class: "input-field" }, CAT.data.formulas.filter(function (f) { return f.status === "active" || (p && f.id === p.formula_id); }).map(function (f) {
      return el("option", { value: String(f.id), text: f.name });
    }));
    var x = { size: el("input", { class: "input-field", type: "number", min: "1", max: "5000" }), price: el("input", { class: "input-field", type: "number", min: "0" }),
      quote: el("input", { type: "checkbox" }), name: el("input", { class: "input-field", maxlength: "60", placeholder: "Ex. Petit comité (facultatif)" }),
      pub: el("input", { type: "checkbox" }), order: el("input", { class: "input-field", type: "number", min: "0" }),
      from: el("input", { class: "input-field", type: "date" }), until: el("input", { class: "input-field", type: "date" }) };
    if (p) {
      formula.value = String(p.formula_id); formula.disabled = true; x.size.value = p.size; x.price.value = p.price_fcfa === null ? "" : p.price_fcfa;
      x.quote.checked = p.on_quote; x.name.value = p.name || ""; x.pub.checked = p.public; x.order.value = p.sort_order;
      x.from.value = dateInput(p.valid_from); x.until.value = dateInput(p.valid_until);
    } else { x.pub.checked = true; if (CAT.packFormula) formula.value = CAT.packFormula; }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Formule", formula), field("Taille (personnes)", x.size, "1 à 5 000. Un pass « + 2 » compte 3 personnes.")]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Prix (FCFA)", x.price), field("Nom commercial", x.name)]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.quote, "Sur devis (pas de prix affiché ; activation en « sur mesure »)"]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.pub, "Visible sur la page publique"]),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Ordre", x.order), field("Valable du (facultatif)", x.from), field("au (facultatif)", x.until)]),
      p && p.used ? el("p", { class: "text-[11px] text-blue-700", text: "Pack déjà vendu : les commandes passées gardent leur prix et leur contenu." }) : null,
    ]);
    modal(p ? "Modifier le pack" : "Nouveau pack", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { size: numOrNull(x.size), price_fcfa: x.quote.checked ? null : numOrNull(x.price), on_quote: x.quote.checked,
          name: x.name.value.trim() || null, public: x.pub.checked, valid_from: dateIso(x.from.value, false), valid_until: dateIso(x.until.value, true) };
        if (x.order.value !== "") payload.sort_order = numOrNull(x.order);
        if (!p) payload.formula_id = parseInt(formula.value, 10);
        b.disabled = true;
        catCall(p ? btApi.patch("/admin/invitations/catalog/packs/" + p.id, payload) : btApi.post("/admin/invitations/catalog/packs", payload), "Pack enregistré.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Options
  function catOptions(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouvelle option", onclick: function () { optionModal(null); } }));
    var list = visible(CAT.data.options);
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucune option. Ex. agent supplémentaire (par soirée), impression des pass papier (par page), design personnalisé (forfait)." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (o) {
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (o.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("span", { class: "font-bold text-sm", text: o.name }),
            el("span", { class: "text-sm font-black text-primary", text: fcfa(o.price_fcfa) + " " + (o.unit === "flat" ? "(forfait)" : "/ " + (o.unit_label || UNIT_TXT[o.unit].replace("par ", ""))) }),
            o.public ? null : chip("Masquée au public", "bg-gray-100 text-gray-600"), statusChip(o), o.used ? chip("Déjà vendue", "bg-blue-50 text-blue-700") : null,
          ]),
          el("p", { class: "text-[11px] text-gray-500", text: "Formules : " + o.formula_ids.map(fname).join(", ") + (o.unit !== "flat" ? " · max " + o.max_qty : "") +
            (o.agents_per_unit ? " · ajoute " + o.agents_per_unit + " agent(s) par unité" : "") }),
        ]),
        rowActions("options", o, optionModal),
      ]));
    });
    box.appendChild(table);
  }
  function optionModal(o) {
    var x = { name: el("input", { class: "input-field", maxlength: "80" }), desc: el("input", { class: "input-field", maxlength: "300" }),
      price: el("input", { class: "input-field", type: "number", min: "0" }),
      unit: el("select", { class: "input-field" }, Object.keys(UNIT_TXT).map(function (k) { return el("option", { value: k, text: UNIT_TXT[k] }); })),
      label: el("input", { class: "input-field", maxlength: "30", placeholder: "Ex. agent / soirée, page" }),
      max: el("input", { class: "input-field", type: "number", min: "1", max: "1000" }), agents: el("input", { class: "input-field", type: "number", min: "0", max: "20" }),
      pub: el("input", { type: "checkbox" }), order: el("input", { class: "input-field", type: "number", min: "0" }) };
    var forms = checks(CAT.data.formulas.filter(function (f) { return f.status === "active" || (o && o.formula_ids.indexOf(f.id) >= 0); })
      .map(function (f) { return { id: f.id, label: f.name }; }), o ? o.formula_ids : []);
    if (o) {
      x.name.value = o.name; x.desc.value = o.description || ""; x.price.value = o.price_fcfa; x.unit.value = o.unit; x.label.value = o.unit_label || "";
      x.max.value = o.max_qty; x.agents.value = o.agents_per_unit; x.pub.checked = o.public; x.order.value = o.sort_order;
    } else { x.pub.checked = true; x.max.value = "1"; x.agents.value = "0"; x.order.value = "0"; }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Nom", x.name), field("Prix (FCFA)", x.price)]),
      field("Description (facultatif)", x.desc),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Unité", x.unit), field("Libellé de l'unité", x.label), field("Quantité max", x.max, "Forfait : 1.")]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Agents ajoutés par unité", x.agents, "1 pour « agent supplémentaire » : compté dans les agents inclus."), field("Ordre", x.order)]),
      el("div", {}, [lbl("Formules concernées"), forms]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.pub, "Visible sur la page publique"]),
      el("p", { class: "text-[11px] text-gray-500", text: "Les options ne sont jamais remisées (codes et remises manuelles portent sur le pack uniquement)." }),
    ]);
    modal(o ? "Modifier l'option" : "Nouvelle option", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { name: x.name.value.trim(), description: x.desc.value.trim() || null, price_fcfa: numOrNull(x.price), unit: x.unit.value,
          unit_label: x.label.value.trim() || null, max_qty: numOrNull(x.max) || 1, agents_per_unit: numOrNull(x.agents) || 0,
          public: x.pub.checked, sort_order: numOrNull(x.order) || 0, formula_ids: forms.values() };
        if (!payload.formula_ids.length) { toast("Choisissez au moins une formule.", "error"); return; }
        b.disabled = true;
        catCall(o ? btApi.patch("/admin/invitations/catalog/options/" + o.id, payload) : btApi.post("/admin/invitations/catalog/options", payload), "Option enregistrée.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Codes de remise
  function discountTxt(d) { return d.kind === "pct" ? "−" + d.value + " % du pack" : "−" + fcfa(d.value) + " sur le pack"; }
  function catDiscounts(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouveau code", onclick: function () { discountModal(null); } }));
    var list = visible(CAT.data.discounts);
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucun code de remise." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (d) {
      var scope = (d.formula_ids.length ? d.formula_ids.map(fname).join(", ") : "Toutes les formules") + (d.pack_ids.length ? " · " + d.pack_ids.length + " pack(s) précis" : "");
      var period = (d.valid_from ? "du " + dayLabel(d.valid_from) : "") + (d.valid_until ? " au " + dayLabel(d.valid_until) : "");
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (d.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [el("code", { class: "font-black text-sm", text: d.code }),
            el("span", { class: "text-sm font-bold text-green-700", text: discountTxt(d) }), statusChip(d)]),
          el("p", { class: "text-[11px] text-gray-500", text: "Utilisations : " + d.uses + (d.max_uses ? " / " + d.max_uses : " (illimité)") + " · " + scope + (period ? " · " + period : "") +
            (d.note ? " · " + d.note : "") }),
        ]),
        rowActions("discounts", d, discountModal),
      ]));
    });
    box.appendChild(table);
  }
  function discountModal(d) {
    var x = { code: el("input", { class: "input-field uppercase", maxlength: "40", placeholder: "Ex. PIONNIER" }),
      kind: el("select", { class: "input-field" }, [el("option", { value: "pct", text: "Pourcentage du pack" }), el("option", { value: "fixed", text: "Montant fixe (FCFA)" })]),
      value: el("input", { class: "input-field", type: "number", min: "1" }), max: el("input", { class: "input-field", type: "number", min: "1", placeholder: "Illimité" }),
      from: el("input", { class: "input-field", type: "date" }), until: el("input", { class: "input-field", type: "date" }),
      note: el("input", { class: "input-field", maxlength: "200", placeholder: "Facultatif" }) };
    var forms = checks(CAT.data.formulas.map(function (f) { return { id: f.id, label: f.name }; }), d ? d.formula_ids : []);
    var packs = checks(CAT.data.packs.filter(function (p) { return p.status === "active" || (d && d.pack_ids.indexOf(p.id) >= 0); }).map(function (p) {
      return { id: p.id, label: fname(p.formula_id) + " " + p.size }; }), d ? d.pack_ids : []);
    if (d) {
      x.code.value = d.code; x.code.disabled = true; x.kind.value = d.kind; x.value.value = d.value; x.max.value = d.max_uses || "";
      x.from.value = dateInput(d.valid_from); x.until.value = dateInput(d.valid_until); x.note.value = d.note || "";
    }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Code", x.code, "Immuable après création."), field("Type", x.kind), field("Valeur", x.value)]),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Utilisations max", x.max, "Une annulation libère l'utilisation."), field("Valable du", x.from), field("au", x.until)]),
      el("div", {}, [lbl("Formules concernées (aucune = toutes)"), forms]),
      el("div", {}, [lbl("Packs précis (aucun = tous ; jamais sur un pack sur mesure)"), packs]),
      field("Note", x.note),
      el("p", { class: "text-[11px] text-gray-500", text: "Une seule remise par commande ; elle porte sur le pack uniquement et ne dépasse jamais son prix." }),
    ]);
    modal(d ? "Modifier le code" : "Nouveau code de remise", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { kind: x.kind.value, value: numOrNull(x.value), max_uses: numOrNull(x.max), valid_from: dateIso(x.from.value, false),
          valid_until: dateIso(x.until.value, true), formula_ids: forms.values(), pack_ids: packs.values(), note: x.note.value.trim() || null };
        if (!d) payload.code = x.code.value.trim().toUpperCase();
        b.disabled = true;
        catCall(d ? btApi.patch("/admin/invitations/catalog/discounts/" + d.id, payload) : btApi.post("/admin/invitations/catalog/discounts", payload), "Code enregistré.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Réglages
  function catSettings(box) {
    var st = CAT.data.settings, ro = !canEditCat();
    var cap = el("input", { class: "input-field", type: "number", min: "0", max: "100", style: "max-width:120px" }); cap.value = st.manual_discount_max_pct;
    var vatOn = el("input", { type: "checkbox" }); vatOn.checked = st.vat_enabled;
    var rate = el("input", { class: "input-field", type: "number", min: "0", max: "50", step: "0.01", style: "max-width:120px" }); rate.value = st.vat_rate_bp / 100;
    var mode = el("select", { class: "input-field", style: "max-width:320px" }, [el("option", { value: "ttc", text: "Les prix du catalogue sont TTC" }), el("option", { value: "ht", text: "Les prix du catalogue sont HT (TVA ajoutée)" })]);
    mode.value = st.prices_include_vat ? "ttc" : "ht";
    [cap, vatOn, rate, mode].forEach(function (i) { i.disabled = ro; });
    box.appendChild(el("div", { class: "card p-5 space-y-4" }, [
      field("Plafond de la remise manuelle (% du pack)", cap, "Remise manuelle : administrateur seul, motif obligatoire."),
      el("div", { class: "p-3 rounded-xl bg-gray-50 space-y-3" }, [
        el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [vatOn, "Appliquer la TVA"]),
        field("Taux de TVA (%)", rate), field("Prix saisis", mode),
        el("p", { class: "text-[11px] text-gray-500", text: "Désactivée : « TVA non applicable », total = prix. Activée : HT, TVA et TTC sont calculés au FCFA près et figés dans chaque commande." }),
      ]),
      ro ? null : el("button", { class: "action-btn btn-primary", text: "Enregistrer les réglages", onclick: function () {
        var payload = { manual_discount_max_pct: parseInt(cap.value, 10) || 0, vat_enabled: vatOn.checked, vat_rate: parseFloat(rate.value) || 0, prices_include_vat: mode.value === "ttc" };
        if (vatOn.checked && !window.confirm("Activer la TVA à " + payload.vat_rate + " % ? Les prochains devis et activations l'appliqueront.")) return;
        catCall(btApi.put("/admin/invitations/catalog/settings", payload), "Réglages enregistrés.").catch(function () {});
      } }),
    ]));
  }

  // ── Historique
  function fmtVal(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (v === true) return "oui"; if (v === false) return "non";
    if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
    if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return dayLabel(v);
    return String(v);
  }
  function catHistory(box) {
    box.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/catalog/history?limit=200").then(function (d) {
      box.innerHTML = "";
      if (!d.history.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucune modification." })); return; }
      var list = el("div", { class: "card divide-y divide-gray-100" });
      d.history.forEach(function (h) {
        var keys = Object.keys(h.changes || {});
        list.appendChild(el("div", { class: "p-3 text-xs" }, [
          el("p", { class: "font-bold text-gray-800", text: dt(h.at) + " · " + (h.user || "—") + " · " + (ACTION_TXT[h.action] || h.action) + " · " + (ENTITY_TXT[h.entity] || h.entity) + " « " + (h.label || "") + " »" }),
          keys.length && h.action !== "seed" && h.action !== "create" ? el("ul", { class: "text-gray-600 mt-1 list-disc pl-4" }, keys.map(function (k) {
            return el("li", { text: (FIELD_TXT[k] || k) + " : " + fmtVal(h.changes[k][0]) + " → " + fmtVal(h.changes[k][1]) });
          })) : null,
        ]));
      });
      box.appendChild(list);
    }).catch(function (e) { box.innerHTML = ""; toast(e.message || "Erreur", "error"); });
  }

  // ════════════════════════════════════════════════════════════════
  // ENTRÉE (appelée par showTab('invitations'))
  // ════════════════════════════════════════════════════════════════
  window.invInit = function () {
    var start = function () {
      if (S.view === "event" && S.eventId) openEvent(S.eventId);
      else if (S.view === "catalog") openCatalog();
      else if (S.view === "quotes") openQuotes();
      else if (S.view === "sales") openSales();
      else loadList();
    };
    if (S.meta) { start(); return; }
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/meta").then(function (m) { S.meta = m; start(); })
      .catch(function (e) { sec().textContent = e.message || "Erreur de chargement"; });
  };
  window.invLeave = stopTimer;
  // Depuis l'onglet Tarifs : à appeler AVANT showTab('invitations') (invInit ouvre alors le catalogue)
  window.invOpenCatalog = function () { S.view = "catalog"; CAT.tab = "packs"; };
})();
