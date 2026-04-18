// ============================================================
//  GOEVENT — pwa.js
//  Enregistrement SW, bouton installation, notifications
// ============================================================

(function () {
  "use strict";

  // ── Enregistrement du Service Worker ─────────────────────
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker
        .register("/sw.js")
        .then(function (reg) {
          console.log("[PWA] Service Worker enregistré:", reg.scope);

          // Vérifier si une mise à jour de l'application est disponible
          reg.addEventListener("updatefound", function () {
            var newWorker = reg.installing;
            newWorker.addEventListener("statechange", function () {
              if (
                newWorker.state === "installed" &&
                navigator.serviceWorker.controller
              ) {
                if (typeof btToast === "function") {
                  btToast(
                    "✨ Nouvelle version de GoEvent disponible. Mise à jour...",
                  );
                }
                setTimeout(() => window.location.reload(), 2000);
              }
            });
          });
        })
        .catch(function (err) {
          console.log("[PWA] Erreur SW:", err);
        });

      // Écouter les messages de synchronisation
      navigator.serviceWorker.addEventListener("message", function (event) {
        if (event.data && event.data.type === "SYNC_COMPLETE") {
          if (typeof btToast === "function")
            btToast("✅ Données synchronisées !");
        }
      });
    });
  }

  // ── Bannière d'installation (Le Pop-up Mobile) ────────────
  var deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", function (e) {
    // Empêcher Chrome d'afficher sa vieille bannière par défaut
    e.preventDefault();
    deferredPrompt = e;

    // On vérifie si l'utilisateur n'a pas déjà fermé notre bannière aujourd'hui
    var dismissed = localStorage.getItem("bt-install-dismissed");
    if (dismissed && Date.now() < parseInt(dismissed)) {
      return;
    }

    // Afficher notre belle bannière sur mesure
    showInstallBanner();
  });

  function showInstallBanner() {
    if (document.getElementById("pwa-install-banner")) {
      return;
    }

    var banner = document.createElement("div");
    banner.id = "pwa-install-banner";
    // Design de la bannière avec Tailwind CSS
    // Design de la bannière avec Tailwind CSS (ajout de md:hidden pour cacher sur PC)
    banner.className =
      "md:hidden fixed bottom-4 left-4 right-4 bg-white p-4 rounded-3xl shadow-2xl z-[100] border border-gray-100 flex items-center justify-between transform transition-transform duration-500 translate-y-[150%]";

    banner.innerHTML = `
      <div class="flex items-center gap-3">
        <img src="icons/event.png" class="w-12 h-12 rounded-xl object-cover shadow-sm" alt="Logo GoEvent" />
        <div>
          <p class="font-black text-gray-900 text-sm">Installer GoEvent</p>
          <p class="text-[10px] text-gray-500 font-medium leading-tight mt-0.5">Accès rapide, billetterie hors-ligne.</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button id="btn-pwa-close" class="w-8 h-8 flex items-center justify-center rounded-full bg-gray-50 text-gray-400 active:scale-95 transition-all hover:bg-gray-100">
          <span class="material-symbols-outlined text-[18px]">close</span>
        </button>
        <button id="btn-pwa-install" class="px-4 py-2.5 bg-primary text-white text-xs font-bold rounded-xl active:scale-95 shadow-lg shadow-primary/20 transition-all">
          Installer
        </button>
      </div>
    `;

    document.body.appendChild(banner);

    // Petite animation pour faire glisser la bannière vers le haut
    setTimeout(() => banner.classList.remove("translate-y-[150%]"), 100);

    // Si le client clique sur "Installer"
    document
      .getElementById("btn-pwa-install")
      .addEventListener("click", function () {
        if (deferredPrompt) {
          deferredPrompt.prompt();
          deferredPrompt.userChoice.then(function (choiceResult) {
            deferredPrompt = null;
            banner.classList.add("translate-y-[150%]");
            setTimeout(() => banner.remove(), 500);
          });
        }
      });

    // Si le client clique sur la croix "Fermer"
    document
      .getElementById("btn-pwa-close")
      .addEventListener("click", function () {
        banner.classList.add("translate-y-[150%]");
        // On cache la bannière pendant 24 heures pour ne pas le harceler (86400000 ms = 24h)
        localStorage.setItem("bt-install-dismissed", Date.now() + 86400000);
        setTimeout(() => banner.remove(), 500);
      });
  }

  // ── Demande d'autorisation pour les Notifications ─────────
  function requestNotificationPermission() {
    if (!("Notification" in window)) return;
    if (
      Notification.permission === "granted" ||
      Notification.permission === "denied"
    )
      return;

    // On attend 15 secondes après l'ouverture de l'application pour demander poliment
    setTimeout(function () {
      var asked = localStorage.getItem("bt-notif-asked");
      if (asked) return;

      Notification.requestPermission().then(function (permission) {
        localStorage.setItem("bt-notif-asked", "1");
        if (permission === "granted") {
          if (typeof btToast === "function")
            btToast("🔔 Notifications GoEvent activées !");
        }
      });
    }, 15000);
  }

  document.addEventListener("DOMContentLoaded", requestNotificationPermission);

  // ── Outils pour les développeurs (Toi) ─────────────────────
  window.btPWA = {
    clearCache: function () {
      if (navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: "CLEAR_CACHE" });
        if (typeof btToast === "function")
          btToast("Cache vidé — rechargement de GoEvent...");
        setTimeout(function () {
          window.location.reload();
        }, 1000);
      }
    },
    forceUpdate: function () {
      if (navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({
          type: "SKIP_WAITING",
        });
        window.location.reload();
      }
    },
  };
  // ... tes outils développeurs originaux
  window.btPWA = {
    clearCache: function () {
      /* ... */
    },
    forceUpdate: function () {
      /* ... */
    },
  };

  // 👇 DEBUT DU CODE AJOUTÉ : LOGIQUE DU SPLASH SCREEN 👇
  window.addEventListener("load", function () {
    const splash = document.getElementById("goevent-splash");

    // 1. LE GARDE DU CORPS : On vérifie si le splash a DÉJÀ été vu pendant cette session
    if (sessionStorage.getItem("goeventSplashVu") === "oui") {
      if (splash) {
        splash.style.display = "none"; // On cache le splash instantanément
      }
      return; // On arrête tout, pas d'animation !
    }

    // 2. Si on est là, c'est que c'est la première ouverture. On prépare l'animation.
    const textWrapper = document.getElementById("splash-text-wrapper");
    const typewriterText = document.getElementById("typewriter-text");
    const typewriterDot = document.getElementById("typewriter-dot");
    const logoContainer = document.getElementById("splash-logo");

    if (splash && textWrapper && typewriterText) {
      const mot = "GoEvent";
      let index = 0;

      const intervalId = setInterval(() => {
        typewriterText.textContent += mot[index];
        index++;
        if (index === mot.length) {
          clearInterval(intervalId);
          declencherSuiteAnimation();
        }
      }, 120);

      function declencherSuiteAnimation() {
        typewriterDot.classList.remove("opacity-0");
        setTimeout(() => {
          textWrapper.classList.add("zoom-text");
        }, 300);
        setTimeout(() => {
          textWrapper.classList.add("hide-text");
          textWrapper.style.position = "absolute";
          logoContainer.classList.remove("opacity-0", "scale-75");
          logoContainer.classList.add("opacity-100", "scale-100");
        }, 1300);
        setTimeout(() => {
          splash.style.opacity = "0";
        }, 2800);

        // LA TOUCHE FINALE : On retire le splash ET on enregistre dans la mémoire
        setTimeout(() => {
          splash.style.display = "none";
          sessionStorage.setItem("goeventSplashVu", "oui"); // Le téléphone s'en souviendra !
        }, 3500);
      }
    }
  });
  // 👆 FIN DU CODE AJOUTÉ 👆
})();
