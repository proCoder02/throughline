"""Throughline ops module -- admin dashboard, job tracking, stale-job
alerting. Deliberately has no import-time dependency on app.py, same
isolation convention as nudges/nudge_engine.py and the emotional_intelligence/
scripts: nothing outside this package is ever required to change when ops/
internals change. The only things another file needs from here are:

  - ops.tracking.record_heartbeat / record_fcm_send / start_job_run /
    finish_job_run / record_completed_run -- stable call sites other
    backend code (app.py, run_daily_ei_pipeline.py, nudge_engine.py) calls
    into to report job activity.
  - ops.dashboard.admin_bp -- the Flask Blueprint, registered once via
    app.register_blueprint(admin_bp).
  - ops.alerts.ops_alert_worker -- started once as a background thread,
    same pattern as every other worker in app.py.
"""
