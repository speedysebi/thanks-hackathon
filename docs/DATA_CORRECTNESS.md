# Follow-ups, edits and retries

The dashboard shows all open follow-ups, oldest due first. The period selector
filters charts and recent entries; it does not hide outstanding work. Calendar
subscriptions also retain follow-ups until they are resolved.

Entry edits scrub the same recognizable personal details as new notes. Partial
count edits are checked against the saved record: youth served cannot exceed
people served. An invalid edit returns HTTP 422 and saves none of its fields.

Non-null `client_id` values are unique in SQLite. Concurrent retries return the
entry already saved, subject to the existing ownership checks. Requests without
an ID still use the recent-text duplicate check. Concurrent requests may both
run extraction, but only one entry with that client ID is stored.

## Upgrading an existing database

The first connection replaces the ordinary client-ID index with a unique index.
No entries are deleted automatically. If duplicate IDs already exist, startup
stops with an error pointing here. Back up the database and inspect the groups:

```sql
SELECT client_id, COUNT(*) AS copies, GROUP_CONCAT(id) AS entry_ids
FROM entries
WHERE client_id IS NOT NULL
GROUP BY client_id
HAVING COUNT(*) > 1;
```

Review each group, including any edits made after insertion. If the rows are
copies of one activity, keep the correct record and reconcile the extra records
in the backed-up database. If they represent distinct activities, give each
activity a different client ID. Restart after the groups are reconciled; the
unique index will then be created. Avoid changing IDs solely to bypass the
check, since that leaves duplicate activities in reports.

Run the regressions with `python -m eval.data_correctness_test`. They use
temporary databases, disable AI calls, and force concurrent requests to pass
both initial duplicate checks before any request saves.
