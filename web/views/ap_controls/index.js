// 1.0.104: the AP page's Controls tab (reviewers and admins) — the list of its sub-views (the Valuence controls).
//
// ONE LINE PER SUB-VIEW. A builder adds the file web/views/ap_controls/<name>.js and un-comments (or adds) its line below.
// The tab shows them in ORDER (keys not in ORDER come after, by title); the tab stays hidden while the list is empty.
//
// A sub-view file:
//   export default {
//     key: 'review',                 // the address: #/ap?tab=controls&sub=review
//     title: 'Review',               // the tab's words (English, plain)
//     roles: ['reviewer'],           // who sees it besides admins (any portfolio); [] = everyone who sees Controls
//     async render(el, kit) { ... return () => { /* the page was left: stop timers */ }; },
//   };
//   kit = { api, act, el, clear, t, money, when, toast, me, isAdmin, roles, portfolios, params, go, openCapture, openItem, refresh }
//     api(path, {method, body, form})   the AP page's fetch (GET / POST body / PUT / DELETE / form upload)
//     act(fn, okWords)                 runs fn, shows okWords or the server's plain-words refusal
//     el(tag, attrs, ...children)      the page's element maker; clear(node) empties a node
//     roles                            { "SHELL": ["reviewer", ...], ... } — what this person holds (bootstrap included)
//     portfolios                       [{code, name, is_default, is_active}]
//     params                           the address's other parameters (e.g. {capture: "12"})
//     go(sub, params)                  switch to another Controls sub-view (Needs you links use the same address)
//     openCapture(id) / openItem(id)   the page's own document dialogs
//     refresh()                        reload the AP page's numbers (board, Needs you count)

export const ORDER = ['review', 'vat', 'new_vendors', 'duplicates', 'variances', 'estimates', 'completeness', 'reconciliations', 'evidence'];

export { default as review } from './review.js';                       // Builder R — V05 Review (+ V06 VAT decisions)
export { default as new_vendors } from './new_vendors.js';             // Builder D — V01 New vendors
export { default as duplicates } from './duplicates.js';               // Builder D — V09 Duplicates
export { default as variances } from './variances.js';                 // Builder U — V07 Variances & disputes
export { default as estimates } from './estimates.js';                 // Builder U — V08 Estimates
export { default as completeness } from './completeness.js';        // Builder C — V10 Completeness
export { default as reconciliations } from './reconciliations.js';  // Builder C — V11 Reconciliations
export { default as evidence } from './evidence.js';                   // Builder R — V12 Evidence
