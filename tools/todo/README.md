# To-do (tool folder)

**What it is.** Your own to-do list, the team's board, jobs across teams, stats, and the goals a team lead sets with a reward day when you reach one. Made in the Builder only — tables, pages, blocks and rules; no code of its own. It replaces the built-in To-do page (1.0.55). The old tasks, jobs, goals and reward days were copied in once, with who added them and when.

**Who sees it.** Anyone granted To-do in People & access (`access.tool` = `todo`); owners and admins always.

**The tables** (in `data/`, rows in `data\tools\todo.sqlite`):

| Table | Holds | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| `tasks` | every task: For (Just me / The team), Status, Who, Steps, Size (from the number of steps: up to 3 Small, up to 7 Medium, more Complex), Due, Finished, Done by | each person their own; the whole team sees the rows where For is The team; a manager also sees their team's | everyone |
| `jobs` | a job: Title, What it is for, Lead, Due, Status | the job's active members, and whoever added it | managers |
| `job_members` | who is on which job, and whether their team lead said yes (Requested / Active / Declined / Released) — each person once per job | the same job's members | managers |
| `job_tasks` | the tasks of a job | the job's members | everyone |
| `goals` | a team lead's goal: Target (tasks), Size (empty = any), From, To, Reward, Who counts (each person, or the team together) | the team | managers |
| `reward_days` | one booked day per goal and person | the team | nobody by hand — only the Book rule, once the goal is reached |

**The pages.** My to-do (my list, team tasks I'm on, finished this month) · Team board (drag cards; Open to take with a Take it button) · Jobs · Stats (this week, this month, open; by size; per person) · Goals (where I stand on each person's goals; where the team stands on the team's — its team tasks done together; Book my reward day once reached).

**The rules** (in `flows/`, all on):

| Rule | When | What |
| --- | --- | --- |
| Take it | the button on an open team task | only if nobody has it: Who = me, Status = Doing |
| Mark it done | the Done button | Status Done, Finished today, Done by me |
| Note when a task is done | a task changes (e.g. dragged to Done) | if Done and no finish date: Finished today, Done by the person |
| Clear the finish date … | a task changes | if not Done but it has a finish date: clear it |
| The lead is on the job | a job is added | its lead joins as Active |
| Ask the team lead … | someone is added to a job | the person's team lead approves (Active) or declines (Declined); a lead adding their own team's people is not asked |
| Book my reward day | the button on a reached goal (asks: which day?) | adds the reward day; tells the team lead |

**How to change it.** Admin → Builder → To-do. Everything above is a question on the right-hand side; nothing here is code.

**Tests.** `tests/test_todo_folder.py`: the folder is sound; the Done button and stats; personal rows stay personal, team rows are shared and taken once; a reward day only once reached and only once; jobs are for their members and the member's team lead says yes.
