// Railway settings for the backend service. Replaces railway.toml, which
// Railway stops reading on 2026-12-01 (.claude/CHANGE_REQUEST_019.md).
//
// Railway does NOT read this file during deploys. `railway config apply`
// writes these settings onto the service, where they persist. A change made
// in the dashboard is therefore drift from this file; detect it with
// `railway config plan --detailed-exit-code` (exit 2 = live differs).
import { defineRailway, github, project, service, volume } from "railway/iac";

// This repository manages only its own resources in the environment (the
// postgres service and its volume are not declared, so not touched).
export const partial = "backend";

export default defineRailway(() => {
  // The EXISTING 5 GB volume: Vault and Composer files, and the app's
  // database backups (CR-014). Size and region are the live values; change
  // neither here. What removing lines would do (CR-019 §4.2b):
  //  - Drop only the `volumeMounts` line: DETACH. The plan shows a service
  //    update (mountPath -> null); the volume and its data stay in the
  //    project, unmounted. The app then writes to the container's
  //    throwaway disk, so new uploads and backups vanish on the next restart.
  //  - Drop this `volume(...)` declaration too: this partial owns the volume,
  //    so expect a DELETE. Railway docs: deleted volumes are permanently gone
  //    after 48 h (restore link by email), and wiping a volume deletes all
  //    its Railway backups. Never apply a plan that shows either.
  const data = volume("backend-volume", { region: "sfo", sizeMB: 5000 });

  const backend = service("backend", {
    source: github("PerAspera-dotcom/Tenderizer", { branch: "master", checkSuites: false }),
    // Explicit on purpose: the service's stored builder was RAILPACK (Python
    // 3.13, no requirements.lock, Procfile start without migrations, no
    // pg_dump). Only railway.toml / Dockerfile auto-detection kept builds on
    // the Dockerfile. No `start`: the Dockerfile CMD migrates, then serves.
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    // Railway switches traffic only once this answers 200. The endpoint
    // doesn't touch the database, so it measures "imported and serving" —
    // what failed on 2026-11-23 (docs/INCIDENT_2026-11-23_BACKEND_DEPLOY.md).
    healthcheck: "/api/health-check",
    healthcheckTimeout: 300,
    preDeploy: "alembic upgrade head",
    // Unchanged live values, declared because `railway config migrate` omits them.
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10 },
    replicas: { sfo: 1 },
    volumeMounts: { "/app/data": data },
  });

  return project("tenderizer", {
    variables: { managed: false },
    resources: [backend, data],
  });
});
