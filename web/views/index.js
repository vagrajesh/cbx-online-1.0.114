// Route → view module. Each view exports { title, navRoute?, render(el, ctx) → dispose? }.
// Feature views are added to this map as they are built; unknown routes fall back to not-found.
import home from './home.js';
import users from './users.js';
import audit from './audit.js';
import settings from './settings.js';
import msSignin from './mssignin.js';
import notFound from './not-found.js';

import pdfStudio from './pdf-studio.js';
import pdfProfiles from './pdf-profiles.js';
import pdfHarvest from './pdf-harvest.js';
import masterSheets from './master-sheets.js';
import newRequest from './new-request.js';
import myRequests from './my-requests.js';
import results from './results.js';
import runs from './runs.js';
import messages from './messages.js';
import reqQueue from './req-queue.js';
import reqDetail from './req-detail.js';
import meeting from './meeting.js';
import build from './build.js';
import warehouse from './warehouse.js';
import develop from './develop.js';
import cexa from './cexa.js';
import cexaSetup from './cexa-setup.js';
import agents from './agents.js';
import cexaDev from './cexa-dev.js';
import versions from './versions.js';
import areas from './areas.js';
import docWatch from './doc-watch.js';
import docWatchEntity from './doc-watch-entity.js';
import ownerDoc from './owner-doc.js';
import org from './org.js';
import training from './training.js';
import connections from './connections.js';
import tool from './tool.js';
import siteSchedules from './site-schedules.js';
import ifrs16 from './ifrs16.js';
import ap from './ap.js';
import builder from './builder.js';
import tickets from './tickets.js';
import mailer from './mailer.js';
import { supportDashboard, supportTickets, supportSettings } from './support.js';
import { hub } from './hub.js';

// 1.0.52: pages that belong together share one menu item (a hub); the old addresses still open them
const requestsHub = hub({ title: 'Requests', tabs: [
  { key: 'mine', label: 'My requests', view: myRequests },
  { key: 'new', label: 'New request', view: newRequest }] });
const workHub = hub({ title: 'Work', tabs: [
  { key: 'requests', label: 'Requests', view: reqQueue, count: 'new_requests' },
  { key: 'develop', label: 'Develop', view: develop, count: 'tasks_open' }] });
const peopleHub = hub({ title: 'People & access', tabs: [
  { key: 'users', label: 'People', view: users },
  { key: 'access', label: 'Tool access', view: users },
  { key: 'roles', label: 'Roles & permissions', view: users, roles: ['owner', 'admin'] },
  { key: 'rules', label: 'Row-level rules', view: users, roles: ['owner', 'admin'] },
  { key: 'org', label: 'Org chart', view: org, roles: ['owner', 'admin'] }] });
const cexaHub = hub({ title: 'C-EXA', tabs: [
  { key: 'talk', label: 'Talk', view: cexa, roles: ['owner'] },
  { key: 'now', label: 'Now', view: agents, roles: ['owner'], also: 'can_see_teams', count: 'agents_running' },
  { key: 'teams', label: 'Teams', view: agents, roles: ['owner'], also: 'can_see_teams' },
  { key: 'jobs', label: 'Jobs', view: agents, roles: ['owner'], also: 'can_see_teams' },
  { key: 'standing', label: 'Standing agents', view: agents, roles: ['owner'] }, // 1.0.106: owner only — /api/cexa/agents is require_owner (a viewer got a 403 box)
  { key: 'developer', label: 'Developer', view: cexaDev, roles: ['owner'], count: 'proposals' }] });
const settingsHub = hub({ title: 'Settings', tabs: [
  { key: 'mine', label: 'My settings', view: settings },
  { key: 'branding', label: 'Branding', view: settings, roles: ['owner'] },
  { key: 'cexa', label: 'C-EXA voice & models', view: cexaSetup, roles: ['owner'] },
  { key: 'connections', label: 'API connections', view: connections, roles: ['owner', 'admin'] },
  { key: 'microsoft', label: 'Microsoft sign-in', view: msSignin, roles: ['owner'] },
  { key: 'warehouse', label: 'Data warehouse', view: warehouse, roles: ['owner', 'admin'] }] });

export const VIEWS = {
  home, audit, 'not-found': notFound,
  requests: requestsHub, work: workHub, people: peopleHub, cexa: cexaHub, settings: settingsHub, help: training,
  'pdf-studio': pdfStudio, 'site-schedules': siteSchedules, ifrs16, ap, results, 'pdf-profiles': pdfProfiles, 'pdf-harvest': pdfHarvest, 'master-sheets': masterSheets,
  messages, runs,
  'req-detail': reqDetail, meeting,
  build, versions, areas,
  'doc-watch': docWatch, 'doc-watch-entity': docWatchEntity, 'owner-doc': ownerDoc,
  tool, builder,
  tickets, mailer, support: supportDashboard, 'support-tickets': supportTickets, 'support-settings': supportSettings,
};
