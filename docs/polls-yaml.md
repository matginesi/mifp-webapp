# Polls: canonical privacy and YAML schema 1

**Create → Edit → Preview → Publish → Copy Public Link** uses the original visual
editor for Blank, existing templates and YAML. The Privacy tab shows the purpose
and complete notice preview; **Privacy details** holds this survey's legal fields.
Response mode, deadline and retention are in Settings. There are no organisation
profiles, custom-mode selectors or secondary approvals.

Each poll has one privacy object:

`anonymous` currently means **no name/email requested**, not verified irreversible
anonymisation. JSONL retains a submission day, retry ID and append order. Text,
distinctive availability combinations and external logs can permit identification.
The application therefore keeps applicable disclosure checks for this mode; it
does not infer a legal exemption from the selector. Aruba mailing-list processing
is separate. No generic consent checkbox is added. New Blank/template Drafts reuse
only the administrator-confirmed MIFP controller/address/contact; legal basis,
recipients and transfers remain unset. YAML values are preserved as supplied.

```yaml
schema_version: 1
poll:
  title: "School interest and availability"
  description: "Preliminary survey"
  response_mode: anonymous
  deadline: null
  retention_until: null
  privacy:
    controller: ""
    contact: ""
    purpose: "Collect preliminary interest and availability"
    legal_basis: ""
    recipients: ""
    transfers: ""
    legitimate_interest: ""
    dpo: ""
    additional_disclosures: ""
  questions:
    - type: date_availability
      question: "Which days are suitable?"
      required: false
      dates: ["2027-02-21", "2027-02-22"]
```

This is an importable incomplete Draft, not a publication-ready legal notice.
Fill facts accurately; the application cannot infer the legal basis, processors
or transfers. Publishing requires controller, valid contact email, purpose, legal
basis, recipients and transfers; `legitimate_interest` is also required for that
basis. Optional `dpo` and `additional_disclosures` are included when supplied.
All fields are editable and persisted per poll, including imported values.

`legal_basis` is blank in Drafts or one of `consent`, `legitimate_interest`,
`public_task`, `contract`. Anonymous consent-based collection remains unsupported
by the existing individual-withdrawal model. Identified consent-based polls ask
participants for the processing consent described by that particular notice.

Save Draft requires valid field/question syntax but no legal approval or complete
publication settings. Publish saves progress, validates the current configuration,
and displays the full participant notice for one explicit **Publish poll** action.
The final confirmation checks completeness again and rejects changed Drafts.
Published notices have an immutable snapshot and cannot be rewritten by editing
another poll. Retention is manual administrator deletion by the configured date;
exports and backups need separate handling. No automatic scheduler is installed.

New publications use notice renderer version 2, explaining correlation risks and
separating mailing-list processing. Published version 1 notices retain their
original renderer. This version is a content snapshot, not an approval state.

## Questions and import limits

`title` and 1–30 questions are required. Each question needs `type` and `question`.
`response_mode` defaults to `anonymous`; `identified` is supported. `required` is a
boolean, default false. Deadline/retention may be omitted, `null` or `""` in Drafts.
Dates are ISO calendar days without timezone conversion.

| Type | Additional settings |
| --- | --- |
| `text` | `style`: `single_line` (default) or `multi_line` |
| `yes_no` | `yes_label`, `no_label`: distinct text labels, defaults `Yes`, `No` |
| `single_choice` | `options`: 2–30 distinct text labels |
| `multiple_choice` | `options`: 2–30 distinct text labels |
| `date` | None |
| `date_availability` | `dates`: 1–60 distinct dates; Yes/Maybe/No/unanswered |

Privacy field limits: controller/contact/DPO 200 characters, purpose 1,000,
legal basis 80, recipients/transfers/interest 500, additional disclosures 2,000.
Quote labels such as `Yes`/`No` to avoid implicit YAML booleans.

Parsing uses safe_load with 1 MiB UTF-8, 6,000 events and depth 8 limits. Aliases,
anchors, explicit tags, duplicate keys and unsupported fields are rejected with
field or syntax locations. Import needs a session-bound preview confirmation,
creates a new Draft with fresh IDs, and cannot overwrite a poll. An old boolean
`confirmed` is discarded; historic `privacy_source` metadata has no configuration
effect and is not exported. Neither is authoritative approval.

Export downloads the last saved complete definition: privacy text, questions,
options, dates, mode and schedules. Reimport preserves these settings. Approval
flags, technical tokens, respondent identities and answers are excluded.

## Winter School and old Drafts

The user file is `~/Scaricati/MIFP_Winter_School_2027_Poll.yaml`; CI uses the matching
[canonical fixture](../TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml).
Controller/address/contact were supplied by the administrator from MIFP's policy.
Legal basis, legitimate interest and transfers remain blank for confirmation;
recipients are a proposed description, not a verified declaration. The file can
be imported, edited and saved without those publication details.

Old custom notices remain directly readable/editable in Drafts. **Adopt privacy
configuration** normalizes their existing values explicitly. For old purpose-only
Drafts it can copy previously stored organisation facts once, read-only, on that
explicit action. It never approves them, changes the old settings file or affects
new polls. Published notices and answers are not migrated.

See [implementation and verification report](POLLS_PRIVACY_CLEANUP_REPORT.md).
