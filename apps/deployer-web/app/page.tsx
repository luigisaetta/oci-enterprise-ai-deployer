"use client";

/*
 * Author: L. Saetta
 * Version: 0.1.0
 * Last modified: 2026-05-05
 * License: MIT
 */

import {
  CheckCircle2,
  ChevronDown,
  FileCode2,
  FileText,
  Play,
  RotateCcw,
  Save,
  Settings2,
  TerminalSquare,
  UploadCloud,
} from "lucide-react";
import { ChangeEvent, useMemo, useRef, useState } from "react";

type FileKind = "yaml" | "env";

type UploadedFile = {
  name: string;
  size: number;
  content: string;
};

type ActionKey = "validate" | "render" | "dry-run" | "build" | "deploy";

type RunEvent = {
  id: number;
  kind: "status" | "log" | "done" | "error";
  level?: "info" | "success" | "warning" | "error";
  message: string;
};

const ACTIONS: Record<ActionKey, string> = {
  validate: "Validate configuration",
  render: "Render JSON artifacts",
  "dry-run": "Review dry run",
  build: "Build container image",
  deploy: "Deploy",
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_DEPLOYER_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_DEPLOYER_API_KEY ?? "";

const SAMPLE_YAML = `application:
  name: my-agent-app-dev
  compartment_name: my-compartment
  region: eu-frankfurt-1
  region_key: fra

container:
  context: examples/hello_world_container
  dockerfile: Dockerfile
  image_repository: enterprise-ai/sample-agent
  tag_strategy: explicit
  ocir_namespace: auto
  tag: dev

hosted_application:
  display_name: Sample Agent App
  security:
    auth_type: NO_AUTH

hosted_deployment:
  display_name: Sample Agent Deployment
`;

const SAMPLE_ENV = `MY_AGENT_API_KEY=replace-with-local-development-secret
LOG_LEVEL=INFO
`;

function formatBytes(size: number) {
  if (!size) {
    return "0 B";
  }
  const units = ["B", "KB", "MB"];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), 2);
  return `${(size / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function fileSummary(file: UploadedFile | null, fallback: string) {
  if (!file) {
    return fallback;
  }
  return `${file.name} · ${formatBytes(file.size)}`;
}

function apiHeaders() {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }
  return headers;
}

export default function DeployerConsole() {
  const [yamlFile, setYamlFile] = useState<UploadedFile | null>(null);
  const [envFile, setEnvFile] = useState<UploadedFile | null>(null);
  const [yamlContent, setYamlContent] = useState(SAMPLE_YAML);
  const [envContent, setEnvContent] = useState(SAMPLE_ENV);
  const [selectedAction, setSelectedAction] = useState<ActionKey>("validate");
  const [profile, setProfile] = useState("DEFAULT");
  const [region, setRegion] = useState("eu-frankfurt-1");
  const [outputDir, setOutputDir] = useState("enterprise_ai_deployment/generated");
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [runState, setRunState] = useState<"idle" | "running" | "succeeded" | "failed">(
    "idle",
  );
  const streamControllerRef = useRef<AbortController | null>(null);
  const eventCounterRef = useRef(0);

  const readiness = useMemo(() => {
    const checks = [
      { label: "YAML file", ready: yamlContent.trim().length > 0 },
      { label: "Environment file", ready: envContent.trim().length > 0 },
      { label: "OCI profile", ready: profile.trim().length > 0 },
      { label: "Region", ready: region.trim().length > 0 },
    ];
    return checks;
  }, [envContent, profile, region, yamlContent]);

  const canRun = readiness.every((item) => item.ready);

  function handleUpload(kind: FileKind, event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result ?? "");
      const uploaded = {
        name: file.name,
        size: file.size,
        content,
      };

      if (kind === "yaml") {
        setYamlFile(uploaded);
        setYamlContent(content);
      } else {
        setEnvFile(uploaded);
        setEnvContent(content);
      }
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  function resetSamples() {
    setYamlFile(null);
    setEnvFile(null);
    setYamlContent(SAMPLE_YAML);
    setEnvContent(SAMPLE_ENV);
    setRunEvents([]);
    setRunState("idle");
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
  }

  function pushRunEvent(event: Omit<RunEvent, "id">) {
    eventCounterRef.current += 1;
    setRunEvents((current) => [
      ...current,
      {
        id: eventCounterRef.current,
        ...event,
      },
    ]);
  }

  async function runAction() {
    if (!canRun) {
      return;
    }

    streamControllerRef.current?.abort();
    eventCounterRef.current = 0;
    setRunEvents([]);
    setRunState("running");
    pushRunEvent({
      kind: "status",
      message: `Starting ${ACTIONS[selectedAction].toLowerCase()}.`,
    });

    try {
      const response = await fetch(`${API_BASE_URL}/api/actions/preview`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          yaml: yamlContent,
          env: envContent,
          action: selectedAction,
          profile,
          region,
          output_dir: outputDir,
        }),
      });

      if (!response.ok) {
        throw new Error(`Backend returned HTTP ${response.status}`);
      }

      const data = (await response.json()) as { run_id: string };
      const controller = new AbortController();
      streamControllerRef.current = controller;
      const streamResponse = await fetch(
        `${API_BASE_URL}/api/runs/${data.run_id}/events`,
        {
          headers: apiHeaders(),
          signal: controller.signal,
        },
      );

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error(`Backend stream returned HTTP ${streamResponse.status}`);
      }

      const reader = streamResponse.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";

        for (const chunk of chunks) {
          handleStreamEvent(chunk);
        }
      }

      if (buffer.trim()) {
        handleStreamEvent(buffer);
      }
      streamControllerRef.current = null;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setRunState("failed");
      pushRunEvent({
        kind: "error",
        level: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to start the preview run.",
      });
    }
  }

  function handleStreamEvent(chunk: string) {
    const lines = chunk.split("\n");
    const eventType = lines
      .find((line) => line.startsWith("event: "))
      ?.slice("event: ".length);
    const data = lines
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice("data: ".length))
      .join("\n");

    if (!eventType || !data) {
      return;
    }

    if (eventType === "status") {
      const payload = JSON.parse(data) as {
        state: string;
        step: string;
      };
      pushRunEvent({
        kind: "status",
        message: `Status: ${payload.state} · Step: ${payload.step}`,
      });
      return;
    }

    if (eventType === "log") {
      const payload = JSON.parse(data) as {
        level: RunEvent["level"];
        message: string;
      };
      pushRunEvent({
        kind: "log",
        level: payload.level ?? "info",
        message: payload.message,
      });
      return;
    }

    if (eventType === "done") {
      const payload = JSON.parse(data) as {
        state: "succeeded" | "failed";
        message: string;
      };
      setRunState(payload.state);
      pushRunEvent({
        kind: "done",
        level: payload.state === "succeeded" ? "success" : "error",
        message: payload.message,
      });
    }
  }

  return (
    <main className="workspace">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">
            <UploadCloud size={22} aria-hidden="true" />
          </div>
          <div>
            <h1>OCI Enterprise AI Deployer</h1>
            <p>Hosted apps console</p>
          </div>
        </div>

        <section className="panel">
          <div className="panelTitle">
            <UploadCloud size={18} aria-hidden="true" />
            <h2>Upload Files</h2>
          </div>
          <label className="fileDrop">
            <FileCode2 size={18} aria-hidden="true" />
            <span>
              <strong>YAML configuration</strong>
              <small>{fileSummary(yamlFile, "Upload .yaml or .yml")}</small>
            </span>
            <input
              accept=".yaml,.yml,text/yaml,text/plain"
              type="file"
              onChange={(event) => handleUpload("yaml", event)}
            />
          </label>
          <label className="fileDrop">
            <FileText size={18} aria-hidden="true" />
            <span>
              <strong>Environment file</strong>
              <small>{fileSummary(envFile, "Upload .env")}</small>
            </span>
            <input
              accept=".env,text/plain"
              type="file"
              onChange={(event) => handleUpload("env", event)}
            />
          </label>
        </section>

        <section className="panel">
          <div className="panelTitle">
            <Settings2 size={18} aria-hidden="true" />
            <h2>Run Settings</h2>
          </div>
          <label className="field">
            <span>OCI profile</span>
            <input value={profile} onChange={(event) => setProfile(event.target.value)} />
          </label>
          <label className="field">
            <span>Region</span>
            <input value={region} onChange={(event) => setRegion(event.target.value)} />
          </label>
          <label className="field">
            <span>Output directory</span>
            <input
              value={outputDir}
              onChange={(event) => setOutputDir(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Action</span>
            <div className="selectWrap">
              <select
                value={selectedAction}
                onChange={(event) => setSelectedAction(event.target.value as ActionKey)}
              >
                {Object.entries(ACTIONS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} aria-hidden="true" />
            </div>
          </label>
        </section>

        <section className="panel compact">
          <h2>Readiness</h2>
          <div className="checks">
            {readiness.map((item) => (
              <div className="check" key={item.label}>
                <CheckCircle2
                  className={item.ready ? "readyIcon" : "mutedIcon"}
                  size={16}
                  aria-hidden="true"
                />
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </section>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Deployment workspace</p>
            <h2>Configuration files</h2>
          </div>
          <div className="actions">
            <button className="secondaryButton" type="button" onClick={resetSamples}>
              <RotateCcw size={17} aria-hidden="true" />
              Reset
            </button>
            <button className="secondaryButton" type="button">
              <Save size={17} aria-hidden="true" />
              Save Draft
            </button>
            <button
              className="primaryButton"
              type="button"
              onClick={runAction}
              disabled={!canRun}
            >
              <Play size={17} aria-hidden="true" />
              Run Preview
            </button>
          </div>
        </header>

        <section className="mainStage">
          <div className="stageHeader">
            <div>
              <h3>File contents</h3>
              <p>Editable YAML and environment inputs</p>
            </div>
            <div className="stagePills">
              <span>YAML</span>
              <span>ENV</span>
            </div>
          </div>

          <div className="editorGrid">
            <article className="editorPane yamlPane">
              <div className="editorHeader">
                <div>
                  <h3>YAML Configuration</h3>
                  <p>{fileSummary(yamlFile, "Sample content loaded")}</p>
                </div>
                <span>{yamlContent.split("\n").length} lines</span>
              </div>
              <textarea
                spellCheck={false}
                value={yamlContent}
                onChange={(event) => setYamlContent(event.target.value)}
                aria-label="Editable YAML configuration"
              />
            </article>

            <article className="editorPane envPane">
              <div className="editorHeader">
                <div>
                  <h3>Environment File</h3>
                  <p>{fileSummary(envFile, "Sample content loaded")}</p>
                </div>
                <span>{envContent.split("\n").length} lines</span>
              </div>
              <textarea
                spellCheck={false}
                value={envContent}
                onChange={(event) => setEnvContent(event.target.value)}
                aria-label="Editable environment file"
              />
            </article>
          </div>
        </section>

        <section className="runPanel">
          <div>
            <div className="runTitle">
              <TerminalSquare size={18} aria-hidden="true" />
              <h3>{ACTIONS[selectedAction]}</h3>
            </div>
            <p>
              Profile <strong>{profile || "Not set"}</strong> · Region{" "}
              <strong>{region || "Not set"}</strong> · Output{" "}
              <strong>{outputDir || "Not set"}</strong>
            </p>
            <span className={`runBadge ${runState}`}>{runState}</span>
          </div>
          <div className="runLog">
            {runEvents.length === 0 ? (
              <p className="emptyLog">No action has been launched in this UI session.</p>
            ) : (
              runEvents.map((event) => (
                <div className={`logLine ${event.level ?? event.kind}`} key={event.id}>
                  <span>{event.kind}</span>
                  <p>{event.message}</p>
                </div>
              ))
            )}
          </div>
        </section>
      </section>
    </main>
  );
}
