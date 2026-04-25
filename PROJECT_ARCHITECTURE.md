# FEAnalyzer — Project Architecture

## 1. Purpose of the Application

**FEAnalyzer** (Frontend Architecture Complexity Analyzer) is a web application
that automatically measures the *structural complexity* of any publicly
accessible web page. The user submits a URL, and the system fetches the page,
parses its HTML, enumerates its architectural components, and produces a
single numeric **Complexity Index (C)** together with a rich breakdown of
where that complexity comes from (internal vs. external resources, dominant
third-party hosts, etc.).

The practical goal of the tool is to give front-end architects, tech leads,
and QA engineers an **objective, repeatable metric** for:

* comparing the architectural weight of competing pages or site versions;
* detecting regressions ("our landing page grew from C = 18 to C = 34 in one
  sprint — why?");
* quantifying the cost of third-party dependencies;
* supporting academic research into front-end complexity (this is the context
  in which the tool was originally built — a Bachelor's diploma project).

A user-facing dashboard lets analysts store projects, keep a history of scans,
visualise the trend of complexity over time, and export any completed report
as a formatted Excel workbook.

---

## 2. System Architecture

FEAnalyzer is split into **five cooperating services**, each running in its
own Docker container and orchestrated via `docker-compose.yml`:

```
                    ┌─────────────────────┐
                    │  React SPA (Vite)   │
                    │   served by Nginx   │
                    │   port 80 / 8080    │
                    └──────────┬──────────┘
                               │ HTTPS / JSON
                               ▼
                    ┌─────────────────────┐
                    │  Django + DRF API   │
                    │   (Gunicorn/WSGI)   │
                    │   JWT authenticated │
                    └──────┬───────┬──────┘
                           │       │
            (Celery broker)│       │(ORM)
                           ▼       ▼
                ┌──────────────┐  ┌──────────────┐
                │   Redis 7    │  │ PostgreSQL16 │
                │ broker+cache │  │  users/reports│
                └──────┬───────┘  └──────────────┘
                       │ task queue
                       ▼
                ┌──────────────────────┐
                │  Celery Worker(s)    │
                │  HTML fetch + parse  │
                │  complexity compute  │
                └──────────────────────┘
```

### 2.1 Frontend — React 19 Single-Page Application

* **Stack:** React 19, Vite, Tailwind CSS v4, React Router, Recharts, Axios.
* **Responsibilities:** authentication UI, project/report CRUD, dashboard
  with trend chart, report detail view with pie/bar charts, asynchronous
  polling of in-flight scans, file download for the Excel export.
* **Deployment:** the Vite build output is served by **Nginx** inside the
  `frontend` container. Nginx also proxies `/api/` requests to the Django
  upstream, so the browser only talks to a single origin.

### 2.2 API Backend — Django 5 + Django REST Framework

* **Stack:** Django 5.1.4, DRF 3.15.2, SimpleJWT, django-cors-headers,
  drf-spectacular, WhiteNoise, psycopg 3, openpyxl.
* **Responsibilities:** user registration/login (JWT access + refresh),
  project and report persistence, dispatching analysis tasks to Celery,
  serving OpenAPI 3 schema + Swagger UI at `/api/docs/`, and streaming the
  Excel export as a binary response.
* **Runtime:** Gunicorn WSGI server inside the `web` container.
* **Zero template rendering.** The Django admin is the only place HTML
  templates are used; every user-facing endpoint is a pure JSON REST
  resource.

### 2.3 Asynchronous Workers — Celery

* **Stack:** Celery 5.4.0, one or more worker processes, broker = Redis DB 0,
  result backend = Redis DB 1.
* **Responsibilities:** the heavy, slow, network-bound work — fetching the
  target URL with `requests`, parsing the HTML with BeautifulSoup / lxml,
  classifying each `<a>`, `<link rel="stylesheet">`, and `<script>` element as
  internal or external, tallying counts, computing the complexity index, and
  persisting the result back to the `AnalysisReport` row.
* **Why a separate worker?** Scanning a real page can take anywhere from
  200 ms to 30 s depending on the site. If that ran inside the HTTP request
  cycle, Gunicorn workers would block, user requests would time out, and a
  burst of concurrent scans would knock the API offline. Offloading to Celery
  keeps the API response times in the tens of milliseconds regardless of
  target site latency.

### 2.4 Message Broker / Cache — Redis 7

Redis plays two complementary roles:

1. **Celery broker** — the transport Django uses to hand a task to the
   worker pool.
2. **Celery result backend** — where the worker stores the task's final
   state (SUCCESS / FAILED + metadata). The frontend polling endpoint reads
   from the DB first (source of truth) and uses Redis only as advisory data.

### 2.5 Relational Store — PostgreSQL 16

Persists three core tables:

* `auth_user` (Django's default, extended via `UserProfile` serializer),
* `analyzer_project` — an optional grouping container owned by one user,
* `analyzer_analysisreport` — every scan ever submitted, including its
  Celery task id, status, weight coefficients, component counts, complexity
  index, and the raw metadata JSON used to power the dashboard charts.

All queries are owner-scoped — a user can never retrieve another user's
project or report, even by guessing its primary key.

---

## 3. Why an API Architecture Instead of Django Templates

A classical "Django + templates" approach would have the server render
every page as HTML and ship it back to the browser. For FEAnalyzer this
would have been the **wrong choice**, for four concrete reasons.

### 3.1 The workload is inherently asynchronous

Analysing a page can easily take **10–30 seconds**: we fetch the URL over
the open internet, parse it, and classify dozens to hundreds of elements.
A synchronous template view would either:

* **hold the HTTP connection open** for the entire duration, blocking a
  Gunicorn worker and risking browser/proxy timeouts (typically 30 s); or
* **force the user to refresh manually** to see whether the scan is done.

Neither is acceptable for a modern product. With an API + SPA architecture
we instead use the **202 Accepted + polling pattern**:

1. `POST /api/reports/` returns `202` with a `task_id` in **~20 ms**.
2. The SPA polls `GET /api/tasks/<task_id>/` every 2 seconds.
3. When `status` flips to `SUCCESS`, the SPA navigates to the detail page.

This is standard for any long-running job in the REST world and is
impossible to express cleanly in a classical request-response template
flow.

### 3.2 A Single-Page Application gives us rich, stateful UX

The dashboard needs **interactive charts** (Recharts pie, bar, and trend
line), in-place validation on the analysis form, toast-style error handling,
JWT token refresh in the background, and optimistic updates of the scan
history table. All of this requires client-side JavaScript state; rebuilding
it on every full-page reload would be both slower and janky.

Serving the UI as a **React SPA** means:

* the user loads the JS bundle **once**;
* every subsequent view change is a soft route transition (no full reload);
* data is fetched in parallel and cached per view;
* the back-end only ships **small JSON payloads**, not entire HTML documents.

### 3.3 A JSON API is consumable by more than one client

The same `/api/` surface can be hit by:

* the React SPA we actually ship,
* the interactive **Swagger UI at `/api/docs/`** — live-documented and
  executable — which is invaluable for a diploma defence demo,
* any future native app, CLI, or CI integration (e.g. a GitHub Action that
  fails the build if `C` regresses past a threshold),
* automated end-to-end tests that talk JSON directly, without a headless
  browser.

A template-rendered app is tied to its HTML; a REST API is reusable.

### 3.4 Clean separation of concerns — independent deploys, independent scale

Because the front-end and back-end are decoupled:

* the React bundle is a **static artefact** cacheable by a CDN;
* the API can be scaled horizontally behind a load balancer without touching
  the UI;
* the Celery worker pool scales **independently** of the API — if users queue
  a lot of scans, we add worker replicas, not API replicas;
* front-end and back-end engineers can iterate without blocking each other,
  since the contract between them is the OpenAPI schema generated by
  drf-spectacular.

This strict separation is exactly what the course syllabus calls *service-
oriented architecture*, and it is what a production-grade web product
looks like in 2025.

---

## 4. The Complexity Formula

The **Architectural Complexity Index** is defined as a weighted linear
combination of three fundamental HTML component counts:

```
C = W_links × N_links  +  W_styles × N_styles  +  W_scripts × N_scripts
```

where

| Symbol       | Meaning                                                       |
|--------------|---------------------------------------------------------------|
| `N_links`    | Number of `<a href>` elements on the page                     |
| `N_styles`   | Number of stylesheet inclusions (`<link rel="stylesheet">` + `<style>`) |
| `N_scripts`  | Number of `<script>` elements (inline and external)           |
| `W_links`    | Weight coefficient for links, default `0.3333`                |
| `W_styles`   | Weight coefficient for stylesheets, default `0.3333`          |
| `W_scripts`  | Weight coefficient for scripts, default `0.3334`              |

### 4.1 The weight constraint

The three weights are required to satisfy:

```
W_links + W_styles + W_scripts = 1.0     (±1e-3 for rounding safety)
```

This normalisation is enforced by `AnalysisReportSerializer` on the server
and by the React form on the client. Because the weights form a *convex
combination*, `C` can be read as a **weighted average cost per component**,
and reports with different weight profiles remain comparable in relative
terms.

The defaults (≈ 1/3 each) give equal importance to all three component
classes — a neutral prior suitable for initial benchmarking. A team that
cares more about JavaScript weight (e.g. for a mobile-first site) can shift
`W_scripts` upward in a custom scan while keeping the sum at 1.

### 4.2 Interpreting the value

The raw `C` value is accompanied by a categorical **level**, derived in
`analyzer/exports.py`:

| Range         | Level   | Colour   |
|---------------|---------|----------|
| `C < 15`      | LOW     | green    |
| `15 ≤ C < 40` | MEDIUM  | yellow   |
| `C ≥ 40`      | HIGH    | red      |

These thresholds are empirical — derived from scanning a reference set of
well-known landing pages — and are surfaced visually in both the dashboard
and the exported Excel report.

### 4.3 How the numbers are produced

The Celery task `analyzer.tasks.run_analysis` performs the following
pipeline on every submission:

1. **Fetch** the target URL with `requests` (with a sane timeout, a custom
   User-Agent, and a size cap).
2. **Parse** the response body with BeautifulSoup (lxml backend).
3. **Enumerate** `<a>`, `<link rel="stylesheet">`, `<style>`, and `<script>`
   elements, resolving each `href` / `src` against the page's base URL.
4. **Classify** each resource as *internal* (same registrable domain) or
   *external*, and bucket external hosts for the top-N chart.
5. **Compute** `C = Σ Wᵢ × Nᵢ` using the per-report weights.
6. **Persist** the counts, the complexity index, and a `raw_metadata` JSON
   blob to the `AnalysisReport` row, flipping status from `RUNNING` to
   `SUCCESS`.
7. **Surface** the finished report through the REST API, where both the
   SPA and the Excel exporter can consume it.

The Excel export (`analyzer/exports.py`) reads exactly the same metadata
and renders it as two formatted sheets (Summary + Resource Breakdown),
giving users an offline-friendly artefact to attach to reports, audits, or
academic submissions.
