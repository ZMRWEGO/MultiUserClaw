## ADDED Requirements

### Requirement: Folder download must use an asynchronous archive job

The system SHALL expose an asynchronous archive workflow so that downloading a workspace directory produces a zip file without blocking the request handler or the gateway proxy.

The workflow MUST consist of three endpoints under `/api/workspace/archive`:
- `POST /api/workspace/archive` — accepts `{ "path": "<workspace-relative path>" }` and returns `{ "job_id": "<hex>", "status": "pending" }` with HTTP 201. The target MUST be an existing directory inside the user's workspace; otherwise return 400 (path not a directory) or 404 (not found).
- `GET  /api/workspace/archive/{job_id}` — returns `{ "status": "pending" | "running" | "ready" | "failed", "bytes_written": <int>, "total_bytes_estimate": <int>, "error": <string|null> }`. Unknown `job_id` returns 404.
- `GET  /api/workspace/archive/{job_id}/download` — returns the zip file with `Content-Type: application/zip` and `Content-Disposition: attachment; filename="<dir>.zip"` (RFC 5987 encoded for non-ASCII). MUST return 409 when status is not `ready`, 404 when job is unknown.

#### Scenario: Create archive job for a directory
- **WHEN** the client `POST`s `{"path": "subdir"}` to `/api/workspace/archive` and `subdir` is an existing directory under the workspace
- **THEN** the response is HTTP 201 with body `{"job_id": "<12-32 hex>", "status": "pending"}` and a background task starts compressing `subdir` into a temp zip

#### Scenario: Reject non-directory path
- **WHEN** the client `POST`s `{"path": "README.md"}` (a file) to `/api/workspace/archive`
- **THEN** the response is HTTP 400 with body `{"detail": "Path is not a directory"}`

#### Scenario: Reject path outside workspace
- **WHEN** the client `POST`s `{"path": "../etc/passwd"}` to `/api/workspace/archive`
- **THEN** the response is HTTP 400 and no temp file is created

#### Scenario: Status reflects progress
- **WHEN** a job has been compressing for some time and the client `GET`s `/api/workspace/archive/{job_id}`
- **THEN** the response status is `running` or `ready` and `bytes_written` is monotonically non-decreasing across successive polls

#### Scenario: Download zip when ready
- **WHEN** a job's `status` is `ready` and the client `GET`s `/api/workspace/archive/{job_id}/download`
- **THEN** the response is HTTP 200 with `Content-Type: application/zip`, `Content-Disposition: attachment; filename="<dir>.zip"`, and the body is a valid zip whose entries are paths relative to `<dir>`

#### Scenario: Download before ready
- **WHEN** the client `GET`s `/api/workspace/archive/{job_id}/download` while status is `pending` or `running`
- **THEN** the response is HTTP 409 with body `{"detail": "Archive not ready"}`

#### Scenario: Job failure surfaces error
- **WHEN** the compression task raises an exception (e.g. permission error mid-walk)
- **THEN** subsequent `GET /api/workspace/archive/{job_id}` MUST return `{"status": "failed", "error": "<message>"}` and the temp directory is retained until TTL expiry for diagnostics

### Requirement: Single-file workspace download MUST remain unchanged

The existing `GET /api/workspace/download?path=<file>` endpoint SHALL continue to work for files exactly as today. When the path is a directory it MUST return HTTP 400 with body `{"detail": "Use POST /api/workspace/archive for directories"}` instead of attempting an inline zip.

#### Scenario: File still downloads inline
- **WHEN** the client `GET`s `/api/workspace/download?path=notes.md`
- **THEN** the response is HTTP 200 with `Content-Disposition: attachment; filename="notes.md"` and the original bytes

#### Scenario: Directory request is rejected with guidance
- **WHEN** the client `GET`s `/api/workspace/download?path=subdir` where `subdir` is a directory
- **THEN** the response is HTTP 400 with body `{"detail": "Use POST /api/workspace/archive for directories"}` and no zip is generated

### Requirement: Archive temp files MUST be cleaned up

The system SHALL ensure archive temp files do not accumulate on disk.

The system MUST clean up a job's temp directory in any of three triggers:
1. After the `/download` endpoint finishes streaming (success path).
2. After `ARCHIVE_TTL_SECONDS` (default 600s, env-overridable via `NANOBOT_ARCHIVE_TTL_SECONDS`) following the job entering `ready` or `failed`.
3. On nanobot web process startup, before accepting requests, by purging any leftover `workspace/.cache/archive/*`.

#### Scenario: Temp file removed after download completes
- **WHEN** the client successfully downloads a zip via `/api/workspace/archive/{job_id}/download`
- **THEN** the temp zip and its parent job directory under `workspace/.cache/archive/{job_id}/` are removed within 5 seconds

#### Scenario: Stale job is cleaned up
- **WHEN** a job has been in `ready` state for `ARCHIVE_TTL_SECONDS` without being downloaded
- **THEN** the temp directory is removed and `GET /api/workspace/archive/{job_id}` returns 404

#### Scenario: Crash residue is purged at boot
- **WHEN** the nanobot web process starts and `workspace/.cache/archive/` contains directories from a previous run
- **THEN** those directories are removed before the HTTP server begins accepting connections

### Requirement: Gateway proxy MUST stream binary responses without buffering whole body

The platform gateway proxy at `/api/nanobot/{path:path}` SHALL stream non-JSON upstream responses to the client using chunked I/O instead of buffering the entire body in memory, and SHALL preserve the upstream `Content-Type`, `Content-Length`, and `Content-Disposition` headers.

JSON responses (where upstream `Content-Type` starts with `application/json`) MAY continue to be deserialized and re-encoded as today.

The gateway SHALL NOT impose a finite read timeout on archive download paths; for other proxy traffic the existing 120-second read timeout MAY remain.

#### Scenario: Zip response is streamed end-to-end
- **WHEN** the upstream returns `Content-Type: application/zip` of size 50MB
- **THEN** the gateway forwards it to the client as a `StreamingResponse` whose chunks are read from `httpx.AsyncClient.stream(...)` and whose `Content-Type`, `Content-Length`, and `Content-Disposition` headers match the upstream response

#### Scenario: JSON responses keep working
- **WHEN** the upstream returns `Content-Type: application/json` (e.g. `/api/sessions`)
- **THEN** the gateway returns the parsed JSON as today and downstream clients see no behavior change

#### Scenario: Archive download is not subject to 120s timeout
- **WHEN** an archive download response takes longer than 120 seconds to fully stream
- **THEN** the gateway does not abort the connection due to a read timeout

### Requirement: Frontend MUST show progress feedback during folder download

The frontend `FileTree` directory download button SHALL provide visible feedback while the archive job is in progress and surface failures with an actionable message.

When the user clicks the download button on a directory, the frontend MUST:
1. Replace the button icon with an in-line spinner (e.g. `Loader2 animate-spin`) for that row.
2. Show a toast message in Chinese ("正在压缩 …" or equivalent localized string).
3. Call `POST /api/nanobot/workspace/archive`, then poll `GET /api/nanobot/workspace/archive/{job_id}` at 1-second intervals until status is `ready` or `failed`.
4. On `ready`, fetch the zip from `GET /api/nanobot/workspace/archive/{job_id}/download` with the auth header, create an object URL, programmatically trigger the download with `<dir>.zip` as filename, and dismiss the toast.
5. On `failed`, dismiss the in-progress toast and show an error toast containing the server-provided `error` message.
6. Restore the button icon to the default `Download` icon at the end of the operation regardless of outcome.

The single-file download branch SHALL continue to call `downloadWorkspacePath` and SHALL NOT show the compression toast.

#### Scenario: Directory download shows loading indicators
- **WHEN** the user clicks the download icon on a directory row in the file tree
- **THEN** that row's icon turns into a spinning loader and a toast "正在压缩 …" appears

#### Scenario: Successful directory download triggers browser download
- **WHEN** the archive job reaches `ready`
- **THEN** the browser receives a download named `<dir>.zip` (the directory's basename), the spinner reverts to a download icon, and the in-progress toast is dismissed

#### Scenario: Failed compression shows error toast
- **WHEN** the archive job reaches `failed` with `error: "permission denied"`
- **THEN** the in-progress toast is dismissed, an error toast displaying "压缩失败：permission denied" appears, and the row spinner reverts to the download icon

#### Scenario: File download path is unaffected
- **WHEN** the user clicks the download icon on a file row
- **THEN** the request goes to `/api/nanobot/workspace/download?path=<file>` (no `/archive` call), no compression toast appears, and the file downloads as before

### Requirement: An end-to-end Playwright test MUST cover the folder download flow

The repository SHALL include a Playwright e2e test (`frontend/tests/folder-download.spec.ts`) that runs in **headed** mode and exercises the full folder download path. It MUST be the gating quality check for this change.

The test MUST:
1. Sign in as the project's e2e test account (the credentials documented in `docs/e2e-test-account.md`).
2. Programmatically prepare a workspace directory with at least 3 files (one >100KB) before the test, e.g. via the workspace HTTP API or a test fixture.
3. Open the `/` chat page so the FileTree is visible.
4. Click the download button on the prepared directory row.
5. Assert the row spinner appears and a toast containing "正在压缩" is rendered.
6. Listen for `page.waitForEvent('download')`, then assert the suggested filename ends with `.zip`, the saved file path exists, and `fs.statSync(path).size > 0`.
7. Validate the downloaded file is a real zip by `unzipper.Open.file(path)` (or equivalent) and assert all prepared filenames are present as entries.
8. Assert that downloading a single file (sibling row) still works and DOES NOT show the compression toast.

#### Scenario: e2e green-lights folder download
- **WHEN** `npx playwright test frontend/tests/folder-download.spec.ts --headed` is run against a fresh `start_local.py` stack
- **THEN** the test passes, the artifact is a valid zip whose entries match the prepared files, and the report shows the loading toast was rendered then dismissed

#### Scenario: e2e is the gating check
- **WHEN** the change is being merged
- **THEN** `frontend/tests/folder-download.spec.ts` SHALL be required to pass; failure of this test alone blocks the merge
