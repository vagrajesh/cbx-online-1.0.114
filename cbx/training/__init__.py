"""Training: interactive lessons that run on the real screens.

A lesson is a script of steps the browser plays over the live page — a spotlight on the thing to
look at or click, a sentence saying why, and for the doing steps a wait until the person has done
it. The scripts live in `web/training/lessons.js`; this package holds what the server needs to know:
which lessons exist and who they are for (`catalog`), the practice data a lesson works on so nothing
real is touched (`demo`), and where each person is in each lesson (`models.TrainingProgress`).
"""
