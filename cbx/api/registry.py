"""Feature routers register here. Each module exposes `router`. Missing modules are skipped so
the core still boots while a feature is being built."""
from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

FEATURE_MODULES = [
    "cbx.api.home",
    "cbx.api.pdfstudio",
    "cbx.api.master_sheets",
    "cbx.api.requests_api",
    "cbx.api.interviews",
    "cbx.api.tickets",
    "cbx.api.warehouse_api",
    "cbx.api.develop",
    "cbx.api.cexa_api",
    "cbx.api.versions_api",
    "cbx.api.areas_api",
    "cbx.api.docwatch",
    "cbx.api.engine_api",
    "cbx.api.builder_api",
    "cbx.api.viewas_api",
    "cbx.api.flows_api",
    "cbx.api.missions_api",
    "cbx.api.owner_api",
    "cbx.api.shares_api",
    "cbx.api.agents_now_api",
    "cbx.api.voice_api",
    "cbx.api.support_api",
    "cbx.api.support_screen_api",  # 1.0.109 (round 2): the reply editor, Ask AXL, previews, quick find
    "cbx.api.support_flow_api",  # 1.0.109 (round 2): the responsible person, the follow-up clock, assignee suggestions
    "cbx.api.maildesk_api",
    "cbx.api.mailer_api",
    "cbx.api.schedules_api",
    "cbx.api.ifrs16_page_api",
    "cbx.api.ap_api",
    "cbx.api.ms_api",
]


def include_feature_routers(app: FastAPI) -> None:
    for mod in FEATURE_MODULES:
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name == mod:
                logging.getLogger("cbx").warning("feature module %s not present yet", mod)
                continue
            raise
        app.include_router(m.router)
        for extra in getattr(m, "extra_routers", ()):  # e.g. a page outside /api (cbx/api/missions_api.py)
            app.include_router(extra)
