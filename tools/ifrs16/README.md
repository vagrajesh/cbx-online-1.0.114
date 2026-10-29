# IFRS 16 (tool folder)

**What it is.** Monthly Lease Accelerator imports from the RR06 / ARO06 movements and the LA register, and the ledger check. Made in the Builder only (1.0.56); the lease engine itself (`cbx/ifrs16/engine`, unchanged) is run as three server jobs. It replaces the built-in IFRS 16 page; its kept uploads, months and ledger checks were copied in once, with their files.

**Who sees it.** Anyone granted IFRS 16 in People & access (`access.tool` = `ifrs16`); owners and admins always. Each person sees their own uploads, months and checks; admins see all.

**The tables.** `uploads` (the four source files — up to 100 MB each — what was read, the months in them, Status New · Reading · Ready · Could not read, a note) · `months` (one per month worked out: the import workbooks in one zip, the monthly report, how many import workbooks, the engine's reconciliation lines, Status Working · Done · Failed; only the rule adds them) · `ledger` (the ledger export, as at, months back, the consolidated report and its overview).

**The pages.** *Monthly workflow*: a form for the source files, the kept uploads with **Work out a month** (it asks which month), and the months. *Ledger check*: a form for the export and the list of checks.

**The rules** (all on):

| Rule | When | What |
| --- | --- | --- |
| Read new source files | an upload's row changes, both required files are there, and it is New | Status Reading → the job *Read the IFRS 16 source files* (background) fills in the months → Ready; if it cannot read them: Could not read, and why |
| Work out a month | the button on an upload (asks the month) | stops unless the upload is Ready and holds that month; adds the month's row; the job *Work out an IFRS 16 month* (background, taking the files from the upload) puts the zip and the report in it → Done, or Failed and why |
| Check a ledger | a check's row changes (its export arrives) and it is New | Working → the job *Check an IFRS 16 ledger* (background) → Done, or Failed and why |

The page shows the row as soon as a background job is done.

**Tests.** `tests/test_ifrs16_folder.py`: the folder is sound; files are read and a month worked out (report and workbooks); unreadable files say so on the row; the ledger check; people see their own and nobody adds a month by hand.
