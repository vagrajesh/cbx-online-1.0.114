// The renderer's own words (search, add, save …) in the four languages, and the person's language
// choice. Tool words come from the tool's folder; these are the few the engine itself needs.
export const LANGS = { en: 'English', ar: 'العربية', pt: 'Português', fr: 'Français' };
export const RTL = new Set(['ar']);
const LOCALE = { en: 'en-ZA', ar: 'ar', pt: 'pt-PT', fr: 'fr-FR' };
const KEY = 'cbx.lang';

const UI = {
  en: { search: 'Search', add: 'Add', save: 'Save', cancel: 'Cancel', edit: 'Edit', delete: 'Delete', close: 'Close', no_rows: 'Nothing here yet.', rows: 'rows', items: 'items', item: 'item', add_to: 'Add to', of: 'of', all: 'All', filter: 'Filter', yes: 'Yes', no: 'No', open: 'Open', saved: 'Saved', deleted: 'Deleted', confirm_delete: 'Delete this? It cannot be undone.', required: 'required', language: 'Language', new_row: 'New', added_by: 'Added by', pages: 'Pages', tool_page: 'Tool page', more: 'Show more', file: 'File', download: 'Download', remove: 'Remove', replace: 'Replace', fill_in: 'Fill in: {name}.', none_to_pick: 'There is no {name} to pick yet: add one first.' },
  ar: { search: 'بحث', add: 'إضافة', save: 'حفظ', cancel: 'إلغاء', edit: 'تعديل', delete: 'حذف', close: 'إغلاق', no_rows: 'لا شيء هنا بعد.', rows: 'صفوف', items: 'عناصر', item: 'عنصر', add_to: 'إضافة إلى', of: 'من', all: 'الكل', filter: 'تصفية', yes: 'نعم', no: 'لا', open: 'فتح', saved: 'تم الحفظ', deleted: 'تم الحذف', confirm_delete: 'حذف هذا الصف؟ لا يمكن التراجع عن ذلك.', required: 'مطلوب', language: 'اللغة', new_row: 'جديد', added_by: 'أضافه', pages: 'الصفحات', tool_page: 'صفحة الأداة', more: 'عرض المزيد', file: 'ملف', download: 'تنزيل', remove: 'إزالة', replace: 'استبدال', fill_in: 'املأ: {name}.', none_to_pick: 'لا يوجد {name} للاختيار بعد: أضف واحدًا أولًا.' },
  pt: { search: 'Pesquisar', add: 'Adicionar', save: 'Guardar', cancel: 'Cancelar', edit: 'Editar', delete: 'Eliminar', close: 'Fechar', no_rows: 'Ainda não há nada aqui.', rows: 'linhas', items: 'itens', item: 'item', add_to: 'Adicionar a', of: 'de', all: 'Todos', filter: 'Filtrar', yes: 'Sim', no: 'Não', open: 'Abrir', saved: 'Guardado', deleted: 'Eliminado', confirm_delete: 'Eliminar esta linha? Não é possível anular.', required: 'obrigatório', language: 'Idioma', new_row: 'Novo', added_by: 'Adicionado por', pages: 'Páginas', tool_page: 'Página da ferramenta', more: 'Mostrar mais', file: 'Ficheiro', download: 'Transferir', remove: 'Remover', replace: 'Substituir', fill_in: 'Preencha: {name}.', none_to_pick: 'Ainda não há {name} para escolher: adicione primeiro.' },
  fr: { search: 'Rechercher', add: 'Ajouter', save: 'Enregistrer', cancel: 'Annuler', edit: 'Modifier', delete: 'Supprimer', close: 'Fermer', no_rows: 'Rien ici pour l’instant.', rows: 'lignes', items: 'éléments', item: 'élément', add_to: 'Ajouter à', of: 'sur', all: 'Tous', filter: 'Filtrer', yes: 'Oui', no: 'Non', open: 'Ouvrir', saved: 'Enregistré', deleted: 'Supprimé', confirm_delete: 'Supprimer cette ligne ? Cette action est définitive.', required: 'obligatoire', language: 'Langue', new_row: 'Nouveau', added_by: 'Ajouté par', pages: 'Pages', tool_page: 'Page de l’outil', more: 'Afficher plus', file: 'Fichier', download: 'Télécharger', remove: 'Retirer', replace: 'Remplacer', fill_in: 'À remplir : {name}.', none_to_pick: 'Aucun élément « {name} » à choisir : ajoutez-en un d’abord.' },
};

export const lang = {
  get() { try { const v = localStorage.getItem(KEY); return v && UI[v] ? v : 'en'; } catch { return 'en'; } },
  set(v) { try { if (UI[v]) localStorage.setItem(KEY, v); } catch { /* remembered for this page only */ } window.dispatchEvent(new CustomEvent('cbx:lang', { detail: v })); },
  ui(k) { const l = lang.get(); return (UI[l] && UI[l][k]) || UI.en[k] || k; },
  locale(l = lang.get()) { return LOCALE[l] || 'en-ZA'; },
  /** A number in the person's language: 1 250,50 for pt/fr, ١٬٢٥٠ for ar, 1 250.50 for en-ZA. */
  num(n, dp) { if (n === null || n === undefined || n === '') return '—'; try { return Number(n).toLocaleString(lang.locale(), dp === undefined ? {} : { minimumFractionDigits: dp, maximumFractionDigits: dp }); } catch { return String(n); } },
  /** A date in the person's language: 15 Sept 2026 / 15 sept. 2026 / ١٥ سبتمبر ٢٠٢٦. */
  date(iso) { if (!iso) return '—'; const d = new Date(String(iso).length <= 10 ? iso + 'T00:00:00' : iso); if (Number.isNaN(d.getTime())) return String(iso); try { return d.toLocaleDateString(lang.locale(), { day: 'numeric', month: 'short', year: 'numeric' }); } catch { return String(iso).slice(0, 10); } },
};
