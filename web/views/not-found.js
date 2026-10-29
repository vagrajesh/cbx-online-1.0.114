export default {
  title: 'Not found',
  async render(el) {
    el.innerHTML = `<div class="head"><div><h1>That page isn't here</h1><p>Use the menu on the left.</p></div></div>`;
  },
};
