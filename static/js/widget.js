/* =============================================================
   LAW INTAKE BOT — widget.js
   Floating chat widget: UI, messaging, lead capture
   Compatible with: app.py, index.html, widget.css
============================================================= */

(function () {
  "use strict";

  /* -----------------------------------------------------------
     CONFIGURATION
     Reads from window.LawBotConfig set in index.html
  ----------------------------------------------------------- */
  var config = window.LawBotConfig || {};
  var FIRM_NAME = config.firmName || "Our Law Firm";
  var API_BASE  = config.apiBase  || "";

  /* -----------------------------------------------------------
     STATE
  ----------------------------------------------------------- */
  var state = {
    sessionId:     null,
    isOpen:        false,
    isTyping:      false,
    isDisabled:    false,
    leadCaptured:  false,
    messageCount:  0,
    collectedData: null,
    initialized:   false
  };

  /* -----------------------------------------------------------
     CONSTANTS
  ----------------------------------------------------------- */
  var ENDPOINTS = {
    chat:   API_BASE + "/api/chat",
    submit: API_BASE + "/api/lead/submit"
  };

  var BOT_NAME   = "Alex";
  var BOT_AVATAR = "&#9878;";

  var WELCOME_MSG = (
    "Hello! I'm " + BOT_NAME + ", the virtual intake assistant for " +
    FIRM_NAME + ". I'm here to help connect you with the right attorney. " +
    "How can I assist you today?"
  );

  /* -----------------------------------------------------------
     GENERATE SESSION ID
  ----------------------------------------------------------- */
  function generateSessionId() {
    return "ses_" + Math.random().toString(36).substr(2, 9) +
           "_" + Date.now().toString(36);
  }

  /* -----------------------------------------------------------
     FORMAT TIMESTAMP
  ----------------------------------------------------------- */
  function formatTime(date) {
    var d   = date || new Date();
    var h   = d.getHours();
    var m   = d.getMinutes();
    var ampm = h >= 12 ? "PM" : "AM";
    h = h % 12;
    h = h ? h : 12;
    m = m < 10 ? "0" + m : m;
    return h + ":" + m + " " + ampm;
  }

  /* -----------------------------------------------------------
     ESCAPE HTML — prevent XSS in message rendering
  ----------------------------------------------------------- */
  function escapeHtml(str) {
    if (typeof str !== "string") return "";
    return str
      .replace(/&/g,  "&amp;")
      .replace(/</g,  "&lt;")
      .replace(/>/g,  "&gt;")
      .replace(/"/g,  "&quot;")
      .replace(/'/g,  "&#039;");
  }

  /* -----------------------------------------------------------
     FORMAT BOT TEXT
     Converts basic markdown-like patterns to HTML
  ----------------------------------------------------------- */
  function formatBotText(text) {
    if (typeof text !== "string") return "";
    return escapeHtml(text)
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br />");
  }

  /* -----------------------------------------------------------
     BUILD DOM — creates all widget elements
  ----------------------------------------------------------- */
  function buildWidget() {
    if (document.getElementById("lawbot-launcher")) return;

    /* ---- LAUNCHER BUTTON ---- */
    var launcher = document.createElement("button");
    launcher.id            = "lawbot-launcher";
    launcher.setAttribute("aria-label", "Open legal intake chat");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-controls", "lawbot-window");
    launcher.innerHTML = (
      '<div class="lawbot-launcher-icon" id="launcherIcon">' +
        '&#128172;' +
      '</div>' +
      '<div class="lawbot-launcher-dot" id="launcherDot" ' +
           'aria-hidden="true"></div>'
    );

    /* ---- CHAT WINDOW ---- */
    var win = document.createElement("div");
    win.id = "lawbot-window";
    win.setAttribute("role", "dialog");
    win.setAttribute("aria-modal", "true");
    win.setAttribute("aria-label", FIRM_NAME + " chat assistant");

    /* ---- HEADER ---- */
    var header = document.createElement("div");
    header.className = "lawbot-header";
    header.innerHTML = (
      '<div class="lawbot-avatar" aria-hidden="true">' + BOT_AVATAR + '</div>' +
      '<div class="lawbot-header-info">' +
        '<div class="lawbot-header-name">' + escapeHtml(BOT_NAME) + '</div>' +
        '<div class="lawbot-header-status">' +
          '<div class="lawbot-status-dot" aria-hidden="true"></div>' +
          '<span class="lawbot-status-text">Online — here to help</span>' +
        '</div>' +
      '</div>' +
      '<button class="lawbot-header-close" id="lawbotClose" ' +
              'aria-label="Close chat">' +
        '&times;' +
      '</button>'
    );

    /* ---- PROGRESS BAR ---- */
    var progress = document.createElement("div");
    progress.className = "lawbot-progress";
    progress.id        = "lawbotProgress";
    progress.style.display = "none";
    progress.setAttribute("aria-label", "Intake progress");
    progress.innerHTML = (
      '<div class="lawbot-progress-bar-track">' +
        '<div class="lawbot-progress-bar-fill" id="progressFill" ' +
             'style="width:0%"></div>' +
      '</div>' +
      '<span class="lawbot-progress-label" id="progressLabel"></span>'
    );

    /* ---- MESSAGES AREA ---- */
    var messages = document.createElement("div");
    messages.className         = "lawbot-messages";
    messages.id                = "lawbotMessages";
    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");
    messages.setAttribute("aria-label", "Chat messages");

    /* Welcome screen inside messages */
    var welcome = document.createElement("div");
    welcome.className = "lawbot-welcome";
    welcome.id        = "lawbotWelcome";
    welcome.innerHTML = (
      '<div class="lawbot-welcome-avatar" aria-hidden="true">' +
        BOT_AVATAR +
      '</div>' +
      '<div class="lawbot-welcome-title">Welcome to ' +
        escapeHtml(FIRM_NAME) +
      '</div>' +
      '<div class="lawbot-welcome-divider" aria-hidden="true"></div>' +
      '<div class="lawbot-welcome-subtitle">' +
        'Chat with ' + escapeHtml(BOT_NAME) + ' to get connected ' +
        'with an attorney. Available 24/7.' +
      '</div>'
    );
    messages.appendChild(welcome);

    /* ---- INPUT AREA ---- */
    var inputArea = document.createElement("div");
    inputArea.className = "lawbot-input-area";
    inputArea.innerHTML = (
      '<div class="lawbot-input-wrapper">' +
        '<textarea ' +
          'class="lawbot-input" ' +
          'id="lawbotInput" ' +
          'placeholder="Type your message..." ' +
          'rows="1" ' +
          'aria-label="Type a message" ' +
          'autocomplete="off" ' +
          'autocorrect="off" ' +
          'autocapitalize="sentences" ' +
          'spellcheck="true"' +
        '></textarea>' +
      '</div>' +
      '<button class="lawbot-send-btn" id="lawbotSend" ' +
              'aria-label="Send message" disabled>' +
        '&#9658;' +
      '</button>'
    );

    /* ---- DISCLAIMER ---- */
    var disclaimer = document.createElement("div");
    disclaimer.className = "lawbot-disclaimer";
    disclaimer.innerHTML = (
      '<p>This chat is for informational purposes only and does not ' +
      'constitute legal advice. No attorney-client relationship is formed.</p>'
    );

    /* ---- ASSEMBLE WINDOW ---- */
    win.appendChild(header);
    win.appendChild(progress);
    win.appendChild(messages);
    win.appendChild(inputArea);
    win.appendChild(disclaimer);

    /* ---- INJECT INTO PAGE ---- */
    document.body.appendChild(launcher);
    document.body.appendChild(win);

    /* ---- BIND EVENTS ---- */
    bindEvents();

    state.initialized = true;
  }

  /* -----------------------------------------------------------
     BIND EVENTS
  ----------------------------------------------------------- */
  function bindEvents() {
    var launcher  = document.getElementById("lawbot-launcher");
    var closeBtn  = document.getElementById("lawbotClose");
    var input     = document.getElementById("lawbotInput");
    var sendBtn   = document.getElementById("lawbotSend");

    /* Toggle open/close */
    launcher.addEventListener("click", function () {
      toggleWidget();
    });

    closeBtn.addEventListener("click", function () {
      closeWidget();
    });

    /* Send on button click */
    sendBtn.addEventListener("click", function () {
      handleSend();
    });

    /* Send on Enter (not Shift+Enter) */
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });

    /* Enable/disable send button based on input content */
    input.addEventListener("input", function () {
      var val = input.value.trim();
      sendBtn.disabled = (val.length === 0 || state.isDisabled);
      autoResizeTextarea(input);
    });

    /* Close on overlay click (outside window) */
    document.addEventListener("click", function (e) {
      if (!state.isOpen) return;
      var win      = document.getElementById("lawbot-window");
      var launcher = document.getElementById("lawbot-launcher");
      if (
        win      && !win.contains(e.target) &&
        launcher && !launcher.contains(e.target)
      ) {
        closeWidget();
      }
    });

    /* Close on Escape key */
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && state.isOpen) {
        closeWidget();
      }
    });
  }

  /* -----------------------------------------------------------
     AUTO RESIZE TEXTAREA
  ----------------------------------------------------------- */
  function autoResizeTextarea(el) {
    el.style.height = "auto";
    var maxH = 100;
    el.style.height = Math.min(el.scrollHeight, maxH) + "px";
  }

  /* -----------------------------------------------------------
     TOGGLE WIDGET
  ----------------------------------------------------------- */
  function toggleWidget() {
    if (state.isOpen) {
      closeWidget();
    } else {
      openWidget();
    }
  }

  /* -----------------------------------------------------------
     OPEN WIDGET
  ----------------------------------------------------------- */
  function openWidget() {
    var win          = document.getElementById("lawbot-window");
    var launcher     = document.getElementById("lawbot-launcher");
    var launcherIcon = document.getElementById("launcherIcon");
    var launcherDot  = document.getElementById("launcherDot");
    var input        = document.getElementById("lawbotInput");

    state.isOpen = true;

    win.classList.add("lawbot-window--open");
    launcher.setAttribute("aria-expanded", "true");
    launcher.setAttribute("aria-label", "Close chat");

    if (launcherIcon) {
      launcherIcon.innerHTML = "&times;";
      launcherIcon.className = "lawbot-launcher-icon lawbot-launcher-icon--close";
    }

    /* Hide notification dot once opened */
    if (launcherDot) launcherDot.style.display = "none";

    /* Send welcome message on first open */
    if (state.messageCount === 0) {
      if (!state.sessionId) {
        state.sessionId = generateSessionId();
      }
      setTimeout(function () {
        appendMessage("bot", WELCOME_MSG);
      }, 350);
    }

    /* Focus input */
    setTimeout(function () {
      if (input && !state.isDisabled) input.focus();
    }, 300);

    scrollToBottom();
  }

  /* -----------------------------------------------------------
     CLOSE WIDGET
  ----------------------------------------------------------- */
  function closeWidget() {
    var win          = document.getElementById("lawbot-window");
    var launcher     = document.getElementById("lawbot-launcher");
    var launcherIcon = document.getElementById("launcherIcon");

    state.isOpen = false;

    win.classList.remove("lawbot-window--open");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "Open legal intake chat");

    if (launcherIcon) {
      launcherIcon.innerHTML = "&#128172;";
      launcherIcon.className = "lawbot-launcher-icon";
    }
  }

  /* -----------------------------------------------------------
     HANDLE SEND
  ----------------------------------------------------------- */
  function handleSend() {
    var input = document.getElementById("lawbotInput");
    if (!input) return;

    var text = input.value.trim();
    if (!text || state.isDisabled) return;

    /* Clear input */
    input.value    = "";
    input.style.height = "auto";

    var sendBtn = document.getElementById("lawbotSend");
    if (sendBtn) sendBtn.disabled = true;

    /* Hide welcome screen on first message */
    var welcome = document.getElementById("lawbotWelcome");
    if (welcome) {
      welcome.style.display = "none";
    }

    /* Append user message */
    appendMessage("user", text);

    /* Send to API */
    sendMessage(text);
  }

  /* -----------------------------------------------------------
     SEND MESSAGE TO API
  ----------------------------------------------------------- */
  function sendMessage(text) {
    disableInput();
    showTyping();

    var payload = {
      message:    text,
      session_id: state.sessionId || ""
    };

    fetch(ENDPOINTS.chat, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload)
    })
    .then(function (res) {
      if (!res.ok) {
        throw new Error("Server responded with status " + res.status);
      }
      return res.json();
    })
    .then(function (data) {
      hideTyping();

      /* Store session ID from server */
      if (data.session_id) {
        state.sessionId = data.session_id;
      }

      /* Append bot reply */
      if (data.reply) {
        appendMessage("bot", data.reply);
      }

      /* Handle lead captured */
      if (data.lead_captured && data.lead_data && !state.leadCaptured) {
        state.collectedData = data.lead_data;
        state.leadCaptured  = true;
        updateProgress(4, 4);
        submitLead(data.lead_data);
        return;
      }

      /* Update progress bar based on collected fields */
      if (data.lead_data) {
        var fields  = data.lead_data;
        var filled  = 0;
        if (fields.name)        filled++;
        if (fields.email)       filled++;
        if (fields.phone)       filled++;
        if (fields.legal_issue) filled++;
        updateProgress(filled, 4);
      }

      enableInput();
    })
    .catch(function (err) {
      hideTyping();
      console.error("[LawBot] Chat error:", err);
      appendErrorMessage(
        "I'm sorry, I'm having a technical issue. Please try again or " +
        "call us directly."
      );
      enableInput();
    });
  }

  /* -----------------------------------------------------------
     SUBMIT LEAD TO API
  ----------------------------------------------------------- */
  function submitLead(leadData) {
    var payload = {
      name:        leadData.name        || "",
      email:       leadData.email       || "",
      phone:       leadData.phone       || "",
      legal_issue: leadData.legal_issue || "",
      session_id:  state.sessionId      || ""
    };

    fetch(ENDPOINTS.submit, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload)
    })
    .then(function (res) {
      return res.json();
    })
    .then(function (data) {
      if (data.success) {
        showCompletionScreen(data.lead_score || "");
      } else {
        /* Validation error from server — re-enable so user can correct */
        var errMsg = data.error || "There was an issue saving your information.";
        appendErrorMessage(errMsg);
        state.leadCaptured = false;
        enableInput();
      }
    })
    .catch(function (err) {
      console.error("[LawBot] Submit error:", err);
      appendErrorMessage(
        "There was a problem submitting your information. " +
        "Please try again or call us directly."
      );
      state.leadCaptured = false;
      enableInput();
    });
  }

  /* -----------------------------------------------------------
     APPEND MESSAGE BUBBLE
  ----------------------------------------------------------- */
  function appendMessage(role, text) {
    var container = document.getElementById("lawbotMessages");
    if (!container) return;

    state.messageCount++;

    var wrapper = document.createElement("div");
    wrapper.className = "lawbot-message lawbot-message--" + role;

    var html = "";

    if (role === "bot") {
      html += (
        '<div class="lawbot-message-avatar" aria-hidden="true">' +
          BOT_AVATAR +
        '</div>'
      );
    }

    html += (
      '<div>' +
        '<div class="lawbot-bubble">' +
          (role === "bot" ? formatBotText(text) : escapeHtml(text)) +
        '</div>' +
        '<div class="lawbot-message-time">' +
          formatTime(new Date()) +
        '</div>' +
      '</div>'
    );

    wrapper.innerHTML = html;
    container.appendChild(wrapper);
    scrollToBottom();
  }

  /* -----------------------------------------------------------
     APPEND ERROR MESSAGE
  ----------------------------------------------------------- */
  function appendErrorMessage(text) {
    var container = document.getElementById("lawbotMessages");
    if (!container) return;

    var wrapper = document.createElement("div");
    wrapper.className = "lawbot-message lawbot-message--bot lawbot-error-msg";

    wrapper.innerHTML = (
      '<div class="lawbot-message-avatar" aria-hidden="true">' +
        BOT_AVATAR +
      '</div>' +
      '<div>' +
        '<div class="lawbot-bubble">' +
          escapeHtml(text) +
        '</div>' +
        '<div class="lawbot-message-time">' +
          formatTime(new Date()) +
        '</div>' +
      '</div>'
    );

    container.appendChild(wrapper);
    scrollToBottom();
  }

  /* -----------------------------------------------------------
     SHOW TYPING INDICATOR
  ----------------------------------------------------------- */
  function showTyping() {
    if (state.isTyping) return;
    state.isTyping = true;

    var container = document.getElementById("lawbotMessages");
    if (!container) return;

    var typing = document.createElement("div");
    typing.className = "lawbot-typing";
    typing.id        = "lawbotTyping";
    typing.setAttribute("aria-label", BOT_NAME + " is typing");
    typing.innerHTML = (
      '<div class="lawbot-typing-avatar" aria-hidden="true">' +
        BOT_AVATAR +
      '</div>' +
      '<div class="lawbot-typing-bubble" aria-hidden="true">' +
        '<div class="lawbot-typing-dot"></div>' +
        '<div class="lawbot-typing-dot"></div>' +
        '<div class="lawbot-typing-dot"></div>' +
      '</div>'
    );

    container.appendChild(typing);
    scrollToBottom();
  }

  /* -----------------------------------------------------------
     HIDE TYPING INDICATOR
  ----------------------------------------------------------- */
  function hideTyping() {
    state.isTyping = false;
    var typing = document.getElementById("lawbotTyping");
    if (typing) typing.parentNode.removeChild(typing);
  }

  /* -----------------------------------------------------------
     DISABLE INPUT
  ----------------------------------------------------------- */
  function disableInput() {
    state.isDisabled = true;
    var input   = document.getElementById("lawbotInput");
    var sendBtn = document.getElementById("lawbotSend");
    if (input)   { input.disabled   = true; }
    if (sendBtn) { sendBtn.disabled = true; }
  }

  /* -----------------------------------------------------------
     ENABLE INPUT
  ----------------------------------------------------------- */
  function enableInput() {
    state.isDisabled = false;
    var input   = document.getElementById("lawbotInput");
    var sendBtn = document.getElementById("lawbotSend");
    if (input) {
      input.disabled = false;
      if (state.isOpen) input.focus();
    }
    if (sendBtn) {
      var val = input ? input.value.trim() : "";
      sendBtn.disabled = (val.length === 0);
    }
  }

  /* -----------------------------------------------------------
     SCROLL TO BOTTOM
  ----------------------------------------------------------- */
  function scrollToBottom() {
    var container = document.getElementById("lawbotMessages");
    if (!container) return;
    setTimeout(function () {
      container.scrollTop = container.scrollHeight;
    }, 50);
  }

  /* -----------------------------------------------------------
     UPDATE PROGRESS BAR
  ----------------------------------------------------------- */
  function updateProgress(filled, total) {
    var progressBar   = document.getElementById("lawbotProgress");
    var progressFill  = document.getElementById("progressFill");
    var progressLabel = document.getElementById("progressLabel");

    if (!progressBar || !progressFill || !progressLabel) return;

    if (filled <= 0) {
      progressBar.style.display = "none";
      return;
    }

    progressBar.style.display = "flex";

    var pct = Math.round((filled / total) * 100);
    progressFill.style.width = pct + "%";

    var labels = ["", "Name collected", "Contact info noted",
                  "Almost done", "Information complete"];
    progressLabel.textContent = labels[Math.min(filled, labels.length - 1)];
  }

  /* -----------------------------------------------------------
     SHOW COMPLETION SCREEN
     Replaces input area with thank-you message
  ----------------------------------------------------------- */
  function showCompletionScreen(score) {
    var messages  = document.getElementById("lawbotMessages");
    var inputArea = document.querySelector(".lawbot-input-area");
    var progress  = document.getElementById("lawbotProgress");
    var disclaimer = document.querySelector(".lawbot-disclaimer");

    /* Hide progress and input */
    if (progress)   progress.style.display   = "none";
    if (inputArea)  inputArea.style.display   = "none";
    if (disclaimer) disclaimer.style.display  = "none";

    /* Score badge text */
    var scoreText = "";
    if (score === "Hot") {
      scoreText = "&#128293; High Priority — attorney notified";
    } else if (score === "Warm") {
      scoreText = "&#127777; Good Case — we'll be in touch";
    } else {
      scoreText = "&#9993; Received — we'll review your inquiry";
    }

    /* Clear messages and show completion */
    if (messages) {
      messages.innerHTML = "";
      var completion = document.createElement("div");
      completion.className = "lawbot-completion";
      completion.innerHTML = (
        '<div class="lawbot-completion-icon" aria-hidden="true">&#10003;</div>' +
        '<div class="lawbot-completion-title">Thank You!</div>' +
        '<div class="lawbot-completion-body">' +
          'Your information has been received. An attorney from ' +
          escapeHtml(FIRM_NAME) +
          ' will contact you within 2 business hours.' +
        '</div>' +
        '<div class="lawbot-completion-score">' +
          scoreText +
        '</div>' +
        '<div class="lawbot-completion-phone">' +
          'Need immediate help? Call us directly.' +
        '</div>'
      );
      messages.appendChild(completion);
    }
  }

  /* -----------------------------------------------------------
     PUBLIC API
     Exposed on window.LawBot for external access
     e.g. <button onclick="window.LawBot.open()">Chat</button>
  ----------------------------------------------------------- */
  window.LawBot = {
    open:   openWidget,
    close:  closeWidget,
    toggle: toggleWidget
  };

  /* -----------------------------------------------------------
     INIT — runs after DOM is ready
  ----------------------------------------------------------- */
  function init() {
    if (state.initialized) return;
    buildWidget();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();