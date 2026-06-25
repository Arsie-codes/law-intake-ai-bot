/* =============================================================
   LAW INTAKE BOT — admin.js
   Admin dashboard: stats, leads table, filters, modal,
   status updates, toast notifications
   Compatible with: app.py, admin.html, admin.css
============================================================= */

(function () {
  "use strict";

  /* -----------------------------------------------------------
     CONSTANTS
  ----------------------------------------------------------- */
  var ENDPOINTS = {
    leads: "/api/admin/leads",
    stats: "/api/admin/stats",
    update: "/api/admin/leads/"
  };

  var REFRESH_INTERVAL = 60000; // Auto-refresh every 60 seconds

  /* -----------------------------------------------------------
     STATE
  ----------------------------------------------------------- */
  var state = {
    leads:          [],
    filteredLeads:  [],
    currentLeadId:  null,
    refreshTimer:   null,
    isLoading:      false
  };

  /* -----------------------------------------------------------
     UTILITY — ESCAPE HTML
  ----------------------------------------------------------- */
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g,  "&amp;")
      .replace(/</g,  "&lt;")
      .replace(/>/g,  "&gt;")
      .replace(/"/g,  "&quot;")
      .replace(/'/g,  "&#039;");
  }

  /* -----------------------------------------------------------
     UTILITY — FORMAT DATE
  ----------------------------------------------------------- */
  function formatDate(dateStr) {
    if (!dateStr) return "—";
    try {
      var d = new Date(dateStr.replace(" ", "T") + "Z");
      if (isNaN(d.getTime())) {
        d = new Date(dateStr);
      }
      if (isNaN(d.getTime())) return dateStr;
      var months = [
        "Jan","Feb","Mar","Apr","May","Jun",
        "Jul","Aug","Sep","Oct","Nov","Dec"
      ];
      var month = months[d.getMonth()];
      var day   = d.getDate();
      var year  = d.getFullYear();
      var h     = d.getHours();
      var m     = d.getMinutes();
      var ampm  = h >= 12 ? "PM" : "AM";
      h = h % 12;
      h = h ? h : 12;
      m = m < 10 ? "0" + m : m;
      return month + " " + day + ", " + year +
             "<br /><small>" + h + ":" + m + " " + ampm + "</small>";
    } catch (e) {
      return escapeHtml(dateStr);
    }
  }

  /* -----------------------------------------------------------
     UTILITY — FORMAT DATE FOR MODAL (no HTML)
  ----------------------------------------------------------- */
  function formatDatePlain(dateStr) {
    if (!dateStr) return "";
    try {
      var d = new Date(dateStr.replace(" ", "T") + "Z");
      if (isNaN(d.getTime())) d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      var months = [
        "January","February","March","April","May","June",
        "July","August","September","October","November","December"
      ];
      var h    = d.getHours();
      var m    = d.getMinutes();
      var ampm = h >= 12 ? "PM" : "AM";
      h = h % 12;
      h = h ? h : 12;
      m = m < 10 ? "0" + m : m;
      return "Received " + months[d.getMonth()] + " " + d.getDate() +
             ", " + d.getFullYear() + " at " + h + ":" + m + " " + ampm;
    } catch (e) {
      return escapeHtml(dateStr);
    }
  }

  /* -----------------------------------------------------------
     UTILITY — SCORE BADGE HTML
  ----------------------------------------------------------- */
  function getScoreBadge(score) {
    var s = (score || "Cold").trim();
    var map = {
      "Hot":  { cls: "badge-score--hot",  icon: "&#128293;" },
      "Warm": { cls: "badge-score--warm", icon: "&#127777;" },
      "Cold": { cls: "badge-score--cold", icon: "&#10052;"  }
    };
    var def  = map[s] || map["Cold"];
    return (
      '<span class="badge-score ' + def.cls + '">' +
        def.icon + " " + escapeHtml(s) +
      '</span>'
    );
  }

  /* -----------------------------------------------------------
     UTILITY — STATUS BADGE HTML
  ----------------------------------------------------------- */
  function getStatusBadge(status) {
    var s = (status || "new").trim().toLowerCase();
    var map = {
      "new":       "badge-status--new",
      "contacted": "badge-status--contacted",
      "closed":    "badge-status--closed"
    };
    var cls = map[s] || "badge-status--new";
    var label = s.charAt(0).toUpperCase() + s.slice(1);
    return '<span class="badge-status ' + cls + '">' + label + '</span>';
  }

  /* -----------------------------------------------------------
     UTILITY — TRUNCATE TEXT
  ----------------------------------------------------------- */
  function truncate(str, len) {
    if (!str) return "—";
    str = String(str);
    return str.length > len ? str.substring(0, len) + "…" : str;
  }

  /* -----------------------------------------------------------
     SHOW / HIDE TABLE STATES
  ----------------------------------------------------------- */
  function showState(stateName) {
    var states = {
      loading: document.getElementById("tableLoading"),
      error:   document.getElementById("tableError"),
      empty:   document.getElementById("tableEmpty"),
      table:   document.getElementById("tableWrapper")
    };
    Object.keys(states).forEach(function (key) {
      if (states[key]) {
        states[key].style.display = (key === stateName) ? "" : "none";
      }
    });
  }

  /* -----------------------------------------------------------
     UPDATE LAST REFRESHED TIME
  ----------------------------------------------------------- */
  function updateLastRefreshed() {
    var el = document.getElementById("lastRefreshed");
    if (!el) return;
    var now  = new Date();
    var h    = now.getHours();
    var m    = now.getMinutes();
    var ampm = h >= 12 ? "PM" : "AM";
    h = h % 12;
    h = h ? h : 12;
    m = m < 10 ? "0" + m : m;
    el.textContent = "Updated " + h + ":" + m + " " + ampm;
  }

  /* -----------------------------------------------------------
     LOAD STATS
  ----------------------------------------------------------- */
  function loadStats() {
    fetch(ENDPOINTS.stats, {
      method:      "GET",
      credentials: "same-origin"
    })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = "/admin/login";
        return null;
      }
      if (!res.ok) throw new Error("Stats fetch failed");
      return res.json();
    })
    .then(function (data) {
      if (!data) return;
      var map = {
        statTotal:     data.total     || 0,
        statHot:       data.hot       || 0,
        statWarm:      data.warm      || 0,
        statCold:      data.cold      || 0,
        statNew:       data.new       || 0,
        statContacted: data.contacted || 0,
        statClosed:    data.closed    || 0
      };
      Object.keys(map).forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.textContent = map[id];
      });
    })
    .catch(function (err) {
      console.error("[Admin] Stats error:", err);
    });
  }

  /* -----------------------------------------------------------
     LOAD LEADS
  ----------------------------------------------------------- */
  function loadLeads() {
    if (state.isLoading) return;
    state.isLoading = true;
    showState("loading");

    fetch(ENDPOINTS.leads, {
      method:      "GET",
      credentials: "same-origin"
    })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = "/admin/login";
        return null;
      }
      if (!res.ok) throw new Error("Leads fetch failed: " + res.status);
      return res.json();
    })
    .then(function (data) {
      state.isLoading = false;
      if (!data) return;
      state.leads = data;
      applyFilters();
      updateLastRefreshed();
    })
    .catch(function (err) {
      state.isLoading = false;
      console.error("[Admin] Leads error:", err);
      var errEl = document.getElementById("tableErrorMsg");
      if (errEl) errEl.textContent = "Failed to load leads. Please refresh.";
      showState("error");
    });
  }

  /* -----------------------------------------------------------
     APPLY FILTERS
  ----------------------------------------------------------- */
  function applyFilters() {
    var scoreFilter  = document.getElementById("filterScore");
    var statusFilter = document.getElementById("filterStatus");

    var scoreVal  = scoreFilter  ? scoreFilter.value  : "";
    var statusVal = statusFilter ? statusFilter.value : "";

    state.filteredLeads = state.leads.filter(function (lead) {
      var matchScore  = !scoreVal  || lead.lead_score === scoreVal;
      var matchStatus = !statusVal || lead.status     === statusVal;
      return matchScore && matchStatus;
    });

    renderTable(state.filteredLeads);
  }

  /* expose to inline onchange handlers */
  window.applyFilters = applyFilters;

  /* -----------------------------------------------------------
     RENDER TABLE
  ----------------------------------------------------------- */
  function renderTable(leads) {
    var badge = document.getElementById("leadsCountBadge");
    if (badge) badge.textContent = leads.length;

    if (!leads || leads.length === 0) {
      showState("empty");
      return;
    }

    var tbody = document.getElementById("leadsTableBody");
    if (!tbody) return;

    var html = "";

    leads.forEach(function (lead) {
      var zapierHtml = lead.zapier_sent
        ? '<span class="zapier-sent"    title="Sent to Zapier"    aria-label="Sent to Zapier">&#10003;</span>'
        : '<span class="zapier-unsent"  title="Not sent to Zapier" aria-label="Not sent">&#8212;</span>';

      html += (
        '<tr data-lead-id="' + lead.id + '">' +

          /* ID */
          '<td class="col-id">' + escapeHtml(lead.id) + '</td>' +

          /* Name */
          '<td class="col-name">' +
            '<div class="lead-name">' + escapeHtml(lead.name) + '</div>' +
          '</td>' +

          /* Contact */
          '<td class="col-contact">' +
            '<span class="lead-contact-email">' +
              '<a href="mailto:' + escapeHtml(lead.email) + '" ' +
                 'title="' + escapeHtml(lead.email) + '">' +
                escapeHtml(truncate(lead.email, 28)) +
              '</a>' +
            '</span>' +
            '<span class="lead-contact-phone">' +
              '<a href="tel:' + escapeHtml(lead.phone) + '">' +
                escapeHtml(lead.phone) +
              '</a>' +
            '</span>' +
          '</td>' +

          /* Legal Issue */
          '<td class="col-issue">' +
            '<div class="lead-issue-text" title="' +
              escapeHtml(lead.legal_issue) + '">' +
              escapeHtml(truncate(lead.legal_issue, 80)) +
            '</div>' +
          '</td>' +

          /* AI Summary */
          '<td class="col-summary">' +
            '<div class="lead-summary-text" title="' +
              escapeHtml(lead.ai_summary) + '">' +
              escapeHtml(truncate(lead.ai_summary, 90)) +
            '</div>' +
          '</td>' +

          /* Score */
          '<td class="col-score">' +
            getScoreBadge(lead.lead_score) +
          '</td>' +

          /* Status */
          '<td class="col-status">' +
            '<select ' +
              'class="status-select" ' +
              'aria-label="Update status for ' + escapeHtml(lead.name) + '" ' +
              'onchange="updateStatus(' + lead.id + ', this.value, this)"' +
            '>' +
              '<option value="new"' +
                (lead.status === "new"       ? " selected" : "") +
              '>New</option>' +
              '<option value="contacted"' +
                (lead.status === "contacted" ? " selected" : "") +
              '>Contacted</option>' +
              '<option value="closed"' +
                (lead.status === "closed"    ? " selected" : "") +
              '>Closed</option>' +
            '</select>' +
          '</td>' +

          /* Date */
          '<td class="col-date">' +
            '<div class="lead-date">' + formatDate(lead.created_at) + '</div>' +
          '</td>' +

          /* Zapier */
          '<td class="col-zapier">' + zapierHtml + '</td>' +

        '</tr>' +

        /* Expandable detail row — view button */
        '<tr class="lead-detail-trigger-row">' +
          '<td colspan="9" style="padding:0 16px 10px; border-bottom: ' +
            '1px solid var(--gray-100);">' +
            '<button ' +
              'class="btn-view-lead" ' +
              'onclick="openLeadModal(' + lead.id + ')" ' +
              'aria-label="View full details for ' + escapeHtml(lead.name) + '"' +
            '>' +
              '&#128269; View Full Details' +
            '</button>' +
          '</td>' +
        '</tr>'
      );
    });

    tbody.innerHTML = html;
    showState("table");
  }

  /* -----------------------------------------------------------
     UPDATE STATUS (inline select in table)
  ----------------------------------------------------------- */
  function updateStatus(leadId, newStatus, selectEl) {
    if (!leadId || !newStatus) return;

    var validStatuses = ["new", "contacted", "closed"];
    if (validStatuses.indexOf(newStatus) === -1) return;

    /* Visual feedback — disable select while saving */
    if (selectEl) selectEl.disabled = true;

    fetch(ENDPOINTS.update + leadId, {
      method:      "PATCH",
      headers:     { "Content-Type": "application/json" },
      credentials: "same-origin",
      body:        JSON.stringify({ status: newStatus })
    })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = "/admin/login";
        return null;
      }
      if (!res.ok) throw new Error("Update failed: " + res.status);
      return res.json();
    })
    .then(function (data) {
      if (!data) return;
      if (selectEl) selectEl.disabled = false;

      if (data.success) {
        /* Update local state */
        state.leads = state.leads.map(function (lead) {
          if (lead.id === leadId) {
            lead.status = newStatus;
          }
          return lead;
        });
        showToast("Status updated to " + newStatus, "success");
        loadStats();
      } else {
        if (selectEl) selectEl.disabled = false;
        showToast("Failed to update status.", "error");
      }
    })
    .catch(function (err) {
      console.error("[Admin] Update error:", err);
      if (selectEl) selectEl.disabled = false;
      showToast("Error updating status. Please try again.", "error");
    });
  }

  /* expose to inline onchange handlers */
  window.updateStatus = updateStatus;

  /* -----------------------------------------------------------
     OPEN LEAD MODAL
  ----------------------------------------------------------- */
  function openLeadModal(leadId) {
    var lead = null;
    for (var i = 0; i < state.leads.length; i++) {
      if (state.leads[i].id === leadId) {
        lead = state.leads[i];
        break;
      }
    }
    if (!lead) return;

    state.currentLeadId = leadId;

    /* Populate modal fields */
    var modalLeadName   = document.getElementById("modalLeadName");
    var modalLeadDate   = document.getElementById("modalLeadDate");
    var modalBadges     = document.getElementById("modalBadges");
    var modalName       = document.getElementById("modalName");
    var modalEmail      = document.getElementById("modalEmail");
    var modalPhone      = document.getElementById("modalPhone");
    var modalLegalIssue = document.getElementById("modalLegalIssue");
    var modalSummary    = document.getElementById("modalSummary");
    var modalStatus     = document.getElementById("modalStatusSelect");

    if (modalLeadName)   modalLeadName.textContent   = lead.name || "Lead Detail";
    if (modalLeadDate)   modalLeadDate.textContent   = formatDatePlain(lead.created_at);
    if (modalName)       modalName.textContent       = lead.name  || "—";
    if (modalLegalIssue) modalLegalIssue.textContent = lead.legal_issue || "—";
    if (modalSummary)    modalSummary.textContent    = lead.ai_summary  || "No summary available.";

    /* Email — clickable link */
    if (modalEmail) {
      if (lead.email) {
        modalEmail.innerHTML = (
          '<a href="mailto:' + escapeHtml(lead.email) + '">' +
            escapeHtml(lead.email) +
          '</a>'
        );
      } else {
        modalEmail.textContent = "—";
      }
    }

    /* Phone — clickable link */
    if (modalPhone) {
      if (lead.phone) {
        modalPhone.innerHTML = (
          '<a href="tel:' + escapeHtml(lead.phone) + '">' +
            escapeHtml(lead.phone) +
          '</a>'
        );
      } else {
        modalPhone.textContent = "—";
      }
    }

    /* Badges */
    if (modalBadges) {
      modalBadges.innerHTML = (
        getScoreBadge(lead.lead_score) +
        " " +
        getStatusBadge(lead.status) +
        (lead.zapier_sent
          ? ' <span class="badge-score" style="background:#ecfdf5;' +
            'color:#059669;border:1px solid rgba(5,150,105,0.2);">' +
            '&#10003; Zapier Sent</span>'
          : ""
        )
      );
    }

    /* Set current status in select */
    if (modalStatus) {
      modalStatus.value = lead.status || "new";
    }

    /* Show modal */
    var modal = document.getElementById("leadModal");
    if (modal) {
      modal.style.display = "flex";
      document.body.style.overflow = "hidden";

      /* Focus the modal for accessibility */
      setTimeout(function () {
        modal.focus();
      }, 50);
    }
  }

  /* expose to inline onclick handlers */
  window.openLeadModal = openLeadModal;

  /* -----------------------------------------------------------
     CLOSE LEAD MODAL
  ----------------------------------------------------------- */
  function closeLeadModal() {
    var modal = document.getElementById("leadModal");
    if (modal) {
      modal.style.display = "none";
      document.body.style.overflow = "";
    }
    state.currentLeadId = null;
  }

  /* expose to inline onclick handlers */
  window.closeLeadModal = closeLeadModal;

  /* -----------------------------------------------------------
     UPDATE STATUS FROM MODAL
  ----------------------------------------------------------- */
  function updateStatusFromModal() {
    if (!state.currentLeadId) return;

    var selectEl   = document.getElementById("modalStatusSelect");
    var updateBtn  = document.getElementById("modalUpdateBtn");
    var newStatus  = selectEl ? selectEl.value : null;

    if (!newStatus) return;

    /* Disable button during save */
    if (updateBtn) {
      updateBtn.disabled    = true;
      updateBtn.textContent = "Saving…";
    }

    fetch(ENDPOINTS.update + state.currentLeadId, {
      method:      "PATCH",
      headers:     { "Content-Type": "application/json" },
      credentials: "same-origin",
      body:        JSON.stringify({ status: newStatus })
    })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = "/admin/login";
        return null;
      }
      if (!res.ok) throw new Error("Update failed");
      return res.json();
    })
    .then(function (data) {
      if (!data) return;

      if (updateBtn) {
        updateBtn.disabled    = false;
        updateBtn.textContent = "Save Status";
      }

      if (data.success) {
        /* Update local state */
        state.leads = state.leads.map(function (lead) {
          if (lead.id === state.currentLeadId) {
            lead.status = newStatus;
          }
          return lead;
        });

        /* Update modal badges */
        var modalBadges = document.getElementById("modalBadges");
        var currentLead = null;
        for (var i = 0; i < state.leads.length; i++) {
          if (state.leads[i].id === state.currentLeadId) {
            currentLead = state.leads[i];
            break;
          }
        }
        if (modalBadges && currentLead) {
          modalBadges.innerHTML = (
            getScoreBadge(currentLead.lead_score) +
            " " +
            getStatusBadge(newStatus) +
            (currentLead.zapier_sent
              ? ' <span class="badge-score" style="background:#ecfdf5;' +
                'color:#059669;border:1px solid rgba(5,150,105,0.2);">' +
                '&#10003; Zapier Sent</span>'
              : ""
            )
          );
        }

        /* Re-render table row select */
        applyFilters();
        loadStats();

        showToast("Status updated to " + newStatus, "success");
        closeLeadModal();

      } else {
        if (updateBtn) {
          updateBtn.disabled    = false;
          updateBtn.textContent = "Save Status";
        }
        showToast("Failed to update status.", "error");
      }
    })
    .catch(function (err) {
      console.error("[Admin] Modal update error:", err);
      if (updateBtn) {
        updateBtn.disabled    = false;
        updateBtn.textContent = "Save Status";
      }
      showToast("Error updating status. Try again.", "error");
    });
  }

  /* expose to inline onclick handlers */
  window.updateStatusFromModal = updateStatusFromModal;

  /* -----------------------------------------------------------
     TOAST NOTIFICATION
  ----------------------------------------------------------- */
  var toastTimer = null;

  function showToast(message, type) {
    var toast   = document.getElementById("toast");
    var toastMsg = document.getElementById("toastMessage");
    if (!toast || !toastMsg) return;

    /* Clear existing timer */
    if (toastTimer) clearTimeout(toastTimer);

    toastMsg.textContent = message;
    toast.className      = "toast";

    if (type === "success") toast.classList.add("toast--success");
    if (type === "error")   toast.classList.add("toast--error");

    toast.style.display = "block";

    toastTimer = setTimeout(function () {
      toast.style.display = "none";
    }, 3000);
  }

  /* -----------------------------------------------------------
     REFRESH DASHBOARD
  ----------------------------------------------------------- */
  function refreshDashboard() {
    var btn = document.getElementById("refreshBtn");
    if (btn) {
      btn.textContent = "&#8635; Refreshing…";
      btn.disabled    = true;
    }

    loadStats();
    loadLeads();

    setTimeout(function () {
      if (btn) {
        btn.textContent = "&#8635; Refresh";
        btn.disabled    = false;
      }
    }, 1500);
  }

  /* expose to inline onclick handlers */
  window.refreshDashboard = refreshDashboard;

  /* -----------------------------------------------------------
     MODAL KEYBOARD TRAP
     Close on Escape, trap Tab inside modal
  ----------------------------------------------------------- */
  document.addEventListener("keydown", function (e) {
    var modal = document.getElementById("leadModal");
    if (!modal || modal.style.display === "none") return;

    if (e.key === "Escape") {
      closeLeadModal();
      return;
    }

    /* Trap focus inside modal */
    if (e.key === "Tab") {
      var focusable = modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      var first = focusable[0];
      var last  = focusable[focusable.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  });

  /* -----------------------------------------------------------
     MODAL OVERLAY CLICK — close on outside click
  ----------------------------------------------------------- */
  var modalOverlay = document.getElementById("leadModal");
  if (modalOverlay) {
    modalOverlay.addEventListener("click", function (e) {
      if (e.target === modalOverlay) {
        closeLeadModal();
      }
    });
  }

  /* -----------------------------------------------------------
     AUTO REFRESH
  ----------------------------------------------------------- */
  function startAutoRefresh() {
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(function () {
      loadStats();
      loadLeads();
    }, REFRESH_INTERVAL);
  }

  /* -----------------------------------------------------------
     INIT
  ----------------------------------------------------------- */
  function init() {
    /* Load data immediately */
    loadStats();
    loadLeads();

    /* Start auto-refresh */
    startAutoRefresh();

    /* Re-bind modal overlay click after DOM is ready */
    var overlay = document.getElementById("leadModal");
    if (overlay) {
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeLeadModal();
      });
    }
  }

  /* Run after DOM is ready */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();