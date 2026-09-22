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

**On `robots.txt`: BluOryn has decided the tool does not consult it.** That is
the shipped default (`obey_robots: false`) and it is a deliberate business
decision, not an accident — so it is written down here rather than left for
each operator to discover.

What that decision does and does not mean:

- robots.txt is an advisory convention, not an access control. Ignoring it is
  not a technical circumvention and it is not hacking.
- It does raise our exposure to a site's terms of service, and where a site has
  expressly asked crawlers not to come, a regulator weighing our
  legitimate-interest basis for holding EU personal data will weigh that too.
  That is the cost BluOryn has accepted. If you are uncomfortable applying it to
  a specific source, say so before running rather than after.
- It changes nothing else. Every other limit below still binds.

**These are not negotiable:**

- **Never bypass a login, a paywall, or any access control.** robots.txt is a
  request; a password is a boundary. Only public pages.
- **Never disable certificate validation**, and never route traffic through a
  proxy the tool has not validated. `tools/proxy_sources.py` exists precisely so
  that an untrusted proxy cannot read what we fetch.
- **Do not raise `concurrency` past 8 or shorten the per-host delays** to make a
  run finish sooner. Per-host throttling is what keeps this a crawl rather than
  an attack, and a run that gets our address blocked costs more than a slow one.
- **Do not point it at a site to cause disruption.** Volume against one host is
  the line between research and a denial of service.

If a source returns blocks or captchas, report it. The run now counts those
separately (`blocked_no_pages_seen`, and the `reachability:` line), so say what
the number was — that is useful information, not a failure to hide.

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
