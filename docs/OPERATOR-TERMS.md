# Operator terms

Read this once before your first run. It is short on purpose.

You have been given access to HarvestKit to produce lead lists for BluOryn. The
software and everything it produces belong to BluOryn (see [LICENSE](../LICENSE)).
These are the working rules that licence refers to.

---

## The lists are personal data, and they are not yours

Every row is a real person's name, job and email address. Under GDPR that makes
BluOryn the controller and you the processor: you handle the data on BluOryn's
instructions and for no other purpose.

**Do:**

- Keep the CSVs on the machine you were authorised to run this on.
- Hand finished lists to BluOryn through the channel you were told to use.
- Delete local copies when you are told to, or when your access ends.

**Do not:**

- Send a list, or any part of one, to anyone outside BluOryn.
- Upload lists to a personal cloud drive, a scratch spreadsheet, an email
  account, or an AI tool that retains what you paste into it.
- Keep a copy "just in case" after you stop working on this.
- Use any address in a list for anything other than the campaign you were told
  about — not your own outreach, not a side project, not a favour.

If someone asks you for the data and you are not certain they should have it,
ask before sending. Nobody has ever been in trouble for asking.

## The software is not yours either

Do not copy it to another machine, publish it, share it, put it in your
portfolio, or reuse any part of it in other work. Your access can be withdrawn
at any time, and when it is, delete your copy.

## Do not make it rude

The tool is deliberately polite: it honours `robots.txt`, throttles per host,
and identifies itself. That is what keeps our IPs working and keeps us within
what these sites permit.

**Never set `obey_robots: false`.** There is exactly one legitimate exception,
already built in: the Swiss commercial register issues credentials, and holding
them is the permission. Nothing else qualifies. If a source seems blocked, that
is usually a real answer — report it rather than routing around it.

Do not raise `concurrency` past 8 or shorten the per-host delays to make a run
finish sooner. A run that gets our address blocked costs more than a slow one.

## Sources we do not touch

No LinkedIn. No Apollo, ZoomInfo, Lusha, or any other paid contact database. No
buying lists. Their terms forbid it and it would poison everything else we do.

If you find a promising new source, say so. Do not just add it.

## When something looks wrong

Tell BluOryn, do not improvise:

- A run produces names that are clearly not people.
- A row's `source_person_url` does not actually contain that person.
- A site starts returning blocks or captchas.
- Anyone asks you for the data.
- You think you may have sent something to the wrong place. **Especially this
  one** — a mistake reported in ten minutes is a small problem, and the same
  mistake found in a month is not.

## What to do on your last day

Delete the repository folder, the `.cache/` directory, the `output/` directory,
and every list you exported anywhere else. Confirm to BluOryn that you have.

---

Questions about any of this: hriday.vig@bluoryn.com
