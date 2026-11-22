# Speech: 4:00, matches the Canva deck "ImpactText Pitch" (9 slides; the CI/CD slide sits between 7 and 8)

PURPOSE: the word-for-word speech for finals, one block per slide, plus the fixes the deck still needs.
[Brackets] are actions, not spoken. About 65 spoken words per slide (130 per minute). If it runs long, cut, never speed up.
All examples are synthetic. Cost figures are estimates from the README (check current prices).

---

## Slide 1 · 0:00–0:30 · Title
> ComForce workers are out in the neighborhood, not at a desk. They're feeding kids, sitting with families after a shooting, connecting people to help. Then funders ask for numbers, and writing reports becomes one more job that takes time away from people.
>
> ImpactText fixes that. A worker says what they did in plain words, and we turn it into a record ComForce can track, follow up on, and report.

## Slide 2 · 0:30–1:00 · Three ways to log
> There are three ways to log, and none of them needs an app or training.
>
> Text a number, from any phone, even a flip phone with no data. Tap the mic and talk. Or type a short note.
>
> No account is needed to send a note. And Milwaukee is bilingual, so if you text in Spanish, ImpactText answers in Spanish.

## Slide 3 · 1:00–1:30 · One note becomes a record
> Here's a made-up example. A worker texts: "fed 20 kids at sherman park, need to call back friday."
>
> ImpactText reads that and fills in the record: food and meals, 20 people served, all 20 youth, Sherman Park, and a follow-up for Friday. [point at the confirmation text]
>
> The worker gets a confirmation text back. They never filled in a single form field.

## Slide 4 · 1:30–2:00 · Follow-ups stay visible
> On the staff side, the dashboard brings every note together: people served, youth, referrals, and hours, by neighborhood and by worker.
>
> Follow-ups stay on screen until someone marks them done, and they can go straight into a calendar.
>
> And once a month, ImpactText emails ComForce last month's impact report, the one funders ask for. No one has to build it by hand.

## Slide 5 · 2:00–2:30 · Designed for trust and ownership
> Here's how it works. A text, voice, or typed note goes in. AI reads it and fills in the fields. If the AI is down, a simple keyword reader takes over, so it never stops working.
>
> Phone numbers, emails, and street addresses are removed before anything is saved. Admins sign in with a two-step code. And ComForce owns its records: one file, exportable to a spreadsheet anytime.
>
> One honest limit: first names can still end up in notes, so people still need to review them.

## Slide 6 · 2:30–3:00 · Keyword extraction
> We tested it on 30 notes we held back, scoring six fields each.
>
> Our first keyword reader got 65 percent of the fields right. After improving it, 90.6 percent. That's the version that runs with no AI at all, for free.
>
> We haven't measured the AI version yet. That's the first thing we'd test in a pilot. These are small, synthetic tests, not real-world results.

## Slide 7 · 3:00–3:30 · Low cost to run
> It's cheap to run. Hosting with storage is about 7 dollars and 25 cents a month.
>
> For about 300 notes a month, AI adds about 2 dollars 50, or 10 cents with a smaller model. And it works with no AI at all for zero dollars.
>
> Texting adds a few dollars a month, plus a one-time phone number registration. These are estimates; real bills depend on use.

## Slide 7b · 3:30–3:55 · Every change is checked automatically
> Volunteers can't afford a broken website, so every update has to pass automated checks first.
>
> Each change is tested for sign-in, privacy, accuracy, and text replies. Then an automated browser clicks through it like a real person would, on simulated phones and desktops, and restarts the app to make sure no saved notes are lost.
>
> The checks use our free keyword reader, so they cost nothing to run. Once hosting is connected, only changes that pass every check go live.

## Slide 8 · 3:55–4:20 · Next step
> What we'd ask of ComForce: a small pilot. Agree on data handling, finish hosting and texting, test on workers' phones, then measure the AI and ask workers if it fits their day.
>
> ImpactText helps ComForce document its work and prove its impact. Just text what you did. Thank you.

**Runs about 4:20 with the CI/CD slide. To land at 4:00, trim ~20 seconds (e.g. slide 7 down to its main number, or drop the restart detail on slide 7b).**

---

## Deck fixes still to do in Canva
- [ ] **Slides 2, 3, 4:** replace the image placeholders with screenshots (note-entry screen, SMS confirmation, dashboard with follow-ups). Placeholder text currently shows on screen.
- [ ] **Slide 3:** the label under "food & meals" says SYNTHETIC EXAMPLE; change it to **CATEGORY** and put "Synthetic example" as a small caption by the text bubble.
- [ ] **Slide 7:** delete the stray dot under the title; fill the empty right half (e.g. a big "**$0** for AI with the keyword reader"). Lead with the total, since cost is a heavy bonus.
- [ ] **Slide 2:** add "**Spanish text replies**" (built, PR #9).
- [ ] **Slide 4:** add "**Monthly impact report emailed to ComForce**" (built, PR #10).
- [ ] **Slide 5:** plain words for non-technical judges: "Claude with structured schema" → "**AI reads the note**"; "Keyword fallback" → "**Works even without AI**".
- [ ] **Speaker notes:** paste in the blocks above. Current notes have wrong times (slides 5 and 7 both say 2:30), slide 2 stops mid-sentence ("or even"), slide 7 ends in a broken fragment, and the "Sources:" lines should go.

## Before you go on
- Rehearse with a timer at least 3 times.
- If you do a live demo instead of screenshots, run it locally (`python -m app.seed --reset` then `uvicorn app.main:app`, open `http://localhost:8000`), already open before you start. No Render needed.
- Likely questions: *How accurate is the AI?* "Not measured yet; the free keyword version is 90.6% on held-out notes, and the pilot would measure the AI." *Who owns the data?* "ComForce: one database file plus CSV export." *What about privacy?* "Phone numbers, emails and addresses are scrubbed before saving; names still need human review."
- *Do updates deploy automatically?* GitHub Actions runs the checks and can request a Render deployment after a passing push to `main`. We still need to configure the deploy hook and verify a live rollout.
