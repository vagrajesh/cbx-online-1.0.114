# Site Schedules (tool folder)

**What it is.** A Swiftnet co-location quotation in, the Site Schedule Agreement out — a Word document, ready to sign. Made in the Builder only (1.0.56); it replaces the built-in Site Schedules page. The quotations read before were copied in once, with their PDFs and agreements.

**Who sees it.** Anyone granted Site Schedules in People & access (`access.tool` = `site-schedules`); owners and admins always. Each person sees the quotations they added; admins see all.

**The table** `quotes`: the quotation (PDF), site, quote number, province, equipment accommodation, antennas, the fees (shown as money), *What to check* (a checklist), how many lines to check / checked, Status (New · Read · Built), the agreement (Word).

**The page.** A form with only the quotation file (*Read it*), a short how-to, and the quotations with two buttons on each row: *Build the agreement* and *Read it again*.

**The rules** (in `flows/`, all on):

| Rule | When | What |
| --- | --- | --- |
| Read a new quotation | a row changes (its PDF arrives) and it is New | the server job *Read a Swiftnet quotation* fills the row; what it is unsure of becomes lines under *What to check*; Status Read |
| Read the quotation again | the button | the same, on purpose (what was corrected is replaced) |
| Build the agreement | the button | stops unless the site is filled in and every line is ticked (and says so); makes the Word document from `assets/site_schedule.docx` into the row; Status Built |

**The template.** `assets/site_schedule.docx` — Word, with `{{site}}`, `{{quote_no}}`, `{{province}}`, `{{equipment}}`, `{{antennas}}`, the fee columns and `{{today}}` where the row's values go. Replace it in the Builder (the rule's *Upload a template…*) with the company's own; keep the `{{…}}` names.

**Tests.** `tests/test_site_schedules_folder.py`: the folder is sound; a quote is read, checked and built into a Word document with the Rand amounts; someone without the tool is refused.

1.0.88: the engine is CBX-Connect 2's (`cbx/schedules/agreement.py` over `reader.py` and `template.py`): the quote and the NTP or an earlier agreement in, every comment its own charge line, the row holds every field of the agreement, and **Make the agreement** writes the Word document like the real one.
