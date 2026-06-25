# =============================================================
# LAW INTAKE BOT — app.py
# =============================================================
# Flask backend: chat engine, lead capture, admin dashboard,
# OpenAI integration, Zapier webhook, email notifications
#
# Features:
#   - OpenAI GPT conversation + structured extraction
#   - Optimized extraction trigger (regex gate, not every message)
#   - International phone validation (ITU-T E.164)
#   - Email format validation
#   - Duplicate lead prevention (same email within 24 hours)
#   - Zapier webhook (primary notification)
#   - Email notification via SMTP (fallback)
#   - SQLite database (auto-created on first run)
#   - Admin dashboard with session auth
#   - Health check endpoint
#   - Session security (HttpOnly, SameSite)
# =============================================================

import os
import sqlite3
import json
import uuid
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

from flask import (
    Flask, render_template, request,
    jsonify, session, redirect, url_for
)
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
import requests


# =============================================================
# ENVIRONMENT VARIABLES
# =============================================================

load_dotenv()

SECRET_KEY         = os.getenv("SECRET_KEY",        "fallback-dev-key-change-in-production")
ADMIN_PASSWORD     = os.getenv("ADMIN_PASSWORD",     "admin123")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL       = os.getenv("OPENAI_MODEL",       "gpt-4o-mini")
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL")
FIRM_NAME          = os.getenv("FIRM_NAME",          "Smith & Associates Law Firm")
FIRM_PHONE         = os.getenv("FIRM_PHONE",         "(713) 555-0100")
FIRM_EMAIL         = os.getenv("FIRM_EMAIL",         "contact@smithlaw.com")
SMTP_HOST          = os.getenv("SMTP_HOST",          "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT",      587))
SMTP_USER          = os.getenv("SMTP_USER")
SMTP_PASSWORD      = os.getenv("SMTP_PASSWORD")
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL")


# =============================================================
# FLASK INITIALIZATION
# =============================================================

app = Flask(__name__)

# --- Security configuration ---
app.secret_key                        = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True   # JS cannot read session cookie
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # CSRF mitigation
app.config["SESSION_COOKIE_SECURE"]   = False  # Set True in production (HTTPS only)

CORS(app)

# OpenAI client — initialized once at startup
client = OpenAI(api_key=OPENAI_API_KEY)


# =============================================================
# DATABASE SETUP
# =============================================================

DB_PATH = "database/intake.db"


def get_db():
    """
    Returns a SQLite connection with dict-style row access.
    Creates the database/ directory if it does not exist.
    """
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    """
    Creates leads and conversations tables if they do not exist.
    Called once at app startup via app context.
    """
    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id          INTEGER  PRIMARY KEY AUTOINCREMENT,
            name        TEXT     NOT NULL,
            email       TEXT     NOT NULL,
            phone       TEXT     NOT NULL,
            legal_issue TEXT     NOT NULL,
            ai_summary  TEXT,
            lead_score  TEXT     DEFAULT 'Cold',
            status      TEXT     DEFAULT 'new',
            zapier_sent INTEGER  DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER  PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT     NOT NULL,
            role        TEXT     NOT NULL,
            content     TEXT     NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Tables ready at {DB_PATH}")


# =============================================================
# VALIDATION HELPERS
# =============================================================

# Email validation regex — standard RFC 5322 simplified
EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def validate_email(email):
    """
    Returns True if email matches standard format.
    Returns False for empty strings or malformed addresses.

    Accepts:  john@example.com, john.doe+filter@firm.co.uk
    Rejects:  john@, @example.com, notanemail, john@.com
    """
    if not email:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def validate_phone(phone):
    """
    Validates phone numbers internationally using the
    ITU-T E.164 standard digit count range (7-15 digits).

    Strips all non-digit characters, then checks that the
    remaining digit count falls within the valid range.
    Also rejects malformed + placement.

    Accepts:
      +92 300 1234567     -> 12 digits  valid  (Pakistan)
      +1 555 123 4567     -> 11 digits  valid  (USA/Canada)
      +44 20 1234 5678    -> 12 digits  valid  (UK)
      555-123-4567        -> 10 digits  valid  (US local)
      0300-1234567        -> 11 digits  valid  (local with trunk)

    Rejects:
      123                 ->  3 digits  invalid (too short)
      abc-defg-hijk       ->  0 digits  invalid (no digits)
      ++92300123          ->  malformed invalid (double +)
      1234567890123456    -> 16 digits  invalid (too long)
    """
    if not phone:
        return False

    stripped = phone.strip()

    # Reject double + or + not at start
    if stripped.count('+') > 1:
        return False
    if '+' in stripped and not stripped.startswith('+'):
        return False

    # Remove all non-digit characters
    digits_only = re.sub(r'\D', '', stripped)

    # ITU-T E.164: minimum 7 digits, maximum 15 digits
    return 7 <= len(digits_only) <= 15


def sanitize_lead_score(score):
    """
    Normalizes GPT-returned score to exactly one of:
    'Hot', 'Warm', or 'Cold'.

    Handles GPT edge cases: lowercase, 'Medium', 'High', 'Low'.
    Falls back to 'Cold' if value is unrecognized.
    """
    if not score:
        return "Cold"

    normalized = score.strip().capitalize()

    if normalized in ("Hot", "Warm", "Cold"):
        return normalized

    # Handle common GPT variations
    score_map = {
        "Medium": "Warm",
        "Low":    "Cold",
        "High":   "Hot",
    }
    return score_map.get(normalized, "Cold")


# =============================================================
# KNOWLEDGE BASE
# =============================================================

def load_knowledge_base():
    """
    Reads knowledge_base/firm_info.txt into memory at startup.
    Returns the full content as a string.
    Falls back to a minimal prompt if the file is missing.
    Called once — result stored in KNOWLEDGE_BASE constant.
    """
    kb_path = os.path.join("knowledge_base", "firm_info.txt")
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            content = f.read()
        print("[KB] Knowledge base loaded successfully.")
        return content
    except FileNotFoundError:
        print("[KB] WARNING: firm_info.txt not found. Using fallback prompt.")
        return (
            f"You are a professional legal intake assistant for {FIRM_NAME}. "
            f"Be empathetic and professional. Your goal is to collect the "
            f"visitor's full name, email address, phone number, and a brief "
            f"description of their legal issue."
        )


# Loaded once at startup — held in memory for all requests
KNOWLEDGE_BASE = load_knowledge_base()

# =============================================================
# MOCK MODE
# Activates automatically when OpenAI returns a quota error.
# Production-safe: real API key with credits = mock never runs.
# No configuration required.
# =============================================================

_mock_mode_active = False  # flipped to True on first quota error

MOCK_RESPONSES_CONTEXTUAL = {
    "divorce":     "Family law matters including divorce are one of our core practice areas. I can connect you with the right attorney. May I start with your full name?",
    "custody":     "Child custody cases require experienced representation. Our family law attorneys are here to help. Could I start with your full name?",
    "accident":    "I'm sorry to hear about your accident. Personal injury is one of our practice areas and we work on contingency — no fee unless we win. Could I get your name and when the incident occurred?",
    "injury":      "Personal injury cases are something we handle regularly. Could you share your name and a brief description of what happened?",
    "injured":     "I'm sorry to hear that. Our personal injury attorneys handle cases like yours regularly. May I have your full name to get started?",
    "criminal":    "Facing criminal charges is serious and you deserve strong representation. Our defense attorneys are available. May I have your name and contact number?",
    "arrested":    "If you or a loved one has been arrested, time is critical. Please share your name and number and an attorney will contact you as soon as possible.",
    "dui":         "DUI charges require immediate attention. Our criminal defense team has handled many cases like this. Could I get your name and phone number?",
    "immigration": "Our immigration attorneys assist with visas, green cards, deportation defense, and more. May I have your name to get started?",
    "visa":        "Immigration cases require careful handling. Our attorneys are experienced in this area. Could I start with your full name?",
    "deported":    "Deportation defense is a serious matter and our immigration attorneys are ready to help. May I have your name and contact number?",
    "fired":       "Wrongful termination is something our attorneys can review. May I have your name and email so an attorney can follow up?",
    "landlord":    "Tenant and landlord disputes fall under our civil litigation practice. Could I get your name and contact details?",
    "contract":    "Contract disputes are a common area we handle. Could you share your name and briefly describe the situation?",
    "business":    "Business legal matters are something our attorneys can assist with. May I have your name and contact information?",
    "will":        "Estate planning including wills and trusts is something our attorneys handle. May I have your name and a good time to call?",
    "sue":         "If you are considering legal action, our attorneys can advise you on your options. May I start with your full name?",
    "lawsuit":     "Our attorneys handle civil litigation matters. Could I get your name and a brief description of the situation?",
}

MOCK_RESPONSES_FOLLOWUP = [
    "Thank you. What is the best email address to reach you?",
    "Got it. Could I also get your phone number so an attorney can call you directly?",
    "Thank you. Could you briefly describe your legal situation so the right attorney is prepared when they contact you?",
    "Perfect. An attorney from {firm} will be in touch within 2 business hours. Is there anything else you would like to add?",
]

MOCK_RESPONSES_DEFAULT = [
    "Thank you for reaching out to {firm}. I am here to help connect you with the right attorney. Could I start with your full name?",
    "I understand. Our attorneys handle a wide range of legal matters. To get started, may I have your full name?",
    "I can help connect you with one of our attorneys. Could I start with your full name and best contact number?",
    "That is something our legal team can assist with. To connect you with the right attorney, may I have your name?",
    "Thank you for contacting {firm}. Before I connect you with an attorney, may I have your name and email address?",
]


def get_mock_response(user_message, message_count, session_id=None):
    """
    Returns a realistic law firm response without any API call.
    Tracks which fields have already been collected and asks
    for the next missing one in sequence.
    Never asks for the same field twice.
    """
    import random
    msg_lower = user_message.lower()

    # --- Check what fields are already in conversation ---
    has_name        = False
    has_email       = False
    has_phone       = False
    has_legal_issue = False

    if session_id:
        history = get_conversation_history(session_id)
        full_user_text = " ".join([
            m["content"] for m in history if m["role"] == "user"
        ])

        # Name: two capitalized words anywhere in user messages
        has_name = bool(re.search(
            r'\b[A-Z][a-z]+\s[A-Z][a-z]+\b',
            full_user_text
        ))

        # Email
        has_email = bool(re.search(
            r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
            full_user_text
        ))

        # Phone
        has_phone = bool(re.search(
            r'(\+?[\d]{1,3}[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})',
            full_user_text
        ))

        # Legal issue: at least one user message with 10+ words
        has_legal_issue = any(
            len(m["content"].split()) >= 10
            for m in history if m["role"] == "user"
        )

    # --- First message: greet + ask about legal issue ---
    if message_count <= 2:
        # Check for legal keywords to give contextual opener
        for keyword, response in MOCK_RESPONSES_CONTEXTUAL.items():
            if keyword in msg_lower:
                return response.replace("{firm}", FIRM_NAME)
        # Generic opener
        return random.choice(MOCK_RESPONSES_DEFAULT).replace("{firm}", FIRM_NAME)

    # --- Sequential field collection ---
    # Ask for each missing field in order: name → email → phone → issue

    if not has_name:
        return (
            "Thank you for reaching out. To connect you with the right "
            "attorney, may I have your full name?"
        )

    if not has_email:
        return (
            "Thank you. What is the best email address "
            "to reach you?"
        )

    if not has_phone:
        return (
            "Got it. Could I also get your phone number "
            "so an attorney can call you directly?"
        )

    if not has_legal_issue:
        return (
            "Almost done. Could you briefly describe your legal situation "
            "so the right attorney is prepared when they contact you?"
        )

    # --- All 4 fields collected ---
    return (
        f"Thank you. I have everything I need. An attorney from "
        f"{FIRM_NAME} will be in touch within 2 business hours. "
        f"Is there anything else you would like to add?"
    )

def mock_extract_fields(history):
    """
    Extracts lead fields from conversation history using
    regex only — zero API cost.
    Called instead of extract_lead_fields() when mock mode is active.
    Returns dict with all 4 fields if found, else None.
    """
    full_text = " ".join([
        m["content"] for m in history if m["role"] == "user"
    ])

    email_match = re.search(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        full_text
    )
    phone_match = re.search(
        r'(\+?[\d]{1,3}[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})',
        full_text
    )
    name_match = re.search(
        r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b',
        full_text
    )

    email = email_match.group(0) if email_match else None
    phone = phone_match.group(0) if phone_match else None
    name  = name_match.group(1)  if name_match  else None

    # Use the longest user message as the legal issue description
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    legal_issue = max(user_msgs, key=len) if user_msgs else None

    if all([name, email, phone, legal_issue]):
        return {
            "name":        name.strip(),
            "email":       email.strip(),
            "phone":       phone.strip(),
            "legal_issue": legal_issue.strip()
        }
    return None

#Part Two
# =============================================================
# OPENAI INTEGRATION
# =============================================================

def build_system_prompt():
    """
    Constructs the complete system prompt for the chat AI.
    Injects firm identity from env vars + full knowledge base.
    Called on every chat request to ensure fresh firm context.
    """
    return f"""
You are Alex, a professional and empathetic legal intake assistant for {FIRM_NAME}.
Your phone number is {FIRM_PHONE}. Your email is {FIRM_EMAIL}.

Your responsibilities:
1. Warmly greet visitors and answer general questions about the firm
2. Naturally collect these four pieces of information during conversation:
   - Full name
   - Email address
   - Phone number
   - Brief description of their legal issue
3. Never provide specific legal advice or guarantee case outcomes
4. Always clarify this chat is not legal advice if the visitor presses for it
5. If someone describes an emergency (arrest, active violence, imminent
   court date), immediately provide the firm phone number: {FIRM_PHONE}
6. Keep responses to 2-4 sentences unless more detail is clearly needed
7. Be warm, human, and empathetic — never robotic or dismissive

FIRM KNOWLEDGE BASE:
{KNOWLEDGE_BASE}

COLLECTION GUIDANCE:
Gather name, email, phone, and legal issue naturally across the conversation.
Do not ask for all four fields at once. Do not sound like a form.
Once you have confirmed all four, tell the visitor an attorney will be
in touch within 2 business hours and thank them warmly.
"""


def get_conversation_history(session_id):
    """
    Fetches all messages for a session from the conversations table.
    Returns OpenAI-compatible list of role/content dicts,
    ordered chronologically (oldest first).
    """
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content
        FROM   conversations
        WHERE  session_id = ?
        ORDER  BY created_at ASC
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def save_message(session_id, role, content):
    """
    Persists a single message (user or assistant) to the
    conversations table.
    """
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO conversations (session_id, role, content)
        VALUES (?, ?, ?)
    """, (session_id, role, content))
    conn.commit()
    conn.close()


def get_message_count(session_id):
    """
    Returns the total number of messages (all roles) in a session.
    Used by check_extraction_trigger() to catch long conversations.
    """
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM   conversations
        WHERE  session_id = ?
    """, (session_id,))
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0


def get_ai_response(session_id, user_message):
    """
    Core OpenAI conversational call.

    Flow:
    1. Save user message to DB
    2. Fetch full conversation history
    3. Build messages array (system prompt + history)
    4. Call OpenAI GPT
    5. Save assistant reply to DB
    6. Return reply string

    Uses OPENAI_MODEL from env (default: gpt-4o-mini).
    timeout=30 prevents indefinite hanging on slow API responses.
    Falls back to a human-readable error message on any exception.
    """
    # Persist user message before API call
    save_message(session_id, "user", user_message)

    history  = get_conversation_history(session_id)
    messages = [
        {"role": "system", "content": build_system_prompt()}
    ] + history

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
            timeout=30
        )
        reply = response.choices[0].message.content.strip()

    except Exception as e:
        error_str = str(e).lower()
        print(f"[OpenAI] Conversation error: {e}")

        # Quota exceeded — activate mock mode silently
        if "quota" in error_str or "insufficient" in error_str or "429" in error_str:
            global _mock_mode_active
            if not _mock_mode_active:
                _mock_mode_active = True
                print("[Mock] OpenAI quota exceeded — mock mode activated")
            reply = get_mock_response(
                user_message, get_message_count(session_id), session_id
            )
        else:
            reply = (
                f"I apologize, I'm experiencing a technical issue right now. "
                f"Please call us directly at {FIRM_PHONE} "
                f"and we'll be happy to assist you."
            )

    # Persist assistant reply
    save_message(session_id, "assistant", reply)
    return reply


# =============================================================
# OPTIMIZED LEAD EXTRACTION
# =============================================================
# extract_lead_fields() is NOT called on every user message.
#
# Before running the extraction API call, check_extraction_trigger()
# scans raw user messages with regex (zero API cost).
#
# Extraction fires only when:
#   (a) Email pattern AND phone pattern detected in user messages, OR
#   (b) Total message count in session >= 6
#
# Typical result: extraction called 1-2x per lead instead of
# once per message — ~75-85% reduction in extraction API costs.
# =============================================================

def check_extraction_trigger(session_id):
    """
    Scans raw user message text with lightweight regex.
    Returns True if extraction is worth attempting.
    Zero API cost — pure string matching.

    Trigger conditions:
      - Email pattern AND phone pattern found in user messages, OR
      - Total message count (both roles) >= 6
    """
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT content
        FROM   conversations
        WHERE  session_id = ? AND role = 'user'
        ORDER  BY created_at ASC
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()

    full_text     = " ".join([row["content"] for row in rows])
    message_count = get_message_count(session_id)

    has_email = bool(re.search(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        full_text
    ))
    has_phone = bool(re.search(
        r'(\+?[\d]{1,3}[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})',
        full_text
    ))

    strong_signal     = has_email and has_phone
    long_conversation = message_count >= 6
    should_extract    = strong_signal or long_conversation

    print(
        f"[Trigger] session={session_id[:8]}... "
        f"email={has_email} phone={has_phone} "
        f"msgs={message_count} → extract={should_extract}"
    )
    return should_extract


def extract_lead_fields(session_id):
    """
    Second OpenAI call — structured JSON field extraction.
    Only called when check_extraction_trigger() returns True.

    Sends full transcript to GPT and requests a strict JSON
    object with the 4 required lead fields.

    Returns a dict with all 4 fields if complete.
    Returns None if any required field is missing.

    Uses OPENAI_MODEL from env. temperature=0 for determinism.
    timeout=30 prevents hanging.
    """
    history = get_conversation_history(session_id)

    # Mock mode — use regex extraction, zero API cost
    if _mock_mode_active:
        return mock_extract_fields(history)

    transcript = "\n".join([
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in history
    ])

    extraction_prompt = """
You are a data extraction assistant. Read the conversation transcript below
and extract these four fields if they have been clearly provided by the user:

- name: Full name of the person
- email: Their email address
- phone: Their phone number
- legal_issue: 1-2 sentence description of their legal matter

Return ONLY a raw JSON object with exactly these four keys.
If a field has not been provided, set its value to null.
Do not include any explanation, preamble, or markdown formatting.

Example output:
{
  "name": "John Smith",
  "email": "john@example.com",
  "phone": "+1 555 123 4567",
  "legal_issue": "Involved in a rear-end car accident last month, seeking compensation."
}

CONVERSATION TRANSCRIPT:
""" + transcript

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=300,
            temperature=0,
            timeout=30
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if GPT wraps output
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$',     '', raw)

        data = json.loads(raw)

        required_fields = ["name", "email", "phone", "legal_issue"]
        if all(data.get(field) for field in required_fields):
            print(f"[Extract] All 4 fields found — session {session_id[:8]}...")
            return data
        else:
            missing = [f for f in required_fields if not data.get(f)]
            print(f"[Extract] Still missing: {missing}")
            return None

    except (json.JSONDecodeError, Exception) as e:
        print(f"[Extract] Error: {e}")
        return None


def generate_lead_summary(name, legal_issue, session_id):
    """
    Third OpenAI call — generates a professional AI summary
    and assigns a Hot / Warm / Cold lead score.

    Scoring guidance injected into prompt:
      Hot:  Urgent, clear case, within practice areas, full contact info
      Warm: Possible case, needs follow-up, some info missing
      Cold: Unclear, out of scope, or very limited information

    Uses OPENAI_MODEL from env. temperature=0.3 for slight variation.
    timeout=30 prevents hanging.
    sanitize_lead_score() guards against unexpected GPT output.

    Returns dict: { "summary": str, "score": "Hot"|"Warm"|"Cold" }
    """
    # Mock mode — return a plain summary, zero API cost
    if _mock_mode_active:
        return {
            "summary": (
                f"{name} contacted {FIRM_NAME} regarding: {legal_issue}. "
                f"Captured via AI intake assistant. "
                f"Requires attorney follow-up within 2 business hours."
            ),
            "score": "Warm"
        }

    history = get_conversation_history(session_id)

    transcript = "\n".join([
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in history[-10:]
        ])

    summary_prompt = f"""
You are a legal intake coordinator at {FIRM_NAME}.
Based on the conversation transcript below, write a professional 2-3 sentence
summary of this potential client's legal matter, suitable for an attorney
to read before a consultation call.

Then assign a lead score using exactly one of these three words:
  Hot  — Urgent case, clear legal issue, within firm practice areas, full info provided
  Warm — Possible case with merit but needs more information or clarification
  Cold — Unclear issue, likely outside practice areas, or very limited information provided

Return ONLY a raw JSON object with exactly two keys: "summary" and "score".
No explanation. No markdown. No preamble. Raw JSON only.

Example:
{{
  "summary": "John Smith was rear-ended at a red light on November 1st and sustained back injuries. He has not yet spoken with an insurance adjuster and is seeking guidance on next steps.",
  "score": "Hot"
}}

Client Name:   {name}
Legal Issue:   {legal_issue}

CONVERSATION TRANSCRIPT:
{transcript}
"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=300,
            temperature=0.3,
            timeout=30
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$',     '', raw)

        data  = json.loads(raw)
        score = sanitize_lead_score(data.get("score", "Cold"))

        return {
            "summary": data.get("summary", "No summary available."),
            "score":   score
        }

    except (json.JSONDecodeError, Exception) as e:
        print(f"[Summary] Error: {e}")
        return {
            "summary": f"{name} submitted an inquiry regarding: {legal_issue}",
            "score":   "Cold"
        }


# =============================================================
# DUPLICATE LEAD PREVENTION
# =============================================================

def find_recent_lead_by_email(email):
    """
    Checks if a lead with the same email was submitted
    within the last 24 hours.

    Returns the existing lead row (sqlite3.Row) if found.
    Returns None if no recent duplicate exists.

    Case-insensitive email comparison.
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT *
        FROM   leads
        WHERE  LOWER(email) = LOWER(?)
        AND    created_at   >= ?
        ORDER  BY created_at DESC
        LIMIT  1
    """, (email.strip(), cutoff.strftime("%Y-%m-%d %H:%M:%S")))
    row = cursor.fetchone()
    conn.close()
    return row


def save_lead(name, email, phone, legal_issue, ai_summary, lead_score):
    """
    Saves a lead to the database with duplicate prevention.

    If a lead with the same email exists within the last 24 hours:
      → Updates the existing record with fresh data
      → Returns (lead_id, True)   ← is_duplicate = True

    If no recent duplicate found:
      → Inserts a new lead record
      → Returns (lead_id, False)  ← is_duplicate = False

    Returns tuple: (lead_id: int, is_duplicate: bool)
    """
    existing = find_recent_lead_by_email(email)
    conn     = get_db()
    cursor   = conn.cursor()

    if existing:
        lead_id = existing["id"]
        cursor.execute("""
            UPDATE leads
            SET    name        = ?,
                   phone       = ?,
                   legal_issue = ?,
                   ai_summary  = ?,
                   lead_score  = ?,
                   updated_at  = CURRENT_TIMESTAMP
            WHERE  id = ?
        """, (name, phone, legal_issue, ai_summary, lead_score, lead_id))
        conn.commit()
        conn.close()
        print(f"[DB] Duplicate — updated lead #{lead_id} for {email}")
        return lead_id, True

    else:
        cursor.execute("""
            INSERT INTO leads
                (name, email, phone, legal_issue, ai_summary, lead_score)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, email, phone, legal_issue, ai_summary, lead_score))
        conn.commit()
        lead_id = cursor.lastrowid
        conn.close()
        print(f"[DB] New lead #{lead_id} saved — {name} | {lead_score}")
        return lead_id, False


# =============================================================
# ZAPIER WEBHOOK
# =============================================================

def send_to_zapier(lead_data):
    """
    POSTs lead data as JSON to the configured Zapier webhook URL.

    Returns True  on HTTP 200/201 response.
    Returns False on timeout, connection error, or bad status.
    Never raises — all exceptions are caught and logged.
    Skipped gracefully if ZAPIER_WEBHOOK_URL is not set in .env.
    """
    if not ZAPIER_WEBHOOK_URL:
        print("[Zapier] No webhook URL configured. Skipping.")
        return False

    payload = {
        "event":      "lead.captured",
        "firm_name":  FIRM_NAME,
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "lead": {
            "name":         lead_data.get("name"),
            "email":        lead_data.get("email"),
            "phone":        lead_data.get("phone"),
            "legal_issue":  lead_data.get("legal_issue"),
            "ai_summary":   lead_data.get("ai_summary"),
            "lead_score":   lead_data.get("lead_score"),
            "status":       "new",
            "is_duplicate": lead_data.get("is_duplicate", False)
        }
    }

    try:
        response = requests.post(
            ZAPIER_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        if response.status_code in (200, 201):
            print(f"[Zapier] Sent successfully — status {response.status_code}")
            return True
        else:
            print(f"[Zapier] Unexpected status: {response.status_code}")
            return False

    except requests.exceptions.Timeout:
        print("[Zapier] Request timed out after 10s.")
        return False
    except Exception as e:
        print(f"[Zapier] Error: {e}")
        return False


# =============================================================
# EMAIL NOTIFICATION (SMTP FALLBACK)
# =============================================================

def send_email_notification(lead_data):
    """
    Sends an HTML email to NOTIFICATION_EMAIL for each captured lead.
    Used as a fallback when Zapier fails or is not configured.

    Builds a professional HTML email with:
      - Color-coded lead score badge
      - Full lead details table
      - Duplicate indicator if applicable
      - Firm branding (navy and gold)

    Returns True  on successful send.
    Returns False on auth failure, connection error, or missing config.
    Never raises — all exceptions are caught and logged.
    Skipped gracefully if SMTP_USER is not set in .env.
    """
    if not SMTP_USER or not NOTIFICATION_EMAIL:
        print("[Email] SMTP not configured. Skipping.")
        return False

    score_colors = {
        "Hot":  "#e74c3c",
        "Warm": "#f39c12",
        "Cold": "#3498db"
    }
    score       = lead_data.get("lead_score", "Cold")
    score_color = score_colors.get(score, "#888888")
    is_dup      = lead_data.get("is_duplicate", False)

    dup_badge = (
        ' &nbsp;<span style="background:#888888;color:white;'
        'padding:3px 10px;border-radius:20px;font-size:12px;">'
        'Updated Record</span>'
    ) if is_dup else ""

    subject = (
        f"[{score} Lead{' - Updated' if is_dup else ''}] "
        f"New Intake: {lead_data.get('name')} — {FIRM_NAME}"
    )

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px;
                 margin: 0 auto; background: #f4f4f4; padding: 20px;">

      <div style="background: #1a2e4a; padding: 24px 28px;
                  border-radius: 8px 8px 0 0;">
        <h2 style="color: #c9a84c; margin: 0 0 4px 0; font-size: 20px;">
          New Lead Captured
        </h2>
        <p style="color: rgba(255,255,255,0.7); margin: 0; font-size: 14px;">
          {FIRM_NAME} — AI Intake Bot
        </p>
      </div>

      <div style="background: #ffffff; border: 1px solid #e0e0e0;
                  padding: 28px; border-radius: 0 0 8px 8px;">

        <div style="margin-bottom: 24px;">
          <span style="background: {score_color}; color: white;
                       padding: 6px 18px; border-radius: 20px;
                       font-weight: bold; font-size: 14px;">
            {score} Lead
          </span>{dup_badge}
        </div>

        <table style="width: 100%; border-collapse: collapse;
                      font-size: 14px;">
          <tr style="border-bottom: 1px solid #eeeeee;">
            <td style="padding: 12px 0; font-weight: 600;
                       color: #555555; width: 32%;">Name</td>
            <td style="padding: 12px 0; color: #222222;">
              {lead_data.get('name', 'N/A')}
            </td>
          </tr>
          <tr style="border-bottom: 1px solid #eeeeee;">
            <td style="padding: 12px 0; font-weight: 600; color: #555555;">
              Email
            </td>
            <td style="padding: 12px 0;">
              <a href="mailto:{lead_data.get('email', '')}"
                 style="color: #1a2e4a;">
                {lead_data.get('email', 'N/A')}
              </a>
            </td>
          </tr>
          <tr style="border-bottom: 1px solid #eeeeee;">
            <td style="padding: 12px 0; font-weight: 600; color: #555555;">
              Phone
            </td>
            <td style="padding: 12px 0;">
              <a href="tel:{lead_data.get('phone', '')}"
                 style="color: #1a2e4a;">
                {lead_data.get('phone', 'N/A')}
              </a>
            </td>
          </tr>
          <tr style="border-bottom: 1px solid #eeeeee;">
            <td style="padding: 12px 0; font-weight: 600; color: #555555;">
              Legal Issue
            </td>
            <td style="padding: 12px 0; color: #222222;">
              {lead_data.get('legal_issue', 'N/A')}
            </td>
          </tr>
          <tr>
            <td style="padding: 12px 0; font-weight: 600; color: #555555;">
              AI Summary
            </td>
            <td style="padding: 12px 0; color: #444444; font-style: italic;">
              {lead_data.get('ai_summary', 'N/A')}
            </td>
          </tr>
        </table>

        <div style="margin-top: 24px; padding: 14px 16px;
                    background: #f8f8f8; border-radius: 6px;
                    border-left: 3px solid #c9a84c;">
          <p style="margin: 0; color: #888888; font-size: 12px;">
            Received: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
            &nbsp;|&nbsp; {FIRM_NAME} Intake Bot
            {'&nbsp;|&nbsp; <strong>Duplicate updated</strong>' if is_dup else ''}
          </p>
        </div>

      </div>
    </body>
    </html>
    """

    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = NOTIFICATION_EMAIL
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, NOTIFICATION_EMAIL, msg.as_string())

        print(f"[Email] Notification sent to {NOTIFICATION_EMAIL}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[Email] Auth failed. Check SMTP_USER and SMTP_PASSWORD.")
        return False
    except Exception as e:
        print(f"[Email] Error: {e}")
        return False

#Part 3
# =============================================================
# HEALTH CHECK
# =============================================================

@app.route("/health", methods=["GET"])
def health_check():
    """
    Liveness probe endpoint.
    Used by Railway, Render, UptimeRobot, and load balancers
    to verify the application is running.

    Returns HTTP 200 with { "status": "ok" }
    No auth required.
    """
    return jsonify({"status": "ok"}), 200


# =============================================================
# PUBLIC ROUTES
# =============================================================

@app.route("/")
def index():
    """
    Serves the main landing page with embedded chat widget.
    Passes firm_name to template for dynamic rendering.
    """
    return render_template("index.html", firm_name=FIRM_NAME)


# =============================================================
# CHAT API
# =============================================================

@app.route("/api/chat", methods=["POST"])
def chat_endpoint():
    """
    Main chat endpoint. Called by widget.js on every user message.

    Flow:
    1. Validate message and session_id (generate if missing)
    2. get_ai_response()        — always runs  (1 OpenAI call)
    3. check_extraction_trigger() — regex only, zero API cost
    4. If triggered:
       extract_lead_fields()   — conditional   (1 OpenAI call)
    5. Return JSON response to widget

    Response shape:
    {
      "reply":         string,
      "session_id":    string,
      "lead_captured": boolean,
      "lead_data":     object | null
    }
    """
    data       = request.get_json(silent=True) or {}
    user_msg   = (data.get("message")    or "").strip()
    session_id = (data.get("session_id") or "").strip()

    if not user_msg:
        return jsonify({"error": "Message is required."}), 400

    # Generate session ID if widget did not provide one
    if not session_id:
        session_id = str(uuid.uuid4())

    # Step 1: Conversational AI response (always)
    reply = get_ai_response(session_id, user_msg)

    # Step 2: Check if extraction is worth running (regex gate)
    lead_data     = None
    lead_captured = False

    if check_extraction_trigger(session_id):
        lead_data = extract_lead_fields(session_id)
        if lead_data:
            lead_captured = True

    return jsonify({
        "reply":         reply,
        "session_id":    session_id,
        "lead_captured": lead_captured,
        "lead_data":     lead_data
    })


# =============================================================
# LEAD SUBMISSION API
# =============================================================

@app.route("/api/lead/submit", methods=["POST"])
def submit_lead_endpoint():
    """
    Called by widget.js when lead_captured = True.
    Validates, scores, saves, and notifies for a captured lead.

    Validations applied:
      - All 4 fields must be present (name, email, phone, legal_issue)
      - Email must pass regex format check
      - Phone must pass E.164 digit count check (7-15 digits)

    Duplicate prevention:
      - Same email within 24 hours → update existing lead, not new insert

    Notification chain:
      1. Zapier webhook (primary)
      2. Email via SMTP (fallback if Zapier fails or not configured)

    Response shape:
    {
      "success":      true,
      "lead_id":      integer,
      "lead_score":   "Hot" | "Warm" | "Cold",
      "is_duplicate": boolean
    }
    """
    data = request.get_json(silent=True) or {}

    name        = (data.get("name")        or "").strip()
    email       = (data.get("email")       or "").strip()
    phone       = (data.get("phone")       or "").strip()
    legal_issue = (data.get("legal_issue") or "").strip()
    session_id  = (data.get("session_id")  or "").strip()

    # --- Presence validation ---
    missing = [
        field for field, value in {
            "name":        name,
            "email":       email,
            "phone":       phone,
            "legal_issue": legal_issue
        }.items() if not value
    ]
    if missing:
        return jsonify({
            "error": f"Missing required fields: {', '.join(missing)}"
        }), 400

    # --- Email format validation ---
    if not validate_email(email):
        return jsonify({
            "error": "Invalid email address format."
        }), 400

    # --- International phone validation (ITU-T E.164) ---
    if not validate_phone(phone):
        return jsonify({
            "error": (
                "Invalid phone number. Please include your country "
                "code if outside the US, e.g. +92 300 1234567."
            )
        }), 400

    # --- Generate AI summary and lead score ---
    result     = generate_lead_summary(name, legal_issue, session_id)
    ai_summary = result["summary"]
    lead_score = result["score"]

    # --- Save to database (with duplicate check) ---
    lead_id, is_duplicate = save_lead(
        name, email, phone, legal_issue, ai_summary, lead_score
    )

    # --- Build notification payload ---
    lead_payload = {
        "name":         name,
        "email":        email,
        "phone":        phone,
        "legal_issue":  legal_issue,
        "ai_summary":   ai_summary,
        "lead_score":   lead_score,
        "is_duplicate": is_duplicate
    }

    # --- Zapier (primary notification) ---
    zapier_success = send_to_zapier(lead_payload)

    if zapier_success:
        # Mark as sent in database
        conn = get_db()
        conn.execute(
            "UPDATE leads SET zapier_sent = 1 WHERE id = ?",
            (lead_id,)
        )
        conn.commit()
        conn.close()
    else:
        # Zapier failed or not configured — try email fallback
        send_email_notification(lead_payload)

    return jsonify({
        "success":      True,
        "lead_id":      lead_id,
        "lead_score":   lead_score,
        "is_duplicate": is_duplicate
    })


# =============================================================
# ADMIN AUTH DECORATOR
# =============================================================

def admin_required(f):
    """
    Route decorator that enforces admin authentication.
    Checks session["admin_logged_in"] flag.
    Redirects unauthenticated requests to /admin/login.
    JSON routes return 401 instead of redirecting.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            # API routes: return 401 JSON
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required."}), 401
            # Page routes: redirect to login
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# =============================================================
# ADMIN PAGE ROUTES
# =============================================================

@app.route("/admin")
@admin_required
def admin_dashboard():
    """
    Serves the admin dashboard page.
    Requires active admin session.
    Template receives firm_name for header display.
    """
    return render_template("admin.html", firm_name=FIRM_NAME)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """
    GET:  Render the login form (admin.html with login_page=True)
    POST: Validate submitted password against ADMIN_PASSWORD env var
          On success: set session flag, redirect to /admin
          On failure: re-render with error message
    """
    error = None

    if request.method == "POST":
        submitted = request.form.get("password", "")
        if submitted == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            session.permanent          = False  # expires on browser close
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Incorrect password. Please try again."

    return render_template(
        "admin.html",
        login_page=True,
        error=error,
        firm_name=FIRM_NAME
    )


@app.route("/admin/logout")
def admin_logout():
    """
    Clears the admin session entirely and redirects to login page.
    No auth check needed — clearing is always safe.
    """
    session.clear()
    return redirect(url_for("admin_login"))


# =============================================================
# ADMIN API ROUTES (consumed by admin.js)
# =============================================================

@app.route("/api/admin/leads", methods=["GET"])
@admin_required
def api_admin_leads():
    """
    Returns all leads as a JSON array, ordered newest first.
    Includes all fields needed by admin.js to render the table.

    Protected — requires active admin session.
    Returns 401 JSON if not authenticated (API-aware decorator).
    """
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT   id, name, email, phone, legal_issue,
                 ai_summary, lead_score, status,
                 zapier_sent, created_at, updated_at
        FROM     leads
        ORDER BY created_at DESC
    """)
    rows  = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def api_admin_stats():
    """
    Returns lead count statistics for the dashboard stat cards.

    Response shape:
    {
      "total":     integer,
      "hot":       integer,
      "warm":      integer,
      "cold":      integer,
      "new":       integer,
      "contacted": integer,
      "closed":    integer
    }

    Protected — requires active admin session.
    """
    conn   = get_db()
    cursor = conn.cursor()
    stats  = {}

    cursor.execute("SELECT COUNT(*) as count FROM leads")
    stats["total"] = cursor.fetchone()["count"]

    for score in ("Hot", "Warm", "Cold"):
        cursor.execute(
            "SELECT COUNT(*) as count FROM leads WHERE lead_score = ?",
            (score,)
        )
        stats[score.lower()] = cursor.fetchone()["count"]

    for status in ("new", "contacted", "closed"):
        cursor.execute(
            "SELECT COUNT(*) as count FROM leads WHERE status = ?",
            (status,)
        )
        stats[status] = cursor.fetchone()["count"]

    conn.close()
    return jsonify(stats)


@app.route("/api/admin/leads/<int:lead_id>", methods=["PATCH"])
@admin_required
def api_update_lead(lead_id):
    """
    Updates the status field of a specific lead.

    Accepts JSON body: { "status": "new" | "contacted" | "closed" }
    Validates status against allowed values before updating.
    Updates updated_at timestamp on change.

    Protected — requires active admin session.

    Response: { "success": true }
    """
    data       = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()

    valid_statuses = ("new", "contacted", "closed")
    if new_status not in valid_statuses:
        return jsonify({
            "error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        }), 400

    conn = get_db()
    conn.execute(
        """UPDATE leads
           SET status     = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (new_status, lead_id)
    )
    conn.commit()
    conn.close()

    print(f"[Admin] Lead #{lead_id} status → '{new_status}'")
    return jsonify({"success": True})


# =============================================================
# STARTUP
# =============================================================

# Create tables on import (works with both direct run and WSGI)
with app.app_context():
    create_tables()


if __name__ == "__main__":
    print(f"\n{'=' * 56}")
    print(f"  {FIRM_NAME}")
    print(f"  Law Intake Bot — Starting up")
    print(f"{'=' * 56}")
    print(f"  Demo page       →  http://localhost:5000")
    print(f"  Admin dashboard →  http://localhost:5000/admin")
    print(f"  Health check    →  http://localhost:5000/health")
    print(f"  OpenAI model    →  {OPENAI_MODEL}")
    print(
        f"  Zapier          →  "
        f"{'configured ✓' if ZAPIER_WEBHOOK_URL else 'not configured ✗'}"
    )
    print(
        f"  Email notify    →  "
        f"{'configured ✓' if SMTP_USER else 'not configured ✗'}"
    )
    print(f"{'=' * 56}\n")

    port = int(os.getenv("PORT", 5000))
    app.run(
        host="0.0.0.0",
        debug=(os.getenv("FLASK_ENV") == "development"),
        port=port
    )