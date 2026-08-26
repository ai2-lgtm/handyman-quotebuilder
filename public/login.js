(function () {
  "use strict";

  document.getElementById("googleSignIn").addEventListener("click", function () {
    window.location.href = "/api/auth/google/start";
  });

  var params = new URLSearchParams(window.location.search);
  var error = params.get("error");
  if (error) {
    var el = document.getElementById("authError");
    el.textContent = error;
    el.style.display = "";
  }

  // If already logged in, skip straight to the app.
  fetch("/api/auth/me", { credentials: "same-origin" }).then(function (r) {
    if (r.ok) window.location.href = "/";
  }).catch(function () {});
})();
