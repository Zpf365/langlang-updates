# LangLang Project Rules

These rules are mandatory for every future code change.

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
9. After every user-requested change, build the latest application and run
   `tools/deploy_latest_local.ps1`. It must close all older LangLang
   processes, back up `D:\WG\LangLang`, deploy the latest build there, and
   launch it. If the build fails, keep the existing deployment intact and
   report the failure instead of claiming the change is complete.
10. Never publish a GitHub Release, push release assets, or change the hosted
    automatic-update manifest unless the user explicitly asks to publish a
    new version. Updating `D:\WG\LangLang` after a requested change is
    mandatory and independent of cloud release approval.
