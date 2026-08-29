"""WHAT A JOB KNOWS HOW TO DO — the authored procedure library a job seeds.

Founder, 2026-08-28: *"Reasoning is a commodity, in the future it's going to
be even cheaper. I want this specific agent to be great at specific work, so
we could say this is the agent you need for financial things — not because of
the AI model, but because of tools and others."*

The demonstrable version of that claim, and the reason this module exists at
all: **a normal agent looks at a scanned invoice and guesses the digits; this
one reads them.** Same model both times. Measured on a real 10-page, 260-line
invoice, text extraction recovered 1,040 of 1,040 numbers exactly, at roughly
a fifth of the tokens the vision path spends. Nothing about that is the model.

── WHY THE LIBRARY IS SERVER-SIDE ────────────────────────────────────────
`agent-create-job.ts` owns which jobs exist and what their cards say. It does
NOT own this: a skill body is a product asset measured in kilobytes, and
shipping four of them to every browser that loads the create card — then
posting them back for the server to validate — would put an authored
procedure in the client's hands and give this repo two copies of one fact.
The precedent is already here and one line up: `purpose_preset` seeds
`instructions` server-side from `_PURPOSE_PRESET_INSTRUCTIONS`, and the wire
carries only the preset id. A job seeds skills exactly the same way.

── WHY IT IS KEYED ON THE JOB, NOT ON A PRESET ───────────────────────────
It cannot be keyed on the preset pair. Bookkeeping, General, Research and
Operations all resolve to `internal_assistant` + `standard`/`knowledge` — the
two preset axes genuinely do not distinguish them, which is precisely why the
job picker exists as a third thing. So `fleet_create_agent` learns the job
vocabulary, and `agent-create-job.test.ts` reads THIS file to prove the two id
sets agree rather than either one confirming itself.

── A SKILL THAT NAMES A TOOL THE MACHINE DOES NOT HAVE IS A LIE ──────────
Every command named in a body below is installed by
`scripts/install-agent-computer.sh` (`install_data_toolchain`) — poppler's
`pdftotext`, and a Python environment carrying pandas / duckdb / pypdf /
openpyxl. The two must be changed together; `test_agent_job_skills.py` asserts
each named binary appears in the installer, from the installer's own source,
so a body that grows a dependency the box does not get fails loudly.

That guarantee stops at the container boundary and the bodies say so
themselves rather than pretending: on a box where Docker is running,
`shell.execute` lands in a stock `debian:bookworm-slim` with no Python and no
network, so the toolchain is not reachable there. Each procedure therefore
opens by CHECKING, and every one of them ends the same way when the check
fails — say the figures could not be read, never present guessed digits as
read ones. That is the outcome-honesty law pointed at the one place in this
product where a confident wrong number costs somebody money.

── THESE ARE PROCEDURES, NOT PROMPTS ─────────────────────────────────────
A body is what a competent bookkeeper does, in order, including the checks
that catch their own mistakes. The arithmetic checks are not decoration: an
agent that reads 260 line items and never re-adds them has guessed with extra
steps. Anything that reads as encouragement ("be careful", "be accurate") is
removed on sight — it is what a body says INSTEAD of a procedure.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Sequence

# Every external tool a body below tells the agent to reach for. Kept as data
# rather than left implicit in prose so the installer-drift test has an actual
# set to compare against the installer's own source — the expected set and the
# actual set from different places, never one file confirming itself.
#
# The declaration is checked in BOTH directions, and the second direction is
# the one that caught a mistake on the day it was written: a name here that no
# body actually uses is a package installed on every customer's box for
# nothing, and a tool a body names that is missing here is a promise the
# installer was never held to. `python3` itself is deliberately absent — no
# procedure names the binary, they express Python through imports, and
# apt_install_system_deps has installed it unconditionally since long before
# any of this.
REQUIRED_BINARIES: frozenset = frozenset({"pdftotext"})
REQUIRED_PYTHON_IMPORTS: frozenset = frozenset({"pandas", "duckdb", "pypdf", "openpyxl"})


_READ_AN_INVOICE = """
Read an invoice, bill or receipt into figures you can defend, and check the
arithmetic before you report anything.

## The rule this whole procedure exists for

A PDF invoice almost always carries a **text layer** — the exact characters
the supplier's own system wrote. Read that. Do not look at the page and
transcribe what the digits appear to say: at nine point type a 3 and an 8, a
1 and a 7, a 6 and a 5 are the same handful of pixels, and a transcription
error of one digit in a total is indistinguishable from a correct answer
until somebody pays it.

Reading the text layer is also five times cheaper in tokens than looking at
the pages, so there is no case where guessing is the faster option.

## 1. Get the text out

```
pdftotext -layout invoice.pdf -
```

`-layout` is not optional. It preserves column positions, which is what keeps
a quantity in the quantity column instead of running every field of a line
into one string. For one page of a long document, add `-f 3 -l 3`.

Know how many pages you are reading before you start, so a truncated read is
distinguishable from a short invoice:

```python
import pypdf
print(len(pypdf.PdfReader("invoice.pdf").pages))
```

Now decide whether you actually got anything:

- **Hundreds of characters per page, and you can see the supplier name and
  the line items.** This is a real text layer. Continue.
- **Empty, or a few dozen characters of noise.** There is no text layer —
  this is a photograph or a scan of paper. Skip to "When there is no text
  layer" at the bottom. Do not continue down this path.

If `pdftotext` is not on this machine, say that you cannot read the file
rather than opening the image and reading digits off it.

For a spreadsheet or CSV instead of a PDF, there is nothing to extract —
read it directly with pandas, and skip to step 2.

## 2. Pull the header facts

Find and record, each as it is literally written:

- invoice number, and the supplier's own name for it if it differs
- issue date, and due date (or the payment terms, e.g. "Net 30")
- supplier name, and their tax/VAT registration number if present
- the currency — **read it, never assume it.** A supplier who bills you in
  one currency and quotes another in the same document is common.
- subtotal, tax, any discount or shipping, and the stated total

If a field is absent, record it as absent. An invoice with no due date is a
fact about the invoice, not a gap for you to fill with "probably 30 days".

## 3. Pull the line items

One record per line: description, quantity, unit price, line amount, and the
tax rate or code if the document carries one per line.

Watch for the two things that quietly corrupt a table:

- **A description that wraps onto a second line** has no numbers on its
  continuation row. Join it upward; do not emit it as a line item with null
  amounts.
- **A page break** repeats the column headers and often a "carried forward"
  subtotal. Neither is a line item. Including a carried-forward figure double
  counts everything above it.

Note how many line items you found. If the invoice states a count, compare
them.

## 4. Check the arithmetic — this is the part that makes the answer worth
   something

Three checks, in order. Do them with a calculation, not by eye.

1. **Per line:** `quantity x unit price == line amount`, to the document's
   own rounding.
2. **Subtotal:** `sum(line amounts) == stated subtotal`.
3. **Total:** `subtotal + tax + shipping - discount == stated total`.

Tax is worth a fourth look when every line carries a rate: `sum(line amount x
line rate)` should land on the stated tax within a rounding cent or two.

**When a check fails, do not fix it.** You do not know which of the two
figures is the wrong one, and silently adopting either is how a mistake
becomes the record. Report it as: which check, the two numbers, the
difference, and the line numbers involved if it is a per-line failure. A
difference that equals exactly one line item's amount means you dropped or
double-counted that line — check step 3 before blaming the supplier.

A difference of a few cents across many lines is ordinary rounding and should
be named as rounding rather than raised as a discrepancy.

## 5. Report

State the header facts, the line-item count, and the result of each check.
Give the total once, as the supplier wrote it. If anything failed a check,
that goes at the top, not in a footnote — the whole reason to check is so the
person hears about it before they pay.

Record the figures wherever this team keeps them so nobody re-reads the same
document next month.

## When there is no text layer

Say so, in those words, before anything else: this is a scan, so the figures
below were read from the image and are not verified.

Then read what you can, and hold yourself to a narrower claim. Read the
totals and the header fields; do not transcribe two hundred line items off a
photograph and present them as data. Re-read every figure you report a second
time and say plainly if the two readings disagree.

If the file matters — a bill somebody is about to pay — ask for a text PDF or
the supplier's own CSV rather than working from the picture. That request
costs one message and removes the entire class of error.
""".strip()


_RECONCILE_A_STATEMENT = """
Match a bank statement against the ledger, and produce three lists: what
matched, what the bank has that the ledger does not, and what the ledger has
that the bank does not.

## Before you start: how big is the file?

```
ls -l statement.csv
```

- **Under about 50 MB** — pandas is fine.
- **Bigger than that, or you do not know** — use duckdb instead. It queries
  the file where it sits; pandas loads the whole thing into memory at several
  times the file size, and on a machine with a memory limit that ends with
  the command being killed with no output and no explanation.

```python
import duckdb
duckdb.sql("SELECT * FROM 'statement.csv' LIMIT 5").show()
duckdb.sql("SELECT count(*) FROM 'statement.csv'").show()
```

A bank export is usually CSV; `.xlsx` reads the same way through pandas
(`read_excel`, needs openpyxl). Look at the first five rows before writing
any matching logic. Bank exports routinely carry a preamble of account
metadata above the real header row, and a footer row of totals below the last
transaction — both will parse as transactions if you do not look.

## 1. Normalise both sides before comparing anything

Most failed reconciliations are a formatting difference wearing the costume
of a missing transaction.

- **Dates** to one format. A bank statement in `DD/MM/YYYY` and a ledger in
  `MM/DD/YYYY` agree on the 1st through the 12th of every month and disagree
  on everything else — which looks like a real reconciliation problem for
  two thirds of the rows. If you cannot tell which convention a file uses,
  find a row with a day above 12 and let it tell you.
- **Amounts** to a signed decimal. Strip currency symbols and thousands
  separators. Decide one sign convention — money leaving the account is
  negative — and apply it to both sides. Some exports use a `(1,234.56)`
  parenthesis for negative, some use two separate debit and credit columns.
- **Never use floating point for money.** Use `Decimal`, or integer minor
  units. `0.1 + 0.2 != 0.3` in float, and a reconciliation that compares
  totals with `==` will report a discrepancy of 0.00 and be right about the
  numbers and wrong about the conclusion.

## 2. Match in passes, strictest first

Each pass only looks at what is still unmatched. A transaction that matches
in pass 1 is removed before pass 2 runs, and every match is one-to-one — a
single bank line may not satisfy two ledger entries.

1. **Exact:** same amount, same date.
2. **Same amount, date within a few days.** A card payment settles one to
   three days after it is made. Record the day gap on the match.
3. **Amount close, date close.** Usually a bank fee taken out of the
   transfer, or a foreign exchange difference. Record the difference as its
   own figure — this is a real amount that belongs in the books, not noise to
   absorb into the match.
4. **Many-to-one:** several ledger entries summing to one bank line, or one
   deposit covering several invoices. Look for these only among what is left,
   and only when the sum is exact.

Beyond that, stop. A "probably this one" match is worse than an unmatched
line, because an unmatched line gets looked at by a person and a wrong match
does not.

## 3. Look for the three things that are usually wrong

- **Duplicates.** The same amount, the same date, the same description, twice
  on one side. Either a double payment or a double entry, and both matter.
  Never quietly match two ledger rows against one bank row to make a
  duplicate disappear.
- **Amounts that match with the sign flipped.** A refund entered as a charge.
- **Round numbers on only one side.** Often a manual entry someone made from
  memory rather than from a document.

## 4. Report

Give the counts first: how many matched, how many bank-only, how many
ledger-only, and the total value of each group. Then the unmatched lines
themselves, with dates, amounts and descriptions, largest value first — the
person's time goes to the biggest item, not the first one alphabetically.

Say the closing balance comparison plainly: the statement's own closing
balance, the ledger's balance for that account on that date, and the
difference. If the difference equals the sum of the ledger-only items, the
reconciliation is explained and you can say so. If it does not, say that too
and give the residual — an unexplained residual is the single most useful
number in the whole report.

Never adjust a ledger figure to make a reconciliation balance.
""".strip()


_CHASE_WHAT_IS_OVERDUE = """
Work out what this business is actually owed, how late each item is, and what
to do about it.

## 1. Age from the DUE date, not the invoice date

An invoice issued on the 1st with 30-day terms is not late on the 15th. Age
every open item as `today - due date`, and where no due date was recorded,
derive it from the terms and say that you derived it.

Group into the buckets everyone in finance already reads:

| bucket | meaning |
| --- | --- |
| Current | not yet due |
| 1-30 | a slow payer, usually not a problem |
| 31-60 | needs a person to make contact |
| 61-90 | something is wrong; find out what |
| 90+ | may not be collectable |

## 2. Only count what is genuinely open

Before ageing anything, remove:

- invoices already paid, including ones paid after the export was taken
- credit notes, and any invoice a credit note fully offsets — a credit note
  is a negative, so leaving it in overstates the balance and applying it to
  the wrong invoice understates one and overstates another
- anything already in dispute, which is a different conversation from a late
  payment and must not be chased as if it were one
- partial payments: chase the remaining balance, never the original amount.
  Billing somebody for money they have already sent is worse than not
  chasing them at all.

## 3. Sort by what is worth doing

Rank by amount inside each bucket, oldest bucket first. One 90-day invoice
for a large amount is the whole afternoon; twenty 5-day invoices for small
amounts are not worth a single message.

Look at each customer as a whole, not each invoice: five overdue invoices
from one customer is one conversation, and five separate reminders to the
same person reads as an automated system nobody needs to answer.

## 4. Report, and then stop

Give the total outstanding, the total overdue, and the bucket table. Then the
list that matters: customer, invoice, amount, days overdue, and — where you
know it — what happened last time. Flag anything that has crossed into a
worse bucket since the last time this was looked at; that change is the
actual news.

**Draft the messages; do not send them.** A payment reminder goes out over
this business's name to a real customer relationship. Write what you would
send, per customer, and let the owner press send. This holds even when the
case is obvious.

Where a reminder is warranted, keep it short and factual: the invoice number,
the amount, the date it was due, and how to pay. No pressure language, no
threat of escalation unless the owner has said to use one — this is a
customer, and the goal is to be paid and keep them.

## 5. Say what you could not determine

If the export has no due dates, or no payment records, or you cannot tell a
credit note from an invoice, say so and give the narrower answer you can
stand behind. An aged debt report that is quietly wrong gets acted on.
""".strip()


_CLOSE_OUT_A_MONTH = """
Produce the month's picture: what came in, what went out, what is still
unresolved, and what changed.

## 1. Fix the cutoff first

Decide the period and apply it by TRANSACTION date, not by the date something
was entered into the books. An invoice dated the 30th and entered on the 3rd
belongs in the month it was dated.

Then look either side of the boundary: a transaction dated the 1st that
obviously belongs to the previous month, or a batch entered on the last day
covering weeks of activity, both distort a comparison. Name them rather than
silently moving them.

## 2. Do the reconciliation before the summary, not after

A month-end summary built on unreconciled accounts is a number that will
change. Reconcile each account first (see the reconciliation procedure), and
carry the count of unresolved items into the summary as its own line. A
summary with three unreconciled transactions is honest; a summary that
implies everything is settled when it is not is not.

## 3. The figures

- Income and expenses for the period, and by category.
- Comparison against the previous month, as both the difference and the
  percentage. A percentage on its own hides the size of the thing changing.
- The largest movements, in either direction, with what each one was. "Travel
  up 340%" is not information; "travel up 340% — one flight booking of X on
  the 12th" is.
- Outstanding receivables and payables at the cutoff, aged.
- Anything unusual: a category with no activity that normally has some, a
  duplicate-looking pair, a round-number entry with no document behind it.

## 4. What is unresolved

This section is the reason the report is worth reading, so it goes near the
top and not at the end:

- transactions that did not reconcile, with amounts
- expenses with no receipt or invoice attached
- anything you had to categorise on a guess — say which, and what you
  guessed, so somebody can correct it in one line
- figures that came from a scan rather than a text document, and are
  therefore not verified

## 5. Write it where it will be found

Put the summary in this team's own documents, not only in a message. A
month-end that exists only in a conversation cannot be compared against next
month's.

State the period, the cutoff date, which accounts are included, and when the
data was pulled. A report without those four facts cannot be reproduced, and
a figure nobody can reproduce is a figure nobody can check.
""".strip()


# job id (agent-create-job.ts's AgentCreateJobId) -> the procedures that job
# ships with. A job absent from this map seeds NOTHING, which is a real answer
# and the correct one for five of the six jobs today: "General" narrows
# nothing by definition, and inventing procedures for a job nobody has
# authored them for would be exactly the marketing copy this module's header
# rules out. Absence here is deliberate, not a gap to fill.
#
# `description` is what the model sees in its always-on skill listing and is
# therefore the whole basis on which it decides to open a body. It names the
# WORK ("read an invoice", "match a bank statement"), never the mechanism — a
# description saying "uses pdftotext and pandas" tells the model nothing about
# when the skill is relevant.
JOB_SKILLS: Dict[str, List[Dict[str, str]]] = {
    "bookkeeping": [
        {
            "name": "Read an invoice or receipt",
            "description": (
                "Turn an invoice, bill or receipt into checked figures — supplier, dates, "
                "line items and totals — and verify the arithmetic before reporting. Use "
                "whenever a document with amounts on it needs to become data."
            ),
            "body": _READ_AN_INVOICE,
        },
        {
            "name": "Reconcile a statement against the ledger",
            "description": (
                "Match a bank or card statement against the books and report what matched, "
                "what only the bank has, and what only the ledger has. Use for reconciling "
                "an account, chasing a balance that does not agree, or finding a duplicate."
            ),
            "body": _RECONCILE_A_STATEMENT,
        },
        {
            "name": "Chase what is overdue",
            "description": (
                "Work out what the business is owed, how late each item is, and draft the "
                "reminders. Use for aged receivables, outstanding invoices, or any question "
                "about who has not paid."
            ),
            "body": _CHASE_WHAT_IS_OVERDUE,
        },
        {
            "name": "Close out a month",
            "description": (
                "Produce the month's income, expenses, movements against the previous month, "
                "and everything still unresolved. Use for a month-end or period summary."
            ),
            "body": _CLOSE_OUT_A_MONTH,
        },
    ],
}


def seed_skills_for_job(job_id: str) -> List[Dict[str, Any]]:
    """The `install_metadata.skills` records a newly created agent starts with.

    Returns [] for every job with no authored library — which is the common
    case and is not an error. The shape is the one `fleet_tools.
    resolve_agent_skills` reads back and `claude_agent_sdk_bridge.
    build_skills_plugin_dir` materialises: id / name / description / body /
    kind / enabled.

    Ids are minted here, per install, in the same `sk_<hex12>` shape
    `fleet_tools._normalize_skills_patch` mints on a save — so a seeded skill
    is indistinguishable from one the owner typed, and editing or deleting one
    from the Skills editor works with no special case. A SHARED id across
    installs would have been the obvious alternative and is wrong: the editor
    keys rows on it, and two agents' skills are not the same record.

    Never raises. A job id this module has never heard of resolves to no
    skills rather than an exception, because this runs inside agent creation
    and an unrecognised job must not be able to fail a create.
    """
    entries = JOB_SKILLS.get(str(job_id or "").strip().lower()) or []
    return [
        {
            "id": f"sk_{uuid.uuid4().hex[:12]}",
            "name": entry["name"],
            "description": entry["description"],
            "body": entry["body"],
            "kind": "skill",
            "enabled": True,
        }
        for entry in entries
    ]


def job_ids_with_skills() -> Sequence[str]:
    """The job ids this module actually seeds something for. Read by the
    frontend's own drift test to prove the two id vocabularies agree."""
    return tuple(sorted(JOB_SKILLS.keys()))
