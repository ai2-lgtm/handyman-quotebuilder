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

  function confirmDialog(message) {
    return new Promise(function (resolve) {
      var overlay = document.getElementById("modalOverlay");
      document.getElementById("modalMessage").textContent = message;
      overlay.classList.add("show");
      function cleanup(result) {
        overlay.classList.remove("show");
        confirmBtn.removeEventListener("click", onConfirm);
        cancelBtn.removeEventListener("click", onCancel);
        resolve(result);
      }
      var confirmBtn = document.getElementById("modalConfirm");
      var cancelBtn = document.getElementById("modalCancel");
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
      });
    });
  }

  function goToTab(name) {
    var btn = document.querySelector('.tab[data-tab="' + name + '"]');
    if (btn) btn.click();
  }

  // ==================================================================
  // NEW QUOTE (formerly "Quote Builder")
  // ==================================================================
  var STORAGE_KEY = "handyman_quote_draft_v4";
  var builder = null;
  var PB_CATALOG = { materials: [], labour: [], fixedServices: [] };

  function defaultBuilderState() {
    var date = todayISO();
    return {
      id: null, quoteNo: "(assigned on save)", quoteSeq: null, status: "Draft", revisionNumber: 1,
      date: date, validUntil: addDays(date, 14), staff: "", technician: "", preparedBy: CURRENT_USER,
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
        '<td class="num"><input type="number" step="0.01" min="0" data-k="qty" value="' + item.qty + '" style="text-align:right"></td>' +
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
          recalcBuilder();
          saveDraft();
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
      API.get("/api/pricebook/materials").then(function (d) { PB_CATALOG.materials = d.materials; }),
      API.get("/api/pricebook/labour").then(function (d) { PB_CATALOG.labour = d.labour; }),
      API.get("/api/pricebook/fixed-services").then(function (d) { PB_CATALOG.fixedServices = d.fixedServices; })
    ]).then(function () {
      populateItemCatalogSelect();
    }).catch(function () { toast("Could not reach the server for the price book"); });
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
        recalcBuilder();
        saveDraft();
      });
    });
    document.getElementById("discountRange").addEventListener("input", function () {
      builder.discountPct = +this.value || 0;
      document.getElementById("discountPct").value = builder.discountPct;
      recalcBuilder();
      saveDraft();
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
    document.getElementById("itemKindSelect").addEventListener("change", populateItemCatalogSelect);

    document.getElementById("btnPrint").addEventListener("click", function () { window.print(); });
    document.getElementById("btnWhatsapp").addEventListener("click", function () {
      copyToClipboard(buildWhatsAppText())
        .then(function () { toast("Quote copied — paste into WhatsApp"); })
        .catch(function () { toast("Could not copy automatically — select text manually"); });
    });
    document.getElementById("btnSaveQuote").addEventListener("click", function () {
      recalcBuilder();
      var payload = JSON.parse(JSON.stringify(builder));
      var req = builder.id ? API.put("/api/quotes/" + builder.id, payload) : API.post("/api/quotes", payload);
      req.then(function (saved) {
        loadQuoteIntoBuilder(saved);
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
    bindBuilderFields();
    wireBuilderActions();
    initBuilderView();
    loadCatalogIntoBuilder();
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
    if (CURRENT_USER_ROLE === "admin") { loadCompanySettingsForm(); loadUsers(); }
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

  function reloadPbMaterials() {
    var q = document.getElementById("pbMaterialsSearch").value;
    API.get("/api/pricebook/materials" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.materials = d.materials;
      renderPbMaterials(d.materials);
    }).catch(function () { toast("Could not load materials"); });
  }
  function reloadPbLabour() {
    var q = document.getElementById("pbLabourSearch").value;
    API.get("/api/pricebook/labour" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.labour = d.labour;
      renderPbLabour(d.labour);
    }).catch(function () { toast("Could not load labour"); });
  }
  function reloadPbFixed() {
    var q = document.getElementById("pbFixedSearch").value;
    API.get("/api/pricebook/fixed-services" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      PB_CATALOG.fixedServices = d.fixedServices;
      renderPbFixed(d.fixedServices);
    }).catch(function () { toast("Could not load fixed services"); });
  }

  function pbEditableCell(value, onSave) {
    return { value: value, onSave: onSave };
  }

  function renderPbMaterials(rows) {
    var body = document.getElementById("pbMaterialsBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (m) {
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
        var editBtn = document.createElement("button");
        editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
        editBtn.addEventListener("click", function () {
          var newCost = prompt("Cost (AED)", m.cost == null ? "" : m.cost);
          if (newCost === null) return;
          var newSell = prompt("Default Sell (AED)", m.defaultSell == null ? "" : m.defaultSell);
          if (newSell === null) return;
          API.put("/api/pricebook/materials/" + m.id, { cost: newCost === "" ? null : +newCost, defaultSell: newSell === "" ? null : +newSell })
            .then(function () { toast("Updated"); reloadPbMaterials(); })
            .catch(function () { toast("Could not update"); });
        });
        tr.querySelector(".pb-actions").appendChild(editBtn);
      }
      body.appendChild(tr);
    });
  }

  function renderPbLabour(rows) {
    var body = document.getElementById("pbLabourBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (l) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(l.roleName) + "</td><td>" + escapeHtml(l.labourType || "—") + "</td>" +
        '<td class="num">' + (l.cost == null ? "—" : fmt(l.cost)) + "</td>" +
        '<td class="num">' + (l.defaultSell == null ? "—" : fmt(l.defaultSell)) + "</td>" +
        "<td>" + escapeHtml(l.unit || "—") + "</td>" +
        "<td>" + escapeHtml((l.lastUpdated || "").slice(0, 10)) + "</td>" +
        '<td class="pb-actions admin-only" style="' + (isAdmin ? "" : "display:none") + '"></td>';
      if (isAdmin) {
        var editBtn = document.createElement("button");
        editBtn.className = "btn btn-ghost btn-sm"; editBtn.textContent = "Edit";
        editBtn.addEventListener("click", function () {
          var newCost = prompt("Cost (AED)", l.cost == null ? "" : l.cost);
          if (newCost === null) return;
          var newSell = prompt("Default Sell (AED)", l.defaultSell == null ? "" : l.defaultSell);
          if (newSell === null) return;
          API.put("/api/pricebook/labour/" + l.id, { cost: newCost === "" ? null : +newCost, defaultSell: newSell === "" ? null : +newSell })
            .then(function () { toast("Updated"); reloadPbLabour(); })
            .catch(function () { toast("Could not update"); });
        });
        tr.querySelector(".pb-actions").appendChild(editBtn);
      }
      body.appendChild(tr);
    });
  }

  function renderPbFixed(rows) {
    var body = document.getElementById("pbFixedBody");
    body.innerHTML = "";
    var isAdmin = CURRENT_USER_ROLE === "admin";
    rows.forEach(function (f) {
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
        editBtn.addEventListener("click", function () {
          var newCost = prompt("Estimated Cost (AED)", f.estimatedCost == null ? "" : f.estimatedCost);
          if (newCost === null) return;
          var newSell = prompt("Standard Sell (AED)", f.standardSell == null ? "" : f.standardSell);
          if (newSell === null) return;
          API.put("/api/pricebook/fixed-services/" + f.id, { estimatedCost: newCost === "" ? null : +newCost, standardSell: newSell === "" ? null : +newSell })
            .then(function () { toast("Updated"); reloadPbFixed(); })
            .catch(function () { toast("Could not update"); });
        });
        tr.querySelector(".pb-actions").appendChild(editBtn);
      }
      body.appendChild(tr);
    });
  }

  function wirePricebookV2() {
    document.getElementById("pbMaterialsSearch").addEventListener("input", debounce(reloadPbMaterials, 250));
    document.getElementById("pbLabourSearch").addEventListener("input", debounce(reloadPbLabour, 250));
    document.getElementById("pbFixedSearch").addEventListener("input", debounce(reloadPbFixed, 250));

    document.getElementById("pbAddMaterialBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbMaterialsAddBar");
      var item = bar.querySelector(".pb-new-item").value.trim();
      if (!item) { toast("Enter an item name"); return; }
      API.post("/api/pricebook/materials", {
        category: bar.querySelector(".pb-new-cat").value.trim(),
        itemName: item,
        brand: bar.querySelector(".pb-new-brand").value.trim(),
        modelOrSize: bar.querySelector(".pb-new-model").value.trim(),
        unit: bar.querySelector(".pb-new-unit").value.trim(),
        cost: bar.querySelector(".pb-new-cost").value === "" ? null : +bar.querySelector(".pb-new-cost").value,
        defaultSell: bar.querySelector(".pb-new-sell").value === "" ? null : +bar.querySelector(".pb-new-sell").value,
        supplier: bar.querySelector(".pb-new-supplier").value.trim()
      }).then(function () {
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Material added");
        reloadPbMaterials();
      }).catch(function () { toast("Could not add material"); });
    });

    document.getElementById("pbAddLabourBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbLabourAddBar");
      var role = bar.querySelector(".pb-new-role").value.trim();
      if (!role) { toast("Enter a role name"); return; }
      API.post("/api/pricebook/labour", {
        roleName: role,
        labourType: bar.querySelector(".pb-new-labour-type").value,
        cost: bar.querySelector(".pb-new-cost").value === "" ? null : +bar.querySelector(".pb-new-cost").value,
        defaultSell: bar.querySelector(".pb-new-sell").value === "" ? null : +bar.querySelector(".pb-new-sell").value,
        unit: bar.querySelector(".pb-new-unit").value.trim()
      }).then(function () {
        bar.querySelector(".pb-new-role").value = "";
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Labour rate added");
        reloadPbLabour();
      }).catch(function () { toast("Could not add labour rate"); });
    });

    document.getElementById("pbAddFixedBtn").addEventListener("click", function () {
      var bar = document.getElementById("pbFixedAddBar");
      var name = bar.querySelector(".pb-new-service").value.trim();
      if (!name) { toast("Enter a service name"); return; }
      API.post("/api/pricebook/fixed-services", {
        category: bar.querySelector(".pb-new-cat").value.trim(),
        serviceName: name,
        estimatedCost: bar.querySelector(".pb-new-est-cost").value === "" ? null : +bar.querySelector(".pb-new-est-cost").value,
        standardSell: bar.querySelector(".pb-new-std-sell").value === "" ? null : +bar.querySelector(".pb-new-std-sell").value
      }).then(function () {
        bar.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        toast("Fixed service added");
        reloadPbFixed();
      }).catch(function () { toast("Could not add fixed service"); });
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
        '<td class="num"><input type="number" step="0.01" min="0" data-k="qty" value="' + item.qty + '" style="text-align:right"></td>' +
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
    API.get("/api/dashboard").then(function (d) {
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
    var params = [];
    if (q) params.push("q=" + encodeURIComponent(q));
    if (status) params.push("status=" + encodeURIComponent(status));
    API.get("/api/quotes" + (params.length ? "?" + params.join("&") : "")).then(function (d) {
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

  function boot() {
    initTabs();
    initCollapsibleCards();
    initPrintFix();
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
