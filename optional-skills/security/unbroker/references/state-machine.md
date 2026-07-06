# Case state machine

One case = one (subject x broker). `pdd.py record` validates every transition against this table and
appends it to `audit.jsonl`. Authoritative definition lives in `scripts/ledger.py`.

## States

| State | Meaning |
|---|---|
| `new` | Case created, nothing done |
| `searching` | Scan in progress |
| `not_found` | Subject not listed (will be re-checked next cycle) |
| `found` | Listing confirmed; action needed |
| `indirect_exposure` | Subject's PII (email/phone/name) appears on a **third party's** record (e.g. named in a relative's "Family" field). Not removable via self-service opt-out; needs a targeted CCPA/GDPR delete-my-PII request |
| `action_selected` | Tier/method chosen |
| `submitted` | Opt-out submitted |
| `form_required` | Broker's email reply didn't confirm the request - it demands a web form (suppression center / iframe widget / support ticket / PDF) instead |
| `verification_pending` | Awaiting email/callback verification |
| `awaiting_processing` | Submitted, no verification needed; broker processing |
| `confirmed_removed` | Verified gone |
| `reappeared` | Was removed, now listed again |
| `human_task_queued` | Needs an operator step (captcha/ID/phone/fax/mail) |
| `blocked` | Broker dead / mechanics broken -> flag for DB re-verification |

## Allowed transitions

```
new                  -> searching | found | not_found | indirect_exposure | blocked
searching            -> not_found | found | indirect_exposure | blocked
not_found            -> searching | found | indirect_exposure | blocked
found                -> action_selected | submitted | human_task_queued | indirect_exposure | blocked
indirect_exposure    -> submitted | human_task_queued | not_found | found | blocked
action_selected      -> submitted | human_task_queued | blocked
submitted            -> verification_pending | awaiting_processing | form_required
                        | human_task_queued | blocked
form_required        -> submitted | human_task_queued | blocked
verification_pending -> awaiting_processing | confirmed_removed | form_required
                        | human_task_queued | blocked
awaiting_processing  -> confirmed_removed | human_task_queued | blocked
confirmed_removed    -> reappeared | confirmed_removed   (recheck refreshes the date)
reappeared           -> found | indirect_exposure
human_task_queued    -> found | indirect_exposure | action_selected | submitted | form_required
                        | verification_pending | awaiting_processing | confirmed_removed | blocked
blocked              -> searching | found | not_found | indirect_exposure | action_selected
                        | human_task_queued | form_required
```

A transition to the same state is always allowed (idempotent field updates).

## Notes / gotchas learned in the field

- **`submitted -> not_found` is ILLEGAL.** A lodged request that then finds no matching profile is a
  no-op that resolves as `awaiting_processing`, never a walk back to `not_found`. (This is why a guided
  opt-out whose matcher says "no results" after you have already submitted is recorded
  `awaiting_processing`, not `not_found`.)
- **`blocked -> submitted` is ILLEGAL directly** - go `blocked -> action_selected -> submitted`.
- **Recording an operator's manual verdict:** attach an `operator_manual_check` evidence note. A
  dead / 404 site, or an operator-confirmed "no results" search, is a valid `not_found`.
- **`--evidence` shell gotcha:** an `--evidence` JSON string containing a literal `&` trips the shell's
  backgrounding guard - write the word "and" instead of `&`.
- **A broker reply is not automatically the final step.** Some replies to a `submitted`/
  `verification_pending` opt-out demand a web form instead of confirming - run
  `pdd.py classify-reply <subject> <broker> --text '<reply body>'` (see `scripts/form_reply.py`).
  If it detects one, the case moves to `form_required` with the extracted `form_url` /
  `required_fields` / `hidden_gates` / `due_date` stored as evidence. Missing required fields or a
  hard gate (captcha / gov-ID / account creation) route to `human_task_queued` instead of guessing;
  otherwise `next` emits a `form_fill_web` action and the case returns to `submitted` once the form
  is filled and resubmitted.
