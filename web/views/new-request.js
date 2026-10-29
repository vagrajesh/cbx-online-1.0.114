import { api } from '../api.js';
import { toast } from '../ui.js';

export default {
  title: 'New request',
  async render(root, ctx) {
    let attachment = null;
    root.innerHTML = `
    <div class="head"><div><h1>New request</h1><p>Three things. C-EXA reads it straight away; if it's approved, we'll come back to you for the detail.</p></div></div>
    <div class="card req">
      <div class="sec"><h2>1 · The problem</h2><p class="why">What happens today, and what goes wrong.</p>
        <textarea id="problem" placeholder="Every month-end I open about 40 bills one by one and type the totals into the master sheet…"></textarea></div>
      <div class="sec"><h2>2 · The impact</h2><p class="why">Who it affects, how often, and what it costs when it goes wrong.</p>
        <textarea id="impact" style="min-height:72px" placeholder="Me and one colleague, every month. Two bills were missed last quarter…"></textarea></div>
      <div class="sec"><h2>3 · How you'd like it fixed</h2><p class="why">In your own words. You don't need to know how — just what "fixed" looks like.</p>
        <textarea id="wish" style="min-height:72px" placeholder="The bills get read and added on their own, and I get one email when it's done."></textarea>
        <div class="row" style="margin-top:10px;gap:8px"><button class="btn sm" id="attach">Attach an example</button><span class="small muted" id="attach-name">optional</span><input type="file" id="file" style="display:none"></div></div>
      <div class="req-foot">
        <span class="small muted">You'll see it under My requests within a minute. If it's approved, someone — or C-EXA — will ask you for the detail.</span>
        <button class="btn primary" id="send">Send request</button>
      </div>
    </div>`;
    const file = root.querySelector('#file');
    root.querySelector('#attach').onclick = () => file.click();
    file.onchange = async () => {
      const f = file.files[0]; if (!f) return;
      root.querySelector('#attach-name').textContent = 'Uploading…';
      try { attachment = await api.upload('/api/requests/attachment', [f]); root.querySelector('#attach-name').textContent = attachment.name; }
      catch (e) { attachment = null; root.querySelector('#attach-name').textContent = 'optional'; toast(e.detail || 'Upload failed', 'err'); }
    };
    root.querySelector('#send').onclick = async () => {
      const body = { problem: root.querySelector('#problem').value.trim(), impact: root.querySelector('#impact').value.trim(), wish: root.querySelector('#wish').value.trim(), attachment_id: attachment ? attachment.id : null };
      if (body.problem.length < 10) { toast('Tell us a little more about the problem first.', 'err'); root.querySelector('#problem').focus(); return; }
      const btn = root.querySelector('#send'); btn.disabled = true; btn.textContent = 'Sending…';
      try {
        const r = await api.post('/api/requests', body);
        toast(`Sent · request #${r.request.number} · C-EXA is reading it now`);
        ctx.refreshCounts && ctx.refreshCounts();
        ctx.go('requests');
      } catch (e) { toast(e.detail || 'Could not send', 'err'); btn.disabled = false; btn.textContent = 'Send request'; }
    };
  },
};
