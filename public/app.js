(function () {
  "use strict";

  // ------------------------------------------------------------------
  // API helper
  // ------------------------------------------------------------------
  function handleAuthAndErrors(r) {
    if (r.status === 401 && !r.url.endsWith("/api/auth/me")) {
      window.location.href = "/login.html";
      throw new Error("Not authenticated");
    }
    if (!r.ok) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        var err = new Error(body.error || ("Request failed: " + r.status));
        err.status = r.status;
        err.body = body;
        throw err;
      });
    }
    return r.json();
  }

  var API = {
    get: function (path) {
      return fetch(path, { credentials: "same-origin" }).then(handleAuthAndErrors);
    },
    post: function (path, body) {
      return fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
        .then(handleAuthAndErrors);
    },
    put: function (path, body) {
      return fetch(path, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
        .then(handleAuthAndErrors);
    },
    del: function (path) {
      return fetch(path, { method: "DELETE", credentials: "same-origin" }).then(handleAuthAndErrors);
    }
  };

  // ------------------------------------------------------------------
  // small utilities
  // ------------------------------------------------------------------
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  // labels is optional {confirmLabel, cancelLabel} - this modal is shared by
  // every confirm dialog in the app, so its buttons default back to
  // "Cancel"/"Yes, continue" after each use rather than leaking a custom
  // label (e.g. "No, Dubai only") into the next, unrelated confirmation.
  function confirmDialog(message, labels) {
    return new Promise(function (resolve) {
      var overlay = document.getElementById("modalOverlay");
      document.getElementById("modalMessage").textContent = message;
      var confirmBtn = document.getElementById("modalConfirm");
      var cancelBtn = document.getElementById("modalCancel");
      var defaultConfirmLabel = confirmBtn.textContent;
      var defaultCancelLabel = cancelBtn.textContent;
      confirmBtn.textContent = (labels && labels.confirmLabel) || defaultConfirmLabel;
      cancelBtn.textContent = (labels && labels.cancelLabel) || defaultCancelLabel;
      overlay.classList.add("show");
      function cleanup(result) {
        overlay.classList.remove("show");
        confirmBtn.removeEventListener("click", onConfirm);
        cancelBtn.removeEventListener("click", onCancel);
        confirmBtn.textContent = defaultConfirmLabel;
        cancelBtn.textContent = defaultCancelLabel;
        resolve(result);
      }
      function onConfirm() { cleanup(true); }
      function onCancel() { cleanup(false); }
      confirmBtn.addEventListener("click", onConfirm);
      cancelBtn.addEventListener("click", onCancel);
    });
  }

  function fmt(n) {
    n = +n || 0;
    return n.toLocaleString("en-AE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function pct(n) { return ((+n || 0) * 100).toFixed(1) + "%"; }

  function escapeAttr(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function todayISO(d) { d = d || new Date(); return d.toISOString().slice(0, 10); }
  function addDays(iso, n) { var d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + n); return todayISO(d); }

  var COMPANY = {
    name: "Handyman.ae", legal: "GIJO Technical Services LLC",
    address: "P.O. Box 55152, Dubai, U.A.E.", licence: "1225534",
    email: "info@handyman.ae", web: "www.handyman.ae"
  };

  var DEFAULT_TERMS =
    "1. Prices are in AED and exclude VAT unless stated.\n" +
    "2. This quotation is valid for the period stated above.\n" +
    "3. Any additional works found during execution will be quoted separately and require written approval.\n" +
    "4. Payment terms: 50% on acceptance, balance on completion, unless otherwise agreed.\n" +
    "5. Warranty: 90 days on workmanship. Manufacturer warranty applies to supplied parts.\n" +
    "6. No warranty is provided on client-supplied materials.\n" +
    "7. Access, parking and building permits to be arranged by the client unless stated.\n" +
    "8. Labor, materials, waste disposal and transport charges are included unless stated otherwise.";

  // Pricing-engine defaults - mirrors pricing.py DEFAULTS. Overwritten by /api/settings on load.
  var SETTINGS = {
    hourlyRate: 250, transportFee: 125, callOutFee: 150, vatPct: 5,
    marginMinPct: 30, marginTargetPct: 40, marginUpperPct: 50, maxDiscountPct: 100,
    defaultMaterialMarkupPct: 50
  };

  var APPROVAL_THRESHOLD = 2000;

  var ITEM_KIND_LABELS = {
    material: "Material", staff_labour: "Staff Labour", outside_labour: "Outside Labour",
    fixed_service: "Fixed Service", project_management: "Project Management", other: "Other"
  };

  // Reflects the current (possibly admin-configured) SETTINGS.maxDiscountPct
  // into the discount input's own client-side bound, so typing an out-of-
  // range value is caught before Save round-trips to the server at all.
  function applySettingsToUI() {
    var maxDiscount = SETTINGS.maxDiscountPct;
    var discountNum = document.getElementById("discountPct");
    var discountRange = document.getElementById("discountRange");
    if (discountNum) discountNum.max = maxDiscount;
    if (discountRange) discountRange.max = maxDiscount;
    var hintVal = document.getElementById("maxDiscountHintVal");
    if (hintVal) hintVal.textContent = maxDiscount;
  }

  function marginBand(marginPctFraction) {
    var mp = marginPctFraction * 100;
    if (mp < SETTINGS.marginMinPct) return "CRITICAL";
    if (mp < SETTINGS.marginTargetPct) return "WARN";
    if (mp > SETTINGS.marginUpperPct) return "ABOVE TARGET";
    return "ON TARGET";
  }
  function bandClass(band) {
    return { "CRITICAL": "critical", "WARN": "warn", "ON TARGET": "target", "ABOVE TARGET": "above" }[band] || "";
  }

  // The Cost / Markup% / Sell three-way link for Material lines - mirrors
  // pricing.materials_markup_link() exactly. `edited` is "cost", "sell" or
  // "markupPct" - whichever field the user just typed into.
  function materialsMarkupLink(edited, cost, sell, markupPct) {
    cost = +cost || 0; sell = +sell || 0; markupPct = +markupPct || 0;
    if (edited === "sell") {
      markupPct = cost > 0 ? ((sell - cost) / cost) * 100 : 0;
    } else {
      sell = cost * (1 + markupPct / 100);
    }
    return { cost: cost, sell: sell, markupPct: markupPct };
  }

  // ------------------------------------------------------------------
  // Tabs
  // ------------------------------------------------------------------
  function initTabs() {
    document.querySelectorAll(".tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".tab").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        document.querySelectorAll(".tabpanel").forEach(function (p) { p.style.display = "none"; });
        document.getElementById("tab-" + btn.dataset.tab).style.display = "";
        if (btn.dataset.tab === "pricebook") loadPricebookV2();
        if (btn.dataset.tab === "saved") loadSaved();
        if (btn.dataset.tab === "home") loadHome();
        if (btn.dataset.tab === "templates") loadTemplates();
        if (btn.dataset.tab === "settings") loadCompanySettingsForm();
        if (btn.dataset.tab === "users") loadUsers();
        if (btn.dataset.tab === "amc") loadAmcAll();
        if (btn.dataset.tab === "amc-proposals") loadAmcProposalsAll();
      });
    });
  }

  function goToTab(name) {
    var btn = document.querySelector('.tab[data-tab="' + name + '"]');
    if (btn) btn.click();
  }

  // ------------------------------------------------------------------
  // Location / team switch (Dubai vs MAG City) - Materials, Labour, Fixed
  // Services and Suppliers are catalogued separately per team; Contractors
  // stay global since a subcontractor may serve either location.
  // ------------------------------------------------------------------
  var TEAM_STORAGE_KEY = "handyman_active_team_v1";
  var TEAM_LABELS = { dubai: "Dubai", magcity: "MAG City" };
  var ACTIVE_TEAM = localStorage.getItem(TEAM_STORAGE_KEY) || "dubai";

  function initTeamSwitch() {
    var wrap = document.getElementById("teamSwitch");
    if (!wrap) return;
    renderTeamSwitch();
    wrap.querySelectorAll(".team-pill").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.dataset.team === ACTIVE_TEAM) return;
        ACTIVE_TEAM = btn.dataset.team;
        localStorage.setItem(TEAM_STORAGE_KEY, ACTIVE_TEAM);
        renderTeamSwitch();
        toast(TEAM_LABELS[ACTIVE_TEAM] + " catalog active");
        onTeamChanged();
      });
    });
  }

  function renderTeamSwitch() {
    document.querySelectorAll("#teamSwitch .team-pill").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.team === ACTIVE_TEAM);
    });
    var lbl = document.getElementById("pbSuppliersTeamLabel");
    if (lbl) lbl.textContent = TEAM_LABELS[ACTIVE_TEAM];
  }

  function onTeamChanged() {
    if (document.getElementById("tab-home").style.display !== "none") {
      loadHome();
    }
    if (document.getElementById("tab-pricebook").style.display !== "none") {
      reloadPbMaterials(); reloadPbLabour(); reloadPbFixed(); reloadPbSuppliers(); reloadPbContractors();
    }
    if (document.getElementById("tab-builder").style.display !== "none") {
      loadCatalogIntoBuilder();
    }
    if (document.getElementById("tab-saved").style.display !== "none") {
      loadSaved();
    }
    if (document.getElementById("tab-amc").style.display !== "none") {
      loadAmcAll();
    }
    if (document.getElementById("tab-amc-proposals").style.display !== "none") {
      loadAmcProposalsAll();
      apLoadCommRates();
      apLoadHighlights();
      apLoadSavedPrice();
    }
  }

  // ==================================================================
  // NEW QUOTE (formerly "Quote Builder")
  // ==================================================================
  var STORAGE_KEY = "handyman_quote_draft_v4";
  var builder = null;
  var PB_CATALOG = { materials: [], labour: [], fixedServices: [], contractors: [] };

  var LAST_STAFF_TECH_KEY = "handyman_last_staff_tech_v1";
  function rememberLastStaffTech(staff, technician) {
    try { localStorage.setItem(LAST_STAFF_TECH_KEY, JSON.stringify({ staff: staff || "", technician: technician || "" })); } catch (e) {}
  }
  function lastStaffTech() {
    try { var raw = localStorage.getItem(LAST_STAFF_TECH_KEY); if (raw) return JSON.parse(raw); } catch (e) {}
    return { staff: "", technician: "" };
  }

  function defaultBuilderState() {
    var date = todayISO();
    var last = lastStaffTech();
    return {
      id: null, quoteNo: "(assigned on save)", quoteSeq: null, status: "Draft", revisionNumber: 1,
      date: date, validUntil: addDays(date, 14), staff: last.staff, technician: last.technician, preparedBy: CURRENT_USER,
      createdBy: CURRENT_USER,
      client: { name: "", phone: "", address: "", email: "" },
      duration: "", scope: "", internalNotes: "",
      items: [],
      transportQty: 1, transportFee: SETTINGS.transportFee,
      callOut: false, callOutFee: SETTINGS.callOutFee,
      discountPct: 0, overridePrice: 0, vatPct: SETTINGS.vatPct,
      terms: DEFAULT_TERMS
    };
  }

  function newItemRow(kind, desc, cost, sell, markupPct, qty) {
    return { kind: kind, desc: desc || "", cost: cost || 0, sell: sell || 0, markupPct: markupPct == null ? null : markupPct, qty: qty == null ? 1 : qty };
  }

  function itemTotal(it) { return (+it.sell || 0) * (+it.qty || 0); }
  function itemProfit(it) { return ((+it.sell || 0) - (+it.cost || 0)) * (+it.qty || 0); }

  function saveDraft() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(builder)); } catch (e) {} }
  function loadDraft() {
    try { var raw = localStorage.getItem(STORAGE_KEY); if (raw) return JSON.parse(raw); } catch (e) {}
    return null;
  }

  function isLocked() { return builder.status === "Sent to Jobber"; }

  // recalcBuilder() touches ~20 DOM nodes and rebuilds the status action bar;
  // saveDraft() serializes the whole quote to localStorage. Both are cheap
  // once, but a held-down number-input spin button fires an "input" event on
  // every 0.01 step - rapid-fire enough that this synchronous work starves
  // the browser's own repaint of the input box being spun, so it visibly
  // shows a stale/rounded digit while Total/Profit (computed fresh from the
  // real value) race ahead. Debouncing the expensive part lets the browser
  // catch up; each row's own Total/Profit cells still update immediately
  // since those are just two cheap textContent writes.
  var debouncedRecalcAndSave = debounce(function () { recalcBuilder(); saveDraft(); }, 120);

  // ---- unified, type-polymorphic items table ----
  function renderItemsTable() {
    var bodyEl = document.getElementById("itemsBody");
    bodyEl.innerHTML = "";
    var locked = isLocked();
    builder.items.forEach(function (item, idx) {
      var tr = document.createElement("tr");
      var isMaterial = item.kind === "material";
      var markupCell = isMaterial
        ? '<input type="number" step="1" min="0" data-k="markupPct" value="' + (item.markupPct == null ? SETTINGS.defaultMaterialMarkupPct : item.markupPct) + '" style="text-align:right">'
        : '<span style="color:var(--muted)">—</span>';
      tr.innerHTML =
        "<td>" + (idx + 1) + "</td>" +
        '<td><span class="item-kind-badge">' + escapeHtml(ITEM_KIND_LABELS[item.kind] || item.kind) + "</span></td>" +
        '<td><input type="text" data-k="desc" value="' + escapeAttr(item.desc) + '"></td>' +
        '<td class="num"><input type="number" step="0.01" min="0" data-k="cost" value="' + item.cost + '" style="text-align:right"></td>' +
        '<td class="num"><input type="number" step="1" min="0" data-k="qty" value="' + item.qty + '" style="text-align:right"></td>' +
        '<td class="num"><input type="number" step="0.01" min="0" data-k="sell" value="' + item.sell + '" style="text-align:right"></td>' +
        '<td class="num markup-cell">' + markupCell + "</td>" +
        '<td class="num" data-k="total">' + fmt(itemTotal(item)) + "</td>" +
        '<td class="num" data-k="profit">' + fmt(itemProfit(item)) + "</td>" +
        '<td><button class="row-del" title="Remove" data-idx="' + idx + '">&times;</button></td>';

      tr.querySelectorAll("input").forEach(function (inp) {
        inp.addEventListener("input", function () {
          var k = inp.getAttribute("data-k");
          if (k === "desc") {
            item.desc = inp.value;
          } else if (k === "markupPct" && isMaterial) {
            var linked = materialsMarkupLink("markupPct", item.cost, item.sell, +inp.value || 0);
            item.markupPct = linked.markupPct; item.sell = linked.sell;
            tr.querySelector('[data-k="sell"]').value = round2(item.sell);
          } else if (k === "sell") {
            item.sell = +inp.value || 0;
            if (isMaterial) {
              var linked2 = materialsMarkupLink("sell", item.cost, item.sell, item.markupPct);
              item.markupPct = linked2.markupPct;
              tr.querySelector('[data-k="markupPct"]').value = round2(item.markupPct);
            }
          } else if (k === "cost") {
            item.cost = +inp.value || 0;
            if (isMaterial) {
              var linked3 = materialsMarkupLink("cost", item.cost, item.sell, item.markupPct);
              item.sell = linked3.sell;
              tr.querySelector('[data-k="sell"]').value = round2(item.sell);
            }
          } else if (k === "qty") {
            item.qty = +inp.value || 0;
          }
          tr.querySelector('[data-k="total"]').textContent = fmt(itemTotal(item));
          tr.querySelector('[data-k="profit"]').textContent = fmt(itemProfit(item));
          debouncedRecalcAndSave();
        });
      });
      tr.querySelector(".row-del").addEventListener("click", function () {
        builder.items.splice(idx, 1);
        renderItemsTable();
        recalcBuilder();
        saveDraft();
      });
      if (locked) tr.querySelectorAll("input,button").forEach(function (el) { el.disabled = true; });
      bodyEl.appendChild(tr);
    });
  }
  function round2(n) { return Math.round((+n || 0) * 100) / 100; }

  function populateItemCatalogSelect(kindSelId, catSelId) {
    kindSelId = kindSelId || "itemKindSelect"; catSelId = catSelId || "itemCatalogSelect";
    var kind = document.getElementById(kindSelId).value;
    var sel = document.getElementById(catSelId);
    sel.innerHTML = "";
    var list = [];
    if (kind === "material") {
      list = PB_CATALOG.materials.map(function (m) {
        return { label: (m.category ? m.category + " - " : "") + m.itemName + " — AED " + fmt(m.defaultSell || m.cost || 0), cost: m.cost || 0, sell: m.defaultSell == null ? (m.cost || 0) : m.defaultSell, desc: m.itemName, id: m.id };
      });
    } else if (kind === "staff_labour" || kind === "outside_labour") {
      var wantType = kind === "staff_labour" ? "staff" : "outside";
      list = PB_CATALOG.labour.filter(function (l) { return l.labourType === wantType; }).map(function (l) {
        return { label: l.roleName + " — AED " + fmt(l.defaultSell || l.cost || 0), cost: l.cost || 0, sell: l.defaultSell == null ? (l.cost || 0) : l.defaultSell, desc: l.roleName, id: l.id };
      });
      if (kind === "outside_labour") {
        list = list.concat(flattenContractorRates().map(function (r) {
          return { label: r.label + " — AED " + (r.sell == null ? "TBD" : fmt(r.sell)), cost: r.cost || 0, sell: r.sell == null ? 0 : r.sell, desc: r.desc, id: r.id };
        }));
      }
    } else if (kind === "fixed_service") {
      list = PB_CATALOG.fixedServices.map(function (f) {
        return { label: (f.category ? f.category + " - " : "") + f.serviceName + " — AED " + fmt(f.standardSell || f.estimatedCost || 0), cost: f.estimatedCost || 0, sell: f.standardSell == null ? (f.estimatedCost || 0) : f.standardSell, desc: f.serviceName, id: f.id };
      });
    }
    if (!list.length) {
      var o = document.createElement("option");
      o.textContent = "(no Price Book items for this type yet)";
      o.disabled = true;
      sel.appendChild(o);
      return;
    }
    list.forEach(function (entry) {
      var o = document.createElement("option");
      o.textContent = entry.label;
      o.dataset.cost = entry.cost; o.dataset.sell = entry.sell; o.dataset.desc = entry.desc; o.dataset.refId = entry.id;
      sel.appendChild(o);
    });
  }

  function loadCatalogIntoBuilder() {
    return Promise.all([
      API.get("/api/pricebook/materials" + pbTeamQuery()).then(function (d) { PB_CATALOG.materials = d.materials; }),
      API.get("/api/pricebook/labour" + pbTeamQuery()).then(function (d) { PB_CATALOG.labour = d.labour; }),
      API.get("/api/pricebook/fixed-services" + pbTeamQuery()).then(function (d) { PB_CATALOG.fixedServices = d.fixedServices; }),
      API.get("/api/pricebook/contractors" + pbTeamQuery()).then(function (d) { PB_CATALOG.contractors = d.contractors; })
    ]).then(function () {
      populateItemCatalogSelect();
    }).catch(function () { toast("Could not reach the server for the price book"); });
  }

  // Flattens every contractor's rate-card rows into one list of {label, sell, cost, id}
  // entries, used to offer contractor rates as Outside Labour catalog items.
  function flattenContractorRates() {
    var out = [];
    (PB_CATALOG.contractors || []).forEach(function (c) {
      (c.pricing || []).forEach(function (p) {
        out.push({ label: c.name + " — " + p.label, sell: p.price, cost: null, desc: c.name + " — " + p.label, id: "contractor:" + c.id + ":" + p.id });
      });
    });
    return out;
  }

  // Looks up a wizard service's own Fixed Service / Labour Price Book entry
  // by name, for the rate the Guided Wizard / Quick Add should bill at.
  function lookupWizardServiceRate(serviceName) {
    var fs = PB_CATALOG.fixedServices.find(function (f) { return f.serviceName === serviceName; });
    if (fs && fs.standardSell != null) return { sell: fs.standardSell, cost: fs.estimatedCost || 0 };
    return null;
  }

  function sum(items, fn) { return items.reduce(function (s, it) { return s + fn(it); }, 0); }

  function recalcBuilder() {
    var itemsCostSubtotal = sum(builder.items, function (i) { return (+i.cost || 0) * (+i.qty || 0); });
    var itemsSellSubtotal = sum(builder.items, function (i) { return (+i.sell || 0) * (+i.qty || 0); });

    var transportQty = +builder.transportQty || 0;
    var transportFee = +builder.transportFee || 0;
    var vehicle = transportQty * transportFee;

    var callOut = !!builder.callOut;
    var callOutFee = +builder.callOutFee || 0;
    var callOutAmount = callOut ? callOutFee : 0;

    var vatPct = +builder.vatPct || 0;
    var discountPct = +builder.discountPct || 0;
    var overridePrice = +builder.overridePrice || 0;

    var costPrice = itemsCostSubtotal + vehicle;
    var gross = itemsSellSubtotal + vehicle + callOutAmount;
    var discountAmount = gross * (discountPct / 100);
    var netSelling = Math.max(0, gross - discountAmount);
    var sellingPrice = overridePrice > 0 ? overridePrice : netSelling;
    var profit = sellingPrice - costPrice;
    var markupPct = costPrice > 0 ? profit / costPrice : 0;
    var marginPct = sellingPrice > 0 ? profit / sellingPrice : 0;
    var band = marginBand(marginPct);
    var vatAmount = sellingPrice * (vatPct / 100);
    var grandTotal = sellingPrice + vatAmount;

    document.getElementById("itemsSellTotal").textContent = fmt(itemsSellSubtotal);
    document.getElementById("itemsProfitTotal").textContent = fmt(itemsSellSubtotal - itemsCostSubtotal);

    document.getElementById("sumItemsCost").textContent = "AED " + fmt(itemsCostSubtotal);
    document.getElementById("sumItemsSell").textContent = "AED " + fmt(itemsSellSubtotal);
    document.getElementById("sumVehicle").textContent = "AED " + fmt(vehicle);
    document.getElementById("sumCallOut").textContent = "AED " + fmt(callOutAmount);
    document.getElementById("sumCostPrice").textContent = "AED " + fmt(costPrice);
    document.getElementById("sumGross").textContent = "AED " + fmt(gross);
    document.getElementById("sumDiscountAmt").textContent = "AED " + fmt(discountAmount);
    document.getElementById("sumSellingPrice").textContent = "AED " + fmt(sellingPrice);
    document.getElementById("sumProfit").textContent = "AED " + fmt(profit);
    document.getElementById("sumMarkupPct").textContent = pct(markupPct);
    document.getElementById("sumMarginPct").textContent = pct(marginPct);
    document.getElementById("sumVat").textContent = "AED " + fmt(vatAmount);
    document.getElementById("sumGrand").textContent = "AED " + fmt(grandTotal);

    updateGauge(marginPct, band, markupPct, sellingPrice);
    renderStatusUI(sellingPrice);

    return {
      itemsCostSubtotal: itemsCostSubtotal, itemsSellSubtotal: itemsSellSubtotal, vehicle: vehicle,
      callOutAmount: callOutAmount, costPrice: costPrice, gross: gross, discountAmount: discountAmount,
      netSelling: netSelling, sellingPrice: sellingPrice, profit: profit, markupPct: markupPct,
      marginPct: marginPct, marginBand: band, vatAmount: vatAmount, grandTotal: grandTotal
    };
  }

  function updateGauge(marginPct, band, markupPct, sellingPrice) {
    var p = marginPct * 100;
    var cls = bandClass(band);

    var pctEl = document.getElementById("gaugeMarginPct");
    pctEl.textContent = p.toFixed(1) + "%";
    pctEl.className = "gauge-pct " + cls;

    document.getElementById("gaugeMarkupPct").textContent = "Mark-up " + (markupPct * 100).toFixed(1) + "%";

    var badgeEl = document.getElementById("gaugeBandBadge");
    badgeEl.textContent = band === "ON TARGET" ? "On target" : band === "ABOVE TARGET" ? "Above target" : band === "WARN" ? "Under target" : "Check this";
    badgeEl.className = "band-badge-lg " + cls;

    var marker = document.getElementById("gaugeMarker");
    marker.style.left = Math.max(0, Math.min(100, p)) + "%";
    marker.style.borderColor = { critical: "var(--red)", warn: "#C25E00", target: "var(--green)", above: "var(--blue)" }[cls];

    var min = SETTINGS.marginMinPct, target = SETTINGS.marginTargetPct, upper = SETTINGS.marginUpperPct;
    var msg;
    if (sellingPrice <= 0) {
      msg = "Add items to see your margin here.";
    } else if (band === "CRITICAL") {
      msg = "Margin is " + p.toFixed(1) + "%, against a minimum of " + min + "%. Review pricing before sending this quote.";
    } else if (band === "WARN") {
      msg = "Margin is " + p.toFixed(1) + "%, below your " + target + "-" + upper + "% target.";
    } else if (band === "ABOVE TARGET") {
      msg = "Margin is " + p.toFixed(1) + "%, above your " + target + "-" + upper + "% band. Fine if the job warrants it.";
    } else {
      msg = "Margin is " + p.toFixed(1) + "%, within your " + target + "-" + upper + "% target band.";
    }
    document.getElementById("gaugeMessage").textContent = msg;
  }

  // ---- status pill + workflow actions ----
  function statusPillClass(status) {
    return { "Draft": "draft", "Approval Required": "approval", "Sent to Jobber": "sent" }[status] || "draft";
  }

  function renderStatusUI(sellingPrice) {
    var pillEl = document.getElementById("quoteStatusPill");
    pillEl.textContent = builder.status + (builder.revisionNumber > 1 ? " (Revision " + builder.revisionNumber + ")" : "");
    pillEl.className = "status-pill " + statusPillClass(builder.status);
    document.getElementById("quoteLockedHint").style.display = isLocked() ? "" : "none";
    document.getElementById("btnSaveQuote").style.display = isLocked() ? "none" : "";

    var actions = document.getElementById("statusActions");
    actions.innerHTML = "";
    if (!builder.id) {
      actions.innerHTML = '<span class="hint">Save the quote to unlock approval / send-to-Jobber actions.</span>';
      return;
    }

    function addBtn(label, cls, handler) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "btn " + cls; b.textContent = label;
      b.addEventListener("click", handler);
      actions.appendChild(b);
    }

    // Note: a Draft is auto-escalated to Approval Required by the server the
    // moment its selling price crosses the threshold (see save_quote()), so
    // a saved Draft should never actually be sitting here over threshold -
    // "Send to Jobber" is always safe to offer for a Draft. "Submit for
    // Approval Anyway" stays available for anyone who wants a second set of
    // eyes on a smaller quote even though it isn't required.
    if (builder.status === "Draft") {
      addBtn("Send to Jobber", "btn-primary", function () { runStatusAction("send_to_jobber"); });
      addBtn("Submit for Approval Anyway", "btn-ghost", function () { runStatusAction("submit_for_approval"); });
    } else if (builder.status === "Approval Required") {
      if (CURRENT_USER_ROLE === "admin") {
        addBtn("Approve & Send to Jobber", "btn-primary", function () { runStatusAction("approve_and_send"); });
        addBtn("Return to Draft", "btn-ghost", function () { runStatusAction("return_to_draft"); });
      } else {
        var note = document.createElement("span");
        note.className = "hint";
        note.textContent = "Awaiting an admin's approval before this can be sent to Jobber.";
        actions.appendChild(note);
        if (builder.createdBy === CURRENT_USER) addBtn("Return to Draft", "btn-ghost", function () { runStatusAction("return_to_draft"); });
      }
    } else if (builder.status === "Sent to Jobber") {
      addBtn("Create Revision", "btn-orange", function () { doRevise(); });
    }

    // Duplicate as New Quote makes sense from any status, not just once a
    // quote is locked - it's always the very last action in the row.
    addBtn("Duplicate as New Quote", "btn-outline", function () { doDuplicate(); });
  }

  function loadQuoteHistory() {
    var details = document.getElementById("historyDetails");
    if (!builder.id) { details.style.display = "none"; return; }
    details.style.display = "";
    API.get("/api/quotes/" + builder.id + "/audit").then(function (data) {
      var list = document.getElementById("historyList");
      if (!data.entries.length) { list.innerHTML = '<p class="hint">No history yet.</p>'; return; }
      var html = '<table><thead><tr><th>When</th><th>Event</th><th>By</th><th>Details</th></tr></thead><tbody>';
      data.entries.forEach(function (e) {
        html += "<tr><td>" + escapeHtml((e.at || "").replace("T", " ")) + "</td><td>" + escapeHtml(e.eventType) + "</td>" +
          "<td>" + escapeHtml(e.actorEmail || "—") + "</td><td>" + escapeHtml(e.summary || "—") + "</td></tr>";
      });
      html += "</tbody></table>";
      list.innerHTML = html;
    }).catch(function () { document.getElementById("historyList").innerHTML = '<p class="hint">Could not load history.</p>'; });
  }

  function runStatusAction(action) {
    API.post("/api/quotes/" + builder.id + "/status", { action: action }).then(function (updated) {
      loadQuoteIntoBuilder(updated);
      toast("Quote status: " + updated.status);
    }).catch(function (e) { toast(e.message || "Could not update status"); });
  }

  function doRevise() {
    confirmDialog("Create a new editable revision of this quote? The original stays as the historical record.").then(function (ok) {
      if (!ok) return;
      API.post("/api/quotes/" + builder.id + "/revise").then(function (rev) {
        loadQuoteIntoBuilder(rev);
        toast("Revision created — this is now Draft " + rev.quoteNo);
      }).catch(function (e) { toast(e.message || "Could not create revision"); });
    });
  }

  function doDuplicate(quoteId) {
    var id = quoteId || builder.id;
    API.post("/api/quotes/" + id + "/duplicate").then(function (dup) {
      loadQuoteIntoBuilder(dup);
      goToTab("builder");
      toast("Duplicated as new quote " + dup.quoteNo);
    }).catch(function (e) { toast(e.message || "Could not duplicate"); });
  }

  function fillBuilderFields() {
    document.getElementById("quoteNo").value = builder.quoteNo || "";
    document.getElementById("quoteDate").value = builder.date || "";
    document.getElementById("validUntil").value = builder.validUntil || "";
    document.getElementById("staff").value = builder.staff || "";
    document.getElementById("technician").value = builder.technician || "";
    document.getElementById("preparedByDisplay").value = builder.preparedBy || "";
    document.getElementById("clientName").value = builder.client.name || "";
    document.getElementById("clientPhone").value = builder.client.phone || "";
    document.getElementById("clientAddress").value = builder.client.address || "";
    document.getElementById("clientEmail").value = builder.client.email || "";
    document.getElementById("duration").value = builder.duration || "";
    document.getElementById("scope").value = builder.scope || "";
    document.getElementById("internalNotes").value = builder.internalNotes || "";
    document.getElementById("terms").value = builder.terms || "";

    document.getElementById("discountRange").value = builder.discountPct;
    document.getElementById("transportQty").value = builder.transportQty;
    document.getElementById("transportFee").value = builder.transportFee;
    document.getElementById("callOutFee").value = builder.callOutFee;
    document.getElementById("callOut").checked = !!builder.callOut;
    document.getElementById("discountPct").value = builder.discountPct;
    document.getElementById("overridePrice").value = builder.overridePrice;
    document.getElementById("vatPct").value = builder.vatPct;

    var locked = isLocked();
    document.querySelectorAll("#tab-builder input, #tab-builder textarea, #tab-builder select").forEach(function (el) {
      if (el.id === "quoteNo" || el.id === "preparedByDisplay") return;
      el.disabled = locked;
    });
  }

  function bindBuilderFields() {
    var map = {
      quoteDate: "date", validUntil: "validUntil", staff: "staff", technician: "technician",
      clientName: ["client", "name"], clientPhone: ["client", "phone"],
      clientAddress: ["client", "address"], clientEmail: ["client", "email"],
      duration: "duration", scope: "scope", internalNotes: "internalNotes", terms: "terms"
    };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      var path = map[id];
      el.addEventListener("input", function () {
        if (Array.isArray(path)) builder[path[0]][path[1]] = el.value;
        else builder[path] = el.value;
        saveDraft();
      });
    });

    var numericFields = ["transportQty", "transportFee", "callOutFee", "discountPct", "overridePrice", "vatPct"];
    numericFields.forEach(function (id) {
      document.getElementById(id).addEventListener("input", function () {
        builder[id] = +this.value || 0;
        if (id === "discountPct") document.getElementById("discountRange").value = builder[id];
        debouncedRecalcAndSave();
      });
    });
    document.getElementById("discountRange").addEventListener("input", function () {
      builder.discountPct = +this.value || 0;
      document.getElementById("discountPct").value = builder.discountPct;
      debouncedRecalcAndSave();
    });
    document.getElementById("callOut").addEventListener("change", function () {
      builder.callOut = this.checked;
      recalcBuilder();
      saveDraft();
    });
  }

  function initCollapsibleCards() {
    document.querySelectorAll(".card[data-card]").forEach(function (card) {
      var key = "handyman_card_" + card.dataset.card;
      var stored = localStorage.getItem(key);
      var collapsed = stored != null ? stored === "1" : card.dataset.default === "collapsed";
      card.classList.toggle("collapsed", collapsed);
      var btn = card.querySelector(".card-toggle");
      if (!btn) return;
      btn.addEventListener("click", function () {
        var isCollapsed = card.classList.toggle("collapsed");
        localStorage.setItem(key, isCollapsed ? "1" : "0");
      });
    });
  }

  function buildWhatsAppText() {
    var t = recalcBuilder();
    var lines = [];
    lines.push("*" + COMPANY.name + " — Quotation*");
    lines.push(builder.quoteNo);
    lines.push("");
    if (builder.client.name) lines.push("Client: " + builder.client.name);
    if (builder.client.address) lines.push("Property: " + builder.client.address);
    lines.push("");
    if (builder.scope) { lines.push("*Scope of Work:*"); lines.push(builder.scope); lines.push(""); }
    if (builder.duration) lines.push("Estimated Duration: " + builder.duration);
    lines.push("");
    lines.push("Selling Price: AED " + fmt(t.sellingPrice));
    if (t.discountAmount) lines.push("Discount: -AED " + fmt(t.discountAmount));
    lines.push("VAT (" + builder.vatPct + "%): AED " + fmt(t.vatAmount));
    lines.push("*Grand Total: AED " + fmt(t.grandTotal) + "*");
    lines.push("");
    lines.push("Valid until: " + builder.validUntil);
    return lines.join("\n");
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return Promise.reject(new Error("Clipboard not available"));
  }

  // ------------------------------------------------------------------
  // Jobber Entry Helper
  // ------------------------------------------------------------------
  function copyChip(label, value, extraClass) {
    return '<button type="button" class="copy-chip ' + (extraClass || "") + '" data-copy="' + escapeAttr(value) + '">' +
      '<span class="chip-label">' + escapeHtml(label) + '</span><span class="chip-value">' + escapeHtml(value === "" || value == null ? "—" : value) + "</span></button>";
  }

  function renderJobberHelper(q) {
    var body = document.getElementById("jobberBody");
    var html = "";

    html += '<div class="jobber-section"><h4>Client</h4><div class="jobber-row">' +
      copyChip("Client Name", q.client.name, "chip-desc") +
      copyChip("Phone", q.client.phone, "chip-qty") +
      copyChip("Email", q.client.email, "chip-qty") +
      "</div><div class=\"jobber-row\">" +
      copyChip("Property", q.client.address, "chip-desc") +
      copyChip("Quote No.", q.quoteNo, "chip-qty") +
      "</div></div>";

    if (q.scope) {
      html += '<div class="jobber-section"><h4>Scope / Job Description</h4><div class="jobber-row">' +
        copyChip("Scope of Work (full text)", q.scope, "chip-desc") + "</div></div>";
    }

    if (q.items && q.items.length) {
      html += '<div class="jobber-section"><h4>Items</h4><p class="jobber-hint">Click each chip to copy just that value, then paste it into the matching Jobber field.</p>';
      q.items.forEach(function (it) {
        html += '<div class="jobber-row">' +
          copyChip("Description", it.desc, "chip-desc") +
          copyChip("Qty", it.qty, "chip-qty") +
          copyChip("Sell Price", it.sell, "chip-price") +
          "</div>";
      });
      html += "</div>";
    }

    html += '<div class="jobber-section"><h4>Totals</h4><div class="jobber-row">' +
      copyChip("Grand Total (AED)", fmt(q.grandTotal), "chip-qty") + "</div></div>";

    body.innerHTML = html;
    body.querySelectorAll(".copy-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var value = chip.dataset.copy;
        copyToClipboard(value).then(function () {
          chip.classList.add("copied");
          setTimeout(function () { chip.classList.remove("copied"); }, 900);
          toast("Copied: " + (value.length > 40 ? value.slice(0, 40) + "…" : value));
        }).catch(function () { toast("Could not copy — select the text manually"); });
      });
    });
  }

  function openJobberHelper(q) {
    renderJobberHelper(q);
    document.getElementById("jobberOverlay").classList.add("show");
  }
  function closeJobberHelper() {
    document.getElementById("jobberOverlay").classList.remove("show");
  }
  function wireJobberHelper() {
    document.getElementById("jobberClose").addEventListener("click", closeJobberHelper);
    document.getElementById("jobberOverlay").addEventListener("click", function (e) {
      if (e.target === this) closeJobberHelper();
    });
    document.getElementById("btnJobberHelperBuilder").addEventListener("click", function () {
      var t = recalcBuilder();
      openJobberHelper({
        client: builder.client, quoteNo: builder.quoteNo, scope: builder.scope,
        items: builder.items, grandTotal: t.grandTotal
      });
    });
  }

  function wireBuilderActions() {
    document.getElementById("addItemBlank").addEventListener("click", function () {
      var kind = document.getElementById("itemKindSelect").value;
      var markupPct = kind === "material" ? SETTINGS.defaultMaterialMarkupPct : null;
      builder.items.push(newItemRow(kind, "", 0, 0, markupPct, 1));
      renderItemsTable(); recalcBuilder(); saveDraft();
    });
    document.getElementById("addItemFromCatalog").addEventListener("click", function () {
      var kind = document.getElementById("itemKindSelect").value;
      var sel = document.getElementById("itemCatalogSelect");
      var opt = sel.options[sel.selectedIndex];
      if (!opt || opt.disabled) { toast("No Price Book item selected"); return; }
      var cost = +opt.dataset.cost || 0, sellVal = +opt.dataset.sell || 0;
      var markupPct = kind === "material" ? (cost > 0 ? ((sellVal - cost) / cost) * 100 : SETTINGS.defaultMaterialMarkupPct) : null;
      var row = newItemRow(kind, opt.dataset.desc, cost, sellVal, markupPct, 1);
      row.priceBookRefId = opt.dataset.refId;
      builder.items.push(row);
      renderItemsTable(); recalcBuilder(); saveDraft();
    });
    document.getElementById("itemKindSelect").addEventListener("change", function () { populateItemCatalogSelect(); });

    document.getElementById("btnPrint").addEventListener("click", function () { window.print(); });
    document.getElementById("btnWhatsapp").addEventListener("click", function () {
      copyToClipboard(buildWhatsAppText())
        .then(function () { toast("Quote copied — paste into WhatsApp"); })
        .catch(function () { toast("Could not copy automatically — select text manually"); });
    });
    document.getElementById("btnSaveQuote").addEventListener("click", function () {
      recalcBuilder();
      var payload = JSON.parse(JSON.stringify(builder));
      payload.team = ACTIVE_TEAM;
      var req = builder.id ? API.put("/api/quotes/" + builder.id, payload) : API.post("/api/quotes", payload);
      req.then(function (saved) {
        loadQuoteIntoBuilder(saved);
        rememberLastStaffTech(saved.staff, saved.technician);
        loadAutocompleteLists();
        toast("Quote saved — " + saved.quoteNo);
      }).catch(function (e) {
        toast(e.message || "Could not save quote — is the server running?");
      });
    });
    document.getElementById("btnNew").addEventListener("click", function () {
      confirmDialog("Start a new quote? Unsaved changes to the current quote will be lost.").then(function (ok) {
        if (!ok) return;
        builder = defaultBuilderState();
        saveDraft();
        initBuilderView();
        toast("New quote started");
      });
    });
  }

  function initBuilderView() {
    fillBuilderFields();
    renderItemsTable();
    recalcBuilder();
    loadQuoteHistory();
  }

  function loadQuoteIntoBuilder(full) {
    builder = {
      id: full.id, quoteNo: full.quoteNo, quoteSeq: full.quoteSeq, status: full.status || "Draft",
      revisionNumber: full.revisionNumber || 1,
      date: full.date, validUntil: full.validUntil,
      staff: full.staff, technician: full.technician, preparedBy: full.preparedBy, createdBy: full.createdBy,
      client: full.client || { name: "", phone: "", address: "", email: "" },
      duration: full.duration, scope: full.scope, internalNotes: full.internalNotes,
      items: (full.items || []).map(function (i) { return newItemRow(i.kind, i.desc, i.cost, i.sell, i.markupPct, i.qty); }),
      transportQty: full.transportQty == null ? 1 : full.transportQty,
      transportFee: full.transportFee == null ? SETTINGS.transportFee : full.transportFee,
      callOut: !!full.callOut,
      callOutFee: full.callOutFee == null ? SETTINGS.callOutFee : full.callOutFee,
      discountPct: full.discountPct || 0,
      overridePrice: full.overridePrice || 0,
      vatPct: full.vatPct == null ? SETTINGS.vatPct : full.vatPct,
      terms: full.terms || DEFAULT_TERMS
    };
    saveDraft();
    initBuilderView();
  }

  function initBuilder() {
    // a locally-cached draft belongs to whichever user last edited it - on a
    // shared computer, restoring it for a DIFFERENT logged-in user would leak
    // their unsaved client details/items/preparedBy into this session.
    var draft = loadDraft();
    if (draft && draft.createdBy !== CURRENT_USER) draft = null;
    builder = draft || defaultBuilderState();
    document.getElementById("companyFooter").textContent =
      COMPANY.legal + " · " + COMPANY.address + " · Licence " + COMPANY.licence + " · " + COMPANY.email + " · " + COMPANY.web;
    var printName = document.getElementById("printHeaderName");
    var printSub = document.getElementById("printHeaderSub");
    if (printName) printName.textContent = COMPANY.name;
    if (printSub) printSub.textContent = COMPANY.legal + " · " + COMPANY.address + " · " + COMPANY.email + " · " + COMPANY.web;
    bindBuilderFields();
    wireBuilderActions();
    initBuilderView();
    loadCatalogIntoBuilder();
    loadAutocompleteLists();
  }

  // Technician / Client / Property autocomplete, sourced from prior saved
  // quotes (most recently used first).
  function loadAutocompleteLists() {
    API.get("/api/quotes/autocomplete").then(function (d) {
      fillDatalist("techList", d.technicians);
      fillDatalist("clientList", d.clients);
      fillDatalist("propertyList", d.properties);
    }).catch(function () {});
  }
  function fillDatalist(id, values) {
    var el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = (values || []).map(function (v) { return '<option value="' + escapeAttr(v) + '">'; }).join("");
  }

  // ==================================================================
  // GUIDED SERVICE WIZARD
  // ==================================================================
  var WIZARD_DATA = [];
  var wizardState = { step: "category", categoryId: null, serviceId: null, answers: {} };
  var HOURS_PER_DAY = 8;

  function loadWizardData() {
    return API.get("/wizard-fields.json").then(function (data) {
      WIZARD_DATA = data;
      populateQuickServiceSelect();
    }).catch(function () { toast("Could not load the guided wizard's service list"); });
  }

  function openWizard() {
    wizardState = { step: "category", categoryId: null, serviceId: null, answers: {} };
    renderWizard();
    document.getElementById("wizardOverlay").classList.add("show");
  }
  function closeWizard() {
    document.getElementById("wizardOverlay").classList.remove("show");
  }

  function findCategory(id) { return WIZARD_DATA.find(function (c) { return c.id === id; }); }
  function findService(cat, id) { return cat.services.find(function (s) { return s.id === id; }); }

  function renderWizard() {
    if (wizardState.step === "category") renderWizardCategoryStep();
    else if (wizardState.step === "service") renderWizardServiceStep();
    else renderWizardDetailsStep();
  }

  function renderWizardCategoryStep() {
    document.getElementById("wizardTitle").textContent = "Which trade?";
    var body = document.getElementById("wizardBody");
    body.innerHTML = '<p class="tile-hint">Pick the category that matches the work, then the specific service.</p><div class="tile-grid" id="wizardTiles"></div>';
    var grid = document.getElementById("wizardTiles");
    WIZARD_DATA.forEach(function (cat) {
      var tile = document.createElement("div");
      tile.className = "tile";
      tile.innerHTML = '<span class="tile-name">' + escapeHtml(cat.name) + '</span><span class="tile-count">' + cat.services.length + " service" + (cat.services.length === 1 ? "" : "s") + "</span>";
      tile.addEventListener("click", function () {
        wizardState.categoryId = cat.id;
        wizardState.step = "service";
        renderWizard();
      });
      grid.appendChild(tile);
    });
  }

  function renderWizardServiceStep() {
    var cat = findCategory(wizardState.categoryId);
    document.getElementById("wizardTitle").textContent = cat.name + " — which service?";
    var body = document.getElementById("wizardBody");
    body.innerHTML = '<p class="tile-hint">Only the questions relevant to this service will be shown.</p><div class="tile-grid" id="wizardTiles"></div><div class="wizard-actions"><button class="btn btn-ghost" id="wizardBack">&larr; Back</button><span></span></div>';
    var grid = document.getElementById("wizardTiles");
    cat.services.forEach(function (svc) {
      var tile = document.createElement("div");
      tile.className = "tile";
      tile.innerHTML = '<span class="tile-name">' + escapeHtml(svc.name) + '</span><span class="tile-count">' + svc.q.length + " question" + (svc.q.length === 1 ? "" : "s") + "</span>";
      tile.addEventListener("click", function () {
        wizardState.serviceId = svc.id;
        wizardState.answers = {};
        svc.q.forEach(function (f) { wizardState.answers[f.id] = f.def; });
        wizardState.step = "details";
        renderWizard();
      });
      grid.appendChild(tile);
    });
    document.getElementById("wizardBack").addEventListener("click", function () {
      wizardState.step = "category";
      renderWizard();
    });
  }

  function renderWizardField(f) {
    var row = document.createElement("div");
    if (f.type === "toggle") {
      row.className = "field-row toggle-row";
      row.innerHTML =
        "<label>" + escapeHtml(f.label) + '</label><label class="toggle-switch"><input type="checkbox" data-fid="' + f.id + '"' + (wizardState.answers[f.id] ? " checked" : "") + '><span class="toggle-slider"></span></label>';
      row.querySelector("input").addEventListener("change", function () { wizardState.answers[f.id] = this.checked; });
    } else if (f.type === "select") {
      row.className = "field-row";
      var opts = (f.opts || []).map(function (o) {
        return '<option value="' + escapeAttr(o) + '"' + (o === wizardState.answers[f.id] ? " selected" : "") + ">" + escapeHtml(o) + "</option>";
      }).join("");
      row.innerHTML = "<label>" + escapeHtml(f.label) + '</label><select data-fid="' + f.id + '">' + opts + "</select>";
      row.querySelector("select").addEventListener("input", function () { wizardState.answers[f.id] = this.value; });
    } else if (f.type === "number") {
      row.className = "field-row";
      row.innerHTML = "<label>" + escapeHtml(f.label) + '</label><input type="number" step="' + (f.step || 1) + '" data-fid="' + f.id + '" value="' + (wizardState.answers[f.id] == null ? "" : wizardState.answers[f.id]) + '">';
      row.querySelector("input").addEventListener("input", function () { wizardState.answers[f.id] = +this.value || 0; });
    } else {
      row.className = "field-row";
      row.innerHTML = "<label>" + escapeHtml(f.label) + '</label><input type="text" data-fid="' + f.id + '" value="' + escapeAttr(wizardState.answers[f.id] || "") + '">';
      row.querySelector("input").addEventListener("input", function () { wizardState.answers[f.id] = this.value; });
    }
    return row;
  }

  function renderWizardDetailsStep() {
    var cat = findCategory(wizardState.categoryId);
    var svc = findService(cat, wizardState.serviceId);
    document.getElementById("wizardTitle").textContent = svc.name;
    var body = document.getElementById("wizardBody");
    body.innerHTML = "";
    var header = document.createElement("div");
    header.className = "job-header";
    header.innerHTML = '<span class="section-icon">📋</span><div><strong>' + escapeHtml(svc.name) + "</strong><span class=\"section-sub\">" + escapeHtml(cat.name) + "</span></div>";
    body.appendChild(header);
    svc.q.forEach(function (f) { body.appendChild(renderWizardField(f)); });

    var actions = document.createElement("div");
    actions.className = "wizard-actions";
    actions.innerHTML = '<button class="btn btn-ghost" id="wizardBack">&larr; Back</button><button class="btn btn-orange" id="wizardSubmit">Add to Quote</button>';
    body.appendChild(actions);
    document.getElementById("wizardBack").addEventListener("click", function () { wizardState.step = "service"; renderWizard(); });
    document.getElementById("wizardSubmit").addEventListener("click", submitWizard);
  }

  function computeWizardQty(svc, answers) {
    var hasField = function (id) { return svc.q.some(function (f) { return f.id === id; }); };
    var techs = hasField("techs") ? (+answers.techs || 1) : 1;
    if (hasField("ppm") && hasField("hoursPer")) {
      return (+answers.ppm || 0) * (+answers.hoursPer || 0) * techs;
    }
    if (hasField("hours")) {
      return (+answers.hours || 0) * techs;
    }
    if (hasField("days")) {
      return (+answers.days || 0) * HOURS_PER_DAY * techs;
    }
    return 2 * techs;
  }

  // Shared by the full wizard and Quick Add. Adds one Staff Labour line for
  // the service (cost defaults to half the sell rate - there's no per-line
  // markup control on labour, so this just gives a sane starting margin the
  // tech can adjust), plus any material a toggled/answered question suggests.
  function addServiceToQuote(cat, svc, answers) {
    var qty = computeWizardQty(svc, answers);
    var priced = lookupWizardServiceRate(svc.name);
    var sellRate = priced ? priced.sell : (SETTINGS.hourlyRate || 250);
    var costRate = priced ? priced.cost : sellRate * 0.5;
    var desc = cat.name + " - " + svc.name;

    builder.items.push(newItemRow("staff_labour", desc, costRate, sellRate, null, qty));
    renderItemsTable();

    var addedMaterials = 0;
    svc.q.forEach(function (f) {
      if (!f.suggest || !f.suggest.length) return;
      var answer = answers[f.id];
      var triggered = f.type === "toggle" ? !!answer : (answer !== f.def && answer !== "" && answer != null);
      if (!triggered) return;
      f.suggest.forEach(function (name) {
        builder.items.push(newItemRow("material", name + " (suggested)", 0, 0, SETTINGS.defaultMaterialMarkupPct, 1));
        addedMaterials++;
      });
    });
    if (addedMaterials) renderItemsTable();

    recalcBuilder();
    saveDraft();
    var rateNote = priced ? " at your Price Book rate (AED " + fmt(sellRate) + "/hr)" : " at the default rate (AED " + fmt(sellRate) + "/hr — set one in Price Book to use it here instead)";
    toast("Added " + svc.name + " (" + qty + " hrs)" + rateNote + (addedMaterials ? " + " + addedMaterials + " suggested material(s)" : "") + " — review before saving.");
  }

  function submitWizard() {
    var cat = findCategory(wizardState.categoryId);
    var svc = findService(cat, wizardState.serviceId);
    addServiceToQuote(cat, svc, wizardState.answers);
    closeWizard();
  }

  function populateQuickServiceSelect() {
    var sel = document.getElementById("quickServiceSelect");
    if (!sel) return;
    sel.innerHTML = "";
    WIZARD_DATA.forEach(function (cat) {
      var og = document.createElement("optgroup");
      og.label = cat.name;
      cat.services.forEach(function (svc) {
        var o = document.createElement("option");
        o.value = cat.id + "|" + svc.id;
        o.textContent = svc.name;
        og.appendChild(o);
      });
      sel.appendChild(og);
    });
  }

  function wireWizardTrigger() {
    document.getElementById("btnOpenWizard").addEventListener("click", openWizard);
    document.getElementById("wizardClose").addEventListener("click", closeWizard);
    document.getElementById("wizardOverlay").addEventListener("click", function (e) {
      if (e.target === this) closeWizard();
    });
    document.getElementById("btnQuickAddService").addEventListener("click", function () {
      var sel = document.getElementById("quickServiceSelect");
      var opt = sel.options[sel.selectedIndex];
      if (!opt) { toast("The service list hasn't loaded yet — try again in a moment"); return; }
      var parts = opt.value.split("|");
      var cat = findCategory(parts[0]);
      var svc = findService(cat, parts[1]);
      var answers = {};
      svc.q.forEach(function (f) { answers[f.id] = f.def; });
      addServiceToQuote(cat, svc, answers);
    });
  }

  // ==================================================================
  // PRICE BOOK v2 - Materials / Labour / Fixed Services
  // ==================================================================
  function loadPricebookV2() {
    reloadPbMaterials();
    reloadPbLabour();
    reloadPbFixed();
    reloadPbSuppliers();
    reloadPbContractors();
  }

  function loadUsers() {
    API.get("/api/users").then(function (d) { renderUsers(d.users); }).catch(function () { toast("Could not load users"); });
    API.get("/api/admin-allowlist").then(function (d) { renderAllowlist(d.allowlist); }).catch(function () {});
  }

  function renderUsers(users) {
    var body = document.getElementById("usersBody");
    body.innerHTML = "";
    document.getElementById("usersEmpty").style.display = users.length ? "none" : "";
    users.forEach(function (u) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(u.email) + "</td><td>" + escapeHtml(u.name || "—") + "</td>" +
        "<td>" + escapeHtml((u.last_login_at || "—").replace("T", " ")) + "</td><td></td><td></td>";
      var roleCell = tr.children[3];
      var sel = document.createElement("select");
      ["staff", "admin"].forEach(function (r) {
        var o = document.createElement("option");
        o.value = r; o.textContent = r === "admin" ? "Admin" : "Staff";
        if (u.role === r) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        var newRole = sel.value;
        confirmDialog("Change " + u.email + "'s role from " + u.role + " to " + newRole + "?").then(function (ok) {
          if (!ok) { sel.value = u.role; return; }
          API.put("/api/users/" + u.id + "/role", { role: newRole }).then(function () {
            toast(u.email + " is now " + newRole);
            loadUsers();
          }).catch(function (e) { toast(e.message || "Could not update role"); sel.value = u.role; });
        });
      });
      roleCell.appendChild(sel);

      var actionsCell = tr.lastElementChild;
      var delBtn = document.createElement("button");
      delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete";
      delBtn.addEventListener("click", function () {
        confirmDialog("Remove " + u.email + "? They can be re-added later, but this cannot be undone right now.").then(function (ok) {
          if (!ok) return;
          API.del("/api/users/" + u.id).then(function () {
            toast(u.email + " removed");
            loadUsers();
          }).catch(function (e) { toast(e.message || "Could not remove user"); });
        });
      });
      actionsCell.appendChild(delBtn);
      body.appendChild(tr);
    });
  }

  function renderAllowlist(rows) {
    var out = document.getElementById("allowlistList");
    if (!rows.length) { out.innerHTML = ""; return; }
    var html = '<p class="hint" style="margin-bottom:8px">Pre-authorized as admin on first sign-in:</p><ul style="margin:0;padding-left:18px">';
    rows.forEach(function (r) { html += "<li>" + escapeHtml(r.email) + "</li>"; });
    html += "</ul>";
    out.innerHTML = html;
  }

  function wireUsers() {
    document.getElementById("btnAddUser").addEventListener("click", function () {
      var nameEl = document.getElementById("newUserName");
      var emailEl = document.getElementById("newUserEmail");
      var roleEl = document.getElementById("newUserRole");
      var email = emailEl.value.trim();
      if (!email) { toast("Enter an email first"); return; }
      API.post("/api/users", { name: nameEl.value.trim(), email: email, role: roleEl.value }).then(function () {
        toast(email + " added as " + roleEl.value);
        nameEl.value = ""; emailEl.value = ""; roleEl.value = "staff";
        loadUsers();
      }).catch(function (e) { toast(e.message || "Could not add user"); });
    });
  }

  var SETTINGS_FIELD_IDS = {
    vatPct: "setVatPct", maxDiscountPct: "setMaxDiscountPct", hourlyRate: "setHourlyRate",
    defaultMaterialMarkupPct: "setDefaultMaterialMarkupPct", transportFee: "setTransportFee",
    callOutFee: "setCallOutFee", marginMinPct: "setMarginMinPct", marginTargetPct: "setMarginTargetPct",
    marginUpperPct: "setMarginUpperPct"
  };

  function loadCompanySettingsForm() {
    API.get("/api/settings").then(function (s) {
      Object.keys(SETTINGS_FIELD_IDS).forEach(function (key) {
        var el = document.getElementById(SETTINGS_FIELD_IDS[key]);
        if (el && s[key] != null) el.value = s[key];
      });
    }).catch(function () { toast("Could not load company settings"); });
  }

  function wireCompanySettings() {
    document.getElementById("btnSaveSettings").addEventListener("click", function () {
      var updates = {};
      Object.keys(SETTINGS_FIELD_IDS).forEach(function (key) {
        var el = document.getElementById(SETTINGS_FIELD_IDS[key]);
        if (el && el.value !== "") updates[key] = +el.value;
      });
      API.put("/api/settings", updates).then(function (s) {
        Object.assign(SETTINGS, s);
        applySettingsToUI();
        toast("Company settings saved");
      }).catch(function (e) { toast(e.message || "Could not save settings"); });
    });
  }

  function pbTeamQuery(extra) {
    var params = ["team=" + encodeURIComponent(ACTIVE_TEAM)];
    if (extra) params.push(extra);
    return "?" + params.join("&");
  }

  function otherTeam(team) {
    return team === "dubai" ? "magcity" : "dubai";
  }

  // After a material/labour/fixed-service/supplier/contractor is created for
  // ACTIVE_TEAM, offer to create the same row for the other team too -
  // Dubai and MAG City each keep their own catalog, so this is the only way
  // one add creates both without re-typing everything a second time.
  // createForTeam(team) must return the same API.post(...) promise used for
  // the original create, just aimed at `team` instead.
  function maybeDuplicateToOtherTeam(itemLabel, createForTeam) {
    var other = otherTeam(ACTIVE_TEAM);
    return confirmDialog(
      "This " + itemLabel + " has been added to " + TEAM_LABELS[ACTIVE_TEAM] + ". Also add it to " + TEAM_LABELS[other] + "?",
      { confirmLabel: "Yes, add to " + TEAM_LABELS[other] + " too", cancelLabel: "No, " + TEAM_LABELS[ACTIVE_TEAM] + " only" }
    ).then(function (yes) {
      if (!yes) return;
      return createForTeam(other).then(function () {
        toast("Added to " + TEAM_LABELS[other] + " too");
      }).catch(function () {
        toast("Added to " + TEAM_LABELS[ACTIVE_TEAM] + ", but could not add to " + TEAM_LABELS[other]);
      });
    });
  }

  function reloadPbMaterials() {
    var q = document.getElementById("pbMaterialsSearch").value;
    API.get("/api/pricebook/materials" + pbTeamQuery(q ? "q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.materials = d.materials;
      renderPbMaterials(d.materials);
    }).catch(function () { toast("Could not load materials"); });
  }
  function reloadPbLabour() {
    var q = document.getElementById("pbLabourSearch").value;
    API.get("/api/pricebook/labour" + pbTeamQuery(q ? "q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.labour = d.labour;
      renderPbLabour(d.labour);
    }).catch(function () { toast("Could not load labour"); });
  }
  function reloadPbFixed() {
    var q = document.getElementById("pbFixedSearch").value;
    API.get("/api/pricebook/fixed-services" + pbTeamQuery(q ? "q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.fixedServices = d.fixedServices;
      renderPbFixed(d.fixedServices);
    }).catch(function () { toast("Could not load fixed services"); });
  }

  // Suppliers load unfiltered (no server q= here) - the search box and the
  // category pills both filter this same in-memory list client-side, so
  // picking a pill and typing a search term combine instantly with no
  // extra round trip.
  var PB_SUPPLIERS_ALL = [];
  var PB_SUPPLIERS_CATEGORY = "";

  function reloadPbSuppliers() {
    API.get("/api/pricebook/suppliers" + pbTeamQuery()).then(function (d) {
      PB_SUPPLIERS_ALL = d.suppliers;
      PB_SUPPLIERS_CATEGORY = "";
      renderPbSuppliers();
    }).catch(function () { toast("Could not load suppliers"); });
  }

  function filteredSuppliers() {
    var q = document.getElementById("pbSuppliersSearch").value.trim().toLowerCase();
    return PB_SUPPLIERS_ALL.filter(function (s) {
      if (PB_SUPPLIERS_CATEGORY && (s.category || "Uncategorized") !== PB_SUPPLIERS_CATEGORY) return false;
      if (!q) return true;
      var hay = [s.name, s.category, s.location, s.services].join(" ").toLowerCase();
      return hay.indexOf(q) !== -1;
    });
  }

  function reloadPbContractors() {
    var q = document.getElementById("pbContractorsSearch").value;
    API.get("/api/pricebook/contractors" + pbTeamQuery(q ? "q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.contractors = d.contractors;
      renderPbContractors(d.contractors);
    }).catch(function () { toast("Could not load contractors"); });
  }

  function buildMaterialViewRow(m, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + escapeHtml(m.category || "—") + "</td><td>" + escapeHtml(m.itemName) + "</td>" +
      "<td>" + escapeHtml(m.brand || "—") + "</td><td>" + escapeHtml(m.modelOrSize || "—") + "</td>" +
      "<td>" + escapeHtml(m.unit || "—") + "</td>" +
      '<td class="num">' + (m.cost == null ? "—" : fmt(m.cost)) + "</td>" +
      '<td class="num">' + (m.defaultSell == null ? "—" : fmt(m.defaultSell)) + "</td>" +
      "<td>" + escapeHtml(m.supplier || "—") + "</td>" +
      "<td>" + escapeHtml((m.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions admin-only" style="' + (isAdmin ? "" : "display:none") + '"></td>';
    if (isAdmin) {
      var actions = tr.querySelector(".pb-actions");
      var editBtn = document.createElement("button");
      editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
      editBtn.addEventListener("click", function () { tr.replaceWith(buildMaterialEditRow(m, isAdmin)); });
      actions.appendChild(editBtn);
      var delBtn = document.createElement("button");
      delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "6px";
      delBtn.addEventListener("click", function () {
        confirmDialog('Remove material "' + m.itemName + '"?').then(function (ok) {
          if (!ok) return;
          API.del("/api/pricebook/materials/" + m.id).then(function () { toast("Removed"); reloadPbMaterials(); }).catch(function () { toast("Could not remove"); });
        });
      });
      actions.appendChild(delBtn);
    }
    return tr;
  }

  function buildMaterialEditRow(m, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="text" class="mr-cat" value="' + escapeAttr(m.category || "") + '"></td>' +
      '<td><input type="text" class="mr-item" value="' + escapeAttr(m.itemName || "") + '"></td>' +
      '<td><input type="text" class="mr-brand" value="' + escapeAttr(m.brand || "") + '"></td>' +
      '<td><input type="text" class="mr-model" value="' + escapeAttr(m.modelOrSize || "") + '"></td>' +
      '<td><input type="text" class="mr-unit" value="' + escapeAttr(m.unit || "") + '"></td>' +
      '<td><input type="number" class="mr-cost" value="' + (m.cost == null ? "" : m.cost) + '"></td>' +
      '<td><input type="number" class="mr-sell" value="' + (m.defaultSell == null ? "" : m.defaultSell) + '"></td>' +
      '<td><input type="text" class="mr-supplier" value="' + escapeAttr(m.supplier || "") + '"></td>' +
      "<td>" + escapeHtml((m.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions"></td>';
    var actions = tr.querySelector(".pb-actions");
    var saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-orange btn-sm"; saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", function () {
      var updated = {
        category: tr.querySelector(".mr-cat").value.trim(),
        itemName: tr.querySelector(".mr-item").value.trim(),
        brand: tr.querySelector(".mr-brand").value.trim(),
        modelOrSize: tr.querySelector(".mr-model").value.trim(),
        unit: tr.querySelector(".mr-unit").value.trim(),
        cost: tr.querySelector(".mr-cost").value === "" ? null : +tr.querySelector(".mr-cost").value,
        defaultSell: tr.querySelector(".mr-sell").value === "" ? null : +tr.querySelector(".mr-sell").value,
        supplier: tr.querySelector(".mr-supplier").value.trim()
      };
      if (!updated.itemName) { toast("Item name is required"); return; }
      confirmDialog('Save changes to "' + updated.itemName + '"?').then(function (ok) {
        if (!ok) return;
        API.put("/api/pricebook/materials/" + m.id, updated).then(function () {
          toast("Updated"); reloadPbMaterials();
        }).catch(function () { toast("Could not update"); });
      });
    });
    actions.appendChild(saveBtn);
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-ghost btn-sm"; cancelBtn.textContent = "Cancel"; cancelBtn.style.marginLeft = "6px";
    cancelBtn.addEventListener("click", function () { tr.replaceWith(buildMaterialViewRow(m, isAdmin)); });
    actions.appendChild(cancelBtn);
    return tr;
  }

  function renderPbMaterials(rows) {
    var body = document.getElementById("pbMaterialsBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (m) { body.appendChild(buildMaterialViewRow(m, isAdmin)); });
  }

  function buildLabourViewRow(l, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + escapeHtml(l.roleName) + "</td><td>" + escapeHtml(l.labourType || "—") + "</td>" +
      '<td class="num">' + (l.cost == null ? "—" : fmt(l.cost)) + "</td>" +
      '<td class="num">' + (l.defaultSell == null ? "—" : fmt(l.defaultSell)) + "</td>" +
      "<td>" + escapeHtml(l.unit || "—") + "</td>" +
      "<td>" + escapeHtml((l.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions admin-only" style="' + (isAdmin ? "" : "display:none") + '"></td>';
    if (isAdmin) {
      var actions = tr.querySelector(".pb-actions");
      var editBtn = document.createElement("button");
      editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
      editBtn.addEventListener("click", function () { tr.replaceWith(buildLabourEditRow(l, isAdmin)); });
      actions.appendChild(editBtn);
      var delBtn = document.createElement("button");
      delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "6px";
      delBtn.addEventListener("click", function () {
        confirmDialog('Remove labour rate "' + l.roleName + '"?').then(function (ok) {
          if (!ok) return;
          API.del("/api/pricebook/labour/" + l.id).then(function () { toast("Removed"); reloadPbLabour(); }).catch(function () { toast("Could not remove"); });
        });
      });
      actions.appendChild(delBtn);
    }
    return tr;
  }

  function buildLabourEditRow(l, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="text" class="lr-role" value="' + escapeAttr(l.roleName || "") + '"></td>' +
      '<td><select class="lr-type"><option value="staff"' + (l.labourType === "staff" ? " selected" : "") + '>staff</option><option value="outside"' + (l.labourType === "outside" ? " selected" : "") + '>outside</option></select></td>' +
      '<td><input type="number" class="lr-cost" value="' + (l.cost == null ? "" : l.cost) + '"></td>' +
      '<td><input type="number" class="lr-sell" value="' + (l.defaultSell == null ? "" : l.defaultSell) + '"></td>' +
      '<td><input type="text" class="lr-unit" value="' + escapeAttr(l.unit || "") + '"></td>' +
      "<td>" + escapeHtml((l.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions"></td>';
    var actions = tr.querySelector(".pb-actions");
    var saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-orange btn-sm"; saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", function () {
      var updated = {
        roleName: tr.querySelector(".lr-role").value.trim(),
        labourType: tr.querySelector(".lr-type").value,
        cost: tr.querySelector(".lr-cost").value === "" ? null : +tr.querySelector(".lr-cost").value,
        defaultSell: tr.querySelector(".lr-sell").value === "" ? null : +tr.querySelector(".lr-sell").value,
        unit: tr.querySelector(".lr-unit").value.trim()
      };
      if (!updated.roleName) { toast("Role name is required"); return; }
      confirmDialog('Save changes to "' + updated.roleName + '"?').then(function (ok) {
        if (!ok) return;
        API.put("/api/pricebook/labour/" + l.id, updated).then(function () {
          toast("Updated"); reloadPbLabour();
        }).catch(function () { toast("Could not update"); });
      });
    });
    actions.appendChild(saveBtn);
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-ghost btn-sm"; cancelBtn.textContent = "Cancel"; cancelBtn.style.marginLeft = "6px";
    cancelBtn.addEventListener("click", function () { tr.replaceWith(buildLabourViewRow(l, isAdmin)); });
    actions.appendChild(cancelBtn);
    return tr;
  }

  function renderPbLabour(rows) {
    var body = document.getElementById("pbLabourBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (l) { body.appendChild(buildLabourViewRow(l, isAdmin)); });
  }

  function buildFixedViewRow(f, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + escapeHtml(f.category || "—") + "</td><td>" + escapeHtml(f.serviceName) + "</td>" +
      '<td class="num">' + (f.estimatedCost == null ? "—" : fmt(f.estimatedCost)) + "</td>" +
      '<td class="num">' + (f.standardSell == null ? "—" : fmt(f.standardSell)) + "</td>" +
      "<td>" + escapeHtml((f.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions admin-only" style="' + (isAdmin ? "" : "display:none") + '"></td>';
    if (isAdmin) {
      var editBtn = document.createElement("button");
      editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
      editBtn.addEventListener("click", function () { tr.replaceWith(buildFixedEditRow(f, isAdmin)); });
      tr.querySelector(".pb-actions").appendChild(editBtn);
    }
    return tr;
  }

  function buildFixedEditRow(f, isAdmin) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="text" class="fr-cat" value="' + escapeAttr(f.category || "") + '"></td>' +
      '<td><input type="text" class="fr-service" value="' + escapeAttr(f.serviceName || "") + '"></td>' +
      '<td><input type="number" class="fr-cost" value="' + (f.estimatedCost == null ? "" : f.estimatedCost) + '"></td>' +
      '<td><input type="number" class="fr-sell" value="' + (f.standardSell == null ? "" : f.standardSell) + '"></td>' +
      "<td>" + escapeHtml((f.lastUpdated || "").slice(0, 10)) + "</td>" +
      '<td class="pb-actions"></td>';
    var actions = tr.querySelector(".pb-actions");
    var saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-orange btn-sm"; saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", function () {
      var updated = {
        category: tr.querySelector(".fr-cat").value.trim(),
        serviceName: tr.querySelector(".fr-service").value.trim(),
        estimatedCost: tr.querySelector(".fr-cost").value === "" ? null : +tr.querySelector(".fr-cost").value,
        standardSell: tr.querySelector(".fr-sell").value === "" ? null : +tr.querySelector(".fr-sell").value
      };
      if (!updated.serviceName) { toast("Service name is required"); return; }
      confirmDialog('Save changes to "' + updated.serviceName + '"?').then(function (ok) {
        if (!ok) return;
        API.put("/api/pricebook/fixed-services/" + f.id, updated).then(function () {
          toast("Updated"); reloadPbFixed();
        }).catch(function () { toast("Could not update"); });
      });
    });
    actions.appendChild(saveBtn);
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-ghost btn-sm"; cancelBtn.textContent = "Cancel"; cancelBtn.style.marginLeft = "6px";
    cancelBtn.addEventListener("click", function () { tr.replaceWith(buildFixedViewRow(f, isAdmin)); });
    actions.appendChild(cancelBtn);
    return tr;
  }

  function renderPbFixed(rows) {
    var body = document.getElementById("pbFixedBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (f) { body.appendChild(buildFixedViewRow(f, isAdmin)); });
  }

  function renderPbSupplierPills() {
    var wrap = document.getElementById("pbSuppliersCategoryPills");
    if (!wrap) return;
    var counts = {};
    PB_SUPPLIERS_ALL.forEach(function (s) {
      var cat = s.category || "Uncategorized";
      counts[cat] = (counts[cat] || 0) + 1;
    });
    var categories = Object.keys(counts).sort();
    wrap.innerHTML = "";
    var allBtn = document.createElement("button");
    allBtn.type = "button";
    allBtn.className = "amc-subtab" + (PB_SUPPLIERS_CATEGORY === "" ? " active" : "");
    allBtn.innerHTML = "All <span class=\"amc-count\">" + PB_SUPPLIERS_ALL.length + "</span>";
    allBtn.addEventListener("click", function () { PB_SUPPLIERS_CATEGORY = ""; renderPbSuppliers(); });
    wrap.appendChild(allBtn);
    categories.forEach(function (cat) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "amc-subtab" + (PB_SUPPLIERS_CATEGORY === cat ? " active" : "");
      btn.innerHTML = escapeHtml(cat) + " <span class=\"amc-count\">" + counts[cat] + "</span>";
      btn.addEventListener("click", function () { PB_SUPPLIERS_CATEGORY = cat; renderPbSuppliers(); });
      wrap.appendChild(btn);
    });
  }

  function buildSupplierViewCard(s, isAdmin) {
    var card = document.createElement("div");
    card.className = "supplier-card" + (s.preferred ? " preferred" : "");
    card.innerHTML =
      '<div class="supplier-head">' +
        '<div><span class="supplier-name">' + escapeHtml(s.name) + "</span>" + (s.preferred ? '<span class="pref-pill">Preferred</span>' : "") +
        '<div class="supplier-loc">' + escapeHtml(s.category || "Uncategorized") + (s.location ? " · " + escapeHtml(s.location) : "") + "</div></div>" +
        '<div class="supplier-actions"></div>' +
      "</div>" +
      (s.services ? '<div class="supplier-services">' + escapeHtml(s.services) + "</div>" : "") +
      ((s.phone || s.email) ? '<div class="supplier-contacts">' +
        (s.phone ? '<a href="tel:' + escapeAttr(s.phone) + '">' + escapeHtml(s.phone) + "</a>" : "") +
        (s.email ? '<a href="mailto:' + escapeAttr(s.email) + '">' + escapeHtml(s.email) + "</a>" : "") +
      "</div>" : "");
    if (isAdmin) {
      var actions = card.querySelector(".supplier-actions");
      var editBtn = document.createElement("button");
      editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
      editBtn.addEventListener("click", function () {
        card.replaceWith(buildSupplierEditCard(s, isAdmin));
      });
      actions.appendChild(editBtn);
      var delBtn = document.createElement("button");
      delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "6px";
      delBtn.addEventListener("click", function () {
        confirmDialog('Remove supplier "' + s.name + '"?').then(function (ok) {
          if (!ok) return;
          API.del("/api/pricebook/suppliers/" + s.id).then(function () { toast("Removed"); reloadPbSuppliers(); }).catch(function () { toast("Could not remove"); });
        });
      });
      actions.appendChild(delBtn);
    }
    return card;
  }

  function buildSupplierEditCard(s, isAdmin) {
    var card = document.createElement("div");
    card.className = "supplier-card supplier-card-editing";
    card.innerHTML =
      '<div class="supplier-edit-grid">' +
        '<input type="text" class="sc-category" placeholder="Category" value="' + escapeAttr(s.category || "") + '">' +
        '<input type="text" class="sc-name" placeholder="Name" value="' + escapeAttr(s.name || "") + '">' +
        '<input type="text" class="sc-location" placeholder="Location" value="' + escapeAttr(s.location || "") + '">' +
        '<input type="text" class="sc-phone" placeholder="Phone" value="' + escapeAttr(s.phone || "") + '">' +
        '<input type="text" class="sc-email" placeholder="Email" value="' + escapeAttr(s.email || "") + '">' +
        '<input type="text" class="sc-services" placeholder="Services / notes" value="' + escapeAttr(s.services || "") + '">' +
      "</div>" +
      '<label class="supplier-edit-preferred"><input type="checkbox" class="sc-preferred"' + (s.preferred ? " checked" : "") + "> Preferred</label>" +
      '<div class="supplier-edit-actions">' +
        '<button type="button" class="btn btn-orange btn-sm sc-save">Save</button>' +
        '<button type="button" class="btn btn-ghost btn-sm sc-cancel">Cancel</button>' +
      "</div>";
    card.querySelector(".sc-cancel").addEventListener("click", function () {
      card.replaceWith(buildSupplierViewCard(s, isAdmin));
    });
    card.querySelector(".sc-save").addEventListener("click", function () {
      var updated = {
        category: card.querySelector(".sc-category").value.trim(),
        name: card.querySelector(".sc-name").value.trim(),
        location: card.querySelector(".sc-location").value.trim(),
        phone: card.querySelector(".sc-phone").value.trim(),
        email: card.querySelector(".sc-email").value.trim(),
        services: card.querySelector(".sc-services").value.trim(),
        preferred: card.querySelector(".sc-preferred").checked
      };
      if (!updated.name) { toast("Name is required"); return; }
      confirmDialog('Save changes to "' + updated.name + '"?').then(function (ok) {
        if (!ok) return;
        API.put("/api/pricebook/suppliers/" + s.id, updated).then(function () {
          toast("Updated");
          reloadPbSuppliers();
        }).catch(function () { toast("Could not update"); });
      });
    });
    return card;
  }

  function renderPbSuppliers() {
    renderPbSupplierPills();
    var grid = document.getElementById("pbSuppliersCardGrid");
    var countLbl = document.getElementById("pbSuppliersCount");
    var isAdmin = CURRENT_USER_ROLE === "admin";
    var rows = filteredSuppliers();
    grid.innerHTML = "";
    if (countLbl) countLbl.textContent = rows.length + " supplier" + (rows.length === 1 ? "" : "s") + " shown";
    if (!rows.length) {
      grid.innerHTML = '<p class="hint">No suppliers match.</p>';
      return;
    }
    rows.forEach(function (s) { grid.appendChild(buildSupplierViewCard(s, isAdmin)); });
  }

  function renderPbContractors(contractors) {
    var wrap = document.getElementById("pbContractorsList");
    wrap.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    if (!contractors.length) {
      wrap.innerHTML = '<p class="hint">No contractors yet.</p>';
      return;
    }
    contractors.forEach(function (c) {
      var card = document.createElement("div");
      card.className = "contractor-card" + (c.preferred ? " preferred" : "");

      var contactsHtml = "";
      if (c.phone) contactsHtml += '<a href="tel:' + escapeAttr(c.phone) + '">' + escapeHtml(c.phone) + "</a>";
      if (c.email) contactsHtml += '<a href="mailto:' + escapeAttr(c.email) + '">' + escapeHtml(c.email) + "</a>";

      card.innerHTML =
        '<div class="contractor-head">' +
          '<div><span class="contractor-name">' + escapeHtml(c.name) + "</span>" + (c.preferred ? '<span class="pref-pill">Preferred</span>' : "") +
          '<div class="contractor-loc">' + escapeHtml(c.category || "") + (c.location ? " · " + escapeHtml(c.location) : "") + "</div></div>" +
          '<div class="contractor-actions"></div>' +
        "</div>" +
        (c.services ? '<div class="contractor-services">' + escapeHtml(c.services) + "</div>" : "") +
        (contactsHtml ? '<div class="contractor-contacts">' + contactsHtml + "</div>" : "") +
        (c.notes ? '<div class="contractor-notes">' + escapeHtml(c.notes) + "</div>" : "") +
        '<button class="contractor-pricing-toggle" type="button">▶ ' + (c.pricing.length ? "Show Pricing (" + c.pricing.length + ")" : "Show Pricing") + "</button>" +
        '<div class="contractor-pricing"></div>';

      if (isAdmin) {
        var actionsCell = card.querySelector(".contractor-actions");
        var editBtn = document.createElement("button");
        editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
        editBtn.addEventListener("click", function () {
          var category = prompt("Category", c.category || ""); if (category === null) return;
          var name = prompt("Name", c.name || ""); if (name === null) return;
          var location = prompt("Location", c.location || ""); if (location === null) return;
          var phone = prompt("Phone", c.phone || ""); if (phone === null) return;
          var email = prompt("Email", c.email || ""); if (email === null) return;
          var services = prompt("Services / notes", c.services || ""); if (services === null) return;
          var pricingNote = prompt("Pricing note (shown above the rate card)", c.pricingNote || ""); if (pricingNote === null) return;
          var notes = prompt("Notes (internal only - anything worth remembering)", c.notes || ""); if (notes === null) return;
          var preferred = confirm("Preferred contractor? OK = Yes, Cancel = No. (Currently: " + (c.preferred ? "Yes" : "No") + ")");
          API.put("/api/pricebook/contractors/" + c.id, { category: category, name: name, location: location, phone: phone, email: email, services: services, pricingNote: pricingNote, notes: notes, preferred: preferred })
            .then(function () { toast("Updated"); reloadPbContractors(); })
            .catch(function () { toast("Could not update"); });
        });
        actionsCell.appendChild(editBtn);
        var delBtn = document.createElement("button");
        delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "6px";
        delBtn.addEventListener("click", function () {
          confirmDialog('Remove contractor "' + c.name + '"? This also removes their rate card.').then(function (ok) {
            if (!ok) return;
            API.del("/api/pricebook/contractors/" + c.id).then(function () { toast("Removed"); reloadPbContractors(); }).catch(function () { toast("Could not remove"); });
          });
        });
        actionsCell.appendChild(delBtn);
      }

      var pricingWrap = card.querySelector(".contractor-pricing");
      var toggleBtn = card.querySelector(".contractor-pricing-toggle");
      var pricingHtml = "";
      if (c.pricingNote) pricingHtml += '<div class="contractor-pricing-note">' + escapeHtml(c.pricingNote) + "</div>";
      c.pricing.forEach(function (p) {
        pricingHtml +=
          '<div class="contractor-pricing-row"><div><span class="contractor-pricing-name">' + escapeHtml(p.label) + "</span>" +
          (p.note ? ' <span class="contractor-pricing-note-inline">(' + escapeHtml(p.note) + ")</span>" : "") + "</div>" +
          '<div style="display:flex;align-items:center;gap:10px"><span class="contractor-pricing-price">' + (p.price == null ? "TBD" : "AED " + fmt(p.price)) + "</span>" +
          '<span class="contractor-pricing-actions admin-only" style="' + (isAdmin ? "" : "display:none") + '" data-pid="' + p.id + '"></span></div></div>';
      });
      pricingWrap.innerHTML = pricingHtml;

      if (isAdmin) {
        c.pricing.forEach(function (p) {
          var cell = pricingWrap.querySelector('[data-pid="' + p.id + '"]');
          if (!cell) return;
          var editBtn = document.createElement("button");
          editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
          editBtn.addEventListener("click", function () {
            var label = prompt("Label", p.label); if (label === null) return;
            var price = prompt("Price (AED)", p.price == null ? "" : p.price); if (price === null) return;
            var note = prompt("Note", p.note || ""); if (note === null) return;
            API.put("/api/pricebook/contractors/" + c.id + "/pricing/" + p.id, { label: label, price: price === "" ? null : +price, note: note })
              .then(function () { toast("Updated"); reloadPbContractors(); }).catch(function () { toast("Could not update"); });
          });
          cell.appendChild(editBtn);
          var delBtn = document.createElement("button");
          delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "×"; delBtn.title = "Remove row"; delBtn.style.marginLeft = "4px";
          delBtn.addEventListener("click", function () {
            confirmDialog("Remove this pricing row?").then(function (ok) {
              if (!ok) return;
              API.del("/api/pricebook/contractors/" + c.id + "/pricing/" + p.id).then(function () { toast("Removed"); reloadPbContractors(); }).catch(function () { toast("Could not remove"); });
            });
          });
          cell.appendChild(delBtn);
        });

        var addRow = document.createElement("div");
        addRow.className = "contractor-pricing-add admin-only";
        addRow.style.display = isAdmin ? "" : "none";
        addRow.innerHTML =
          '<input type="text" placeholder="Label (e.g. Studio Apt - Unfurnished)" style="flex:1.5">' +
          '<input type="number" placeholder="Price (AED)" style="max-width:110px">' +
          '<input type="text" placeholder="Note" style="max-width:140px">' +
          '<button class="btn btn-outline btn-sm">+ Add pricing row</button>';
        var inputs = addRow.querySelectorAll("input");
        addRow.querySelector("button").addEventListener("click", function () {
          var label = inputs[0].value.trim();
          if (!label) { toast("Enter a label first"); return; }
          API.post("/api/pricebook/contractors/" + c.id + "/pricing", {
            label: label, price: inputs[1].value === "" ? null : +inputs[1].value, note: inputs[2].value.trim()
          }).then(function () { toast("Pricing row added"); reloadPbContractors(); }).catch(function () { toast("Could not add"); });
        });
        pricingWrap.appendChild(addRow);
      }

      toggleBtn.addEventListener("click", function () {
        var open = pricingWrap.classList.toggle("open");
        toggleBtn.textContent = (open ? "▼ Hide Pricing" : "▶ Show Pricing") + (c.pricing.length ? " (" + c.pricing.length + ")" : "");
      });

      wrap.appendChild(card);
    });
  }

  function wirePricebookV2() {
    document.getElementById("pbMaterialsSearch").addEventListener("input", debounce(reloadPbMaterials, 250));
    document.getElementById("pbLabourSearch").addEventListener("input", debounce(reloadPbLabour, 250));
    document.getElementById("pbFixedSearch").addEventListener("input", debounce(reloadPbFixed, 250));
    document.getElementById("pbSuppliersSearch").addEventListener("input", function () { renderPbSuppliers(); });
    document.getElementById("pbContractorsSearch").addEventListener("input", debounce(reloadPbContractors, 250));

    document.getElementById("btnExportPricebook").addEventListener("click", function () {
      window.location.href = "/api/pricebook/export?team=" + encodeURIComponent(ACTIVE_TEAM);
    });

    document.getElementById("btnResetTeamPricebook").addEventListener("click", function () {
      var teamLabel = TEAM_LABELS[ACTIVE_TEAM];
      confirmDialog(
        "Reset " + teamLabel + "'s Materials, Labour, Fixed Services and Suppliers to the original defaults? " +
        "Any items your team has added or edited for " + teamLabel + " will be permanently deleted. Contractors are not affected."
      ).then(function (ok) {
        if (!ok) return;
        API.post("/api/pricebook/reset", { team: ACTIVE_TEAM }).then(function () {
          toast(teamLabel + "'s Price Book reset to defaults");
          loadPricebookV2();
        }).catch(function (e) { toast(e.message || "Could not reset"); });
      });
    });

    document.getElementById("pbAddMaterialBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbMaterialsAddBar");
      var item = bar.querySelector(".pb-new-item").value.trim();
      if (!item) { toast("Enter an item name"); return; }
      var body = {
        category: bar.querySelector(".pb-new-cat").value.trim(),
        itemName: item,
        brand: bar.querySelector(".pb-new-brand").value.trim(),
        modelOrSize: bar.querySelector(".pb-new-model").value.trim(),
        unit: bar.querySelector(".pb-new-unit").value.trim(),
        cost: bar.querySelector(".pb-new-cost").value === "" ? null : +bar.querySelector(".pb-new-cost").value,
        defaultSell: bar.querySelector(".pb-new-sell").value === "" ? null : +bar.querySelector(".pb-new-sell").value,
        supplier: bar.querySelector(".pb-new-supplier").value.trim()
      };
      API.post("/api/pricebook/materials", Object.assign({ team: ACTIVE_TEAM }, body)).then(function () {
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Material added");
        reloadPbMaterials();
        return maybeDuplicateToOtherTeam("material", function (team) {
          return API.post("/api/pricebook/materials", Object.assign({ team: team }, body));
        });
      }).catch(function () { toast("Could not add material"); });
    });

    document.getElementById("pbAddLabourBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbLabourAddBar");
      var role = bar.querySelector(".pb-new-role").value.trim();
      if (!role) { toast("Enter a role name"); return; }
      var body = {
        roleName: role,
        labourType: bar.querySelector(".pb-new-labour-type").value,
        cost: bar.querySelector(".pb-new-cost").value === "" ? null : +bar.querySelector(".pb-new-cost").value,
        defaultSell: bar.querySelector(".pb-new-sell").value === "" ? null : +bar.querySelector(".pb-new-sell").value,
        unit: bar.querySelector(".pb-new-unit").value.trim()
      };
      API.post("/api/pricebook/labour", Object.assign({ team: ACTIVE_TEAM }, body)).then(function () {
        bar.querySelector(".pb-new-role").value = "";
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Labour rate added");
        reloadPbLabour();
        return maybeDuplicateToOtherTeam("labour rate", function (team) {
          return API.post("/api/pricebook/labour", Object.assign({ team: team }, body));
        });
      }).catch(function () { toast("Could not add labour rate"); });
    });

    document.getElementById("pbAddFixedBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbFixedAddBar");
      var name = bar.querySelector(".pb-new-service").value.trim();
      if (!name) { toast("Enter a service name"); return; }
      var body = {
        category: bar.querySelector(".pb-new-cat").value.trim(),
        serviceName: name,
        estimatedCost: bar.querySelector(".pb-new-est-cost").value === "" ? null : +bar.querySelector(".pb-new-est-cost").value,
        standardSell: bar.querySelector(".pb-new-std-sell").value === "" ? null : +bar.querySelector(".pb-new-std-sell").value
      };
      API.post("/api/pricebook/fixed-services", Object.assign({ team: ACTIVE_TEAM }, body)).then(function () {
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Fixed service added");
        reloadPbFixed();
        return maybeDuplicateToOtherTeam("fixed service", function (team) {
          return API.post("/api/pricebook/fixed-services", Object.assign({ team: team }, body));
        });
      }).catch(function () { toast("Could not add fixed service"); });
    });

    document.getElementById("pbAddSupplierBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbSuppliersAddBar");
      var name = bar.querySelector(".pb-new-name").value.trim();
      if (!name) { toast("Enter a supplier name"); return; }
      var body = {
        category: bar.querySelector(".pb-new-cat").value.trim(),
        name: name,
        location: bar.querySelector(".pb-new-location").value.trim(),
        phone: bar.querySelector(".pb-new-phone").value.trim(),
        email: bar.querySelector(".pb-new-email").value.trim(),
        services: bar.querySelector(".pb-new-services").value.trim(),
        preferred: bar.querySelector(".pb-new-preferred").checked
      };
      API.post("/api/pricebook/suppliers", Object.assign({ team: ACTIVE_TEAM }, body)).then(function () {
        bar.querySelectorAll("input[type=text]").forEach(function (i) { i.value = ""; });
        bar.querySelector(".pb-new-preferred").checked = false;
        toast("Supplier added");
        reloadPbSuppliers();
        return maybeDuplicateToOtherTeam("supplier", function (team) {
          return API.post("/api/pricebook/suppliers", Object.assign({ team: team }, body));
        });
      }).catch(function () { toast("Could not add supplier"); });
    });

    document.getElementById("pbAddContractorBtn").addEventListener("click", function () {
      var card = document.getElementById("pbContractorsAddCard");
      var name = card.querySelector(".pb-new-name").value.trim();
      if (!name) { toast("Enter a contractor name"); return; }
      var body = {
        category: card.querySelector(".pb-new-cat").value.trim(),
        name: name,
        location: card.querySelector(".pb-new-location").value.trim(),
        phone: card.querySelector(".pb-new-phone").value.trim(),
        email: card.querySelector(".pb-new-email").value.trim(),
        services: card.querySelector(".pb-new-services").value.trim(),
        notes: card.querySelector(".pb-new-notes").value.trim(),
        preferred: card.querySelector(".pb-new-preferred").checked
      };
      API.post("/api/pricebook/contractors", Object.assign({ team: ACTIVE_TEAM }, body)).then(function () {
        card.querySelectorAll("input[type=text]").forEach(function (i) { i.value = ""; });
        card.querySelector(".pb-new-preferred").checked = false;
        toast("Contractor added — open it to add rate-card rows");
        reloadPbContractors();
        return maybeDuplicateToOtherTeam("contractor", function (team) {
          return API.post("/api/pricebook/contractors", Object.assign({ team: team }, body));
        });
      }).catch(function () { toast("Could not add contractor"); });
    });
  }

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); var args = arguments; t = setTimeout(function () { fn.apply(null, args); }, ms); };
  }

  // ==================================================================
  // TEMPLATES
  // ==================================================================
  var tplEditorItems = [];
  var tplEditorId = null;

  function loadTemplates() {
    closeTemplateEditor();
    var q = document.getElementById("templatesSearch").value;
    API.get("/api/templates" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      renderTemplates(d.templates);
    }).catch(function () { toast("Could not load templates"); });
  }

  function renderTemplates(templates) {
    var list = document.getElementById("templatesList");
    list.innerHTML = "";
    if (!templates.length) {
      list.innerHTML = '<p class="hint">No templates yet.</p>';
      return;
    }
    templates.forEach(function (t) {
      var card = document.createElement("div");
      card.className = "template-card";
      card.innerHTML = "<h4>" + escapeHtml(t.name) + "</h4><p>" + escapeHtml(t.description || "") + "</p>";
      var applyBtn = document.createElement("button");
      applyBtn.className = "btn btn-orange btn-sm"; applyBtn.textContent = "Apply to Quote";
      applyBtn.addEventListener("click", function () {
        API.get("/api/templates/" + t.id).then(function (full) {
          (full.items || []).forEach(function (it) {
            builder.items.push(newItemRow(it.kind, it.desc, it.cost, it.sell,
              it.kind === "material" && it.cost > 0 ? ((it.sell - it.cost) / it.cost) * 100 : (it.kind === "material" ? SETTINGS.defaultMaterialMarkupPct : null),
              it.qty));
          });
          renderItemsTable(); recalcBuilder(); saveDraft();
          goToTab("builder");
          toast("Applied template “" + t.name + "” — review lines before saving.");
        }).catch(function () { toast("Could not load template"); });
      });
      card.appendChild(applyBtn);
      if (CURRENT_USER_ROLE === "admin") {
        var editBtn = document.createElement("button");
        editBtn.className = "btn btn-outline btn-sm"; editBtn.textContent = "Edit";
        editBtn.style.marginLeft = "8px";
        editBtn.addEventListener("click", function () {
          API.get("/api/templates/" + t.id).then(function (full) {
            openTemplateEditor(full);
          }).catch(function () { toast("Could not load template"); });
        });
        card.appendChild(editBtn);

        var delBtn = document.createElement("button");
        delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete";
        delBtn.style.marginLeft = "8px";
        delBtn.addEventListener("click", function () {
          confirmDialog('Delete template "' + t.name + '"?').then(function (ok) {
            if (!ok) return;
            API.del("/api/templates/" + t.id).then(function () { toast("Deleted"); loadTemplates(); });
          });
        });
        card.appendChild(delBtn);
      }
      list.appendChild(card);
    });
  }

  function openTemplateEditor(existing) {
    tplEditorId = existing ? existing.id : null;
    document.getElementById("templateEditorHeading").textContent = existing ? "Edit Template" : "New Template";
    document.getElementById("tplName").value = existing ? existing.name : "";
    document.getElementById("tplDescription").value = existing ? (existing.description || "") : "";
    tplEditorItems = ((existing && existing.items) || []).map(function (it) {
      var markupPct = it.kind === "material" && it.cost > 0 ? ((it.sell - it.cost) / it.cost) * 100 : (it.kind === "material" ? SETTINGS.defaultMaterialMarkupPct : null);
      return newItemRow(it.kind, it.desc, it.cost, it.sell, markupPct, it.qty);
    });
    renderTplItemsTable();
    populateItemCatalogSelect("tplItemKindSelect", "tplItemCatalogSelect");
    document.getElementById("templatesListCard").style.display = "none";
    document.getElementById("templateEditorCard").style.display = "";
  }

  function closeTemplateEditor() {
    document.getElementById("templateEditorCard").style.display = "none";
    document.getElementById("templatesListCard").style.display = "";
  }

  function recalcTplItemsTotal() {
    var total = sum(tplEditorItems, function (i) { return (+i.sell || 0) * (+i.qty || 0); });
    document.getElementById("tplItemsSellTotal").textContent = fmt(total);
  }

  function renderTplItemsTable() {
    var bodyEl = document.getElementById("tplItemsBody");
    bodyEl.innerHTML = "";
    tplEditorItems.forEach(function (item, idx) {
      var tr = document.createElement("tr");
      var isMaterial = item.kind === "material";
      var markupCell = isMaterial
        ? '<input type="number" step="1" min="0" data-k="markupPct" value="' + (item.markupPct == null ? SETTINGS.defaultMaterialMarkupPct : item.markupPct) + '" style="text-align:right">'
        : '<span style="color:var(--muted)">—</span>';
      tr.innerHTML =
        "<td>" + (idx + 1) + "</td>" +
        '<td><span class="item-kind-badge">' + escapeHtml(ITEM_KIND_LABELS[item.kind] || item.kind) + "</span></td>" +
        '<td><input type="text" data-k="desc" value="' + escapeAttr(item.desc) + '"></td>' +
        '<td class="num"><input type="number" step="0.01" min="0" data-k="cost" value="' + item.cost + '" style="text-align:right"></td>' +
        '<td class="num"><input type="number" step="1" min="0" data-k="qty" value="' + item.qty + '" style="text-align:right"></td>' +
        '<td class="num"><input type="number" step="0.01" min="0" data-k="sell" value="' + item.sell + '" style="text-align:right"></td>' +
        '<td class="num markup-cell">' + markupCell + "</td>" +
        '<td class="num" data-k="total">' + fmt(itemTotal(item)) + "</td>" +
        '<td><button class="row-del" title="Remove" data-idx="' + idx + '">&times;</button></td>';

      tr.querySelectorAll("input").forEach(function (inp) {
        inp.addEventListener("input", function () {
          var k = inp.getAttribute("data-k");
          if (k === "desc") {
            item.desc = inp.value;
          } else if (k === "markupPct" && isMaterial) {
            var linked = materialsMarkupLink("markupPct", item.cost, item.sell, +inp.value || 0);
            item.markupPct = linked.markupPct; item.sell = linked.sell;
            tr.querySelector('[data-k="sell"]').value = round2(item.sell);
          } else if (k === "sell") {
            item.sell = +inp.value || 0;
            if (isMaterial) {
              var linked2 = materialsMarkupLink("sell", item.cost, item.sell, item.markupPct);
              item.markupPct = linked2.markupPct;
              tr.querySelector('[data-k="markupPct"]').value = round2(item.markupPct);
            }
          } else if (k === "cost") {
            item.cost = +inp.value || 0;
            if (isMaterial) {
              var linked3 = materialsMarkupLink("cost", item.cost, item.sell, item.markupPct);
              item.sell = linked3.sell;
              tr.querySelector('[data-k="sell"]').value = round2(item.sell);
            }
          } else if (k === "qty") {
            item.qty = +inp.value || 0;
          }
          tr.querySelector('[data-k="total"]').textContent = fmt(itemTotal(item));
          recalcTplItemsTotal();
        });
      });
      tr.querySelector(".row-del").addEventListener("click", function () {
        tplEditorItems.splice(idx, 1);
        renderTplItemsTable();
      });
      bodyEl.appendChild(tr);
    });
    recalcTplItemsTotal();
  }

  function wireTemplates() {
    document.getElementById("templatesSearch").addEventListener("input", debounce(loadTemplates, 250));
    document.getElementById("btnNewTemplate").addEventListener("click", function () {
      openTemplateEditor(null);
    });
    document.getElementById("btnCancelTemplateEditor").addEventListener("click", closeTemplateEditor);
    document.getElementById("btnSaveTemplateEditor").addEventListener("click", function () {
      var name = document.getElementById("tplName").value.trim();
      if (!name) { toast("Enter a template name"); return; }
      if (!tplEditorItems.length) { toast("Add at least one item first"); return; }
      var payload = {
        name: name,
        description: document.getElementById("tplDescription").value.trim(),
        items: tplEditorItems.map(function (i) { return { kind: i.kind, desc: i.desc, cost: i.cost, sell: i.sell, qty: i.qty }; })
      };
      var req = tplEditorId ? API.put("/api/templates/" + tplEditorId, payload) : API.post("/api/templates", payload);
      req.then(function () {
        toast(tplEditorId ? "Template updated" : "Template saved");
        loadTemplates();
      }).catch(function (e) { toast(e.message || "Could not save template"); });
    });
    document.getElementById("tplAddItemBlank").addEventListener("click", function () {
      var kind = document.getElementById("tplItemKindSelect").value;
      var markupPct = kind === "material" ? SETTINGS.defaultMaterialMarkupPct : null;
      tplEditorItems.push(newItemRow(kind, "", 0, 0, markupPct, 1));
      renderTplItemsTable();
    });
    document.getElementById("tplAddItemFromCatalog").addEventListener("click", function () {
      var kind = document.getElementById("tplItemKindSelect").value;
      var sel = document.getElementById("tplItemCatalogSelect");
      var opt = sel.options[sel.selectedIndex];
      if (!opt || opt.disabled) { toast("No Price Book item selected"); return; }
      var cost = +opt.dataset.cost || 0, sellVal = +opt.dataset.sell || 0;
      var markupPct = kind === "material" ? (cost > 0 ? ((sellVal - cost) / cost) * 100 : SETTINGS.defaultMaterialMarkupPct) : null;
      var row = newItemRow(kind, opt.dataset.desc, cost, sellVal, markupPct, 1);
      row.priceBookRefId = opt.dataset.refId;
      tplEditorItems.push(row);
      renderTplItemsTable();
    });
    document.getElementById("tplItemKindSelect").addEventListener("change", function () {
      populateItemCatalogSelect("tplItemKindSelect", "tplItemCatalogSelect");
    });
  }

  // ==================================================================
  // HOME
  // ==================================================================
  function loadHome() {
    API.get("/api/dashboard" + pbTeamQuery()).then(function (d) {
      renderHomeStats(d.stats);
      renderApprovalList(d.approvalRequired);
    }).catch(function () { toast("Could not load dashboard"); });
    loadRecentQuotes();
  }

  function renderHomeStats(stats) {
    document.getElementById("statAvgMarkup").textContent = stats.avgMarkupPct.toFixed(1) + "%";
    document.getElementById("statAvgMargin").textContent = stats.avgGrossMarginPct.toFixed(1) + "%";
    document.getElementById("statAwaiting").textContent = stats.quotesAwaitingApprovalCount;
    document.getElementById("statItemCount").textContent = stats.priceBookItemCount;
    document.getElementById("statQuotesThisMonth").textContent = stats.quotesThisMonth;
    document.getElementById("statSentToJobber").textContent = stats.sentToJobberThisMonth;
    document.getElementById("statDraftsPending").textContent = stats.draftsPending;
  }

  function renderApprovalList(rows) {
    var body = document.getElementById("approvalBody");
    body.innerHTML = "";
    document.getElementById("approvalEmpty").style.display = rows.length ? "none" : "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(r.quoteNo || "") + "</td><td>" + escapeHtml(r.client || "") + "</td>" +
        '<td class="amber-text">' + escapeHtml(r.reason || "") + '</td>' +
        '<td class="num">AED ' + fmt(r.value) + "</td><td>" + escapeHtml(r.preparedBy || "—") + "</td><td></td>";
      var actionsCell = tr.lastElementChild;
      var openBtn = document.createElement("button");
      openBtn.className = "btn btn-outline btn-sm"; openBtn.textContent = "Open";
      openBtn.addEventListener("click", function () {
        API.get("/api/quotes/" + r.id).then(function (full) {
          loadQuoteIntoBuilder(full);
          goToTab("builder");
        });
      });
      actionsCell.appendChild(openBtn);
      if (CURRENT_USER_ROLE === "admin") {
        var approveBtn = document.createElement("button");
        approveBtn.className = "btn btn-primary btn-sm"; approveBtn.textContent = "Approve";
        approveBtn.style.marginLeft = "6px";
        approveBtn.addEventListener("click", function () {
          API.post("/api/quotes/" + r.id + "/status", { action: "approve_and_send" }).then(function () {
            toast("Approved and sent to Jobber");
            loadHome();
          }).catch(function (e) { toast(e.message || "Could not approve"); });
        });
        actionsCell.appendChild(approveBtn);
      }
      body.appendChild(tr);
    });
  }

  var recentQuotesDebounced = debounce(function () { loadRecentQuotes(); }, 250);

  function loadRecentQuotes() {
    var q = document.getElementById("recentSearch").value.trim();
    var status = document.getElementById("recentStatusFilter").value;
    var params = ["team=" + encodeURIComponent(ACTIVE_TEAM)];
    if (q) params.push("q=" + encodeURIComponent(q));
    if (status) params.push("status=" + encodeURIComponent(status));
    API.get("/api/quotes?" + params.join("&")).then(function (d) {
      renderRecentQuotes(d.quotes.slice(0, 10));
    }).catch(function () { toast("Could not load recent quotes"); });
  }

  function renderRecentQuotes(rows) {
    var body = document.getElementById("recentQuotesBody");
    body.innerHTML = "";
    document.getElementById("recentQuotesEmpty").style.display = rows.length ? "none" : "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML =
        "<td>" + escapeHtml(r.date || "") + "</td><td>" + escapeHtml(r.quoteNo || "") + "</td>" +
        "<td>" + escapeHtml((r.client && r.client.name) || "") + "</td>" +
        '<td class="num">AED ' + fmt(r.grandTotal) + "</td>" +
        '<td><span class="status-pill ' + statusPillClass(r.status) + '">' + escapeHtml(r.status || "") + "</span></td>";
      tr.addEventListener("click", function () {
        API.get("/api/quotes/" + r.id).then(function (full) {
          loadQuoteIntoBuilder(full);
          goToTab("builder");
        });
      });
      body.appendChild(tr);
    });
  }

  function wireHome() {
    document.getElementById("homeNewQuote").addEventListener("click", function () {
      builder = defaultBuilderState();
      saveDraft();
      goToTab("builder");
      initBuilderView();
    });
    document.getElementById("recentSearch").addEventListener("input", recentQuotesDebounced);
    document.getElementById("recentStatusFilter").addEventListener("change", function () { loadRecentQuotes(); });

    var homeSearchInput = document.getElementById("homePriceSearch");
    var homeSearchOut = document.getElementById("homeSearchResults");
    var homeSearchCloseBtn = document.getElementById("homeSearchClose");
    var homeSearchSeq = 0;

    function closeHomeSearch() {
      homeSearchInput.value = "";
      homeSearchOut.innerHTML = "";
      homeSearchCloseBtn.style.display = "none";
      homeSearchSeq++; // invalidate any in-flight requests so a late response can't reopen this
    }

    homeSearchCloseBtn.addEventListener("click", closeHomeSearch);

    homeSearchInput.addEventListener("input", debounce(function () {
      var q = homeSearchInput.value.trim();
      var thisSeq = ++homeSearchSeq;
      if (!q) { homeSearchOut.innerHTML = ""; homeSearchCloseBtn.style.display = "none"; return; }
      Promise.all([
        API.get("/api/pricebook/materials?q=" + encodeURIComponent(q)),
        API.get("/api/pricebook/labour?q=" + encodeURIComponent(q)),
        API.get("/api/pricebook/fixed-services?q=" + encodeURIComponent(q))
      ]).then(function (res) {
        // a newer keystroke (or the close button) has superseded this request -
        // drop the stale response instead of letting it re-populate the panel
        if (thisSeq !== homeSearchSeq) return;
        var rows = [];
        res[0].materials.forEach(function (m) { rows.push([m.category || "Material", m.itemName, m.defaultSell]); });
        res[1].labour.forEach(function (l) { rows.push(["Labour", l.roleName, l.defaultSell]); });
        res[2].fixedServices.forEach(function (f) { rows.push([f.category || "Fixed Service", f.serviceName, f.standardSell]); });
        homeSearchCloseBtn.style.display = "";
        if (!rows.length) { homeSearchOut.innerHTML = '<p class="hint">No matches.</p>'; return; }
        var html = '<table><thead><tr><th>Category</th><th>Item</th><th class="num">Price</th></tr></thead><tbody>';
        rows.forEach(function (r) { html += "<tr><td>" + escapeHtml(r[0]) + "</td><td>" + escapeHtml(r[1]) + '</td><td class="num">' + (r[2] == null ? "—" : "AED " + fmt(r[2])) + "</td></tr>"; });
        html += "</tbody></table>";
        homeSearchOut.innerHTML = html;
      });
    }, 250));
  }

  // ==================================================================
  // SAVED QUOTES
  // ==================================================================
  var savedCache = [];
  var fullQuoteCache = {};

  function loadSaved() {
    var params = new URLSearchParams();
    params.set("team", ACTIVE_TEAM);
    var from = document.getElementById("filterFrom").value;
    var to = document.getElementById("filterTo").value;
    var status = document.getElementById("filterStatus").value;
    var preparedBy = document.getElementById("filterPreparedBy").value.trim();
    var q = document.getElementById("filterSearch").value;
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    if (status) params.set("status", status);
    if (preparedBy) params.set("preparedBy", preparedBy);
    if (q) params.set("q", q);
    API.get("/api/quotes?" + params.toString()).then(function (data) {
      savedCache = data.quotes;
      renderSavedTable(data.quotes);
      data.quotes.forEach(function (q) {
        API.get("/api/quotes/" + q.id).then(function (full) { fullQuoteCache[q.id] = full; }).catch(function () {});
      });
    }).catch(function () { toast("Could not reach the local server"); });
  }

  function statusBadgeHtml(status) {
    return '<span class="status-pill ' + statusPillClass(status) + '" style="font-size:10px;padding:3px 9px">' + escapeHtml(status) + "</span>";
  }

  function renderSavedTable(quotes) {
    var body = document.getElementById("savedBody");
    body.innerHTML = "";
    quotes.forEach(function (q) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(q.date || "") + "</td>" +
        "<td>" + escapeHtml(q.quoteNo || "") + "</td>" +
        "<td>" + statusBadgeHtml(q.status) + "</td>" +
        "<td>" + escapeHtml(q.client.name || "") + "</td>" +
        "<td>" + escapeHtml(q.client.address || "") + "</td>" +
        "<td>" + escapeHtml(q.preparedBy || q.createdBy || "—") + "</td>" +
        '<td class="num">AED ' + fmt(q.grandTotal) + "</td>" +
        '<td class="rowactions"></td>';
      var cell = tr.querySelector(".rowactions");

      var viewBtn = document.createElement("button");
      viewBtn.className = "btn btn-outline btn-sm"; viewBtn.textContent = "View / Edit";
      viewBtn.addEventListener("click", function () {
        API.get("/api/quotes/" + q.id).then(function (full) {
          loadQuoteIntoBuilder(full);
          goToTab("builder");
          toast("Quote loaded into builder");
        }).catch(function () { toast("Could not load quote"); });
      });
      cell.appendChild(viewBtn);

      if (q.status === "Sent to Jobber") {
        var revBtn = document.createElement("button");
        revBtn.className = "btn btn-orange btn-sm"; revBtn.textContent = "Create Revision";
        revBtn.addEventListener("click", function () {
          API.post("/api/quotes/" + q.id + "/revise").then(function (rev) {
            loadQuoteIntoBuilder(rev);
            goToTab("builder");
            toast("Revision created");
          }).catch(function (e) { toast(e.message || "Could not create revision"); });
        });
        cell.appendChild(revBtn);
      }

      var dupBtn = document.createElement("button");
      dupBtn.className = "btn btn-outline btn-sm"; dupBtn.textContent = "Duplicate";
      dupBtn.addEventListener("click", function () { doDuplicate(q.id); });
      cell.appendChild(dupBtn);

      var copyBtn = document.createElement("button");
      copyBtn.className = "btn btn-ghost btn-sm"; copyBtn.textContent = "Copy";
      copyBtn.addEventListener("click", function () {
        var cached = fullQuoteCache[q.id];
        if (cached) {
          copyToClipboard(buildJobberText(cached))
            .then(function () { toast("Copied — paste into Jobber"); })
            .catch(function () { toast("Could not copy automatically — select text manually"); });
        } else {
          API.get("/api/quotes/" + q.id).then(function (full) {
            fullQuoteCache[q.id] = full;
            toast("Quote details just loaded — click Copy again");
          }).catch(function () { toast("Could not load quote"); });
        }
      });
      cell.appendChild(copyBtn);

      var helperBtn = document.createElement("button");
      helperBtn.className = "btn btn-ghost btn-sm"; helperBtn.textContent = "Entry Helper";
      helperBtn.addEventListener("click", function () {
        var cached = fullQuoteCache[q.id];
        if (cached) { openJobberHelper(cached); return; }
        API.get("/api/quotes/" + q.id).then(function (full) {
          fullQuoteCache[q.id] = full;
          openJobberHelper(full);
        }).catch(function () { toast("Could not load quote"); });
      });
      cell.appendChild(helperBtn);

      if (q.status === "Draft") {
        var delBtn = document.createElement("button");
        delBtn.className = "btn btn-ghost btn-sm"; delBtn.textContent = "Delete";
        delBtn.addEventListener("click", function () {
          confirmDialog("Delete quote " + q.quoteNo + " for " + (q.client.name || "this client") + "? This cannot be undone.").then(function (ok) {
            if (!ok) return;
            API.del("/api/quotes/" + q.id).then(function () { toast("Quote deleted"); loadSaved(); })
              .catch(function (e) { toast(e.message || "Could not delete quote"); });
          });
        });
        cell.appendChild(delBtn);
      }

      body.appendChild(tr);
    });
    document.getElementById("savedCount").textContent = quotes.length + " quote(s) found";
  }

  function buildJobberText(q) {
    var lines = [];
    lines.push("Client: " + (q.client.name || ""));
    lines.push("Property: " + (q.client.address || ""));
    lines.push("Phone: " + (q.client.phone || ""));
    lines.push("Quote No: " + q.quoteNo + "   Date: " + q.date + "   Valid Until: " + q.validUntil);
    lines.push("");
    if (q.scope) { lines.push("Scope of Work:"); lines.push(q.scope); lines.push(""); }
    if (q.duration) { lines.push("Estimated Duration: " + q.duration); lines.push(""); }

    if (q.items && q.items.length) {
      lines.push("Items");
      lines.push("Type\tDescription\tQty\tSell Price\tTotal");
      q.items.forEach(function (it) {
        lines.push((ITEM_KIND_LABELS[it.kind] || it.kind) + "\t" + it.desc + "\t" + it.qty + "\tAED " + fmt(it.sell) + "\tAED " + fmt(it.sell * it.qty));
      });
      lines.push("");
    }

    lines.push("Total Cost: AED " + fmt(q.costPrice));
    if (q.vehicle) lines.push("Transport: AED " + fmt(q.vehicle));
    if (q.callOutAmount) lines.push("Call-out Fee: AED " + fmt(q.callOutAmount));
    lines.push("Gross: AED " + fmt(q.gross));
    if (q.discountAmount) lines.push("Discount: -AED " + fmt(q.discountAmount));
    lines.push("Selling Price: AED " + fmt(q.sellingPrice));
    lines.push("Mark-up: " + (q.markupPct * 100).toFixed(1) + "%   Gross Margin: " + (q.marginPct * 100).toFixed(1) + "% (" + q.marginBand + ")");
    lines.push("VAT (" + q.vatPct + "%): AED " + fmt(q.vatAmount));
    lines.push("Grand Total: AED " + fmt(q.grandTotal));
    return lines.join("\n");
  }

  function buildJobberTableText(quotes) {
    var lines = ["Date\tQuote No\tStatus\tClient\tProperty\tGrand Total"];
    quotes.forEach(function (q) {
      lines.push([q.date, q.quoteNo, q.status, q.client.name, q.client.address, "AED " + fmt(q.grandTotal)].join("\t"));
    });
    return lines.join("\n");
  }

  function wireSavedActions() {
    document.getElementById("applyFilters").addEventListener("click", loadSaved);
    document.getElementById("clearFilters").addEventListener("click", function () {
      document.getElementById("filterFrom").value = "";
      document.getElementById("filterTo").value = "";
      document.getElementById("filterStatus").value = "";
      document.getElementById("filterPreparedBy").value = "";
      document.getElementById("filterSearch").value = "";
      loadSaved();
    });
    document.getElementById("copyTableBtn").addEventListener("click", function () {
      if (!savedCache.length) { toast("No quotes to copy"); return; }
      copyToClipboard(buildJobberTableText(savedCache))
        .then(function () { toast("Table copied — paste into Jobber or a spreadsheet"); })
        .catch(function () { toast("Could not copy automatically"); });
    });
  }

  // ------------------------------------------------------------------
  // init
  // ------------------------------------------------------------------
  function initPrintFix() {
    var originalHeights = [];
    window.addEventListener("beforeprint", function () {
      document.querySelectorAll("textarea").forEach(function (t) {
        originalHeights.push([t, t.style.height]);
        t.style.height = "auto";
        t.style.height = t.scrollHeight + "px";
      });
    });
    window.addEventListener("afterprint", function () {
      originalHeights.forEach(function (pair) { pair[0].style.height = pair[1]; });
      originalHeights = [];
    });
  }

  var CURRENT_USER = null;
  var CURRENT_USER_ROLE = "staff";

  function applyRoleVisibility() {
    var isAdmin = CURRENT_USER_ROLE === "admin";
    document.querySelectorAll(".admin-only").forEach(function (el) { el.style.display = isAdmin ? "" : "none"; });
    var hint = document.getElementById("pbAdminHint");
    if (hint) hint.style.display = isAdmin ? "none" : "";
    var roleEl = document.getElementById("currentUserRole");
    if (roleEl) { roleEl.textContent = CURRENT_USER_ROLE; roleEl.style.display = ""; }
  }

  function wireLogout() {
    var btn = document.getElementById("btnLogout");
    if (!btn) return;
    btn.addEventListener("click", function () {
      API.post("/api/auth/logout", {}).then(function () {
        window.location.href = "/login.html";
      }).catch(function () { window.location.href = "/login.html"; });
    });
  }

  // ==================================================================
  // AMC TRACKER
  // ==================================================================
  var AMC_META = null;
  var AMC_CLIENTS = [];
  var AMC_CLIENTS_ALL = []; // active + archived - only used by the Tracker's "Show archived" view
  var AMC_REVENUE = [];
  var AMC_REVENUE_MONTHS = []; // closed months - see amcRenderRevenue()
  var AMC_HISTORY = [];
  var AMC_HANDLED = {};
  var amcExpandedRowId = null;
  var AMC_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  function loadAmcMeta() {
    API.get("/api/amc/meta").then(function (d) { AMC_META = d; }).catch(function () {});
  }

  function initAmcTracker() {
    document.querySelectorAll("#amcSubnav .amc-subtab").forEach(function (btn) {
      btn.addEventListener("click", function () { amcSwitchSubtab(btn.dataset.amc); });
    });
    document.getElementById("amcBtnGoAdd").addEventListener("click", function () { amcSwitchSubtab("add"); });
    document.getElementById("amcBtnExport").addEventListener("click", function () {
      window.location.href = "/api/amc/clients/export?team=" + encodeURIComponent(ACTIVE_TEAM) +
        (document.getElementById("amcTrkShowArchived").checked ? "&showArchived=1" : "");
    });

    document.getElementById("amcTrkSearch").addEventListener("input", amcRenderTracker);
    document.getElementById("amcTrkPkg").addEventListener("change", amcRenderTracker);
    document.getElementById("amcTrkStatus").addEventListener("change", amcRenderTracker);
    document.getElementById("amcTrkShowArchived").addEventListener("change", function () {
      amcExpandedRowId = null;
      amcRenderTracker();
    });
    document.getElementById("amcHistSearch").addEventListener("input", amcRenderHistory);
    document.getElementById("amcHideHandled").addEventListener("change", amcRenderActions);
    amcWireRevenue();

    document.getElementById("amcFPackage").addEventListener("input", amcUpdateSchedulePreview);
    document.getElementById("amcFStart").addEventListener("input", amcUpdateSchedulePreview);
    document.getElementById("amcFStart").addEventListener("change", function () {
      var endField = document.getElementById("amcFEnd");
      if (endField.value) return;
      var start = document.getElementById("amcFStart").value;
      if (!start) return;
      endField.value = amcOneYearMinusDay(start);
    });
    document.getElementById("amcLnkOneYear").addEventListener("click", function (e) {
      e.preventDefault();
      var start = document.getElementById("amcFStart").value;
      if (!start) { toast("Set a start date first"); return; }
      document.getElementById("amcFEnd").value = amcOneYearMinusDay(start);
    });
    document.getElementById("amcBtnFormClear").addEventListener("click", function () {
      document.getElementById("amcAddForm").reset();
      amcUpdateSchedulePreview();
      amcShowFormErrors([]);
    });
    document.getElementById("amcAddForm").addEventListener("submit", amcSubmitAddForm);
  }

  function amcSwitchSubtab(name) {
    document.querySelectorAll("#amcSubnav .amc-subtab").forEach(function (b) { b.classList.toggle("active", b.dataset.amc === name); });
    document.querySelectorAll(".amc-panel").forEach(function (p) {
      var isActive = p.id === "amc-" + name;
      p.classList.toggle("active", isActive);
      // classList alone can't win against the inline style="display:none"
      // these panels are born with in index.html - clear it explicitly so
      // the .amc-panel.active CSS rule actually takes effect.
      p.style.display = isActive ? "" : "none";
    });
  }

  function loadAmcAll() {
    Promise.all([
      API.get("/api/amc/clients?team=" + encodeURIComponent(ACTIVE_TEAM)),
      API.get("/api/amc/clients?team=" + encodeURIComponent(ACTIVE_TEAM) + "&showArchived=1"),
      API.get("/api/amc/history?team=" + encodeURIComponent(ACTIVE_TEAM)),
      API.get("/api/amc/actions-handled?team=" + encodeURIComponent(ACTIVE_TEAM)),
      API.get("/api/amc/revenue?team=" + encodeURIComponent(ACTIVE_TEAM)),
      API.get("/api/amc/revenue/months?team=" + encodeURIComponent(ACTIVE_TEAM))
    ]).then(function (res) {
      AMC_CLIENTS = res[0].clients;
      AMC_CLIENTS_ALL = res[1].clients;
      AMC_HISTORY = res[2].history;
      AMC_HANDLED = {};
      (res[3].handled || []).forEach(function (k) { AMC_HANDLED[k] = true; });
      AMC_REVENUE = res[4].entries;
      AMC_REVENUE_MONTHS = res[5].months;
      amcExpandedRowId = null;
      amcRenderDashboard();
      amcRenderTracker();
      amcRenderActions();
      amcRenderHistory();
      amcRenderRevenue();
    }).catch(function () { toast("Could not load AMC data"); });
  }

  function amcFmtDate(iso) {
    if (!iso) return "—";
    var parts = iso.split("-");
    if (parts.length !== 3) return "—";
    var y = +parts[0], m = +parts[1], d = +parts[2];
    if (!y || !m || !d) return "—";
    return String(d).padStart(2, "0") + "-" + AMC_MONTHS[m - 1] + "-" + y;
  }
  function amcIso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function amcOneYearMinusDay(startISO) {
    var parts = startISO.split("-").map(Number);
    var d = new Date(parts[0], parts[1] - 1, parts[2]);
    d.setFullYear(d.getFullYear() + 1);
    d.setDate(d.getDate() - 1);
    return amcIso(d);
  }
  function amcVisitDueDatePreview(startISO, off) {
    var parts = startISO.split("-").map(Number);
    var d = new Date(parts[0], parts[1] - 1, parts[2]);
    if (off.d) { d.setDate(d.getDate() + off.d); return amcIso(d); }
    var day = d.getDate();
    d.setDate(1);
    d.setMonth(d.getMonth() + off.m);
    var lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(day, lastDay));
    return amcIso(d);
  }

  function amcVisitPillClass(status) {
    return { "Done": "target", "OVERDUE": "critical", "Due Soon": "warn", "Scheduled": "above" }[status] || "muted";
  }
  function amcManualStatusPillClass(status) {
    return { "Done": "target", "Overdue": "critical", "Scheduled": "above", "Pending": "warn" }[status] || "muted";
  }
  function amcTrackPillClass(track) {
    return { behind: "critical", ontrack: "target", complete: "above" }[track] || "muted";
  }
  function amcPayPillClass(flag) {
    return { paid: "target", partial: "warn", unpaid: "critical" }[flag] || "muted";
  }
  function amcAlertPillClass(alert) {
    return (alert === "Expired" || alert === "Send now") ? "critical" : "warn";
  }
  function amcBadge(label, cls) {
    return '<span class="band-badge ' + cls + '" style="margin-left:0">' + escapeHtml(label) + "</span>";
  }

  // ---------------------------------------------------------------- DASHBOARD
  function amcRenderDashboard() {
    var clients = AMC_CLIENTS;
    var active = clients.length;
    var renew14 = 0, renew30 = 0, paymentsOut = 0, visitsOverdue = 0, premium = 0;
    var behind = [];
    clients.forEach(function (c) {
      if (c.daysLeft !== null) {
        if (c.daysLeft >= 0 && c.daysLeft <= 14) renew14++;
        if (c.daysLeft >= 0 && c.daysLeft <= 30) renew30++;
      }
      if (c.payStatus === "Unpaid" || c.payStatus === "50% Paid") paymentsOut++;
      visitsOverdue += c.visits.filter(function (v) { return v.status === "OVERDUE"; }).length;
      if (c.trackStatus === "behind") behind.push(c);
      if (c.package === "Premium") premium++;
    });

    var kpis = [
      { label: "Active Contracts", num: active, cls: "" },
      { label: "Renewals ≤ 14 Days", num: renew14, cls: "stat-card-red" },
      { label: "Renewals ≤ 30 Days", num: renew30, cls: "stat-card-amber" },
      { label: "Payments Outstanding", num: paymentsOut, cls: "stat-card-amber" },
      { label: "Visits Overdue", num: visitsOverdue, cls: "stat-card-red" },
      { label: "Behind Schedule", num: behind.length, cls: "stat-card-red" },
      { label: "Premium Clients", num: premium, cls: "stat-card-blue" }
    ];
    document.getElementById("amcKpiRow").innerHTML = kpis.map(function (k) {
      return '<div class="card stat-card ' + k.cls + '"><div class="stat-lbl">' + escapeHtml(k.label) + '</div><div class="stat-val">' + k.num + "</div></div>";
    }).join("");

    var behindListEl = document.getElementById("amcBehindList");
    if (behind.length === 0) {
      behindListEl.innerHTML = '<li class="hint" style="padding:8px 0">Nothing behind schedule right now. Nicely done.</li>';
    } else {
      behindListEl.innerHTML = behind.map(function (c) {
        var overdueVisits = c.visits.filter(function (v) { return v.status === "OVERDUE"; }).length;
        return '<li class="amc-flag-item" data-goto="' + c.id + '"><span><span class="amc-fi-name">' + escapeHtml(c.customer) +
          '</span><br><span class="amc-fi-meta">' + escapeHtml(c.location || "") + " · " + escapeHtml(c.package) + "</span></span>" +
          '<span class="amc-fi-meta">' + overdueVisits + " visit" + (overdueVisits === 1 ? "" : "s") + " overdue</span></li>";
      }).join("");
      behindListEl.querySelectorAll(".amc-flag-item").forEach(function (li) {
        li.addEventListener("click", function () {
          amcSwitchSubtab("tracker");
          setTimeout(function () { amcOpenClientRow(li.dataset.goto); }, 60);
        });
      });
    }

    var counts = { Basic: 0, Standard: 0, Premium: 0 };
    clients.forEach(function (c) { if (counts[c.package] !== undefined) counts[c.package]++; });
    var colors = { Basic: "#6B7A93", Standard: "#02265E", Premium: "#FA6006" };
    var max = Math.max(1, counts.Basic, counts.Standard, counts.Premium);
    document.getElementById("amcSplitChart").innerHTML = Object.keys(counts).map(function (pkg) {
      return '<div class="amc-split-row"><span class="amc-split-label">' + pkg + "</span>" +
        '<span class="amc-split-track"><span class="amc-split-fill" style="width:' + (counts[pkg] / max * 100).toFixed(0) + "%;background:" + colors[pkg] + '"></span></span>' +
        '<span class="amc-split-count">' + counts[pkg] + " clients</span></div>";
    }).join("");

    document.getElementById("amcCntTracker").textContent = active;
    document.getElementById("amcCntActions").textContent = behind.length + paymentsOut + visitsOverdue;
  }

  // ---------------------------------------------------------- REVENUE CALCULATOR
  var AMC_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];

  function amcRevMonthLabel(key) {
    var parts = key.split("-");
    return AMC_MONTH_NAMES[parseInt(parts[1], 10) - 1] + " " + parts[0];
  }

  function amcRenderRevenue() {
    var closedKeys = {};
    AMC_REVENUE_MONTHS.forEach(function (m) { closedKeys[m.monthKey] = true; });
    var currentEntries = AMC_REVENUE.filter(function (e) { return !closedKeys[(e.date || "").slice(0, 7)]; });

    // ---- current-month summary strip ----
    var openMonthKeys = Object.keys(currentEntries.reduce(function (acc, e) { acc[(e.date || "").slice(0, 7)] = true; return acc; }, {}));
    var currentTotal = currentEntries.reduce(function (sum, e) { return sum + (Number(e.value) || 0); }, 0);
    var currentLabel = openMonthKeys.length === 1 ? amcRevMonthLabel(openMonthKeys[0])
      : openMonthKeys.length > 1 ? (openMonthKeys.length + " open months (never closed)")
      : amcRevMonthLabel(todayISO().slice(0, 7));
    document.getElementById("amcRevCurrentSummary").innerHTML =
      '<span class="arc-label">' + escapeHtml(currentLabel) + '</span>' +
      '<span class="arc-total">AED ' + Math.round(currentTotal).toLocaleString() + "</span>" +
      '<span class="arc-meta">' + currentEntries.length + (currentEntries.length === 1 ? " entry" : " entries") + " · not yet closed</span>";

    // ---- current month entries table ----
    var entriesBody = document.getElementById("amcRevEntriesBody");
    if (!currentEntries.length) {
      entriesBody.innerHTML = '<tr><td colspan="4" class="hint" style="padding:14px 8px">Nothing logged yet this month — add the first entry above.</td></tr>';
    } else {
      entriesBody.innerHTML = currentEntries.map(function (e) {
        var who = [e.clientName, e.details].filter(Boolean).map(escapeHtml).join(" — ");
        return "<tr>" +
          "<td>" + amcFmtDate(e.date) + "</td>" +
          "<td>" + (who || '<span class="hint">—</span>') + "</td>" +
          '<td class="num">' + Math.round(e.value).toLocaleString() + "</td>" +
          '<td><button type="button" class="btn btn-ghost btn-sm" data-del-rev="' + e.id + '" title="Delete entry">&times;</button></td>' +
          "</tr>";
      }).join("");
    }

    // ---- month history (closed months) ----
    var monthlyBody = document.getElementById("amcRevMonthlyBody");
    if (!AMC_REVENUE_MONTHS.length) {
      monthlyBody.innerHTML = '<tr><td colspan="4" class="hint" style="padding:14px 8px">No closed months yet — use "Start New Month" once this month is done.</td></tr>';
    } else {
      var sorted = AMC_REVENUE_MONTHS.slice().sort(function (a, b) { return b.monthKey.localeCompare(a.monthKey); });
      monthlyBody.innerHTML = sorted.map(function (m) {
        return "<tr><td>" + escapeHtml(m.label) + "</td><td class=\"num\">" + Math.round(m.total).toLocaleString() +
          "</td><td class=\"num\">" + m.count + "</td><td>" + amcFmtDate((m.closedAt || "").slice(0, 10)) + "</td></tr>";
      }).join("");
    }
  }

  function amcWireRevenue() {
    var dateInput = document.getElementById("amcRevDate");
    if (!dateInput.value) dateInput.value = todayISO();

    document.getElementById("amcRevAddBtn").addEventListener("click", function () {
      var value = parseFloat(document.getElementById("amcRevValue").value);
      if (!value || value <= 0) { toast("Enter a contract value greater than 0"); return; }
      var body = {
        team: ACTIVE_TEAM,
        value: value,
        clientName: document.getElementById("amcRevClient").value.trim(),
        details: document.getElementById("amcRevDetails").value.trim(),
        date: document.getElementById("amcRevDate").value || todayISO()
      };
      API.post("/api/amc/revenue", body).then(function () {
        document.getElementById("amcRevValue").value = "";
        document.getElementById("amcRevClient").value = "";
        document.getElementById("amcRevDetails").value = "";
        document.getElementById("amcRevDate").value = todayISO();
        toast("Entry added");
        loadAmcAll();
      }).catch(function (err) { toast(err.message || "Could not add entry"); });
    });

    document.getElementById("amcRevExportBtn").addEventListener("click", function () {
      window.open("/api/amc/revenue/export?team=" + encodeURIComponent(ACTIVE_TEAM), "_blank");
    });

    document.getElementById("amcRevStartNewMonthBtn").addEventListener("click", function () {
      confirmDialog(
        "Start a new month? Every past month that still has unclosed entries gets its total frozen into Month " +
        "History, permanently - the current month always stays open. This can't be undone."
      ).then(function (ok) {
        if (!ok) return;
        API.post("/api/amc/revenue/close-month", { team: ACTIVE_TEAM }).then(function (res) {
          var closed = res.closed || [];
          toast(closed.length === 1 ? ("Closed " + closed[0].label) : (closed.length + " months closed"));
          loadAmcAll();
        }).catch(function (err) { toast(err.message || "Could not close the month"); });
      });
    });

    document.getElementById("amcRevEntriesBody").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-del-rev]");
      if (!btn) return;
      confirmDialog("Delete this entry? This cannot be undone.").then(function (ok) {
        if (!ok) return;
        API.del("/api/amc/revenue/" + btn.dataset.delRev).then(function () {
          toast("Entry deleted");
          loadAmcAll();
        }).catch(function (err) { toast(err.message || "Could not delete"); });
      });
    });
  }

  // ---------------------------------------------------------------- TRACKER
  function amcPassesFilters(c) {
    var q = document.getElementById("amcTrkSearch").value.trim().toLowerCase();
    var pkg = document.getElementById("amcTrkPkg").value;
    var st = document.getElementById("amcTrkStatus").value;
    if (q && (c.customer || "").toLowerCase().indexOf(q) === -1 && (c.location || "").toLowerCase().indexOf(q) === -1) return false;
    if (pkg && c.package !== pkg) return false;
    if (st && c.trackStatus !== st) return false;
    return true;
  }

  function amcRenderTracker() {
    var tbody = document.getElementById("amcTrackerBody");
    var showArchived = document.getElementById("amcTrkShowArchived").checked;
    var source = showArchived ? AMC_CLIENTS_ALL : AMC_CLIENTS;
    var rows = source.filter(amcPassesFilters);
    document.getElementById("amcTrkCount").textContent = rows.length + " of " + source.length + " clients";

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="15" style="padding:24px;text-align:center;color:var(--muted)">No clients match these filters.</td></tr>';
      return;
    }

    var html = "";
    rows.forEach(function (c) {
      var trackLabel = { behind: "⚠ Behind schedule", ontrack: "✓ On track", complete: "✓ All complete" }[c.trackStatus];
      var isOpen = amcExpandedRowId === c.id;
      var visitCells = c.visits.map(function (v) {
        var label = v.status === "N/A" ? "—" : v.status;
        return "<td>" + amcBadge(label, amcVisitPillClass(v.status)) + "</td>";
      }).join("");
      html += '<tr class="amc-row-main' + (c.flag ? " amc-row-flagged" : "") + (c.archived ? " amc-row-archived" : "") + '" data-id="' + c.id + '">' +
        '<td><span class="amc-chevron">' + (isOpen ? "▾" : "▸") + "</span></td>" +
        '<td class="amc-cust-cell">' + escapeHtml(c.customer) + (c.archived ? ' <span class="amc-count" style="background:var(--muted);color:#fff">Archived</span>' : "") + '<span class="amc-cust-sub">' + escapeHtml(c.location || "") + (c.flag ? " · ⚠ needs review" : "") + "</span></td>" +
        "<td>" + escapeHtml(c.package) + "</td>" +
        "<td>" + amcFmtDate(c.start) + "</td>" +
        "<td>" + amcFmtDate(c.end) + "</td>" +
        '<td class="num">' + (c.daysLeft === null ? "—" : c.daysLeft) + "</td>" +
        "<td>" + (c.renewalAlert ? amcBadge(c.renewalAlert, amcAlertPillClass(c.renewalAlert)) : "") + "</td>" +
        "<td>" + amcBadge(c.payStatus, amcPayPillClass(c.payFlag)) + "</td>" +
        '<td class="amc-pay-edit"><input type="number" class="amc-pay-input" data-pay-for="' + c.id + '" min="0" step="any" placeholder="AED 0.00" title="Amount in AED" value="' + (c.payAmount == null ? "" : c.payAmount) + '"></td>' +
        visitCells +
        "<td>" + amcBadge(trackLabel, amcTrackPillClass(c.trackStatus)) + "</td>" +
        "<td>" + amcBadge(c.wtcStatus || "N/A", amcManualStatusPillClass(c.wtcStatus)) + "</td>" +
        "</tr>";
      if (isOpen) html += amcRenderDetailRow(c);
    });
    tbody.innerHTML = html;

    tbody.querySelectorAll("tr.amc-row-main").forEach(function (tr) {
      tr.addEventListener("click", function () { amcOpenClientRow(tr.dataset.id); });
    });
    tbody.querySelectorAll(".amc-pay-edit").forEach(function (td) {
      td.addEventListener("click", function (e) { e.stopPropagation(); });
      var input = td.querySelector("input");
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") input.blur();
        else if (e.key === "Escape") { input.value = input.defaultValue; input.blur(); }
      });
      input.addEventListener("change", function () { amcSavePaymentNote(td, input); });
    });
    amcWireDetailInputs();
  }

  function amcSavePaymentNote(td, input) {
    var id = input.dataset.payFor;
    var value = input.value.trim();
    if (value !== "" && (isNaN(+value) || +value < 0)) { toast("Enter an amount of 0 or more"); input.value = input.defaultValue; return; }
    API.put("/api/amc/clients/" + id, { payAmount: value === "" ? null : +value }).then(function (updated) {
      [AMC_CLIENTS, AMC_CLIENTS_ALL].forEach(function (list) {
        var idx = list.findIndex(function (c) { return c.id === id; });
        if (idx !== -1) list[idx] = updated;
      });
      input.value = updated.payAmount == null ? "" : updated.payAmount;
      input.defaultValue = input.value;
      var open = document.querySelector('tr[data-detail-for="' + id + '"] [data-field="payAmount"]');
      if (open) open.value = input.value;
      td.classList.add("saved");
      setTimeout(function () { td.classList.remove("saved"); }, 900);
    }).catch(function (err) { toast(err.message || "Could not save payment details"); });
  }

  function amcOpenClientRow(id) {
    amcExpandedRowId = (amcExpandedRowId === id) ? null : id;
    amcRenderTracker();
    if (amcExpandedRowId === id) {
      var el = document.querySelector('tr.amc-row-main[data-id="' + id + '"]');
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function amcOptionList(options, current) {
    return options.map(function (o) { return "<option " + (current === o ? "selected" : "") + ">" + o + "</option>"; }).join("");
  }

  function amcRenderDetailRow(c) {
    var hmAllowed = c.handymanVisitsAllowed || 0;
    var pastYears = AMC_HISTORY.filter(function (h) {
      return h.customer === c.customer && h.location === c.location && !(h.start === c.start && h.end === c.end);
    }).sort(function (a, b) { return (b.start || "").localeCompare(a.start || ""); });

    var historyStatusCls = { Active: "target", Expired: "muted", Cancelled: "critical" };
    var historyHtml = pastYears.length === 0
      ? '<p class="hint" style="padding:0">No earlier contract years on record for this client.</p>'
      : pastYears.map(function (h) {
          return '<div class="amc-history-line"><span class="hl-year">' + escapeHtml(h.year) + "</span>" +
            "<span>" + escapeHtml(h.package) + "</span>" +
            amcBadge(h.status, historyStatusCls[h.status] || "muted") +
            "<span>" + (h.start ? amcFmtDate(h.start) : "—") + " → " + (h.end ? amcFmtDate(h.end) : "—") + "</span>" +
            (h.notes ? '<span class="hl-notes">' + escapeHtml(h.notes) + "</span>" : "") + "</div>";
        }).join("");

    var visitRowsHtml = c.visits.map(function (v) {
      if (v.status === "N/A") {
        return '<div class="amc-visit-row"><span class="v-n">V' + v.n + '</span><span class="v-due">Not applicable</span><span></span><span></span></div>';
      }
      return '<div class="amc-visit-row"><span class="v-n">V' + v.n + '</span><span class="v-due">' + amcFmtDate(v.due) + '</span>' +
        '<input type="date" data-field="v' + v.n + 'Done" value="' + (v.done || "") + '">' +
        amcBadge(v.status, amcVisitPillClass(v.status)) + "</div>";
    }).join("");

    var visitStatusList = ["Pending", "Scheduled", "Done", "Overdue", "N/A"];
    var teamOpts = ["dubai", "magcity"].map(function (t) {
      return '<option value="' + t + '" ' + (c.team === t ? "selected" : "") + ">" + TEAM_LABELS[t] + "</option>";
    }).join("");

    return '<tr class="amc-row-detail" data-detail-for="' + c.id + '"><td colspan="15">' +
      '<div class="amc-detail-grid">' +
        '<div class="amc-detail-block"><div class="amc-detail-block-head"><h4>Contract</h4>' +
          '<button type="button" class="btn btn-ghost btn-sm amc-renew-btn">Renew Contract &rarr;</button></div>' +
          '<div class="amc-field-row"><label>Location</label><input type="text" data-field="location" value="' + escapeAttr(c.location || "") + '"></div>' +
          '<div class="amc-field-row"><label>Address</label><input type="text" data-field="address" value="' + escapeAttr(c.address || "") + '"></div>' +
          '<div class="amc-field-inline">' +
            '<div><label>Owner / Tenant</label><select data-field="ownerTenant">' + amcOptionList(["Owner", "Tenant", "Commercial"], c.ownerTenant) + "</select></div>" +
            '<div><label>Package</label><select data-field="package">' + amcOptionList(["Basic", "Standard", "Premium"], c.package) + "</select></div>" +
          "</div>" +
          '<div class="amc-field-inline">' +
            '<div><label>Start date</label><input type="date" data-field="start" value="' + c.start + '"></div>' +
            '<div><label>End date</label><input type="date" data-field="end" value="' + c.end + '"></div>' +
          "</div>" +
          '<div class="amc-field-row"><label>Team / Location</label><select data-field="team">' + teamOpts + "</select></div>" +
        "</div>" +

        '<div class="amc-detail-block"><h4>Payment</h4>' +
          '<div class="amc-field-inline">' +
            '<div><label>Type</label><select data-field="payType">' + amcOptionList(["Upfront", "50/50", "Monthly", "FOC"], c.payType) + "</select></div>" +
            '<div><label>Status</label><select data-field="payStatus">' + amcOptionList(["Paid", "50% Paid", "Unpaid", "FOC"], c.payStatus) + "</select></div>" +
          "</div>" +
          '<div class="amc-field-row"><label>Amount (AED)</label><input type="number" min="0" step="any" data-field="payAmount" value="' + (c.payAmount == null ? "" : c.payAmount) + '"></div>' +
          '<div class="amc-field-row"><label>Payment notes</label><input type="text" data-field="payNotes" value="' + escapeAttr(c.payNotes || "") + '"></div>' +
        "</div>" +

        '<div class="amc-detail-block"><h4>AC PPM Visits (' + escapeHtml(c.package) + ")</h4>" + visitRowsHtml + "</div>" +

        '<div class="amc-detail-block"><h4>WTC &amp; Handyman</h4>' +
          '<div class="amc-field-inline">' +
            '<div><label>WTC status</label><select data-field="wtcStatus">' + amcOptionList(visitStatusList, c.wtcStatus) + "</select></div>" +
            '<div><label>WTC date</label><input type="date" data-field="wtcDate" value="' + (c.wtcDate || "") + '"></div>' +
          "</div>" +
          '<div class="amc-field-inline">' +
            '<div><label>Handyman V1</label><select data-field="hm1Status">' + amcOptionList(visitStatusList, c.hm1Status) + "</select></div>" +
            '<div><label>Date</label><input type="date" data-field="hm1Date" value="' + (c.hm1Date || "") + '"></div>' +
          "</div>" +
          (hmAllowed >= 2 ?
            '<div class="amc-field-inline">' +
              '<div><label>Handyman V2</label><select data-field="hm2Status">' + amcOptionList(visitStatusList, c.hm2Status) + "</select></div>" +
              '<div><label>Date</label><input type="date" data-field="hm2Date" value="' + (c.hm2Date || "") + '"></div>' +
            "</div>" : "") +
        "</div>" +

        '<div class="amc-detail-block" style="grid-column:1/-1"><h4>Notes</h4><textarea data-field="notes" style="min-height:60px">' + escapeHtml(c.notes || "") + "</textarea></div>" +

        '<div class="amc-detail-block" style="grid-column:1/-1"><h4>Contract History — ' + escapeHtml(c.customer) + (c.location ? " (" + escapeHtml(c.location) + ")" : "") + "</h4><div>" + historyHtml + "</div></div>" +

        (c.flag ? '<div class="amc-detail-notice">⚠ ' + escapeHtml(c.flag) + "</div>" : "") +
        (c.archived ? '<div class="amc-detail-notice">Archived' + (c.archivedByEmail ? " by " + escapeHtml(c.archivedByEmail) : "") + (c.archivedAt ? " on " + amcFmtDate(c.archivedAt.slice(0, 10)) : "") + " — hidden from the default Tracker view, Dashboard and Today's Actions." + "</div>" : "") +
      "</div>" +
      '<div class="amc-detail-actions">' +
        '<button type="button" class="btn btn-orange btn-sm amc-save-btn" style="display:none">Save Changes</button>' +
        '<button type="button" class="btn btn-ghost btn-sm amc-cancel-btn" style="display:none">Cancel</button>' +
        '<button type="button" class="btn btn-ghost btn-sm amc-archive-btn">' + (c.archived ? "Restore" : "Archive") + "</button>" +
        (CURRENT_USER_ROLE === "admin" ? '<button type="button" class="btn btn-ghost btn-sm amc-delete-btn" style="color:var(--red);border-color:var(--red)">Delete Permanently</button>' : "") +
        '<span class="hint amc-save-hint">Editing — changes are not saved until you click Save.</span>' +
      "</div>" +
    "</td></tr>";
  }

  // Fields no longer save one at a time on every change - editing the row
  // reveals Save/Cancel, and Save sends every field in this row as a single
  // batched PUT (the API already accepts any subset of AMC_CLIENT_FIELD_COLUMNS
  // in one request), so a run of edits across several fields becomes one
  // confirmed save instead of one silent request per field.
  function amcWireDetailInputs() {
    document.querySelectorAll("tr.amc-row-detail").forEach(function (tr) {
      var id = tr.dataset.detailFor;
      var saveBtn = tr.querySelector(".amc-save-btn");
      var cancelBtn = tr.querySelector(".amc-cancel-btn");
      var renewBtn = tr.querySelector(".amc-renew-btn");
      var archiveBtn = tr.querySelector(".amc-archive-btn");
      var deleteBtn = tr.querySelector(".amc-delete-btn");
      var hint = tr.querySelector(".amc-save-hint");
      var fields = tr.querySelectorAll("[data-field]");

      function findClient() {
        return AMC_CLIENTS_ALL.filter(function (x) { return x.id === id; })[0] ||
          AMC_CLIENTS.filter(function (x) { return x.id === id; })[0];
      }

      renewBtn.addEventListener("click", function () {
        var client = findClient();
        if (!client || !client.end) { toast("This client has no end date to renew from"); return; }
        var endParts = client.end.split("-").map(Number);
        var newStart = new Date(endParts[0], endParts[1] - 1, endParts[2] + 1);
        var newEnd = amcOneYearMinusDay(amcIso(newStart));
        confirmDialog(
          "Renew " + client.customer + "'s contract? This archives " + amcFmtDate(client.start) + " – " +
          amcFmtDate(client.end) + " into Contract History, then starts a new year from " +
          amcFmtDate(amcIso(newStart)) + " to " + amcFmtDate(newEnd) +
          ". Visit sign-offs, WTC, handyman and payment status all reset for the new year."
        ).then(function (ok) {
          if (!ok) return;
          API.post("/api/amc/clients/" + id + "/renew", {}).then(function () {
            toast("Contract renewed");
            loadAmcAll();
          }).catch(function (err) { toast(err.message || "Could not renew"); });
        });
      });

      archiveBtn.addEventListener("click", function () {
        var client = findClient();
        if (!client) return;
        var archiving = !client.archived;
        var message = archiving
          ? "Archive " + client.customer + "? They'll be hidden from the default Tracker view, Dashboard and Today's Actions until you restore them. Nothing is deleted."
          : "Restore " + client.customer + "? They'll reappear in the default Tracker view, Dashboard and Today's Actions.";
        confirmDialog(message).then(function (ok) {
          if (!ok) return;
          API.put("/api/amc/clients/" + id + "/archive", { archived: archiving }).then(function () {
            toast(archiving ? "Client archived" : "Client restored");
            amcExpandedRowId = null;
            loadAmcAll();
          }).catch(function (err) { toast(err.message || "Could not update"); });
        });
      });

      if (deleteBtn) {
        deleteBtn.addEventListener("click", function () {
          var client = findClient();
          if (!client) return;
          confirmDialog(
            "Permanently delete " + client.customer + "? This cannot be undone. Their Contract History stays on " +
            "record, but the client itself, their AC PPM schedule and any pending action items are gone for good."
          ).then(function (ok) {
            if (!ok) return;
            API.del("/api/amc/clients/" + id).then(function () {
              toast("Client deleted");
              amcExpandedRowId = null;
              loadAmcAll();
            }).catch(function (err) { toast(err.message || "Could not delete"); });
          });
        });
      }

      function showActions() {
        saveBtn.style.display = "";
        cancelBtn.style.display = "";
        hint.textContent = "Unsaved changes.";
      }

      fields.forEach(function (input) {
        input.addEventListener("input", showActions);
        input.addEventListener("change", showActions);
      });

      cancelBtn.addEventListener("click", function () {
        amcRenderTracker();
      });

      saveBtn.addEventListener("click", function () {
        var body = {};
        fields.forEach(function (input) { body[input.dataset.field] = input.value; });
        confirmDialog("Save changes to this client's record?").then(function (ok) {
          if (!ok) return;
          API.put("/api/amc/clients/" + id, body).then(function (updated) {
            var idx = AMC_CLIENTS.findIndex(function (c) { return c.id === id; });
            toast("Saved");
            if (updated.team !== ACTIVE_TEAM) {
              if (idx !== -1) AMC_CLIENTS.splice(idx, 1);
              amcExpandedRowId = null;
            } else if (idx !== -1) {
              AMC_CLIENTS[idx] = updated;
            }
            amcRenderTracker();
            amcRenderDashboard();
          }).catch(function () { toast("Could not save"); });
        });
      });
    });
  }

  // ---------------------------------------------------------------- ACTIONS
  function amcRenderActions() {
    var hideHandled = document.getElementById("amcHideHandled").checked;
    var clients = AMC_CLIENTS;

    var renewals = clients.filter(function (c) { return c.daysLeft !== null && c.daysLeft <= 30; })
      .sort(function (a, b) { return a.daysLeft - b.daysLeft; });
    document.getElementById("amcCntRenewals").textContent = renewals.length;
    amcRenderActionList("amcListRenewals", renewals.map(function (c) {
      var label = c.daysLeft < 0 ? ("Expired " + Math.abs(c.daysLeft) + " days ago") : (c.daysLeft === 0 ? "Expires today" : (c.daysLeft + " days left"));
      return { key: "renew-" + c.id, title: c.customer + " — contract renewal", meta: (c.location || "") + " · " + c.package + " · ends " + amcFmtDate(c.end), due: label, urgent: c.daysLeft <= 14 };
    }), hideHandled);

    var payments = clients.filter(function (c) { return c.payStatus === "Unpaid" || c.payStatus === "50% Paid"; });
    document.getElementById("amcCntPayments").textContent = payments.length;
    amcRenderActionList("amcListPayments", payments.map(function (c) {
      return { key: "pay-" + c.id, title: c.customer + " — " + c.payStatus, meta: c.payNotes || ((c.location || "") + " · " + c.package), due: c.payStatus === "Unpaid" ? "Chase now" : "Balance due", urgent: c.payStatus === "Unpaid" };
    }), hideHandled);

    var overdueVisits = [];
    clients.forEach(function (c) {
      c.visits.filter(function (v) { return v.status === "OVERDUE"; }).forEach(function (v) { overdueVisits.push({ c: c, v: v }); });
    });
    overdueVisits.sort(function (a, b) { return (a.v.due || "").localeCompare(b.v.due || ""); });
    document.getElementById("amcCntVisits").textContent = overdueVisits.length;
    amcRenderActionList("amcListVisits", overdueVisits.map(function (o) {
      var todayMidnight = new Date(); todayMidnight.setHours(0, 0, 0, 0);
      var dueParts = o.v.due.split("-").map(Number);
      var dueDate = new Date(dueParts[0], dueParts[1] - 1, dueParts[2]);
      var daysOver = Math.round((todayMidnight.getTime() - dueDate.getTime()) / 86400000);
      return { key: "visit-" + o.c.id + "-" + o.v.n, title: o.c.customer + " — AC PPM visit " + o.v.n, meta: (o.c.location || "") + " · " + o.c.package + " · was due " + amcFmtDate(o.v.due), due: daysOver + " days overdue", urgent: true };
    }), hideHandled);
  }

  function amcRenderActionList(elId, items, hideHandled) {
    var el = document.getElementById(elId);
    var visible = items.filter(function (it) { return !(hideHandled && AMC_HANDLED[it.key]); });
    if (!visible.length) { el.innerHTML = '<p class="hint">Nothing here right now.</p>'; return; }
    el.innerHTML = visible.map(function (it) {
      var handled = !!AMC_HANDLED[it.key];
      return '<div class="amc-action-item' + (handled ? " handled" : "") + '">' +
        '<input type="checkbox" data-key="' + escapeAttr(it.key) + '" ' + (handled ? "checked" : "") + ">" +
        '<div class="amc-ai-body"><div class="amc-ai-title">' + escapeHtml(it.title) + '</div><div class="amc-ai-meta">' + escapeHtml(it.meta) + "</div></div>" +
        '<div class="amc-ai-due" style="color:' + (it.urgent ? "var(--red)" : "#C25E00") + '">' + escapeHtml(it.due) + "</div>" +
      "</div>";
    }).join("");
    el.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var key = cb.dataset.key;
        AMC_HANDLED[key] = cb.checked;
        API.post("/api/amc/actions-handled", { team: ACTIVE_TEAM, actionKey: key, handled: cb.checked }).catch(function () {});
        amcRenderActions();
      });
    });
  }

  // ---------------------------------------------------------------- ADD CLIENT
  function amcUpdateSchedulePreview() {
    var pkg = document.getElementById("amcFPackage").value;
    var start = document.getElementById("amcFStart").value;
    var el = document.getElementById("amcSchedulePreview");
    if (!pkg) { el.innerHTML = "Select a package and start date to preview the AC PPM visit schedule."; return; }
    if (!AMC_META) { el.innerHTML = "Loading schedule…"; return; }
    var sched = AMC_META.packageSchedule[pkg] || [];
    if (!start) {
      el.innerHTML = "<b>" + pkg + ":</b> " + sched.length + " visits/yr.";
      return;
    }
    var dates = sched.map(function (s) { return "V" + s.n + ": " + amcFmtDate(amcVisitDueDatePreview(start, s.off)); }).join(" &nbsp;·&nbsp; ");
    el.innerHTML = "<b>" + pkg + " — " + sched.length + " visits/yr.</b> " + dates;
  }

  function amcValidateAddForm() {
    var problems = [];
    [["amcFCustomer", "Customer name"], ["amcFLocation", "Location / area"], ["amcFPackage", "Package"], ["amcFStart", "Contract start"], ["amcFEnd", "Contract end"]].forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (!el.value || !el.value.trim()) problems.push({ id: pair[0], label: pair[1] });
    });
    var start = document.getElementById("amcFStart").value, end = document.getElementById("amcFEnd").value;
    if (start && end && end < start) problems.push({ id: "amcFEnd", label: "Contract end must be on or after the start date" });
    return problems;
  }

  function amcShowFormErrors(problems) {
    var box = document.getElementById("amcFormErrors");
    document.querySelectorAll("#amcAddForm .amc-field-error").forEach(function (f) { f.classList.remove("amc-field-error"); });
    if (!problems.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    problems.forEach(function (p) {
      var el = document.getElementById(p.id);
      if (el && el.parentElement) el.parentElement.classList.add("amc-field-error");
    });
    box.innerHTML = "⚠ Please fix before saving: " + problems.map(function (p) { return escapeHtml(p.label); }).join(", ") + ".";
    box.style.display = "";
    document.getElementById(problems[0].id).focus();
  }

  function amcSubmitAddForm(e) {
    e.preventDefault();
    var problems = amcValidateAddForm();
    if (problems.length) { amcShowFormErrors(problems); toast("Missing or invalid fields"); return; }
    amcShowFormErrors([]);
    var payload = {
      team: ACTIVE_TEAM,
      customer: document.getElementById("amcFCustomer").value.trim(),
      location: document.getElementById("amcFLocation").value.trim(),
      address: document.getElementById("amcFAddress").value.trim(),
      package: document.getElementById("amcFPackage").value,
      ownerTenant: document.getElementById("amcFOwnerTenant").value,
      start: document.getElementById("amcFStart").value,
      end: document.getElementById("amcFEnd").value,
      payType: document.getElementById("amcFPayType").value,
      payStatus: document.getElementById("amcFPayStatus").value,
      payNotes: document.getElementById("amcFPayNotes").value.trim()
    };
    API.post("/api/amc/clients", payload).then(function (created) {
      AMC_CLIENTS.push(created);
      document.getElementById("amcAddForm").reset();
      amcUpdateSchedulePreview();
      toast(created.customer + " added");
      amcRenderDashboard();
      amcSwitchSubtab("tracker");
      document.getElementById("amcTrkSearch").value = created.customer;
      amcRenderTracker();
    }).catch(function (err) { toast(err.message || "Could not add client"); });
  }

  // ---------------------------------------------------------------- HISTORY
  function amcRenderHistory() {
    var q = document.getElementById("amcHistSearch").value.trim().toLowerCase();
    var rows = AMC_HISTORY.filter(function (h) { return !q || (h.customer || "").toLowerCase().indexOf(q) !== -1; });
    document.getElementById("amcHistCount").textContent = rows.length + " of " + AMC_HISTORY.length + " records";
    var statusCls = { Active: "target", Expired: "muted", Cancelled: "critical" };
    document.getElementById("amcHistoryBody").innerHTML = rows.map(function (h) {
      return "<tr><td>" + escapeHtml(h.customer) + "</td><td>" + escapeHtml(h.location || "") + "</td><td>" + escapeHtml(h.year) + "</td><td>" + escapeHtml(h.package) + "</td>" +
        "<td>" + (h.start ? amcFmtDate(h.start) : "—") + "</td><td>" + (h.end ? amcFmtDate(h.end) : "—") + "</td>" +
        "<td>" + amcBadge(h.status, statusCls[h.status] || "muted") + "</td>" +
        "<td>" + escapeHtml(h.payment || "") + "</td><td>" + escapeHtml(h.notes || "") + "</td></tr>";
    }).join("");
  }

  // ==================================================================
  // AMC PROPOSALS - Proposal Builder, Contract Builder, Sent Log.
  // Pricing/inclusions logic is mirrored here ONLY for the live preview
  // before a proposal exists - the server (amc_proposal.py) is always the
  // authority and recomputes everything independently when a PDF is
  // actually generated, so a stale/tampered preview can never affect what
  // gets saved or printed.
  // ==================================================================
  var AP_META = null;
  var AP_COMM_RATES = null;
  var AP_PROPOSALS = [];
  var AP_CONTRACTS = [];
  var acPendingProposalId = null;

  function loadAmcProposalsMeta() {
    API.get("/api/amc-proposals/meta").then(function (m) {
      AP_META = m;
      apFillUnits("apPropType", "apAcUnits", false);
      apFillUnits("acPropType", "acAcUnits", false);
      apLoadCommRates();
      apLoadHighlights();
      apLoadSavedPrice();
      acUpdateValueHint();
    }).catch(function () {});
  }

  function apFillUnits(typeSelId, unitsSelId, keepValue) {
    var type = document.getElementById(typeSelId).value;
    var sel = document.getElementById(unitsSelId);
    var range = AP_META.unitRange[type];
    var prev = keepValue ? parseInt(sel.value, 10) : null;
    var opts = [];
    for (var k = range[0]; k <= range[1]; k++) opts.push(k);
    sel.innerHTML = opts.map(function (k) { return '<option value="' + k + '">' + k + "</option>"; }).join("");
    var dflt = AP_META.defaultUnits[type];
    sel.value = (prev && opts.indexOf(prev) !== -1) ? prev : dflt;
  }

  function apLoadCommRates() {
    API.get("/api/amc-proposals/commercial-rates?team=" + encodeURIComponent(ACTIVE_TEAM)).then(function (r) {
      AP_COMM_RATES = r.rates;
      document.getElementById("apRBasicBase").value = AP_COMM_RATES.basic.base;
      document.getElementById("apRBasicPer").value = AP_COMM_RATES.basic.per;
      document.getElementById("apRStandardBase").value = AP_COMM_RATES.standard.base;
      document.getElementById("apRStandardPer").value = AP_COMM_RATES.standard.per;
      document.getElementById("apRPremiumBase").value = AP_COMM_RATES.premium.base;
      document.getElementById("apRPremiumPer").value = AP_COMM_RATES.premium.per;
      apUpdateCommCardVisibility();
      apRenderPreview();
      acUpdateValueHint();
    }).catch(function () {});
  }

  function apUpdateCommCardVisibility() {
    var card = document.getElementById("apCommRatesCard");
    var isCommercial = document.getElementById("apPropType").value === "commercial";
    card.style.display = (isCommercial && CURRENT_USER_ROLE === "admin") ? "" : "none";
  }

  // Cache of admin-saved apartment/villa prices, keyed "type|units" ->
  // {basic,standard,premium} or null (confirmed nothing saved) - a real
  // cache rather than one global value, because Proposal Builder
  // (apPropType/apAcUnits) and Contract Builder (acPropType/acAcUnits) are
  // two independent forms that can each be set to a different property
  // type/units combo, and apPriceFor() needs the right entry for whichever
  // combo it's actually asked about. Unlike the Override boxes (one
  // proposal only), a saved price replaces the team's real default, so
  // both new proposals AND contracts for that combo use it.
  var AP_SAVED_PRICE_CACHE = {};

  function apSavedPriceKey(type, units) { return type + "|" + units; }

  function apLoadSavedPriceFor(type, units) {
    if (type === "commercial" || !units) return Promise.resolve(null);
    return API.get("/api/amc-proposals/prices?team=" + encodeURIComponent(ACTIVE_TEAM) + "&propertyType=" + type + "&units=" + units).then(function (r) {
      AP_SAVED_PRICE_CACHE[apSavedPriceKey(type, units)] = r.saved;
      return r.saved;
    }).catch(function () {
      AP_SAVED_PRICE_CACHE[apSavedPriceKey(type, units)] = null;
      return null;
    });
  }

  function apLoadSavedPrice() {
    var type = document.getElementById("apPropType").value;
    var units = parseInt(document.getElementById("apAcUnits").value, 10);
    apLoadSavedPriceFor(type, units).then(function () { apRenderPreview(); });
  }

  function apPriceFor(type, units, override) {
    var row;
    if (type === "commercial") {
      var r = AP_COMM_RATES || AP_META.commercialDefaults;
      row = ["basic", "standard", "premium"].map(function (t) { return Math.round(r[t].base + r[t].per * units); });
    } else {
      var saved = AP_SAVED_PRICE_CACHE[apSavedPriceKey(type, units)];
      if (saved) {
        row = [saved.basic, saved.standard, saved.premium];
      } else {
        var table = type === "apartment" ? AP_META.apartmentPrices : AP_META.villaPrices;
        row = (table[String(units)] || [0, 0, 0]).slice();
      }
    }
    if (override) {
      row = row.map(function (v, i) { return (override[i] != null) ? override[i] : v; });
    }
    return row;
  }

  function apMonthlyPlan(annual) {
    var planTotal = annual * AP_META.markup;
    var deposit = planTotal * AP_META.deposit;
    var monthly = (planTotal - deposit) / AP_META.instalments;
    return { planTotal: planTotal, deposit: deposit, monthly: monthly };
  }

  // Same call-out counts and per-tier highlight bullets as amc_proposal.py's
  // callouts_for()/card_highlights() - mirrored here for the same reason as
  // the pricing math above: only for this live preview, the server always
  // recomputes independently when the PDF is actually generated.
  function apCalloutsFor(propertyType) {
    if (propertyType === "commercial") return { basic: 12, standard: 24 };
    if (propertyType === "villa") return { basic: 6, standard: 12 };
    return { basic: 8, standard: 12 };
  }

  // AP_HIGHLIGHTS is the team's admin-edited 3x5 bullet grid, loaded from
  // the server (which itself falls back to the same default wording the
  // PDF uses) - the wording lives in one place (the server), not duplicated
  // here; only the {callouts} token substitution is mirrored client-side,
  // same reasoning as apCalloutsFor above.
  var AP_HIGHLIGHTS = null;

  function apCardHighlights(propertyType) {
    var co = apCalloutsFor(propertyType);
    var sub = [String(co.basic), String(co.standard), null];
    var pend = pendingWording("proposal");
    var template = (pend && pend.highlights) || AP_HIGHLIGHTS || [[], [], []];
    return template.map(function (bullets, tierIdx) {
      return bullets.map(function (b) {
        var text = b.text || "";
        if (sub[tierIdx] !== null) text = text.split("{callouts}").join(sub[tierIdx]);
        return [text, !!b.off];
      });
    });
  }

  function apLoadHighlights() {
    API.get("/api/amc-proposals/highlights?team=" + encodeURIComponent(ACTIVE_TEAM)).then(function (r) {
      AP_HIGHLIGHTS = r.highlights;
      apRenderPreview();
    }).catch(function () {});
  }

  function apRenderPreview() {
    if (!AP_META) return;
    var type = document.getElementById("apPropType").value;
    var units = parseInt(document.getElementById("apAcUnits").value, 10);
    if (!units) return;
    var pendP = pendingWording("proposal");
    var prices = apPriceFor(type, units, pendP && pendP.overrides ? pendP.overrides : null);
    var highlightsByTier = apCardHighlights(type);
    var html = AP_META.tiers.map(function (tier, i) {
      var annual = prices[i];
      var perDay = annual / 365;
      var plan = apMonthlyPlan(annual);
      var flagged = i === 1;
      var tagText = flagged ? "Best value" : AP_META.tags[i];
      var bulletsHtml = highlightsByTier[i].map(function (h) {
        return '<div class="ap-card-bullet' + (h[1] ? " off" : "") + '"><span class="ap-card-bullet-dot">&#9679;</span><span>' + escapeHtml(h[0]) + "</span></div>";
      }).join("");
      return (
        '<div class="ap-card' + (flagged ? " flagged" : "") + '">' +
          '<div class="ap-card-head ' + (flagged ? "orange" : "navy") + '">' +
            (flagged ? '<span class="ap-card-ribbon">Recommended</span>' : "") +
            '<div class="ap-card-tier">' + escapeHtml(tier) + "</div>" +
            '<div class="ap-card-tag">' + escapeHtml(tagText) + "</div>" +
          "</div>" +
          '<div class="ap-card-body">' +
            '<div class="ap-card-price"><span>AED</span> <b>' + Math.round(annual).toLocaleString() + "</b></div>" +
            '<div class="ap-card-price-note">per year, paid upfront &middot; incl. VAT</div>' +
            '<div class="ap-card-perday">Works out at <b>AED ' + perDay.toFixed(2) + " a day</b></div>" +
          "</div>" +
          '<div class="ap-card-monthly">' +
            '<div class="ap-card-monthly-label">Or pay monthly</div>' +
            '<div class="ap-card-monthly-val"><b>AED ' + plan.deposit.toFixed(2) + "</b> deposit, then<br><b>AED " + plan.monthly.toFixed(2) + "</b> &times; 11 months</div>" +
          "</div>" +
          '<div class="ap-card-bullets">' + bulletsHtml + "</div>" +
          '<div class="ap-card-footer"><button class="btn ghost btn-sm" style="width:100%" data-create-contract="' + i + '">Create Contract &rarr;</button></div>' +
        "</div>"
      );
    }).join("");
    document.getElementById("apPreviewCards").innerHTML = html;
  }

  function apSaveCommRates() {
    var rates = {
      basic: { base: Number(document.getElementById("apRBasicBase").value), per: Number(document.getElementById("apRBasicPer").value) },
      standard: { base: Number(document.getElementById("apRStandardBase").value), per: Number(document.getElementById("apRStandardPer").value) },
      premium: { base: Number(document.getElementById("apRPremiumBase").value), per: Number(document.getElementById("apRPremiumPer").value) }
    };
    API.put("/api/amc-proposals/commercial-rates", { team: ACTIVE_TEAM, rates: rates }).then(function (r) {
      AP_COMM_RATES = r.rates;
      toast("Commercial rates saved for " + TEAM_LABELS[ACTIVE_TEAM]);
      apRenderPreview();
      acUpdateValueHint();
    }).catch(function (err) { toast(err.message || "Could not save rates"); });
  }

  function apGenerateProposal() {
    var body = {
      team: ACTIVE_TEAM,
      clientName: document.getElementById("apClientName").value.trim(),
      propertyAddress: document.getElementById("apPropAddr").value.trim(),
      propertyType: document.getElementById("apPropType").value,
      acUnits: parseInt(document.getElementById("apAcUnits").value, 10),
      validityDays: parseInt(document.getElementById("apValidity").value, 10)
    };
    var hint = document.getElementById("apGenerateHint");
    var customWording = pendingWording("proposal");
    if (customWording) {
      body.wording = Object.assign({}, customWording);
      delete body.wording.overrides;
      var ov = customWording.overrides;
      if (ov) { body.overrideBasic = ov[0]; body.overrideStandard = ov[1]; body.overridePremium = ov[2]; }
    }
    hint.textContent = "Generating…";
    API.post("/api/amc-proposals", body).then(function (p) {
      hint.textContent = "";
      if (customWording) setPendingWording("proposal", null);
      toast("Proposal generated");
      window.open("/api/amc-proposals/" + p.id + "/pdf", "_blank");
      loadAmcProposalsAll();
    }).catch(function (err) {
      hint.textContent = "";
      toast(err.message || "Could not generate proposal");
    });
  }

  function apCreateContractFromPreview(tierIdx) {
    document.getElementById("acClientName").value = document.getElementById("apClientName").value;
    document.getElementById("acPropAddr").value = document.getElementById("apPropAddr").value;
    document.getElementById("acPropType").value = document.getElementById("apPropType").value;
    apFillUnits("acPropType", "acAcUnits", false);
    var unitsVal = document.getElementById("apAcUnits").value;
    if ([].slice.call(document.getElementById("acAcUnits").options).some(function (o) { return o.value === unitsVal; })) {
      document.getElementById("acAcUnits").value = unitsVal;
    }
    document.getElementById("acPackage").value = AP_META.tiers[tierIdx];
    document.getElementById("acPayPlan").value = "onetime";
    if (!document.getElementById("acStartDate").value) document.getElementById("acStartDate").value = todayISO();
    acPendingProposalId = null;
    var bType = document.getElementById("apPropType").value;
    var bUnits = parseInt(document.getElementById("apAcUnits").value, 10);
    var bPend = pendingWording("proposal");
    var bPrices = apPriceFor(bType, bUnits, bPend && bPend.overrides ? bPend.overrides : null);
    var bStd = apPriceFor(bType, bUnits, null);
    AC_PRICE_OVERRIDE = bPrices.some(function (v, i) { return Math.round(v) !== Math.round(bStd[i]); })
      ? { type: bType, units: bUnits, prices: bPrices, from: "the proposal you're building" } : null;
    acUpdateValueHint();
    apSwitchSubtab("contract");
  }

  function apCreateContractFromLog(proposalId) {
    var p = AP_PROPOSALS.filter(function (x) { return x.id === proposalId; })[0];
    if (!p) return;
    document.getElementById("acClientName").value = p.clientName === "(no name)" ? "" : (p.clientName || "");
    document.getElementById("acPropAddr").value = p.propertyAddress || "";
    document.getElementById("acPropType").value = p.propertyType;
    apFillUnits("acPropType", "acAcUnits", false);
    var unitsSel = document.getElementById("acAcUnits");
    if ([].slice.call(unitsSel.options).some(function (o) { return o.value === String(p.acUnits); })) {
      unitsSel.value = String(p.acUnits);
    }
    document.getElementById("acPackage").value = "Basic";
    document.getElementById("acPayPlan").value = "onetime";
    if (!document.getElementById("acStartDate").value) document.getElementById("acStartDate").value = todayISO();
    acPendingProposalId = proposalId;
    AC_PRICE_OVERRIDE = { type: p.propertyType, units: p.acUnits, prices: [p.prices.basic, p.prices.standard, p.prices.premium],
                          from: "the proposal for " + (p.clientName && p.clientName !== "(no name)" ? p.clientName : "this client") };
    acUpdateValueHint();
    apSwitchSubtab("contract");
  }

  // "Edit" on a sent proposal never touches its stored PDF/record - it just
  // pre-fills the Builder with that proposal's inputs so Generate creates a
  // brand-new proposal (a revision), leaving the original untouched in the
  // log. Proposal PDFs are an immutable record once issued (see the AMC
  // Proposals & Contracts comment above) - this respects that instead of
  // fighting it.
  function apEditProposalFromLog(proposalId) {
    var p = AP_PROPOSALS.filter(function (x) { return x.id === proposalId; })[0];
    if (!p) return;
    document.getElementById("apClientName").value = p.clientName === "(no name)" ? "" : (p.clientName || "");
    document.getElementById("apPropAddr").value = p.propertyAddress || "";
    document.getElementById("apPropType").value = p.propertyType;
    apFillUnits("apPropType", "apAcUnits", false);
    var unitsSel = document.getElementById("apAcUnits");
    if ([].slice.call(unitsSel.options).some(function (o) { return o.value === String(p.acUnits); })) {
      unitsSel.value = String(p.acUnits);
    }
    var validitySel = document.getElementById("apValidity");
    if ([].slice.call(validitySel.options).some(function (o) { return o.value === String(p.validityDays); })) {
      validitySel.value = String(p.validityDays);
    }
    apUpdateCommCardVisibility();
    apRenderPreview();
    apSwitchSubtab("builder");
    toast("Loaded - Generate creates a new proposal, the original stays as-is");
  }

  // The picker (Basic/Standard/Premium, each priced for the current property
  // type + AC units) is the actual UI for choosing a package - the hidden
  // #acPackage select is just where that choice is stored, so acGenerateContract()
  // and every other existing reader of "acPackage".value keeps working unchanged.
  // Prices quoted on the proposal this contract is being created from (set by "→ Contract").
  // They apply only while the contract's property type and unit count still match the proposal's.
  var AC_PRICE_OVERRIDE = null;

  function acEffectivePrices(type, units) {
    var o = AC_PRICE_OVERRIDE;
    return (o && o.type === type && o.units === units) ? o.prices.slice() : apPriceFor(type, units, null);
  }

  function acUsingProposalPrices(type, units) {
    var o = AC_PRICE_OVERRIDE;
    return !!(o && o.type === type && o.units === units);
  }

  function acRenderPackagePicker() {
    var wrap = document.getElementById("acPackagePicker");
    if (!wrap || !AP_META) return;
    var type = document.getElementById("acPropType").value;
    var units = parseInt(document.getElementById("acAcUnits").value, 10);
    var current = document.getElementById("acPackage").value;
    wrap.innerHTML = "";
    if (!units) return;
    var prices = acEffectivePrices(type, units);
    AP_META.tiers.forEach(function (tier, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ac-package-option" + (tier === current ? " selected" : "");
      btn.innerHTML =
        '<div class="ac-pkg-name">' + escapeHtml(tier) + "</div>" +
        '<div class="ac-pkg-price">AED ' + Math.round(prices[i]).toLocaleString() + "</div>" +
        '<div class="ac-pkg-note">per year, incl. VAT</div>';
      btn.addEventListener("click", function () {
        document.getElementById("acPackage").value = tier;
        acUpdateValueHint();
      });
      wrap.appendChild(btn);
    });
  }

  function acUpdateValueHint() {
    if (!AP_META) return;
    acRenderPackagePicker();
    var type = document.getElementById("acPropType").value;
    var units = parseInt(document.getElementById("acAcUnits").value, 10);
    var hint = document.getElementById("acContractValueHint");
    if (!units) { hint.textContent = ""; return; }
    var tierIdx = AP_META.tiers.indexOf(document.getElementById("acPackage").value);
    var annual = acEffectivePrices(type, units)[tierIdx];
    var payPlan = document.getElementById("acPayPlan").value;
    var from = acUsingProposalPrices(type, units) ? '<div style="margin-bottom:4px;color:#8C3007">Prices taken from ' + escapeHtml(AC_PRICE_OVERRIDE.from) + ".</div>" : "";
    if (payPlan === "monthly") {
      var plan = apMonthlyPlan(annual);
      hint.innerHTML = from + "<b>Contract value:</b> AED " + plan.deposit.toFixed(2) + " deposit, then AED " + plan.monthly.toFixed(2) + " &times; 11 monthly instalments (AED " + Math.round(plan.planTotal).toLocaleString() + " total, incl. VAT)";
    } else {
      hint.innerHTML = from + "<b>Contract value:</b> AED " + Math.round(annual).toLocaleString() + " paid upfront, incl. VAT";
    }
  }

  function acGenerateContract() {
    var body = {
      team: ACTIVE_TEAM,
      clientName: document.getElementById("acClientName").value.trim(),
      propertyAddress: document.getElementById("acPropAddr").value.trim(),
      propertyType: document.getElementById("acPropType").value,
      acUnits: parseInt(document.getElementById("acAcUnits").value, 10),
      package: document.getElementById("acPackage").value,
      payPlan: document.getElementById("acPayPlan").value,
      startDate: document.getElementById("acStartDate").value,
      signatory: document.getElementById("acSignatory").value
    };
    if (acPendingProposalId) body.proposalId = acPendingProposalId;
    var quoted = acEffectivePrices(body.propertyType, body.acUnits)[AP_META.tiers.indexOf(body.package)];
    var standardPrice = apPriceFor(body.propertyType, body.acUnits, null)[AP_META.tiers.indexOf(body.package)];
    if (quoted && Math.round(quoted) !== Math.round(standardPrice)) body.annualValue = quoted;
    var customWording = pendingWording("contract");
    if (customWording) { body.terms = customWording.terms; body.layout = customWording.layout; }
    var errBox = document.getElementById("acFormErrors");
    errBox.style.display = "none";
    API.post("/api/amc-contracts", body).then(function (c) {
      AC_PRICE_OVERRIDE = null;
      acUpdateValueHint();
      if (customWording) setPendingWording("contract", null);
      toast("Contract generated");
      window.open("/api/amc-contracts/" + c.id + "/pdf", "_blank");
      acPendingProposalId = null;
      loadAmcProposalsAll();
    }).catch(function (err) {
      errBox.textContent = err.message || "Could not generate contract";
      errBox.style.display = "";
    });
  }

  function acResetForm() {
    document.getElementById("acClientName").value = "";
    document.getElementById("acPropAddr").value = "";
    document.getElementById("acPropType").value = "apartment";
    apFillUnits("acPropType", "acAcUnits", false);
    document.getElementById("acPackage").value = "Basic";
    document.getElementById("acPayPlan").value = "onetime";
    document.getElementById("acStartDate").value = "";
    document.getElementById("acSignatory").value = "tegan";
    acPendingProposalId = null;
    AC_PRICE_OVERRIDE = null;
    setPendingWording("contract", null);
    acUpdateValueHint();
  }

  function apFmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
    if (isNaN(d.getTime())) return iso;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear();
  }

  function loadAmcProposalsAll() {
    Promise.all([
      API.get("/api/amc-proposals?team=" + encodeURIComponent(ACTIVE_TEAM)),
      API.get("/api/amc-contracts?team=" + encodeURIComponent(ACTIVE_TEAM))
    ]).then(function (res) {
      AP_PROPOSALS = res[0].proposals;
      AP_CONTRACTS = res[1].contracts;
      apRenderLog();
    }).catch(function () { toast("Could not load AMC Proposals data"); });
  }

  function apUploadedBadge(d) {
    return d.pdfSource === "uploaded"
      ? ' <span title="' + escapeAttr(d.uploadFilename || "") + '" style="display:inline-block;margin-left:6px;font-size:9px;font-weight:700;color:#8C3007;background:#FEF3EE;border:1px solid #F4B79B;padding:2px 6px;border-radius:10px">Uploaded PDF</span>'
      : "";
  }

  function apRenderLog() {
    document.getElementById("apCntLog").textContent = AP_PROPOSALS.length;
    document.getElementById("apProposalLogBody").innerHTML = AP_PROPOSALS.map(function (p) {
      var badge = p.convertedToContractId ? ' <span style="display:inline-block;margin-left:6px;font-size:9px;font-weight:700;color:#1A6B3C;background:#EAF7EF;border:1px solid #BEE6CC;padding:2px 6px;border-radius:10px">Converted</span>' : "";
      return "<tr>" +
        "<td>" + apFmtDate(p.createdAt) + "</td>" +
        "<td>" + escapeHtml(p.clientName || "(no name)") + badge + apUploadedBadge(p) + "</td>" +
        "<td>" + escapeHtml(p.propertyAddress || "") + "</td>" +
        "<td>" + AP_META.typeLabel[p.propertyType] + "</td>" +
        "<td>" + p.acUnits + "</td>" +
        "<td>" + Math.round(p.prices.basic).toLocaleString() + "</td>" +
        "<td>" + Math.round(p.prices.standard).toLocaleString() + "</td>" +
        "<td>" + Math.round(p.prices.premium).toLocaleString() + "</td>" +
        "<td>" + apFmtDate(p.validUntil) + "</td>" +
        "<td>" + escapeHtml(p.createdByEmail || "") + (p.revision > 1 ? '<div class="hint" style="margin:2px 0 0">Edited ' + apFmtDate(p.updatedAt) + " (v" + p.revision + ")</div>" : "") + "</td>" +
        '<td style="white-space:nowrap">' +
          '<button class="btn ghost btn-sm" data-dl-proposal="' + p.id + '">Download</button> ' +
          '<button class="btn ghost btn-sm" data-edit-proposal="' + p.id + '">Edit</button> ' +
          '<button class="btn ghost btn-sm" data-upload-proposal="' + p.id + '" title="Replace this proposal\'s PDF with your own file">Upload PDF</button> ' +
          '<button class="btn ghost btn-sm" data-dup-proposal="' + p.id + '" title="Load into the Proposal Builder to create a new proposal">Duplicate</button> ' +
          (p.convertedToContractId ? "" : '<button class="btn ghost btn-sm" data-cc-proposal="' + p.id + '">&rarr; Contract</button>') +
        "</td></tr>";
    }).join("") || '<tr><td colspan="11" class="hint">No proposals sent yet.</td></tr>';

    document.getElementById("apContractLogBody").innerHTML = AP_CONTRACTS.map(function (c) {
      var sig = (AP_META.signatories[c.signatory] || {}).name || c.signatory;
      return "<tr>" +
        "<td>" + apFmtDate(c.createdAt) + "</td>" +
        "<td>" + escapeHtml(c.clientName || "") + apUploadedBadge(c) + "</td>" +
        "<td>" + escapeHtml(c.propertyAddress || "") + "</td>" +
        "<td>" + c.package + "</td>" +
        "<td>" + (c.payPlan === "monthly" ? "Monthly" : "One-time") + "</td>" +
        "<td>" + apFmtDate(c.startDate) + "</td>" +
        "<td>" + escapeHtml(sig) + "</td>" +
        "<td>AED " + Math.round(c.contractValueAnnual).toLocaleString() + "</td>" +
        apPaymentCellHtml(c) +
        "<td>" + escapeHtml(c.createdByEmail || "") + (c.revision > 1 ? '<div class="hint" style="margin:2px 0 0">Edited ' + apFmtDate(c.updatedAt) + " (v" + c.revision + ")</div>" : "") + "</td>" +
        '<td style="white-space:nowrap"><button class="btn ghost btn-sm" data-dl-contract="' + c.id + '">Download</button> ' +
          '<button class="btn ghost btn-sm" data-edit-contract="' + c.id + '">Edit</button> ' +
          '<button class="btn ghost btn-sm" data-upload-contract="' + c.id + '" title="Replace this contract\'s PDF with your own file">Upload PDF</button></td>' +
        "</tr>";
    }).join("") || '<tr><td colspan="11" class="hint">No contracts issued yet.</td></tr>';
  }

  // ------------------------------------------------------------------
  // Contracts issued: inline payments + full contract editor
  // ------------------------------------------------------------------
  function apPaymentCellHtml(c) {
    var due = c.balance > 0;
    return '<td class="ap-pay-cell" data-pay-id="' + c.id + '">' +
      '<select class="ap-pay-status">' + ["Unpaid", "Partial", "Paid"].map(function (s) {
        return '<option' + (s === c.payStatus ? " selected" : "") + ">" + s + "</option>";
      }).join("") + "</select>" +
      '<input type="number" class="ap-pay-amt" min="0" step="any" placeholder="Received (AED)" value="' + (c.amountReceived || "") + '">' +
      '<input type="text" class="ap-pay-notes" maxlength="500" placeholder="Notes" value="' + escapeAttr(c.payNotes) + '">' +
      '<div class="ap-pay-bal' + (due ? " due" : "") + '">' + apBalanceText(c) + "</div></td>";
  }

  function apBalanceText(c) {
    return c.balance > 0 ? "Balance AED " + Math.round(c.balance).toLocaleString() + " of " + Math.round(c.amountPayable).toLocaleString()
                         : "Settled — AED " + Math.round(c.amountPayable).toLocaleString();
  }

  function apSavePayment(cell, changed) {
    var c = AP_CONTRACTS.filter(function (x) { return x.id === cell.dataset.payId; })[0];
    if (!c) return;
    var statusEl = cell.querySelector(".ap-pay-status");
    var amtEl = cell.querySelector(".ap-pay-amt");
    var notesEl = cell.querySelector(".ap-pay-notes");
    var amt = parseFloat(amtEl.value) || 0;
    if (amt < 0) { toast("Amount received can't be negative"); return; }
    if (changed === "amt") {
      statusEl.value = amt <= 0 ? "Unpaid" : (amt >= c.amountPayable ? "Paid" : "Partial");
    } else if (changed === "status" && statusEl.value === "Paid" && amt < c.amountPayable) {
      amt = c.amountPayable;
      amtEl.value = amt;
    }
    API.put("/api/amc-contracts/" + c.id + "/payments", {
      payStatus: statusEl.value, amountReceived: amt, payNotes: notesEl.value
    }).then(function (u) {
      Object.assign(c, u);
      var bal = cell.querySelector(".ap-pay-bal");
      bal.textContent = apBalanceText(c);
      bal.classList.toggle("due", c.balance > 0);
      cell.classList.add("saved");
      setTimeout(function () { cell.classList.remove("saved"); }, 900);
    }).catch(function (err) { toast(err.message || "Could not save payment"); });
  }

  // ------------------------------------------------------------------
  // Shared editor for contracts and proposals.
  //   mode "doc":      edits one issued contract/proposal (details + wording)
  //   mode "template": edits the team's standard wording used by all future ones
  // Saving a doc asks whether the change is for this document only (the
  // standard stays as it was) or should also become the standard.
  // ------------------------------------------------------------------
  var CE = null;
  var CE_BLOCK_LABELS = {
    title: "Page title", heading: "Heading", subheading: "Sub-heading", p: "Paragraph", callout: "Highlighted note",
    bullets: "Bullet list", numbered: "Numbered list", definitions: "Definitions", checklist: "Checklist box",
    table: "Table", pagebreak: "Page break", signature: "Signature block"
  };

  function ceNewBlock(type) {
    switch (type) {
      case "bullets": case "numbered": return { type: type, items: [""] };
      case "definitions": return { type: type, items: [["Term", "Description"]] };
      case "checklist": return { type: type, title: "", items: [""] };
      case "table": return { type: type, header: ["Item", "Details"], rows: [["", ""]] };
      case "pagebreak": case "signature": return { type: type };
      default: return { type: type, text: "" };
    }
  }

  // Resolves "this" | "all" | null (keep editing).
  function askSaveScope(noun, canAll, builder) {
    return new Promise(function (resolve) {
      var overlay = document.getElementById("scopeOverlay");
      var thisBtn = document.getElementById("scopeThis");
      var allBtn = document.getElementById("scopeAll");
      var backBtn = document.getElementById("scopeBack");
      var Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
      if (builder) {
        document.getElementById("scopeMessage").textContent = "Use this wording for just this " + noun + ", or make it the standard for all future " + noun + "s?";
        document.getElementById("scopeSub").textContent = "\"Only this " + noun + "\" applies it to the " + noun + " you're building now. The standard wording stays as it is." +
          (noun === "proposal" ? " Prices you changed always apply to this proposal only." : "");
        thisBtn.innerHTML = "No — only this " + noun + "<small>Used for the " + noun + " you generate next. The standard wording is not changed.</small>";
        allBtn.innerHTML = "Yes — make it the standard for all future " + noun + "s<small>" +
          (canAll ? Noun + "s already issued aren't changed." : "Admins only — ask an admin to change the standard wording.") + "</small>";
      } else {
        document.getElementById("scopeMessage").textContent = "Save these changes for this " + noun + " only, or for all future " + noun + "s too?";
        document.getElementById("scopeSub").textContent = "Details such as the client, price and dates always apply to this " + noun + " only. The wording can also become the standard that every new " + noun + " starts from.";
        thisBtn.innerHTML = "This " + noun + " only<small>The standard wording stays exactly as it is. The version being replaced is kept.</small>";
        allBtn.innerHTML = "Save for all future " + noun + "s too<small>" +
          (canAll ? "This " + noun + " is updated and the wording becomes the new standard. " + Noun + "s already issued aren't changed."
                  : "Admins only — ask an admin to change the standard wording.") + "</small>";
      }
      allBtn.disabled = !canAll;
      overlay.classList.add("show");
      function done(result) {
        overlay.classList.remove("show");
        thisBtn.removeEventListener("click", onThis); allBtn.removeEventListener("click", onAll); backBtn.removeEventListener("click", onBack);
        resolve(result);
      }
      function onThis() { done("this"); }
      function onAll() { done("all"); }
      function onBack() { done(null); }
      thisBtn.addEventListener("click", onThis); allBtn.addEventListener("click", onAll); backBtn.addEventListener("click", onBack);
    });
  }

  function ceOpenEditor(contractId) {
    API.get("/api/amc-contracts/" + contractId).then(function (d) {
      CE = { kind: "contract", mode: "doc", id: contractId, doc: d.contract, terms: d.terms, autoTables: d.autoTables, tokens: d.tokens,
             revisions: d.revisions, valueText: d.valueTextOverride, dirty: false, lastStd: null,
             layout: d.layout || {}, layoutDefaults: d.layoutDefaults, layoutLabels: d.layoutLabels };
      ceBuild();
    }).catch(function () { toast("Could not load contract"); });
  }

  function ceOpenProposalEditor(proposalId) {
    API.get("/api/amc-proposals/" + proposalId).then(function (d) {
      CE = { kind: "proposal", mode: "doc", id: proposalId, doc: d.proposal, wording: d.wording, tokens: d.tokens,
             revisions: d.revisions, dirty: false, lastStd: null,
             layout: (d.wording && d.wording.layout) || {}, layoutDefaults: d.layoutDefaults, layoutLabels: d.layoutLabels,
             highlights: JSON.parse(JSON.stringify(d.highlights)),
             comparison: JSON.parse(JSON.stringify(d.comparison)), included: d.included || null, includedAuto: d.autoIncluded };
      ceBuild();
    }).catch(function () { toast("Could not load proposal"); });
  }

  // Wording chosen with "only this proposal/contract" in a Builder: held here and sent
  // with the next Generate, then cleared. It never touches the team's standard.
  var PENDING = { proposal: null, contract: null };

  function pendingWording(kind) {
    var p = PENDING[kind];
    if (p && p.team !== ACTIVE_TEAM) { PENDING[kind] = null; p = null; }
    return p ? p.data : null;
  }

  function setPendingWording(kind, data) {
    PENDING[kind] = data ? { team: ACTIVE_TEAM, data: data } : null;
    updatePendingBanners();
    if (kind === "proposal") apRenderPreview();
  }

  function updatePendingBanners() {
    [["proposal", "apPendingBanner", "proposal"], ["contract", "acPendingBanner", "contract"]].forEach(function (x) {
      var el = document.getElementById(x[1]);
      if (!el) return;
      var on = !!pendingWording(x[0]);
      el.style.display = on ? "" : "none";
      var pw = pendingWording(x[0]);
      var withPrices = !!(pw && pw.overrides && pw.overrides.some(function (o) { return o != null; }));
      el.innerHTML = on ? "Custom wording" + (withPrices ? " and prices" : "") + " will be used for the " + x[2] + " you generate next — the standard is unchanged. " +
        '<a href="#" data-clear-pending="' + x[0] + '">Clear</a>' : "";
    });
  }

  function ceOpenTemplate(kind) {
    var url = kind === "contract" ? "/api/amc-contracts/default-terms?team=" : "/api/amc-proposals/default-wording?team=";
    var val = function (id) { var el = document.getElementById(id); return el ? el.value : ""; };
    var builder = kind === "contract"
      ? { clientName: val("acClientName").trim(), propertyAddress: val("acPropAddr").trim(), propertyType: val("acPropType"),
          acUnits: parseInt(val("acAcUnits"), 10), package: val("acPackage") || "Basic", payPlan: val("acPayPlan"),
          startDate: val("acStartDate"), signatory: val("acSignatory") }
      : { clientName: val("apClientName").trim(), propertyAddress: val("apPropAddr").trim(), propertyType: val("apPropType"),
          acUnits: parseInt(val("apAcUnits"), 10), validityDays: parseInt(val("apValidity"), 10) || 30 };
    Promise.all([API.get(url + encodeURIComponent(ACTIVE_TEAM)), apLoadSavedPriceFor(builder.propertyType, builder.acUnits)]).then(function (res) {
      var d = res[0];
      CE = { kind: kind, mode: "template", id: null, terms: d.terms, wording: d.wording, tokens: d.tokens, autoTables: {},
             revisions: [], dirty: false,
             layout: kind === "contract" ? (d.layout || {}) : ((d.wording && d.wording.layout) || {}),
             layoutDefaults: d.layoutDefaults, layoutLabels: d.layoutLabels,
             highlights: kind === "proposal" ? JSON.parse(JSON.stringify(d.highlights)) : null,
             comparison: kind === "proposal" ? JSON.parse(JSON.stringify(d.comparison)) : null,
             included: kind === "proposal" ? (d.included || null) : null, includedAuto: d.autoIncluded, builder: builder };
      var pend = pendingWording(kind);   // reopening continues from the custom wording, not the standard
      if (pend) {
        var copy = JSON.parse(JSON.stringify(pend));
        if (kind === "contract") { CE.terms = copy.terms; CE.layout = copy.layout || {}; }
        else {
          CE.wording = { terms: copy.terms, note: copy.note }; CE.layout = copy.layout || {};
          CE.highlights = copy.highlights || CE.highlights; CE.comparison = copy.comparison || CE.comparison; CE.included = copy.included || null;
        }
      }
      ceBuild();
    }).catch(function () { toast("Could not load the standard wording"); });
  }

  // The editor works on an issued document (CE.doc, with type/units selects), or on the one
  // being built in a Builder (CE.builder, whose details come from the Builder's form).
  function ceTypeVal() {
    var s = document.getElementById("ceType");
    return s ? s.value : (CE.doc ? CE.doc.propertyType : (CE.builder ? CE.builder.propertyType : "apartment"));
  }
  function ceUnitsVal() {
    var s = document.getElementById("ceUnits");
    return s ? parseInt(s.value, 10) : (CE.doc ? CE.doc.acUnits : (CE.builder ? CE.builder.acUnits : 2));
  }
  function ceTeamVal() { return CE.doc ? CE.doc.team : ACTIVE_TEAM; }
  function ceHasPrices() { return CE.kind === "proposal" && (CE.mode === "doc" || !!CE.builder); }

  function ceStdPrices() {
    return apPriceFor(ceTypeVal(), ceUnitsVal(), null);
  }

  function ceCurrentPrices() {
    var v = function (n) { var x = parseFloat(document.getElementById("cePrice" + n).value); return x > 0 ? x : 0; };
    return { basic: v("Basic"), standard: v("Standard"), premium: v("Premium") };
  }

  // Price overrides vs the standard, as the Builder's Generate expects them (null = use the standard).
  function ceOverrides() {
    var std = ceStdPrices();
    var cur = ceCurrentPrices();
    return [cur.basic, cur.standard, cur.premium].map(function (v, i) { return (v > 0 && Math.round(v) !== Math.round(std[i])) ? v : null; });
  }

  // Keeps the price inputs following the standard price for the chosen
  // type/units/package, unless the user has typed their own figure.
  function ceRefreshStdPrice(forceApply) {
    var type = ceTypeVal();
    var units = ceUnitsVal();
    return apLoadSavedPriceFor(type, units).then(function () {
      var std = ceStdPrices();
      if (CE.kind === "contract") {
        var tierIdx = AP_META.tiers.indexOf(document.getElementById("cePackage").value);
        var input = document.getElementById("ceAnnual");
        var prev = CE.lastStd;
        if (forceApply || prev == null || Math.round(+input.value) === Math.round(prev)) input.value = std[tierIdx];
        CE.lastStd = std[tierIdx];
        document.getElementById("ceStdHint").textContent = "Standard price for this package: AED " + Math.round(std[tierIdx]).toLocaleString();
      } else {
        ["Basic", "Standard", "Premium"].forEach(function (name, i) {
          var input = document.getElementById("cePrice" + name);
          var prev = CE.lastStd && CE.lastStd[i];
          if (forceApply || prev == null || Math.round(+input.value) === Math.round(prev)) input.value = std[i];
        });
        CE.lastStd = std.slice();
        document.getElementById("ceStdHint").textContent = "Standard prices: AED " + std.map(function (v) { return Math.round(v).toLocaleString(); }).join(" / ");
      }
      ceUpdatePaySummary();
    });
  }

  function ceLayoutKeys() {
    return CE.kind === "contract"
      ? ["eyebrow", "titleA", "titleB", "strap", "tagline", "footer", "providerEntity"]
      : ["eyebrow", "titleA", "titleB", "strap", "quote", "quoteAttrib", "tagline", "footer", "inclTitle", "cmpTitle", "cmpStrap",
         "payTitle", "payStrap", "payRowUpfront", "payRowDeposit", "payRowInstal", "payRowTotal"];
  }

  function ceTypeKey() {
    return ceTypeVal() === "commercial" ? "commercial" : "residential";
  }

  // Cover/page text inputs for the given layout keys (ids stay ceLay_<key>).
  function ceLayoutFields(keys) {
    var defs = CE.layoutDefaults[ceTypeKey()];
    var cur = Object.assign({}, defs, CE.layout || {});
    var long = { strap: 1, quote: 1 };
    return '<div class="grid cols2">' + keys.map(function (k) {
      var label = escapeHtml(CE.layoutLabels[k] || k);
      var field = long[k]
        ? '<textarea id="ceLay_' + k + '" rows="2" style="width:100%;box-sizing:border-box">' + escapeHtml(cur[k]) + "</textarea>"
        : '<input type="text" id="ceLay_' + k + '" value="' + escapeAttr(cur[k]) + '">';
      return '<div class="field"' + (long[k] ? ' style="grid-column:1/-1"' : "") + "><label>" + label + "</label>" + field + "</div>";
    }).join("") + "</div>";
  }

  // One editor section, tagged with where it appears in the PDF.
  function ceSection(tag, title, inner, hint) {
    return '<div class="ce-section"><h4><span class="ce-pg">' + escapeHtml(tag) + "</span>" + escapeHtml(title) + "</h4>" +
      (hint ? '<p class="hint" style="margin:0 0 10px">' + hint + "</p>" : "") + inner + "</div>";
  }

  // When the property type changes, any layout text still at the old
  // standard follows the new type's standard (e.g. "Commercial Maintenance Contract").
  function ceSyncLayoutDefaults() {
    var defs = CE.layoutDefaults[ceTypeKey()];
    ceLayoutKeys().forEach(function (k) {
      var el = document.getElementById("ceLay_" + k);
      if (el && el.value === CE.layoutPrev[k]) el.value = defs[k];
    });
    CE.layoutPrev = defs;
  }

  // Only text that differs from the standard is stored; the rest stays automatic.
  function ceCurrentLayout() {
    var defs = CE.layoutDefaults[ceTypeKey()];
    var out = {};
    ceLayoutKeys().forEach(function (k) {
      var el = document.getElementById("ceLay_" + k);
      if (el && el.value.trim() !== "" && el.value !== defs[k]) out[k] = el.value;
    });
    return out;
  }

  function ceApplyLayout(overrides) {
    var defs = CE.layoutDefaults[ceTypeKey()];
    var cur = Object.assign({}, defs, overrides || {});
    ceLayoutKeys().forEach(function (k) { var el = document.getElementById("ceLay_" + k); if (el) el.value = cur[k]; });
  }

  // Admin-only and residential-only (commercial pricing is rate-based): replaces the
  // team's standard Basic/Standard/Premium price for this property type + unit count.
  function ceUpdateStdPriceBtn() {
    var btn = document.getElementById("ceSaveStdPrice");
    if (!btn) return;
    btn.style.display = (CURRENT_USER_ROLE === "admin" && ceTypeVal() !== "commercial") ? "" : "none";
  }

  function ceSaveStdPrice() {
    var type = ceTypeVal();
    var units = ceUnitsVal();
    if (type === "commercial" || !units) return;
    var prices = ["Basic", "Standard", "Premium"].map(function (n) { return parseFloat(document.getElementById("cePrice" + n).value); });
    if (prices.some(function (p) { return !(p > 0); })) { toast("Enter all three prices first"); return; }
    var label = TEAM_LABELS[ceTeamVal()] + " " + AP_META.typeLabel[type] + " (" + units + " unit" + (units === 1 ? "" : "s") + ")";
    confirmDialog("Save AED " + prices.map(function (p) { return Math.round(p).toLocaleString(); }).join(" / ") + " as the new standard Basic/Standard/Premium price for " + label +
      "? This replaces the default for every future proposal and contract — not just this one.", { confirmLabel: "Save as standard" }).then(function (ok) {
      if (!ok) return;
      API.put("/api/amc-proposals/prices", { team: ceTeamVal(), propertyType: type, units: units, basic: prices[0], standard: prices[1], premium: prices[2] })
        .then(function (r) {
          AP_SAVED_PRICE_CACHE[apSavedPriceKey(type, units)] = r.saved;
          CE.lastStd = prices.slice();
          document.getElementById("ceStdHint").textContent = "Standard prices: AED " + prices.map(function (v) { return Math.round(v).toLocaleString(); }).join(" / ");
          toast("Saved as the new standard price for " + TEAM_LABELS[ceTeamVal()]);
          acUpdateValueHint();
          apRenderPreview();
        }).catch(function (err) { toast(err.message || "Could not save price"); });
    });
  }

  function ceOptions(list, sel) {
    return list.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === sel ? " selected" : "") + ">" + o[1] + "</option>"; }).join("");
  }

  function ceHighlightsGridHtml() {
    return AP_META.tiers.map(function (tier, ti) {
      return '<div><div class="ce-hl-tier">' + escapeHtml(tier) + "</div>" + CE.highlights[ti].map(function (b, bi) {
        return '<div class="ce-hl-row"><input type="text" data-hl="text" data-t="' + ti + '" data-b="' + bi + '" value="' + escapeAttr(b.text) + '">' +
          '<label><input type="checkbox" data-hl="off" data-t="' + ti + '" data-b="' + bi + '"' + (b.off ? " checked" : "") + "> greyed out</label></div>";
      }).join("") + "</div>";
    }).join("");
  }

  function ceTokenList() {
    return Object.keys(CE.tokens).map(function (k) { return "<code>{{" + k + "}}</code> " + escapeHtml(CE.tokens[k]); }).join(" &nbsp;·&nbsp; ");
  }

  function ceCommonFields() {
    var c = CE.doc;
    var typeOpts = AP_META.propertyTypes.map(function (t) { return [t, AP_META.typeLabel[t]]; });
    return '<div class="field"><label>Client name</label><input type="text" id="ceClient" value="' + escapeAttr(c.clientName) + '"></div>' +
      '<div class="field"><label>Property / unit</label><input type="text" id="ceAddr" value="' + escapeAttr(c.propertyAddress || "") + '"></div>' +
      '<div class="field"><label>Property type</label><select id="ceType">' + ceOptions(typeOpts, c.propertyType) + "</select></div>" +
      '<div class="field"><label>AC units</label><select id="ceUnits"></select></div>';
  }

  // Read-only table of what the client pays, recalculated from the price fields as you type.
  function ceUpdatePaySummary() {
    var el = document.getElementById("cePaySummary");
    if (!el || !CE || !(CE.mode === "doc" || CE.builder)) return;
    var m0 = function (n) { return "AED " + Math.round(n).toLocaleString(); };
    var m2 = function (n) { return "AED " + n.toLocaleString("en-AE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
    var num = function (id) { var v = parseFloat(document.getElementById(id).value); return v > 0 ? v : 0; };
    var rows;
    if (CE.kind === "proposal") {
      var prices = ["Basic", "Standard", "Premium"].map(function (n) { return num("cePrice" + n); });
      var plans = prices.map(apMonthlyPlan);
      rows = '<tr><th></th>' + AP_META.tiers.map(function (t) { return "<th>" + t + "</th>"; }).join("") + "</tr>" +
        "<tr><td>Paid upfront — total for the year</td>" + prices.map(function (p) { return "<td>" + m0(p) + "</td>"; }).join("") + "</tr>" +
        "<tr><td>Monthly plan — deposit</td>" + plans.map(function (p) { return "<td>" + m2(p.deposit) + "</td>"; }).join("") + "</tr>" +
        "<tr><td>Monthly plan — " + AP_META.instalments + " instalments of</td>" + plans.map(function (p) { return "<td>" + m2(p.monthly) + "</td>"; }).join("") + "</tr>" +
        "<tr><td>Monthly plan — total for the year</td>" + plans.map(function (p) { return "<td>" + m0(p.planTotal) + "</td>"; }).join("") + "</tr>";
    } else {
      var annual = num("ceAnnual");
      var plan = apMonthlyPlan(annual);
      rows = document.getElementById("cePayPlan").value === "monthly"
        ? "<tr><td>Monthly Plan — deposit (25%)</td><td>" + m2(plan.deposit) + "</td></tr>" +
          "<tr><td>Monthly Plan — " + AP_META.instalments + " instalments of</td><td>" + m2(plan.monthly) + "</td></tr>" +
          "<tr><td>Monthly Plan — total for the year (incl. 8% markup)</td><td>" + m0(plan.planTotal) + "</td></tr>"
        : "<tr><td>One-Time Payment — total for the year</td><td>" + m0(annual) + "</td></tr>";
    }
    el.innerHTML = "<table class=\"ce-table\">" + rows + "</table>";
  }

  // ---- sections, in the same order they appear in the PDF ----
  function ceCmpGridHtml() {
    return '<div class="ce-cmp-row ce-cmp-head"><span>Service</span><span>Basic</span><span>Standard</span><span>Premium</span><span></span></div>' +
      CE.comparison.map(function (r, ri) {
        return '<div class="ce-cmp-row">' + r.map(function (c, ci) {
          return '<input type="text" data-cmp="' + ri + ":" + ci + '" value="' + escapeAttr(c) + '">';
        }).join("") + '<button type="button" data-cmp-del="' + ri + '" title="Delete row">&times;</button></div>';
      }).join("");
  }

  function ceIncludedInitialType() { return ceTypeVal(); }
  function ceIncludedTypeKey() { return ceTypeVal(); }

  // Only an edited list is stored; left as-is it keeps adapting to the property type.
  function ceCurrentIncluded() {
    var el = document.getElementById("ceIncluded");
    if (!el) return null;
    var lines = ceLines(el.value);
    return JSON.stringify(lines) === JSON.stringify(CE.includedAuto[ceIncludedTypeKey()]) ? null : lines;
  }

  function ceSyncIncludedDefaults() {
    var el = document.getElementById("ceIncluded");
    if (!el) return;
    var next = CE.includedAuto[ceIncludedTypeKey()];
    if (JSON.stringify(ceLines(el.value)) === JSON.stringify(CE.includedPrevAuto)) el.value = next.join("\n");
    CE.includedPrevAuto = next;
  }

  function ceProposalSections() {
    var isDoc = CE.mode === "doc";
    var hasPrices = ceHasPrices();
    var c = CE.doc;
    var b = CE.builder;
    var priceInit;
    if (isDoc) priceInit = [c.prices.basic, c.prices.standard, c.prices.premium];
    else if (b) {
      var std0 = apPriceFor(b.propertyType, b.acUnits, null);
      var pend0 = pendingWording("proposal");
      priceInit = std0.map(function (v, i) { return pend0 && pend0.overrides && pend0.overrides[i] != null ? pend0.overrides[i] : v; });
    }
    var out = "";
    out += ceSection("Page 1", "Cover", ceLayoutFields(["eyebrow", "titleA", "titleB", "strap"]) +
      (isDoc ? '<div class="grid cols3" style="margin-top:6px">' + ceCommonFields() +
        '<div class="field"><label>Valid until</label><input type="date" id="ceValidUntil" value="' + escapeAttr(c.validUntil) + '"></div></div>'
        : (b ? '<p class="hint" style="margin:8px 0 0">Prepared for <b>' + escapeHtml(b.clientName || "(client name not entered yet)") + "</b> — " +
            escapeHtml(AP_META.typeLabel[b.propertyType]) + ", " + b.acUnits + " AC unit" + (b.acUnits === 1 ? "" : "s") +
            ". These come from the Proposal Builder; change them there.</p>" : "")),
      isDoc ? "Title block, then the Prepared for / Property / Type / AC units strip." : "Title block at the top of the first page.");
    out += ceSection("Page 1", hasPrices ? "Package cards — prices & inclusions" : "Package cards — inclusions",
      (hasPrices ? '<div class="ce-prices">' + ["Basic", "Standard", "Premium"].map(function (n, i) {
          return '<div class="field"><label>' + n + " price (AED, incl. VAT)</label>" +
            '<input type="number" id="cePrice' + n + '" min="1" step="1" value="' + priceInit[i] + '"></div>';
        }).join("") + '</div><div class="hint" id="ceStdHint" style="margin:3px 0 0"></div>' +
        '<button type="button" class="btn ghost btn-sm" id="ceSaveStdPrice" style="margin:8px 0 14px;display:none">Save these prices as the standard for this property type &amp; unit count</button>' +
        (isDoc ? "" : '<p class="hint" style="margin:0 0 14px">Prices you change here apply to the proposal you generate next. Use the button above to change the standard prices instead.</p>') : "") +
      '<div class="field"><label>Package inclusions wording</label>' +
      '<p class="hint" style="margin:0 0 8px">The bullets under each price. Use <code>{callouts}</code> to auto-insert this property type\'s real call-out count. Tick "greyed out" for items a package doesn\'t include.</p>' +
      '<div class="ce-hl-grid" id="ceHlGrid">' + ceHighlightsGridHtml() + "</div></div>");
    out += ceSection("Page 1", "Included in every package",
      ceLayoutFields(["inclTitle"]) +
      '<div class="field" style="margin-top:6px"><label>Items — one per line (shown in two columns)</label>' +
      '<textarea id="ceIncluded" rows="8" style="width:100%;box-sizing:border-box">' +
      escapeHtml((CE.included || CE.includedAuto[ceIncludedInitialType()]).join("\n")) + "</textarea></div>",
      "Left as it is, this list adapts to the property type (villas add water tank cleaning, commercial adds outside-hours scheduling)." +
      (isDoc ? "" : " Editing it here applies to every property type."));
    out += ceSection("Page 2", "What each package covers",
      ceLayoutFields(["cmpTitle", "cmpStrap"]) +
      '<div class="ce-cmp" id="ceCmpGrid" style="margin-top:6px">' + ceCmpGridHtml() + "</div>" +
      '<div style="margin-top:8px"><button type="button" class="btn ghost btn-sm" id="ceCmpAdd">+ Add row</button></div>',
      "The comparison table. <code>{callouts}</code> in the Basic or Standard column inserts that package's real call-out count for the property type.");
    out += ceSection("Page 2", "Payment options",
      ceLayoutFields(["payTitle", "payStrap", "payRowUpfront", "payRowDeposit", "payRowInstal", "payRowTotal"]) +
      (hasPrices ? '<div style="margin-top:8px"><div class="ce-sub" style="font-size:11px;color:var(--muted);margin-bottom:4px">The figures are calculated from the prices above</div><div id="cePaySummary"></div></div>' : ""),
      "Headings and row labels are editable; the amounts are always worked out from the package prices.");
    out += ceSection("Page 2", "Terms & note",
      '<p class="ce-token-list">Placeholders that fill in automatically: ' + ceTokenList() + "</p>" +
      '<div class="field"><label>Terms — one per line (numbered automatically)</label><textarea id="ceTerms" rows="9" style="width:100%;box-sizing:border-box">' +
        escapeHtml(CE.wording.terms.join("\n")) + "</textarea></div>" +
      '<div class="field" style="margin-top:10px"><label>"Please note" box</label><textarea id="ceNote" rows="4" style="width:100%;box-sizing:border-box">' +
        escapeHtml(CE.wording.note) + "</textarea></div>" +
      '<div style="margin-top:10px"><button type="button" class="btn ghost btn-sm" id="ceResetTerms">Reset wording &amp; cover text to the standard</button></div>',
      "<b>**bold**</b> makes text bold.");
    out += ceSection("Page 2", "Closing quote", ceLayoutFields(["quote", "quoteAttrib"]));
    out += ceSection("Every page", "Header & footer", ceLayoutFields(["tagline", "footer"]));
    return out;
  }

  function ceContractSections() {
    var isDoc = CE.mode === "doc";
    var c = CE.doc;
    var out = "";
    out += ceSection("Page 1", "Cover", ceLayoutFields(["eyebrow", "titleA", "titleB", "strap"]) +
      (isDoc ? '<div class="grid cols3" style="margin-top:6px">' + ceCommonFields() +
        '<div class="field"><label>Package</label><select id="cePackage">' + ceOptions(AP_META.tiers.map(function (t) { return [t, t]; }), c.package) + "</select></div>" +
        '<div class="field"><label>Payment plan</label><select id="cePayPlan">' + ceOptions([["onetime", "One-Time Payment"], ["monthly", "Monthly Plan"]], c.payPlan) + "</select></div>" +
        '<div class="field"><label>Start date</label><input type="date" id="ceStart" value="' + escapeAttr(c.startDate) + '"></div>' +
        '<div class="field"><label>Contract date</label><input type="date" id="ceContractDate" value="' + escapeAttr(c.contractDate) + '"></div></div>' : ""),
      isDoc ? "Title block, the dates top-right, then the customer / property / package strip." : "Title block at the top of the first page.");
    if (isDoc) {
      out += ceSection("Page 1", "Contract value (prices)",
        '<div class="grid cols2"><div class="field"><label>Annual value (AED, incl. VAT) <a href="#" id="ceResetPrice" style="font-weight:600;margin-left:6px">reset to standard</a></label>' +
          '<input type="number" id="ceAnnual" min="1" step="1" value="' + c.contractValueAnnual + '"><div class="hint" id="ceStdHint" style="margin:3px 0 0"></div></div>' +
          '<div class="field"><label>Contract value line on the cover (optional)</label>' +
          '<input type="text" id="ceValueText" maxlength="500" placeholder="Leave blank to generate it from the plan and value" value="' + escapeAttr(CE.valueText) + '"></div></div>' +
        '<div style="margin-top:8px"><div class="ce-sub" style="font-size:11px;color:var(--muted);margin-bottom:4px">Calculated from the value and payment plan (used in the payment terms table)</div><div id="cePaySummary"></div></div>',
        "The CONTRACT VALUE box on the cover.");
    }
    out += ceSection("Pages 1–9", "Contract wording",
      '<p class="ce-token-list">Placeholders that fill in automatically: ' + ceTokenList() + "</p>" +
      '<div id="ceBlocks"></div>' +
      '<div style="display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap">' +
        '<select id="ceAddType">' + Object.keys(CE_BLOCK_LABELS).filter(function (t) { return t !== "signature"; }).map(function (t) {
          return '<option value="' + t + '">' + CE_BLOCK_LABELS[t] + "</option>"; }).join("") + "</select>" +
        '<button type="button" class="btn ghost btn-sm" id="ceAddBlock">+ Add block at end</button>' +
        '<button type="button" class="btn ghost btn-sm" id="ceResetTerms">Reset wording &amp; cover text to the standard</button>' +
      "</div>",
      "Everything after the cover details, in page order: change any clause, add or delete blocks, reorder with the arrows, add page breaks. " +
      "<b>**bold**</b> makes text bold; tables marked <i>automatic</i> recalculate from the contract details.");
    var sig = Object.keys(AP_META.signatories).map(function (k) { return [k, AP_META.signatories[k].name]; });
    out += ceSection("Last page", "Signature",
      '<div class="grid cols2">' + (isDoc ? '<div class="field"><label>Signed for handyman.ae by</label><select id="ceSignatory">' + ceOptions(sig, c.signatory) + "</select></div>" : "") +
      "</div>" + ceLayoutFields(["providerEntity"]));
    out += ceSection("Every page", "Header & footer", ceLayoutFields(["tagline", "footer"]));
    return out;
  }

  function ceBuild() {
    var isDoc = CE.mode === "doc";
    var noun = CE.kind;
    document.getElementById("ceTitle").textContent = isDoc
      ? "Edit " + noun + " — " + CE.doc.clientName
      : "Edit " + noun + " wording";
    var revs = "";
    if (isDoc) {
      var base = CE.kind === "contract" ? "/api/amc-contracts/" : "/api/amc-proposals/";
      revs = '<div class="ce-section"><h4>Previous versions</h4><div class="ce-revs">' + (CE.revisions.length
        ? CE.revisions.map(function (r) {
            return '<a href="' + base + CE.id + "/revisions/" + r.revision + '/pdf" target="_blank">Version ' + r.revision +
                   " (replaced " + apFmtDate(r.savedAt) + " by " + escapeHtml(r.savedByEmail) + ")</a>";
          }).join("")
        : '<span class="hint">This is the original version — no earlier versions yet.</span>') + "</div></div>";
    }
    var notice = "";
    if (isDoc && CE.doc.pdfSource === "uploaded") {
      notice = '<div class="amc-form-errors" style="display:block;margin-bottom:14px;background:#FEF3EE;border-color:#F4B79B">' +
        "This " + noun + "'s PDF is currently an <b>uploaded file</b> (" + escapeHtml(CE.doc.uploadFilename || "uploaded.pdf") + "). " +
        "Saving here regenerates the PDF from the details below and replaces the upload — the upload is kept as an earlier version.</div>";
    }
    var uploadBtn = isDoc ? '<div class="ce-section"><h4>Or upload your own PDF</h4><p class="hint" style="margin-bottom:8px">Edited the PDF elsewhere (Word, Acrobat…)? Upload it to replace this ' + noun +
      "'s PDF exactly as it is. Only this " + noun + " changes; the current version is kept as an earlier version.</p>" +
      '<button type="button" class="btn ghost btn-sm" id="ceUploadPdf">Upload a PDF instead…</button></div>' : "";
    document.getElementById("ceBody").innerHTML = "";  // drop the previous editor so ceTypeKey() reads this document
    CE.layoutPrev = CE.layoutDefaults[ceTypeKey()];
    if (CE.kind === "proposal") CE.includedPrevAuto = CE.includedAuto[ceIncludedInitialType()];
    var intro = CE.mode === "template"
      ? '<p class="hint" style="margin-bottom:14px">Edit the wording, laid out in the same order as the PDF. When you save you choose: use it for <b>only the ' + noun + " you're building now</b>, or make it the <b>standard</b> every new " + noun + " starts from (" + escapeHtml(ACTIVE_TEAM === "magcity" ? "MAG City" : "Dubai") + " team). " +
        "Either way, " + noun + "s already issued keep the wording they were issued with.</p>"
      : '<p class="hint" style="margin-bottom:14px">Sections follow the PDF from the first page to the last. The tag on each heading shows where it appears.</p>';
    document.getElementById("ceBody").innerHTML = notice + intro + (CE.kind === "contract" ? ceContractSections() : ceProposalSections()) + uploadBtn + revs;
    document.getElementById("ceSave").textContent = "Save";
    document.getElementById("ceStatus").textContent = "";
    CE.dirty = false;
    if (isDoc) {
      apFillUnits("ceType", "ceUnits", false);
      document.getElementById("ceUnits").value = String(CE.doc.acUnits);
      ceUpdateStdPriceBtn();
      ceUpdatePaySummary();
      apLoadSavedPriceFor(CE.doc.propertyType, CE.doc.acUnits).then(function () {
        var std = ceStdPrices();
        if (CE.kind === "contract") CE.lastStd = std[AP_META.tiers.indexOf(CE.doc.package)];
        else CE.lastStd = std.slice();
        var hint = document.getElementById("ceStdHint");
        if (hint) hint.textContent = CE.kind === "contract"
          ? "Standard price for this package: AED " + Math.round(CE.lastStd).toLocaleString()
          : "Standard prices: AED " + std.map(function (v) { return Math.round(v).toLocaleString(); }).join(" / ");
      });
    }
    if (!isDoc && ceHasPrices()) {
      ceUpdateStdPriceBtn();
      ceUpdatePaySummary();
      var stdB = ceStdPrices();
      CE.lastStd = stdB.slice();
      var hintB = document.getElementById("ceStdHint");
      if (hintB) hintB.textContent = "Standard prices: AED " + stdB.map(function (v) { return Math.round(v).toLocaleString(); }).join(" / ");
    }
    if (CE.kind === "contract") ceRenderBlocks();
    document.getElementById("contractEditOverlay").classList.add("show");
    ceClearPreviewFrames();
    ceApplyPreviewPref();
    ceSchedulePreview(0);
  }

  function ceLines(s) { return String(s).split("\n").filter(function (l) { return l.trim() !== ""; }); }

  function ceBlockBodyHtml(b, i) {
    var f = function (name, extra) { return 'data-f="' + name + '"' + (extra || ""); };
    switch (b.type) {
      case "title": case "heading": case "subheading":
        return '<input type="text" ' + f("text") + ' value="' + escapeAttr(b.text) + '">';
      case "p":
        return '<textarea ' + f("text") + ">" + escapeHtml(b.text) + "</textarea>" +
          '<label style="font-size:12px"><input type="checkbox" ' + f("indent") + (b.indent ? " checked" : "") + "> Indented</label>";
      case "callout":
        return '<textarea ' + f("text") + ">" + escapeHtml(b.text) + "</textarea>";
      case "bullets": case "numbered":
        return '<div class="ce-sub">One item per line</div><textarea ' + f("items") + ">" + escapeHtml(b.items.join("\n")) + "</textarea>" +
          (b.type === "bullets" ? '<label style="font-size:12px"><input type="checkbox" ' + f("columns") + (b.columns === 2 ? " checked" : "") + "> Two columns</label>" : "");
      case "definitions":
        return '<div class="ce-sub">One per line: Term | Description</div><textarea ' + f("defs") + ">" +
          escapeHtml(b.items.map(function (p) { return p[0] + " | " + p[1]; }).join("\n")) + "</textarea>";
      case "checklist":
        return '<input type="text" ' + f("title") + ' value="' + escapeAttr(b.title) + '" placeholder="Box title">' +
          '<div class="ce-sub">One item per line</div><textarea ' + f("items") + ">" + escapeHtml(b.items.join("\n")) + "</textarea>";
      case "table":
        if (b.auto) {
          return '<div class="ce-auto-note">Automatic table (' + escapeHtml(b.header.join(" / ")) + ") — recalculates from the contract details. " +
            (CE.mode === "doc" ? '<button type="button" data-ce="detach">Edit this table manually</button>' : "") + "</div>";
        }
        return '<div class="ce-sub">Column headings, separated by |</div><input type="text" ' + f("thead") + ' value="' + escapeAttr(b.header.join(" | ")) + '">' +
          '<div class="ce-sub">One row per line, cells separated by |</div><textarea ' + f("trows") + ">" +
          escapeHtml(b.rows.map(function (r) { return r.join(" | "); }).join("\n")) + "</textarea>";
      case "pagebreak":
        return '<div class="ce-auto-note">— A new page starts here —</div>';
      case "signature":
        return '<div class="ce-auto-note">Signed-agreement block — shows the signatory and customer automatically.</div>';
    }
    return "";
  }

  function ceRenderBlocks() {
    var insertOpts = '<option value="">+ Insert below…</option>' + Object.keys(CE_BLOCK_LABELS).filter(function (t) { return t !== "signature"; }).map(function (t) {
      return '<option value="' + t + '">' + CE_BLOCK_LABELS[t] + "</option>"; }).join("");
    document.getElementById("ceBlocks").innerHTML = CE.terms.map(function (b, i) {
      return '<div class="ce-block' + (b.type === "pagebreak" ? " ce-pagebreak" : "") + '" data-i="' + i + '">' +
        '<div class="ce-block-head"><span>' + (CE_BLOCK_LABELS[b.type] || b.type) + (b.auto ? " (automatic)" : "") + "</span><span class=\"ce-spacer\"></span>" +
        '<button type="button" data-ce="up" title="Move up"' + (i === 0 ? " disabled" : "") + '>&uarr;</button>' +
        '<button type="button" data-ce="down" title="Move down"' + (i === CE.terms.length - 1 ? " disabled" : "") + '>&darr;</button>' +
        '<select data-ce="insert">' + insertOpts + "</select>" +
        '<button type="button" data-ce="del" title="Delete block">&times;</button></div>' +
        ceBlockBodyHtml(b, i) + "</div>";
    }).join("");
  }

  function ceSetField(i, field, el) {
    var b = CE.terms[i];
    var v = el.type === "checkbox" ? el.checked : el.value;
    if (field === "text") b.text = v;
    else if (field === "title") b.title = v;
    else if (field === "indent") b.indent = v;
    else if (field === "columns") { if (v) b.columns = 2; else delete b.columns; }
    else if (field === "items") b.items = ceLines(v);
    else if (field === "defs") {
      b.items = ceLines(v).map(function (l) {
        var k = l.indexOf("|");
        return k === -1 ? [l.trim(), ""] : [l.slice(0, k).trim(), l.slice(k + 1).trim()];
      });
    }
    else if (field === "thead") b.header = v.split("|").map(function (s) { return s.trim(); });
    else if (field === "trows") b.rows = ceLines(v).map(function (l) { return l.split("|").map(function (s) { return s.trim(); }); });
  }

  function ceCloseEditor(force) {
    var finish = function () {
      clearTimeout(CE_PREVIEW.timer);
      CE_PREVIEW.seq++;
      document.getElementById("contractEditOverlay").classList.remove("show");
      ceClearPreviewFrames();
      CE = null;
    };
    if (force || !CE || !CE.dirty) return finish();
    confirmDialog("Discard your unsaved changes?", { confirmLabel: "Discard changes", cancelLabel: "Keep editing" })
      .then(function (ok) { if (ok) finish(); });
  }

  // ---- live preview: renders the PDF from the editor's current, unsaved values ----
  var CE_PREVIEW = { seq: 0, timer: null, pref: window.innerWidth >= 1100 };

  function cePreviewOn() { return document.getElementById("contractEditOverlay").classList.contains("ce-preview-on"); }

  function cePreviewRequest() {
    if (CE.mode === "doc") {
      var r = ceDocRequest();
      var body = Object.assign({}, r.body, { team: CE.doc.team });
      delete body.saveAsDefault;
      if (CE.kind === "proposal") body.createdDate = (CE.doc.createdAt || "").slice(0, 10);
      return { url: "/api/amc-" + CE.kind + "s/preview", body: body };
    }
    // Building from a Builder: preview with that Builder's own details and prices, so it matches its live preview.
    var b = CE.builder;
    var base = { team: ACTIVE_TEAM, clientName: b.clientName, propertyAddress: b.propertyAddress, propertyType: b.propertyType, acUnits: b.acUnits };
    if (CE.kind === "contract") {
      var stdC = apPriceFor(b.propertyType, b.acUnits, null);
      return { url: "/api/amc-contracts/preview", body: Object.assign(base, {
        package: b.package, payPlan: b.payPlan, startDate: b.startDate || todayISO(), signatory: b.signatory,
        annualValue: stdC[Math.max(AP_META.tiers.indexOf(b.package), 0)], terms: ceCleanTerms(), layout: ceCurrentLayout() }) };
    }
    return { url: "/api/amc-proposals/preview", body: Object.assign(base, {
      validUntil: addDays(todayISO(), b.validityDays || 30), prices: ceCurrentPrices(), wording: ceCurrentWording() }) };
  }

  function cePreviewSetStatus(text) { document.getElementById("cePreviewStatus").textContent = text; }

  function ceSchedulePreview(delay) {
    if (!CE || !cePreviewOn()) return;
    clearTimeout(CE_PREVIEW.timer);
    cePreviewSetStatus("Updating…");
    CE_PREVIEW.timer = setTimeout(ceRenderPreview, delay == null ? 800 : delay);
  }

  function ceClearPreviewFrames() {
    document.querySelectorAll("#cePreviewFrame iframe").forEach(function (f) { URL.revokeObjectURL(f.dataset.blob); f.remove(); });
    document.getElementById("cePreviewMsg").textContent = "";
    cePreviewSetStatus("");
  }

  function ceRenderPreview() {
    if (!CE || !cePreviewOn()) return;
    var seq = ++CE_PREVIEW.seq;
    var req;
    try { req = cePreviewRequest(); } catch (e) { return; }
    fetch(req.url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req.body) })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/login.html"; throw new Error("not authenticated"); }
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || "Could not build the preview"); });
        return r.blob();
      })
      .then(function (blob) {
        if (!CE || seq !== CE_PREVIEW.seq) return;
        var wrap = document.getElementById("cePreviewFrame");
        var url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
        var frame = document.createElement("iframe");
        frame.title = "Live preview";
        frame.dataset.blob = url;
        frame.style.visibility = "hidden";
        var swapped = false;
        function swap() {
          if (swapped) return;
          swapped = true;
          wrap.querySelectorAll("iframe").forEach(function (o) { if (o !== frame) { URL.revokeObjectURL(o.dataset.blob); o.remove(); } });
          frame.style.visibility = "visible";
        }
        frame.addEventListener("load", swap);
        setTimeout(swap, 4000);
        frame.src = url + "#toolbar=0&view=FitH";
        wrap.appendChild(frame);
        document.getElementById("cePreviewMsg").textContent = "";
        cePreviewSetStatus("Up to date");
      })
      .catch(function (err) {
        if (!CE || seq !== CE_PREVIEW.seq) return;
        cePreviewSetStatus("");
        document.getElementById("cePreviewMsg").textContent = "Preview unavailable: " + (err.message || "error");
      });
  }

  function ceApplyPreviewPref() {
    document.getElementById("contractEditOverlay").classList.toggle("ce-preview-on", CE_PREVIEW.pref);
    document.getElementById("cePreviewToggle").checked = CE_PREVIEW.pref;
  }

  function ceCleanTerms() {
    return CE.terms.filter(function (b) {
      return !(["title", "heading", "subheading", "p", "callout"].indexOf(b.type) !== -1 && !String(b.text || "").trim());
    });
  }

  function ceCurrentWording() {
    return { terms: ceLines(document.getElementById("ceTerms").value), note: document.getElementById("ceNote").value, layout: ceCurrentLayout(),
             highlights: CE.highlights, comparison: CE.comparison, included: ceCurrentIncluded() };
  }

  // The request that saves the document currently open in the editor.
  function ceDocRequest() {
    var body, url;
    if (CE.kind === "contract") {
      url = "/api/amc-contracts/" + CE.id;
      body = {
        clientName: document.getElementById("ceClient").value.trim(),
        propertyAddress: document.getElementById("ceAddr").value.trim(),
        propertyType: document.getElementById("ceType").value,
        acUnits: parseInt(document.getElementById("ceUnits").value, 10),
        package: document.getElementById("cePackage").value,
        payPlan: document.getElementById("cePayPlan").value,
        startDate: document.getElementById("ceStart").value,
        contractDate: document.getElementById("ceContractDate").value,
        signatory: document.getElementById("ceSignatory").value,
        annualValue: parseFloat(document.getElementById("ceAnnual").value),
        valueText: document.getElementById("ceValueText").value.trim(),
        terms: ceCleanTerms(),
        layout: ceCurrentLayout()
      };
    } else {
      url = "/api/amc-proposals/" + CE.id;
      body = {
        clientName: document.getElementById("ceClient").value.trim(),
        propertyAddress: document.getElementById("ceAddr").value.trim(),
        propertyType: document.getElementById("ceType").value,
        acUnits: parseInt(document.getElementById("ceUnits").value, 10),
        validUntil: document.getElementById("ceValidUntil").value,
        prices: {
          basic: parseFloat(document.getElementById("cePriceBasic").value),
          standard: parseFloat(document.getElementById("cePriceStandard").value),
          premium: parseFloat(document.getElementById("cePricePremium").value)
        },
        wording: ceCurrentWording()
      };
    }
    return { url: url, body: body };
  }

  function ceSave() {
    var status = document.getElementById("ceStatus");
    var btn = document.getElementById("ceSave");
    var noun = CE.kind;
    var fail = function (err) { status.textContent = err.message || "Could not save"; };
    var finishBusy = function () { btn.disabled = false; };

    if (CE.mode === "template") {
      askSaveScope(noun, CURRENT_USER_ROLE === "admin", true).then(function (scope) {
        if (!scope) return;
        if (scope === "this") {
          var pendingData = noun === "contract" ? { terms: ceCleanTerms(), layout: ceCurrentLayout() } : ceCurrentWording();
          if (noun === "proposal") pendingData.overrides = ceOverrides();
          setPendingWording(noun, pendingData);
          toast("Custom wording" + (noun === "proposal" && pendingData.overrides.some(function (o) { return o != null; }) ? " and prices" : "") +
            " will be used for the " + noun + " you generate next — the standard is unchanged");
          ceCloseEditor(true);
          return;
        }
        btn.disabled = true;
        var req = noun === "contract"
          ? API.put("/api/amc-contracts/default-terms", { team: ACTIVE_TEAM, terms: ceCleanTerms(), layout: ceCurrentLayout() })
          : API.put("/api/amc-proposals/default-wording", { team: ACTIVE_TEAM, wording: ceCurrentWording() });
        req.then(function () {
          // wording is now the standard; any prices changed here still apply to this proposal only
          var ov = noun === "proposal" ? ceOverrides() : [];
          setPendingWording(noun, ov.some(function (o) { return o != null; }) ? Object.assign(ceCurrentWording(), { overrides: ov }) : null);
          toast("Standard " + noun + " wording saved — used for all future " + noun + "s");
          ceCloseEditor(true);
        }).catch(fail).then(finishBusy);
      });
      return;
    }

    var req = ceDocRequest();
    var body = req.body, url = req.url;
    askSaveScope(noun, CURRENT_USER_ROLE === "admin").then(function (scope) {
      if (!scope) return;
      body.saveAsDefault = scope === "all";
      btn.disabled = true;
      status.textContent = "Saving and regenerating the PDF…";
      API.put(url, body).then(function (d) {
        toast(scope === "all"
          ? "Saved — and set as the standard for all future " + noun + "s"
          : "Saved for this " + noun + " only (version " + d.revision + ")");
        ceCloseEditor(true);
        loadAmcProposalsAll();
      }).catch(fail).then(finishBusy);
    });
  }

  function ceResetWording() {
    var url = (CE.kind === "contract" ? "/api/amc-contracts/default-terms?team=" : "/api/amc-proposals/default-wording?team=");
    confirmDialog("Replace ALL the wording and cover text with the current standard? Your edits to the wording here will be lost (the details above are kept).", { confirmLabel: "Reset wording" })
      .then(function (ok) {
        if (!ok) return;
        API.get(url + encodeURIComponent(CE.mode === "doc" ? CE.doc.team : ACTIVE_TEAM)).then(function (d) {
          CE.dirty = true;
          if (CE.kind === "contract") { CE.terms = d.terms; ceRenderBlocks(); ceApplyLayout(d.layout); }
          else {
            document.getElementById("ceTerms").value = d.wording.terms.join("\n");
            document.getElementById("ceNote").value = d.wording.note;
            ceApplyLayout(d.wording.layout);
            CE.highlights = JSON.parse(JSON.stringify(d.highlights));
            document.getElementById("ceHlGrid").innerHTML = ceHighlightsGridHtml();
            CE.comparison = JSON.parse(JSON.stringify(d.comparison));
            document.getElementById("ceCmpGrid").innerHTML = ceCmpGridHtml();
            CE.includedAuto = d.autoIncluded;
            var tk = ceIncludedTypeKey();
            document.getElementById("ceIncluded").value = (d.included || d.autoIncluded[tk]).join("\n");
            CE.includedPrevAuto = d.autoIncluded[tk];
          }
          ceSchedulePreview(0);
        });
      });
  }

  // Replaces a contract's/proposal's PDF with a user-supplied file.
  var UPLOAD_TARGET = null;
  function startPdfUpload(kind, id) {
    UPLOAD_TARGET = { kind: kind, id: id };
    var input = document.getElementById("pdfUploadInput");
    input.value = "";
    input.click();
  }

  function handlePdfChosen(file) {
    var target = UPLOAD_TARGET;
    UPLOAD_TARGET = null;
    if (!file || !target) return;
    var noun = target.kind;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { toast("Please choose a PDF file"); return; }
    if (file.size > 12 * 1024 * 1024) { toast("That PDF is over the 12 MB limit"); return; }
    confirmDialog("Replace this " + noun + "'s PDF with \"" + file.name + "\"? Only this " + noun + " changes (the standard wording is not affected), and the current version is kept as an earlier version.",
      { confirmLabel: "Replace PDF" }).then(function (ok) {
      if (!ok) return;
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result).split(",")[1] || "";
        var base = target.kind === "contract" ? "/api/amc-contracts/" : "/api/amc-proposals/";
        API.post(base + target.id + "/upload", { filename: file.name, dataBase64: b64 }).then(function () {
          toast("PDF replaced with " + file.name);
          if (CE && CE.id === target.id) ceCloseEditor(true);
          loadAmcProposalsAll();
        }).catch(function (err) { toast(err.message || "Could not upload the PDF"); });
      };
      reader.onerror = function () { toast("Could not read that file"); };
      reader.readAsDataURL(file);
    });
  }

  function initContractEditor() {
    document.getElementById("pdfUploadInput").addEventListener("change", function (e) { handlePdfChosen(e.target.files[0]); });
    document.getElementById("ceClose").addEventListener("click", function () { ceCloseEditor(false); });
    document.getElementById("ceCancel").addEventListener("click", function () { ceCloseEditor(false); });
    document.getElementById("ceSave").addEventListener("click", ceSave);
    document.getElementById("apBtnEditStdContract").addEventListener("click", function () { ceOpenTemplate("contract"); });
    document.addEventListener("click", function (e) {
      var clr = e.target.closest && e.target.closest("[data-clear-pending]");
      if (clr) { e.preventDefault(); setPendingWording(clr.dataset.clearPending, null); toast("Back to the standard wording"); }
    });
    document.getElementById("apBtnEditStdProposal").addEventListener("click", function () { ceOpenTemplate("proposal"); });

    var body = document.getElementById("ceBody");
    var markDirty = function () { if (CE) CE.dirty = true; };
    document.getElementById("cePreviewToggle").addEventListener("change", function (e) {
      CE_PREVIEW.pref = e.target.checked;
      ceApplyPreviewPref();
      if (CE_PREVIEW.pref) ceSchedulePreview(0);
    });
    document.getElementById("cePreviewRefresh").addEventListener("click", function () { ceSchedulePreview(0); });
    body.addEventListener("input", function (e) {
      ceUpdatePaySummary();
      if (e.target.dataset.cmp) {
        var rc = e.target.dataset.cmp.split(":");
        CE.comparison[+rc[0]][+rc[1]] = e.target.value;
      }
      if (e.target.dataset.hl) {
        var cell = CE.highlights[+e.target.dataset.t][+e.target.dataset.b];
        if (e.target.dataset.hl === "text") cell.text = e.target.value; else cell.off = e.target.checked;
      }
      ceSchedulePreview();
      markDirty();
      var blockEl = e.target.closest(".ce-block");
      if (blockEl && e.target.dataset.f) ceSetField(parseInt(blockEl.dataset.i, 10), e.target.dataset.f, e.target);
    });
    body.addEventListener("change", function (e) {
      ceUpdatePaySummary();
      ceSchedulePreview();
      markDirty();
      var t = e.target;
      var blockEl = t.closest(".ce-block");
      if (blockEl && t.dataset.ce === "insert" && t.value) {
        var i = parseInt(blockEl.dataset.i, 10);
        CE.terms.splice(i + 1, 0, ceNewBlock(t.value));
        ceRenderBlocks();
        return;
      }
      if (blockEl && t.type === "checkbox" && t.dataset.f) ceSetField(parseInt(blockEl.dataset.i, 10), t.dataset.f, t);
      if (t.id === "ceType") { apFillUnits("ceType", "ceUnits", true); ceRefreshStdPrice(false); ceSyncLayoutDefaults(); ceUpdateStdPriceBtn(); ceSyncIncludedDefaults(); }
      else if (t.id === "ceUnits" || t.id === "cePackage") ceRefreshStdPrice(false);
    });
    body.addEventListener("click", function (e) {
      var t = e.target;
      if (t.tagName === "BUTTON" || t.tagName === "A") ceSchedulePreview();
      if (t.id === "ceResetPrice") { e.preventDefault(); ceRefreshStdPrice(true); markDirty(); return; }
      if (t.id === "ceSaveStdPrice") { ceSaveStdPrice(); return; }
      if (t.id === "ceCmpAdd") {
        CE.comparison.push(["", "", "", ""]);
        document.getElementById("ceCmpGrid").innerHTML = ceCmpGridHtml();
        markDirty();
        return;
      }
      if (t.dataset && t.dataset.cmpDel !== undefined) {
        if (CE.comparison.length > 1) CE.comparison.splice(+t.dataset.cmpDel, 1);
        document.getElementById("ceCmpGrid").innerHTML = ceCmpGridHtml();
        markDirty();
        return;
      }
      if (t.id === "ceUploadPdf") { startPdfUpload(CE.kind, CE.id); return; }
      if (t.id === "ceAddBlock") {
        CE.terms.push(ceNewBlock(document.getElementById("ceAddType").value));
        markDirty(); ceRenderBlocks();
        var list = document.getElementById("ceBlocks");
        list.lastElementChild && list.lastElementChild.scrollIntoView({ block: "center" });
        return;
      }
      if (t.id === "ceResetTerms") { ceResetWording(); return; }
      var action = t.dataset && t.dataset.ce;
      var blockEl = t.closest && t.closest(".ce-block");
      if (!action || !blockEl || action === "insert") return;
      var i = parseInt(blockEl.dataset.i, 10);
      if (action === "up" && i > 0) { var a = CE.terms.splice(i, 1)[0]; CE.terms.splice(i - 1, 0, a); }
      else if (action === "down" && i < CE.terms.length - 1) { var d = CE.terms.splice(i, 1)[0]; CE.terms.splice(i + 1, 0, d); }
      else if (action === "del") { CE.terms.splice(i, 1); }
      else if (action === "detach") {
        var b = CE.terms[i];
        b.rows = CE.autoTables[b.auto].map(function (r) { return r.slice(); });
        delete b.auto;
      } else return;
      markDirty();
      ceRenderBlocks();
    });
  }

  function apSwitchSubtab(name) {
    document.querySelectorAll(".amc-subtab[data-ap]").forEach(function (b) { b.classList.toggle("active", b.dataset.ap === name); });
    ["builder", "contract", "log"].forEach(function (n) {
      var panel = document.getElementById("ap-" + n);
      if (n === name) { panel.style.display = ""; panel.classList.add("active"); } else { panel.style.display = "none"; panel.classList.remove("active"); }
    });
  }

  function initAmcProposals() {
    document.querySelectorAll(".amc-subtab[data-ap]").forEach(function (btn) {
      btn.addEventListener("click", function () { apSwitchSubtab(btn.dataset.ap); });
    });

    document.getElementById("apPropType").addEventListener("change", function () {
      apFillUnits("apPropType", "apAcUnits", false);
      apUpdateCommCardVisibility();
      apRenderPreview();
      apLoadSavedPrice();
    });
    document.getElementById("apAcUnits").addEventListener("change", function () {
      apRenderPreview();
      apLoadSavedPrice();
    });
    document.getElementById("apBtnSaveRates").addEventListener("click", apSaveCommRates);
    document.getElementById("apBtnGenerate").addEventListener("click", apGenerateProposal);
    document.getElementById("apPreviewCards").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-create-contract]");
      if (btn) apCreateContractFromPreview(parseInt(btn.dataset.createContract, 10));
    });

    document.getElementById("acPropType").addEventListener("change", function () {
      apFillUnits("acPropType", "acAcUnits", false);
      acUpdateValueHint();
      apLoadSavedPriceFor(document.getElementById("acPropType").value, parseInt(document.getElementById("acAcUnits").value, 10)).then(acUpdateValueHint);
    });
    document.getElementById("acAcUnits").addEventListener("change", function () {
      acUpdateValueHint();
      apLoadSavedPriceFor(document.getElementById("acPropType").value, parseInt(document.getElementById("acAcUnits").value, 10)).then(acUpdateValueHint);
    });
    ["acPackage", "acPayPlan"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", acUpdateValueHint);
    });
    document.getElementById("acBtnGenerate").addEventListener("click", acGenerateContract);
    document.getElementById("acBtnClear").addEventListener("click", acResetForm);

    document.getElementById("apProposalLogBody").addEventListener("click", function (e) {
      var dl = e.target.closest("[data-dl-proposal]");
      if (dl) { window.open("/api/amc-proposals/" + dl.dataset.dlProposal + "/pdf", "_blank"); return; }
      var ed = e.target.closest("[data-edit-proposal]");
      if (ed) { ceOpenProposalEditor(ed.dataset.editProposal); return; }
      var upl = e.target.closest("[data-upload-proposal]");
      if (upl) { startPdfUpload("proposal", upl.dataset.uploadProposal); return; }
      var dup = e.target.closest("[data-dup-proposal]");
      if (dup) { apEditProposalFromLog(dup.dataset.dupProposal); return; }
      var cc = e.target.closest("[data-cc-proposal]");
      if (cc) apCreateContractFromLog(cc.dataset.ccProposal);
    });
    document.getElementById("apContractLogBody").addEventListener("click", function (e) {
      var dl = e.target.closest("[data-dl-contract]");
      if (dl) { window.open("/api/amc-contracts/" + dl.dataset.dlContract + "/pdf", "_blank"); return; }
      var upc = e.target.closest("[data-upload-contract]");
      if (upc) { startPdfUpload("contract", upc.dataset.uploadContract); return; }
      var ed = e.target.closest("[data-edit-contract]");
      if (ed) ceOpenEditor(ed.dataset.editContract);
    });
    document.getElementById("apContractLogBody").addEventListener("change", function (e) {
      var cell = e.target.closest(".ap-pay-cell");
      if (!cell) return;
      var which = e.target.classList.contains("ap-pay-amt") ? "amt" : (e.target.classList.contains("ap-pay-status") ? "status" : "notes");
      apSavePayment(cell, which);
    });
    document.getElementById("apBtnExportContracts").addEventListener("click", function () {
      window.location.href = "/api/amc-contracts/export?team=" + encodeURIComponent(ACTIVE_TEAM);
    });
    initContractEditor();
  }

  function boot() {
    initTabs();
    initCollapsibleCards();
    initPrintFix();
    initTeamSwitch();
    initBuilder();
    wirePricebookV2();
    wireCompanySettings();
    wireUsers();
    wireTemplates();
    wireHome();
    wireSavedActions();
    wireWizardTrigger();
    wireJobberHelper();
    wireLogout();
    initAmcTracker();
    loadAmcMeta();
    initAmcProposals();
    loadAmcProposalsMeta();
    loadWizardData();
    applyRoleVisibility();
    loadHome();
  }

  fetch("/api/auth/me", { credentials: "same-origin" }).then(function (r) {
    if (!r.ok) { window.location.href = "/login.html"; return null; }
    return r.json();
  }).then(function (me) {
    if (!me) return;
    CURRENT_USER = me.email;
    CURRENT_USER_ROLE = me.role || "staff";
    var userEl = document.getElementById("currentUserEmail");
    if (userEl) userEl.textContent = me.email;
    return API.get("/api/settings").then(function (s) {
      Object.assign(SETTINGS, s);
    }).catch(function () {}).then(boot).then(applySettingsToUI);
  }).catch(function () {
    window.location.href = "/login.html";
  });
})();
