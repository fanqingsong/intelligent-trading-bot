import { stepLabel } from "../lib/status";
import PrefectLink from "./PrefectLink";

export type JobProgressInfo = {
  kind?: string;
  status?: string;
  current_step?: string;
  progress?: number | string;
  steps?: string[];
  step_index?: number;
  step_total?: number;
  prefect_ui_url?: string | null;
  error?: string;
};

type Props = {
  job?: JobProgressInfo | null;
  compact?: boolean;
};

function activeLabel(job: JobProgressInfo) {
  const current = job.current_step || "";
  if (current) return stepLabel(current);
  if (job.status === "queued") return "排队中";
  if (job.status === "running") return "启动中";
  return stepLabel(current);
}

export default function JobProgress({ job, compact = false }: Props) {
  if (!job) return null;
  const steps = job.steps || [];
  const current = job.current_step || "";
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const idx = job.step_index || (current && steps.includes(current) ? steps.indexOf(current) + 1 : 0);
  const total = job.step_total || steps.length;
  const kindLabel =
    job.kind === "predict"
      ? "预测"
      : job.kind === "download"
        ? "数据更新"
        : job.kind === "train"
          ? "训练"
          : "任务";
  const label = activeLabel(job);

  if (compact) {
    return (
      <div className="job-progress compact">
        <div className="muted" style={{ fontSize: "0.8rem" }}>
          {label}
          {total > 0 ? ` · ${idx || "—"}/${total}` : ""} · {progress}%
        </div>
        <div className="progress-track" aria-hidden>
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>
      </div>
    );
  }

  return (
    <div className="job-progress">
      <div className="btn-row" style={{ marginBottom: "0.5rem", alignItems: "center" }}>
        <strong>
          {kindLabel}进行中：{label}
        </strong>
        <span className="muted">
          {total > 0 ? `${idx || 0}/${total} 步` : ""} · {progress}%
        </span>
        <PrefectLink url={job.prefect_ui_url} />
      </div>
      <div className="progress-track" aria-label={`进度 ${progress}%`}>
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
      {steps.length > 0 && (
        <div className="step-rail" aria-label="步骤进度">
          {steps.map((step, i) => {
            const n = i + 1;
            let state = "pending";
            if (job.status === "completed" || (current && n < idx)) state = "done";
            else if (step === current || (job.status === "running" && n === idx)) state = "active";
            else if (job.status === "queued" && n === 1 && !current) state = "active";
            return (
              <span key={step} className={`step-chip ${state}`} title={step}>
                {stepLabel(step)}
              </span>
            );
          })}
        </div>
      )}
      {job.error && (
        <p className="error" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
          {job.error}
        </p>
      )}
    </div>
  );
}
