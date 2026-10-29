// The lessons — one script per lesson id in cbx/training/catalog.py (tests/test_training.py checks
// the two lists match). Each step: where to be (`route`, `params`), what to point at (`target`, a
// CSS selector on the live page), what to say (`title`, `text`), and how the step ends (`action`):
//   next   they read it and press Next
//   click  they click the highlighted thing; the lesson moves on by itself
//   wait   they do something; the lesson moves on when `until` appears on the page
// `optional` steps skip themselves when the thing is not there (not everyone has every button).
// `links` are buttons on the card — used to hand over the practice PDFs.

const demo = (name, label) => ({ label, href: `/api/training/demo/${name}`, download: true });

export const LESSONS = {
  // ---- Start here ------------------------------------------------------------------------------
  'around.sidebar': {
    title: 'Getting around',
    steps: [
      { route: 'home', target: '.side .brand, .x-top .x-logo', title: 'This is CBX-Connect', text: 'Everything you are allowed to do is in the menu on the left. Nothing you cannot use is shown to you — if a tool is missing, you have not been granted it yet.', action: 'next' },
      { target: '.side [data-route="home"], .x-top .x-navt[data-route="home"]', title: 'Home', text: 'What needs you today: results that are ready, things waiting for your answer, and a word from C-EXA when she has one.', action: 'next' },
      { target: '.side .nav-sec, .x-top .x-dd[data-sec]', title: 'Your tools', text: 'The tools you have been granted. Each one is a job: reading PDFs, keeping certificates in date, the team to-do. Managers and admins grant them.', action: 'next' },
      { target: '.side [data-route="tickets"], .x-top .x-dd[data-sec="Workspace"]', title: 'My tickets', text: 'Something not working — here, in Chase, or in another system? Open a ticket. C-EXA reads it, asks if anything is missing, and the support team takes it from there.', action: 'next' },
      { target: '.side [data-route="messages"], .x-top .x-dd[data-sec="Workspace"]', title: 'Messages', text: 'Your colleagues — your team\'s room and direct messages — and C-EXA, who interviews you here when a request is approved. Type @C-EXA in any room and she answers.', action: 'next' },
      { target: '#cexa-fab, #cexa-fab-x', title: 'Ask C-EXA', text: 'This button sits at the top of the menu and works on every page. Ask about what you are looking at — she sees the same screen you do, and only the rows you are allowed to see.', action: 'next', optional: true },
      { target: '.side [data-route="settings"], .x-top .x-me', title: 'Settings', text: 'Your name, your password, which notifications you get, and the devices you are signed in on.', action: 'next' },
      { target: '.side [data-route="help"], .x-top .x-me', title: 'And this is Help', text: 'Come back here any time. Every lesson can be taken again.', action: 'next' },
    ],
    finish: { title: 'You know your way around', text: 'Next: "Ask for something to be built", or jump straight to a tool you hold.' },
  },

  'around.ticket': {
    title: 'Report a problem',
    steps: [
      { route: 'tickets', target: '#new', title: 'New ticket', text: 'Start here whenever something is not working. Your tickets, and where each one is, are listed underneath.', action: 'next' },
      { route: 'tickets', target: '.head', title: 'Three short steps', text: 'What it is about (CBX-Connect, Chase …), your details — already filled in from your account — and the problem, with a few questions for that kind of problem.', action: 'next' },
      { route: 'tickets', target: '.head', title: 'Then C-EXA reads it', text: 'If anything is missing she asks — here and by e-mail. Answer in the ticket or just reply to the mail. When it is complete, the support team assigns someone, and you are told when it is done.', action: 'next' },
    ],
    finish: { title: 'That is all', text: 'You can also simply e-mail the support mailbox — the mail becomes a ticket and you get its number back.' },
  },

  'around.request': {
    title: 'Ask for something to be built',
    steps: [
      { route: 'new-request', target: '#problem', title: 'Say what is wrong', text: 'In your own words. What do you do by hand, how often, what goes wrong. Nobody marks the grammar — C-EXA reads it and asks if anything is unclear.', action: 'next' },
      { target: '#impact', title: 'Who it affects', text: 'You? A team? A client? This is what decides how soon it gets looked at.', action: 'next' },
      { target: '#wish', title: 'What "done" looks like', text: 'The result you want, not how to build it. "The bills get read and added on their own" is perfect.', action: 'next' },
      { target: '#attach', title: 'An example helps', text: 'Optional. One real file — a bill, a sheet — shows C-EXA exactly what you mean.', action: 'next' },
      { target: '#send', title: 'Send', text: 'For the lesson, you do not have to. When you do, the request goes to the admins and C-EXA reads it the same minute.', action: 'next' },
      { route: 'requests', target: '.head', title: 'My requests', text: 'Every request you have sent, with where it is: sent, being assessed, interview, working, ready.', action: 'next' },
      { route: 'messages', target: '.head', title: 'Messages', text: 'Once a request is approved, C-EXA asks you questions here — one at a time — to get the detail right before anything is built. Colleagues live on this page too: your team\'s room, and direct messages.', action: 'next' },
    ],
    finish: { title: 'That is the whole loop', text: 'Ask → C-EXA and the admins look → questions in Messages → the result in Results.' },
  },

  'around.settings': {
    title: 'Your settings and your password',
    steps: [
      { route: 'settings', target: '#name', title: 'Your name', text: 'How you appear to colleagues and in the audit trail. Change it here and Save.', action: 'next' },
      { target: '#chpw', title: 'Change your password', text: '12 characters or more, checked against lists of leaked passwords. Changing it signs out every other browser you were in.', action: 'next' },
      { target: '#others', title: 'Signed in somewhere else?', text: 'Every browser you are signed in on is listed. "Sign out others" ends all of them except this one.', action: 'next' },
    ],
  },

  // ---- PDF Studio ------------------------------------------------------------------------------
  'pdf.batch': {
    title: 'Read a batch of PDFs',
    steps: [
      { route: 'pdf-studio', params: { new: 1 }, target: '.head h1', title: 'PDF Studio', text: 'You give it a template (which columns you want) and a pile of PDFs. C-EXA reads every one and hands back a filled sheet. First, get three practice bills.', links: [demo('bill-1.pdf', 'Bill · month 1'), demo('bill-2.pdf', 'Bill · month 2'), demo('bill-3.pdf', 'Bill · month 3')], action: 'next' },
      { target: '.stepper [data-step="1"]', title: 'Step 1 — Template', text: 'The template is the list of columns you want out of each PDF: account number, period, total, and so on. Pick one that exists or start from the default.', action: 'next' },
      { target: '#next-pdfs', title: 'On to the PDFs', text: 'When the columns are what you want, click here.', action: 'click', what: 'button', delay: 900 },
      { target: '#pdf-drop', title: 'Drop the bills in', text: 'All three at once. Each one becomes a row.', action: 'wait', until: '.stepper [data-step="3"][aria-selected="true"], .stepper [data-step="3"].done', waitText: 'Drop the three practice bills — the lesson moves on when C-EXA starts reading.' },
      { target: '.stepper [data-step="3"]', title: 'Step 3 — Reading', text: 'C-EXA is working. She reads, checks her own work, and reads again until she is sure — you can watch the percentage climb. Made-up bills read fast; real ones take a little longer.', action: 'wait', until: '#see-results', waitText: 'Wait for "See results" to appear.' },
      { target: '#see-results', title: 'See the results', text: 'Click it.', action: 'click', what: 'button', delay: 900 },
      { target: '.stepper [data-step="4"]', title: 'Step 4 — Results', text: 'Your sheet. Download it as Excel, or send it straight into a master sheet so it joins everything else you have read.', action: 'next' },
    ],
    finish: { title: 'You have read a batch', text: 'The next lesson shows what to do when C-EXA is not sure about a cell.' },
  },

  'pdf.results': {
    title: 'Check and fix what C-EXA read',
    steps: [
      { route: 'pdf-studio', target: '.head h1', title: 'Your batches', text: 'Open a finished batch from the list — a practice one from the last lesson is fine.', action: 'next' },
      { target: '[data-open]', title: 'Open one', text: 'Click Open on any batch that is Ready or Needs you.', action: 'click', what: 'Open button', delay: 1200 },
      { target: '.stepper [data-step="4"]', title: 'Results', text: 'Go to the Results step if you are not already on it.', action: 'click', what: 'Results step', delay: 700 },
      { target: '#pane table', title: 'The sheet', text: 'One row per PDF, one column per template column. A cell C-EXA was not sure about is marked — click it, see the PDF beside it, and type what it should be. She learns from that.', action: 'next' },
      { target: '#to-master', title: 'Into a master sheet', text: 'Once it looks right, send the rows to a master sheet. Rows already there are skipped, so sending twice does no harm.', action: 'next', optional: true },
    ],
    finish: { title: 'That is the whole job', text: 'Drop, read, check, send. The checking gets rarer as C-EXA learns your layouts.' },
  },

  // ---- Master Sheets ---------------------------------------------------------------------------
  'sheets.new': {
    title: 'Start a master sheet',
    steps: [
      { route: 'master-sheets', target: '.pdf-tabs', title: 'Master sheets', text: 'Inside PDF Studio, the third tab. One running workbook per kind of document: every batch you send and every harvest adds its rows here, so the sheet grows on its own.', action: 'next' },
      { target: '#new-sheet', title: 'New sheet', text: 'Click it. (Admins only — if the button is not here, ask an admin to make the sheet and you can still feed it.)', action: 'click', what: 'New sheet button', delay: 700, optional: true },
      { target: '.modal #s-name', title: 'Name it', text: 'Something the team will recognise: "Eskom demand", "Capitec vendor invoices".', action: 'next', optional: true },
      { target: '.modal #s-cols', title: 'Columns', text: 'These should match the template you read PDFs with, so rows slot straight in.', action: 'next', optional: true },
      { target: '.modal #s-ok', title: 'Save', text: 'Click Save.', action: 'click', what: 'Save button', delay: 800, optional: true },
    ],
    finish: { title: 'Sheet ready', text: 'From PDF Studio\'s Results step, "Add to master sheet" sends rows here.' },
  },

  // ---- Managing people -------------------------------------------------------------------------
  'admin.people': {
    title: 'Add a person and give them tools',
    steps: [
      { route: 'users', target: '.head h1', title: 'Users & roles', text: 'Deny by default: a new person sees nothing until you grant it.', action: 'next' },
      { target: '.role-cards', title: 'The roles', text: 'What each role can do, and how many people hold it. You can only hand out roles below your own.', action: 'next' },
      { target: '#add-user', title: 'Add user', text: 'Click it to see the form. For the lesson you can Cancel — or add someone real if you have one waiting.', action: 'click', what: 'Add user button', delay: 700 },
      { target: '.modal #email', title: 'Their work email', text: 'It is their sign-in. They get a one-time temporary password from you and replace it themselves on first sign-in — you never see the real one.', action: 'next' },
      { target: '.modal #tools-field', title: 'Tools', text: 'Tick exactly what they need. You can change it any time from the Tool access tab.', action: 'next', optional: true },
      { target: '[data-tab="access"]', title: 'Tool access', text: 'The matrix: every person against every tool. Click a box to grant or remove — it takes effect immediately and is written to the audit trail.', action: 'next' },
    ],
    finish: { title: 'People and tools', text: 'A person who loses their password clicks "Ask the owner"; you see "Reset requested" next to their name.' },
  },

  'admin.roles': {
    title: 'Make a role of your own',
    steps: [
      { route: 'users', target: '[data-tab="roles"]', title: 'Roles & permissions', text: 'Click the tab.', action: 'click', what: 'tab', delay: 800 },
      { target: '#pane tbody tr', title: 'Every role, what it can do', text: 'The four built-ins are locked: the app\'s own checks rely on them. Copy one to start your own.', action: 'next' },
      { target: '#new-role', title: 'New role', text: 'Click it.', action: 'click', what: 'New role button', delay: 700 },
      { target: '.modal #r-caps', title: 'Tick what it may do', text: 'Each box is one capability, with a sentence saying what it opens. A greyed box is something you do not hold yourself — you cannot give away what you do not have.', action: 'next' },
      { target: '.modal #r-rank', title: 'Rank', text: 'Who may hand this role out: only people ranked above it. Nobody can promote themselves or a colleague past themselves.', action: 'next' },
    ],
    finish: { title: 'Roles', text: 'Create it or Cancel. Everything here is written to the audit trail.' },
  },

  'admin.audit': {
    title: 'Read the audit trail',
    steps: [
      { route: 'audit', target: '.head h1', title: 'Every action', text: 'By every person and by C-EXA. Nothing here can be edited or deleted — not by you, not by her.', action: 'next' },
      { target: '#q', title: 'Search', text: 'A name, an entity, a file. Type it and the list narrows.', action: 'next' },
      { target: '#action', title: 'By kind of action', text: 'Sign-ins, grants, promotions, files downloaded. "login_failed" on a Monday morning is worth a look.', action: 'next' },
      { target: '#ai', title: 'C-EXA only', text: 'Everything she did on her own: what she read, what she proposed, what she was refused.', action: 'next', optional: true },
    ],
  },

  // ---- Owner -----------------------------------------------------------------------------------
  'owner.versions': {
    title: 'Put a new version live',
    steps: [
      { route: 'versions', target: '.head h1', title: 'Versions & sandboxes', text: 'New code arrives as a zip. It never goes live untested.', action: 'next' },
      { target: '#drop', title: 'Upload', text: 'Drop the zip here. It is unpacked and checked — no scripts, nothing outside cbx/, web/, tests/, docs/.', action: 'next' },
      { target: '#pane, .card', title: 'Sandbox first', text: 'Run the full test suite on this machine, then click through the sandbox on its own port. Only a version that passed can be promoted.', action: 'next' },
      { target: '.card', title: 'Promote, and roll back', text: 'Promote backs up the live tree, copies the new files, and restarts the server. Roll back puts the backup straight back. Both are one click and both are audited.', action: 'next' },
    ],
  },
};
