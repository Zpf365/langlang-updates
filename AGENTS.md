# LangLang Project Rules

These rules are mandatory for every future code change.

0. Before starting implementation or review, read
   `NEXT_IMPLEMENTATION_QUEUE_CN.md` and update it when a task changes state.
1. Accepted existing behavior is a protected baseline. Do not redesign,
   remove, simplify, or silently change an existing feature.
2. Prefer additive changes. Any change to an existing page, workflow,
   field, default, database meaning, or user-visible behavior requires the
   user's explicit approval.
3. A bug fix may change only the failing path. Do not "clean up" unrelated
   code or rewrite neighboring modules.
4. Every fixed bug must receive a focused regression test. The test must
   reproduce the original failure pattern, not merely check that code runs.
5. After each module change, run its targeted tests and the complete pytest
   suite. A release is blocked if either fails.
6. For UI or browser flows, perform a packaged application smoke test when
   available. Unit tests alone are not sufficient for browser identity,
   login, order status, or layout behavior.
7. Update `FEATURE_REGRESSION_CHECKLIST_CN.md` when a feature is added,
   verified, or intentionally changed with user approval.
8. Never describe an implementation as complete if its real platform path
   has not been verified. Record unverified items explicitly.
9. Build the latest application for a completed change, but run
   `tools/deploy_latest_local.ps1` only when the user explicitly authorizes
   deployment. Without that authorization, leave `D:\WG\LangLang` and its
   running processes untouched and deliver an independent candidate package.
10. Never publish a GitHub Release, push release assets, change a hosted
    automatic-update manifest, or deploy/overwrite/restart `D:\WG\LangLang`
    unless the user explicitly authorizes that exact operation. Local
    candidates are independent of cloud release approval.
11. Every new or changed business module must register an `AnalyticsAdapter`
    with standard event definitions. Feature flags may disable transmission,
    while the registration and navigation-coverage contract tests stay green.
12. If the user explicitly says "先回答我现在这个问题" (or an equivalent
    instruction with the same meaning), pause and freeze the current task
    without committing, publishing, building, deleting, or changing its
    in-progress decisions. Answer the interrupting question first, then
    automatically resume the previously unfinished task unless the user
    explicitly cancels or replaces it. This pause-and-resume rule applies to
    every implementation and review conversation.
13. Ownership is split for the active development stream. The main conversation
    owns only `D:\11.18新项目微信\LangLangOps`: its UI, migrations, Owner RPCs,
    `ops-report` Edge Function, tests, and security. The implementation
    conversation owns the desktop application and its client-side calls to
    `my_ops_capabilities` and `ops-report`; it must not modify LangLangOps.
14. Use two-track versioning. Only a real public/listed release increments the
    formal version (`1.8.93` -> `1.8.94` -> `1.8.95`). Unpublished internal
    bug-fix candidates append a build sequence to the last public version
    (`1.8.93.1`, `1.8.93.2`, ...). Do not consume a new formal version for each
    internal change. After `1.8.94` becomes public, its internal candidates are
    `1.8.94.1`, `1.8.94.2`, and so on. Never publish an internal suffix build
    as GitHub Latest unless the user explicitly promotes it.
15. Account switching, logout, and closing the account session must always be
    possible. Background refresh, polling, observation jobs, uploads, or other
    long-running work must never hard-disable those actions. Safely cancel
    timers, detach/freeze owned jobs, persist their account ownership, discard
    late results from the old account, and let the user proceed. For writes
    whose remote result is unknown, mark verification-pending instead of
    blocking the user or copying the result to the next account.
16. For this active development stream, every completed verifiable feature or
    feature batch must be built, installed over the user's local
    `D:\WG\LangLang`, and tested from that local installation, preserving the
    database, accounts, store secrets, configuration, and logs. GitHub release
    publishing still requires separate explicit authorization. Use `zpfly` as
    the only test account unless the user explicitly names another account.
17. The main conversation must not patch desktop application code in this split.
    It implements and validates the Owner operations console only. Desktop
    defects are sent to the implementation conversation. Any release or local
    desktop deployment action still requires separate user authorization.
18. Development cadence is split by time. During daytime, work only on concrete
    requested features and modules with targeted tests; do not run repeated
    global audits, full-suite sweeps, or broad re-verification. During the
    unattended night window, first determine whether any feature work was done
    that day. If there was no feature work, do not start global or full-suite
    checks. If feature work is still running at the night trigger, wait for all
    feature implementation to finish. After the implementation conversation is
    idle, ask it whether any newly generated module is still expected to be
    completed; only after it confirms there is no pending module work may the
    full cross-module regression and release preparation start. Night QA should
    fix issues it can prove and continue; otherwise record the blocker and
    continue checking other areas. Publish a new stable version only when the
    night QA has no unresolved blocker, tests/build/package gates pass, and the
    user's standing nightly-release policy remains authorized.
19. Human interaction is user-operated. Whenever a workflow requires scanning,
    login, payment, verification, external confirmation, or any other real
    interaction/logic check, launch or navigate the software to the correct
    screen, explain the exact next steps, and let the user perform the action.
    Do not automate the user's authentication, payment, or confirmation.
20. Local candidate versioning uses the current public release as its base:
    `1.8.99.1`, `1.8.99.2`, `1.8.99.3`, then `.4`, `.5`, and so on. These
    internal builds are tested on the user's local `D:\WG\LangLang` first and
    are never published as GitHub Latest automatically. On reaching the
    internal suffix limit of 99, promote directly to the next formal version
    instead of creating a `.100` local package (`1.8.99` -> `1.9.00`). Formal
    releases then increase the last field one at a time (`1.9.00` -> `1.9.01`),
    with a carry at 99 (`1.9.99` -> `1.10.00`). Each formal version starts its
    own local candidate sequence (for example `1.9.00.1`).
