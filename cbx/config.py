"""Runtime settings. Everything is overridable with environment variables or a `.env`
file next to `run.bat`. Nothing secret is hard-coded; the session secret is generated on
first start and kept in DATA_DIR/secret.key.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CBX_", env_file=ROOT / ".env", extra="ignore")

    # --- storage -------------------------------------------------------------
    data_dir: Path = ROOT / "data"
    # SQLite by default (zero install). Set CBX_DATABASE_URL=postgresql+psycopg://... for Postgres.
    database_url: str = ""

    # --- web -----------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    # Comma-separated hostnames the app may be served on. "*" only for the LAN test box.
    allowed_hosts: str = "localhost,127.0.0.1"
    # Session cookies are marked Secure on any request that arrived over HTTPS (the tunnel, a real
    # certificate). True forces it for every request — right once the LAN address is HTTPS too.
    secure_cookies: bool = False
    # Which peers may tell us the real client address (X-Forwarded-For / X-Forwarded-Proto): the ngrok
    # agent and any reverse proxy on this machine. Anyone else's forwarded headers are ignored, so a
    # sign-in rate limit cannot be dodged by sending a new made-up address with every attempt.
    trusted_proxies: str = "127.0.0.1,::1"
    # Attachments and images: per-file caps for the doors that are not the warehouse's own upload.
    max_attachment_mb: int = 25
    session_idle_hours: int = 8
    # Outbound mail from the app's own mailbox (cbx/mail.py). Off until someone turns it on, so a
    # server restored from a backup cannot start emailing real people about real expiry dates.
    mail_enabled: bool = False
    # While this holds addresses, EVERY message is redirected to them instead of the real recipient,
    # with a line at the top saying who it was really for. This is how mail gets switched on against a
    # live database full of real colleagues without a first run going to all of them.
    mail_only_to: str = ""
    mail_daily_cap: int = 200  # a runaway loop stops here rather than at someone's inbox
    # 1.0.71: which automatic mail waits as a draft for a person to check (Mailer → Drafts)
    # until it is changed on the Mailer's Settings: "support" = every ticket mail, "reminder", "flow".
    mail_drafts: str = "support"
    mail_from: str = "cbx-connect@cbreexcellerate.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    # 1.0.66: how mail leaves — "smtp" (above), or "outlook": through Outlook on this server, from the
    # mailbox named in CBX_OUTLOOK_MAILBOX (cbx/support/outlook.py). Outlook must be open on this PC.
    mail_transport: str = "smtp"
    outlook_mailbox: str = ""          # the mailbox in Outlook to send from and read, e.g. cbx-connect@cbreexcellerate.com
    outlook_folder: str = "Inbox"      # where support mail arrives in that mailbox
    support_mail: bool = False         # read the support mailbox and turn mail into tickets
    support_mail_seconds: int = 60     # how often
    support_max_new_per_sender: int = 5  # new tickets one address may open per hour (a mail loop or a flood stops here)
    # The address people open the app at (https://cbx.example.com, or the tunnel address). Links in
    # emails point here. Empty = the address the last admin signed in from, else this machine's name.
    public_url: str = ""

    # Signing in from a browser we have not seen emails a one-time code (cbx/security/devices.py).
    # If this server cannot send mail the code cannot arrive, so sign-in proceeds without it and the
    # gap is recorded — a second factor nobody can complete locks out the whole company.
    device_verification: bool = True
    device_trust_days: int = 30
    # 1.0.47: when a sign-in code cannot be emailed (mail not set up, not proven, or the send failed),
    # "terminal" shows it in the server's CBX window — and to owners/admins in Users & roles, for the
    # people they manage — and the person must still type it; "let_in" is the old way (sign-in
    # proceeds without the code and the gap is written to the audit trail).
    code_fallback: str = "terminal"
    # 1.0.48: scheduled flows (Job) start by themselves on this server; a sandbox sets this false
    flows_schedule: bool = True
    # 1.0.58: the nightly backup of the data (databases, the key, the files). Empty folder = data\backups\data;
    # put it on another disk or a synced share so a lost disk does not take the backups with it.
    backups: bool = True
    backup_dir: str = ""
    backup_at: str = "01:30"
    backup_keep_days: int = 14
    # 1.0.59: other systems read a table with a key (Connections → Keys for our data). Over plain HTTP the key
    # would cross the network readable, so it is refused unless the call comes from this machine.
    data_keys_https_only: bool = True
    # 1.0.60: the test suite fails a model call made while holding the database write lock (see gateway.py)
    strict_db_locks: bool = False
    # two-step verification (1.0.45): who must have a second step — everyone | admins (owner + admin) | none;
    # whether a code to the inbox may be someone's second step (people without an authenticator app);
    # how long owners and admins may skip the code on a remembered browser (0 = every sign-in);
    # how many wrong codes in a row lock the account until an admin unlocks it (Password policy: 3)
    mfa_required: str = "everyone"
    mfa_email_fallback: bool = True
    mfa_admin_trust_days: int = 0
    mfa_max_failed: int = 3
    # Per-IP limits on the two doors. Generous enough that a person fumbling a password never meets
    # them, tight enough that walking the address list does.
    login_rate_limit: int = 10
    login_rate_window_seconds: int = 900

    login_max_attempts: int = 5
    login_lockout_minutes: int = 15  # a wrong-password lock lifts itself after this; 0 = never

    # --- C-EXA / models --------------------------------------------------------
    ollama_url: str = "http://127.0.0.1:11434"
    gpu_slots: int = 1            # 1.0.85: model calls on the card at the same time (CBX_GPU_SLOTS); people come before background work
    digest_hour: int = 17         # 1.0.85: after this hour (server time) C-EXA posts the teams' day in one line
    # C-EXA's brain: profiles, repair, interviews, chat. CBX_AXL_MODEL (the name before 1.0.49) still works.
    cexa_model: str = Field("qwen3.8:27b", validation_alias=AliasChoices("CBX_CEXA_MODEL", "CBX_AXL_MODEL"))
    worker_model: str = "qwen3.5:9b"  # per-page vision worker for misses
    # C-EXA's team (1.0.49). Empty = the same model as C-EXA. The designer looks at screenshots, so hers
    # must be a model that can see (qwen3.8:27b can). The helper does small jobs — a smaller model is fine.
    model_developer: str = ""
    model_designer: str = ""
    model_helper: str = ""
    team_max_calls: int = 150  # model calls one job may spend before C-EXA stops and asks you
    # 1.0.62 — her agents work with tools in a loop, on lighter models, with a heartbeat.
    # The model for each kind of bot, set on Agents → Now → Models (what you pick there wins). These are
    # the fall-backs when nothing is picked: empty = CBX_CEXA_MODEL.
    model_worker_tier: str = ""   # leads, developers, analysts, reviewers — a mid-size model with tools
    model_light_tier: str = ""    # helpers, writers, translators, operators — a small model with tools
    agent_max_steps: int = 30     # tool calls a team agent may make on one piece of work before it asks up (1.0.87: was 12)
    cexa_max_steps: int = 60      # 1.0.87: C-EXA's own steps on one piece of work …
    cexa_rounds: int = 3          # … and how many times she carries on by herself after that before she asks the owner
    agent_calls_per_day: int = 400  # all agents together; then they wait for tomorrow and say so
    heartbeat_minutes: int = 15   # the light beat: no model unless something new turned up
    heartbeat_deep_at: str = "01:30"  # the nightly deep check (server time)
    heartbeat: bool = True
    entity_compliance_copy: bool = True
    # 1.0.64 — C-EXA's ears and voice, on this server (Agents → Now → Voice → Set up voice)
    voice: bool = True
    voice_stt_model: str = "large-v3-turbo"   # Whisper on the graphics card
    voice_stt_cpu_model: str = "small"         # Whisper on the CPU, when there is no card
    voice_tts_voice: str = ""                  # Kokoro's English voice; empty = bf_emma (British)
    voice_studio: bool = True                  # 1.0.68: the studio voice on the graphics card, once set up
    voice_studio_port: int = 8099              # it listens on 127.0.0.1 only
    call_ring_seconds: int = 60                # an unanswered call is missed after this  # 1.0.63: copy the built-in Entity Compliance into its tool at start (the test suite says when)
    gpu_gb: float = 0.0           # 0 = ask nvidia-smi
    # Web search (Ollama's, needs a free ollama.com account): the key stays in .env, never anywhere else.
    ollama_api_key: str = ""
    web_search: bool = True       # on when a key is set
    web_searches_per_hour: int = 30
    team_screenshots: bool = True  # the designer takes screenshots of the draft pages (needs Playwright + Chromium)
    teams_auto: bool = True  # the standing teams run on their own schedules, and Governance reviews every tested version
    ocr_model: str = "glm-ocr"  # scanned pages
    # "live" talks to Ollama; "stub" returns deterministic answers (tests, first install)
    model_mode: str = "live"
    model_batch_ceiling_zar: float = 50.0
    # Temperatures. "voice" = how C-EXA talks (chat replies, briefs, asides): high = lively.
    # "code" = anything that becomes code or a workflow: 0 = the same correct answer every time, no warm code.
    # "data" = reading PDFs, scoring, deciding which tool to use: near-zero, precision over personality.
    temp_voice: float = 2.0
    temp_code: float = 0.0
    temp_data: float = 0.1
    # Path to tesseract.exe for scanned PDFs (Windows: C:\\Program Files\\Tesseract-OCR\\tesseract.exe). Empty = on PATH.
    tesseract_cmd: str = ""

    # --- integrations --------------------------------------------------------
    # API connections may point at addresses on this company's own network (10.x, 192.168.x) only when
    # this is on. Loopback and link-local addresses are never allowed — Ollama and the cloud metadata
    # addresses live there, and a "connection" is meant for outside services.
    api_allow_private: bool = False
    # 1.0.86: the company's own API hosts that may sit on its private network without opening all of it —
    # on the office network api.excellerate.tech (Sequentra, for IFRS 16) resolves to a 10.x address.
    # Comma-separated host names; loopback and link-local stay refused for these too.
    api_private_hosts: str = "api.excellerate.tech"
    # C-EXA's hands in a proposal's sandbox (files, pytest, ruff, git, the sandbox app). False switches them off.
    cexa_hands: bool = Field(True, validation_alias=AliasChoices("CBX_CEXA_HANDS", "CBX_AXL_HANDS"))
    onedrive_enabled: bool = False
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_drive_id: str = ""
    onedrive_root: str = "CBX Warehouse"

    # --- product / branding ----------------------------------------------------
    # These are the DEFAULTS. The owner can override every one of them at runtime from
    # Settings → Branding (stored in DATA_DIR/branding/branding.json, so a promoted version never
    # undoes them). See cbx/branding.py.
    product_name: str = "CBX-Connect"
    org_name: str = "CBRE Excellerate"
    tagline: str = "Digital & Transformation"
    ai_name: str = "C-EXA"  # what the AI controller is called on screen and in her own prompts (was AXL before 1.0.49)
    logo_file: str = ""  # path to a PNG/SVG; empty = the "CBX" text mark. e.g. data\branding\logo.png
    favicon_file: str = ""  # path to a PNG/ICO; empty = the built-in SVG icon
    accent: str = ""  # optional accent colour, e.g. #0B8A63; empty = the stylesheet's default
    timezone: str = "Africa/Johannesburg"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'cbx.sqlite3').as_posix()}"

    @property
    def secret_key(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        f = self.data_dir / "secret.key"
        if not f.exists():
            f.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        return f.read_text(encoding="utf-8").strip()

    def path(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

if os.environ.get("CBX_TESTING"):
    settings.model_mode = "stub"
