# RIVA Tauri Frontend Integration Guide

This document guides building a Tauri frontend for RIVA. It assumes you've read `API_REFERENCE.md` and `INTERNAL_ARCHITECTURE.md`.

---

## Prerequisites

- Tauri 1.x or 2.x with Rust backend
- TypeScript/JavaScript frontend
- Unix socket support (Linux/macOS)
- Ability to invoke Rust commands from Tauri frontend

---

## Communication Layer

### 1. Create a Rust RPC Client

**File:** `src-tauri/src/rpc.rs`

```rust
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::io::{Read, Write};
use byteorder::{BigEndian, WriteBytesExt, ReadBytesExt};

pub async fn call_riva(
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    // Connect to Unix socket
    let mut stream = UnixStream::connect(
        shellexpand::tilde("~/.talkingrock/riva.sock")
            .as_ref()
    ).map_err(|e| format!("Socket connect failed: {}", e))?;

    // Build JSON-RPC request
    let request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    });

    let json_bytes = serde_json::to_vec(&request)
        .map_err(|e| format!("JSON serialization failed: {}", e))?;

    // Send length-prefixed message
    let mut length_buf = Vec::with_capacity(4);
    length_buf.write_u32::<BigEndian>(json_bytes.len() as u32)
        .map_err(|e| format!("Length encoding failed: {}", e))?;

    stream.write_all(&length_buf)
        .map_err(|e| format!("Socket write (length) failed: {}", e))?;
    stream.write_all(&json_bytes)
        .map_err(|e| format!("Socket write (body) failed: {}", e))?;
    stream.flush()
        .map_err(|e| format!("Socket flush failed: {}", e))?;

    // Read response length
    let mut len_buf = [0u8; 4];
    stream.read_exact(&mut len_buf)
        .map_err(|e| format!("Socket read (length) failed: {}", e))?;
    let resp_len = (&len_buf[..]).read_u32::<BigEndian>()
        .map_err(|e| format!("Length decoding failed: {}", e))? as usize;

    // Read response body
    let mut body = vec![0u8; resp_len];
    stream.read_exact(&mut body)
        .map_err(|e| format!("Socket read (body) failed: {}", e))?;

    stream.shutdown(Shutdown::Both)
        .map_err(|e| format!("Socket shutdown failed: {}", e))?;

    // Parse response
    let response: serde_json::Value = serde_json::from_slice(&body)
        .map_err(|e| format!("Response JSON parsing failed: {}", e))?;

    // Check for error
    if let Some(err) = response.get("error") {
        return Err(format!("RPC error: {}", err));
    }

    Ok(response.get("result").unwrap_or(&serde_json::json!(null)).clone())
}
```

### 2. Create Tauri Commands

**File:** `src-tauri/src/main.rs` (additions)

```rust
mod rpc;

#[tauri::command]
async fn ping() -> Result<String, String> {
    let result = rpc::call_riva("riva/ping", serde_json::json!({})).await?;
    Ok(result["result"].as_str().unwrap_or("unknown").to_string())
}

#[tauri::command]
async fn create_project(
    name: String,
    description: String,
    act_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let params = serde_json::json!({
        "name": name,
        "description": description,
        "act_id": act_id
    });
    rpc::call_riva("riva/projects/create", params).await
}

#[tauri::command]
async fn list_agents() -> Result<serde_json::Value, String> {
    rpc::call_riva("riva/agents/list", serde_json::json!({})).await
}

// ... (similar for other methods)

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            ping,
            create_project,
            list_agents,
            // ... (register all commands)
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

### 3. Create TypeScript Bindings

**File:** `src/lib/riva.ts`

```typescript
import { invoke } from "@tauri-apps/api/tauri";

export interface RivaProject {
  id: string;
  name: string;
  description: string;
  act_id?: string;
  act_title?: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface RivaPlan {
  id: string;
  project_id: string;
  title: string;
  user_request: string;
  status: "draft" | "decomposing" | "ready";
  estimated_minutes: number;
  risks: string[];
  steps: PlanStep[];
  created_at: string;
  updated_at: string;
}

export interface PlanStep {
  id: string;
  step_number: number;
  title: string;
  description: string;
  acceptance_criterion: string;
  estimated_minutes?: number;
  status: string;
}

// ... (similar interfaces for Contract, Agent, Audit, etc.)

export const riva = {
  async ping(): Promise<string> {
    return invoke("ping");
  },

  async createProject(
    name: string,
    description: string,
    actId?: string
  ): Promise<RivaProject> {
    return invoke("create_project", { name, description, act_id: actId });
  },

  async listProjects(status?: string): Promise<RivaProject[]> {
    const result = await invoke("list_projects", { status });
    return result.projects;
  },

  async listPlans(projectId: string, status?: string): Promise<RivaPlan[]> {
    const result = await invoke("list_plans", { project_id: projectId, status });
    return result.plans;
  },

  // ... (similar for other methods)
};
```

---

## UI State Management

### Recommended Architecture

```typescript
// src/lib/store.ts (using SvelteKit stores or similar)

import { writable } from "svelte/store";
import { riva } from "./riva";

export interface AppState {
  projects: Map<string, RivaProject>;
  plans: Map<string, RivaPlan>;
  contracts: Map<string, RivaContract>;
  agents: Map<string, Agent>;
  activeSessions: Map<string, SessionState>;
  audits: Map<string, Audit>;
  loading: boolean;
  error: string | null;
}

interface SessionState {
  sessionId: string;
  agentId: string;
  contractId: string;
  events: StreamEvent[];
  nextIndex: number;
  status: "running" | "stopped";
  assistantText: string;
}

interface StreamEvent {
  type:
    | "user"
    | "assistant_delta"
    | "tool_use"
    | "tool_result"
    | "error"
    | "done";
  text?: string;
  tool?: string;
  input?: string;
  is_error?: boolean;
}

// Create stores
export const appState = writable<AppState>({
  projects: new Map(),
  plans: new Map(),
  contracts: new Map(),
  agents: new Map(),
  activeSessions: new Map(),
  audits: new Map(),
  loading: false,
  error: null,
});

// Actions
export const actions = {
  async loadProjects(status?: string) {
    appState.update((s) => ({ ...s, loading: true }));
    try {
      const projects = await riva.listProjects(status);
      appState.update((s) => ({
        ...s,
        projects: new Map(projects.map((p) => [p.id, p])),
        loading: false,
        error: null,
      }));
    } catch (err) {
      appState.update((s) => ({
        ...s,
        loading: false,
        error: String(err),
      }));
    }
  },

  async createProject(name: string, description: string) {
    try {
      const project = await riva.createProject(name, description);
      appState.update((s) => {
        s.projects.set(project.id, project);
        return s;
      });
      return project;
    } catch (err) {
      appState.update((s) => ({ ...s, error: String(err) }));
      throw err;
    }
  },

  async deployAgent(contractId: string, agentId: string) {
    const sessionId = `tmp-${Date.now()}`;
    const sessionState: SessionState = {
      sessionId,
      agentId,
      contractId,
      events: [],
      nextIndex: 0,
      status: "running",
      assistantText: "",
    };

    appState.update((s) => {
      s.activeSessions.set(sessionId, sessionState);
      return s;
    });

    try {
      const response = await riva.deploySession(contractId, agentId);
      appState.update((s) => {
        const session = s.activeSessions.get(sessionId);
        if (session) {
          session.sessionId = response.session_id;
        }
        return s;
      });

      // Start polling
      await actions.pollSession(sessionId, agentId);
    } catch (err) {
      appState.update((s) => {
        s.activeSessions.delete(sessionId);
        s.error = String(err);
        return s;
      });
      throw err;
    }
  },

  async pollSession(sessionId: string, agentId: string) {
    let since = 0;
    let backoffMs = 200;

    while (true) {
      try {
        const result = await riva.pollSession(agentId, since);

        appState.update((s) => {
          const session = s.activeSessions.get(sessionId);
          if (session) {
            // Add new events
            session.events.push(...result.events);
            session.nextIndex = result.next_index;

            // Accumulate assistant text
            for (const event of result.events) {
              if (event.type === "assistant_delta") {
                session.assistantText += event.text || "";
              }
            }

            // Check for completion
            if (
              result.events.some((e) => e.type === "done") ||
              (!result.busy && result.events.length === 0)
            ) {
              session.status = "stopped";
              // Trigger auto-audit here if desired
              return s;
            }
          }
          return s;
        });

        since = result.next_index;

        // Check if done
        const state = appState.get();
        const session = state.activeSessions.get(sessionId);
        if (!session || session.status === "stopped") {
          break;
        }

        // Exponential backoff
        await new Promise((r) => setTimeout(r, backoffMs));
        backoffMs = Math.min(backoffMs * 1.5, 2000);
      } catch (err) {
        appState.update((s) => {
          s.error = String(err);
          const session = s.activeSessions.get(sessionId);
          if (session) {
            session.status = "stopped";
          }
          return s;
        });
        break;
      }
    }
  },
};
```

---

## Component Examples

### Project List

```svelte
<!-- src/components/ProjectList.svelte -->
<script lang="ts">
  import { appState, actions } from "../lib/store";
  import { onMount } from "svelte";

  let creating = false;
  let newProjectName = "";
  let newProjectDescription = "";

  onMount(() => {
    actions.loadProjects("active");
  });

  async function handleCreate() {
    creating = true;
    try {
      await actions.createProject(newProjectName, newProjectDescription);
      newProjectName = "";
      newProjectDescription = "";
    } catch (err) {
      console.error(err);
    }
    creating = false;
  }
</script>

<div class="projects">
  <h2>Projects</h2>

  {#if $appState.loading}
    <p>Loading...</p>
  {/if}

  {#if $appState.error}
    <div class="error">{$appState.error}</div>
  {/if}

  <form on:submit|preventDefault={handleCreate}>
    <input
      type="text"
      placeholder="Project name"
      bind:value={newProjectName}
      disabled={creating}
    />
    <textarea
      placeholder="Description"
      bind:value={newProjectDescription}
      disabled={creating}
    />
    <button type="submit" disabled={creating || !newProjectName}>
      Create Project
    </button>
  </form>

  <ul>
    {#each Array.from($appState.projects.values()) as project (project.id)}
      <li>
        <strong>{project.name}</strong>
        <p>{project.description}</p>
        <small>{project.status}</small>
      </li>
    {/each}
  </ul>
</div>

<style>
  .projects {
    padding: 1rem;
  }
  form {
    margin: 1rem 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  input,
  textarea,
  button {
    padding: 0.5rem;
  }
  button {
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .error {
    color: red;
    padding: 1rem;
    background: #fee;
    border-radius: 4px;
  }
</style>
```

### Session Monitor

```svelte
<!-- src/components/SessionMonitor.svelte -->
<script lang="ts">
  import { appState, actions } from "../lib/store";
  import type { SessionState } from "../lib/store";

  export let session: SessionState;

  async function handleStop() {
    await actions.riva.stopSession(session.agentId);
    appState.update((s) => {
      const sess = s.activeSessions.get(session.sessionId);
      if (sess) {
        sess.status = "stopped";
      }
      return s;
    });
  }
</script>

<div class="session">
  <h3>Session {session.sessionId}</h3>
  <p>
    Agent: <code>{session.agentId}</code>
  </p>
  <p>Status: <strong>{session.status}</strong></p>

  <div class="events-log">
    {#each session.events as event (event.id)}
      {#if event.type === "user"}
        <div class="event user">
          <strong>User:</strong>
          <pre>{event.text}</pre>
        </div>
      {:else if event.type === "assistant_delta"}
        <div class="event assistant">
          <strong>Assistant:</strong>
          <span>{event.text}</span>
        </div>
      {:else if event.type === "tool_use"}
        <div class="event tool-use">
          <strong>Tool:</strong>
          <code>{event.tool}</code>
          <pre>{event.input}</pre>
        </div>
      {:else if event.type === "tool_result"}
        <div class="event tool-result" class:error={event.is_error}>
          <strong>{event.is_error ? "Error" : "Result"}:</strong>
          <pre>{event.text}</pre>
        </div>
      {:else if event.type === "done"}
        <div class="event done">Session complete</div>
      {/if}
    {/each}
  </div>

  {#if session.status === "running"}
    <button on:click={handleStop}>Stop Agent</button>
  {/if}
</div>

<style>
  .session {
    border: 1px solid #ccc;
    padding: 1rem;
    margin: 1rem 0;
    border-radius: 4px;
  }
  .events-log {
    max-height: 500px;
    overflow-y: auto;
    background: #f9f9f9;
    padding: 0.5rem;
    border-radius: 4px;
    margin: 1rem 0;
  }
  .event {
    margin: 0.5rem 0;
    padding: 0.5rem;
    border-left: 3px solid #999;
  }
  .event.user {
    border-left-color: #00a;
    background: #eef;
  }
  .event.assistant {
    border-left-color: #0a0;
    background: #efe;
  }
  .event.tool-use {
    border-left-color: #aa0;
    background: #ffe;
  }
  .event.tool-result {
    border-left-color: #0aa;
    background: #eff;
  }
  .event.tool-result.error {
    border-left-color: #a00;
    background: #fee;
  }
  .event.done {
    border-left-color: #0a0;
    background: #efe;
    font-weight: bold;
  }
  pre,
  code {
    background: #f0f0f0;
    padding: 0.25rem 0.5rem;
    border-radius: 2px;
    overflow-x: auto;
  }
  button {
    padding: 0.5rem 1rem;
    cursor: pointer;
  }
</style>
```

---

## Error Handling

### Entry Guard Rejection

The entry guard returns code -32001 with detailed context:

```typescript
async function createPlan(projectId: string, userRequest: string) {
  try {
    const plan = await riva.createPlan(projectId, userRequest);
    return plan;
  } catch (err: any) {
    if (err.code === -32001) {
      // Entry guard rejection
      const guardType = err.data?.guard_type; // "safety" or "intent"
      const reason = err.data?.reason;

      if (guardType === "safety") {
        showAlert(
          `Safety Check Failed\n\nYour request contains patterns that pose a security risk.\n\nDetails: ${reason}`
        );
      } else if (guardType === "intent") {
        showAlert(
          `Intent Check Failed\n\nYour request doesn't align with the project scope.\n\nDetails: ${reason}`
        );
      }
      return null;
    }
    // Handle other errors
    throw err;
  }
}
```

### Handling Session Errors

```typescript
// In pollSession
if (result.events.some((e) => e.type === "error")) {
  const errorEvent = result.events.find((e) => e.type === "error");
  appState.update((s) => {
    s.error = `Agent error: ${errorEvent?.text}`;
    return s;
  });
}
```

---

## Testing Locally

### Start RIVA Service

```bash
cd /home/kellogg/dev/RIVA
.venv/bin/python -m riva.service
```

### Test via curl

```bash
# Create a request
python3 << 'EOF'
import json, struct, socket
from pathlib import Path

request = {
    "jsonrpc": "2.0",
    "method": "riva/ping",
    "params": {},
    "id": 1
}

json_str = json.dumps(request)
json_bytes = json_str.encode("utf-8")
length_bytes = struct.pack("!I", len(json_bytes))

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(str(Path.home() / ".talkingrock" / "riva.sock"))
sock.sendall(length_bytes + json_bytes)

resp_len_bytes = sock.recv(4)
resp_len = struct.unpack("!I", resp_len_bytes)[0]
resp_bytes = sock.recv(resp_len)
resp = json.loads(resp_bytes)
print(json.dumps(resp, indent=2))
sock.close()
EOF
```

---

## Deployment Checklist

- [ ] RIVA service is running (`riva-service` or systemd unit)
- [ ] Unix socket is at `~/.talkingrock/riva.sock`
- [ ] Tauri backend has `byteorder` and `serde_json` dependencies
- [ ] All RPC command handlers are registered in Tauri
- [ ] TypeScript types match API responses
- [ ] Error handling includes entry guard rejection (code -32001)
- [ ] Session polling uses exponential backoff
- [ ] State management is non-blocking
- [ ] Agent events are displayed in real-time
- [ ] Tests cover happy path and error cases

---

## Performance Considerations

1. **Polling interval:** Start at 200ms, exponential backoff to 2s
2. **Event buffer:** Capped at 10,000 entries in CCManager; old events pruned
3. **History queries:** Use limit parameter (default 100)
4. **Plan decomposition:** Async; poll status until ready
5. **Audit trigger:** Can be slow (git operations); show progress indicator
6. **Agent properties sync:** Synchronous, may take 100ms+

---

## Troubleshooting

### Socket Connection Refused

```
Error: Socket connect failed: No such file or directory
```

**Solution:** Ensure RIVA service is running and socket exists:

```bash
ls -la ~/.talkingrock/riva.sock
ps aux | grep riva-service
```

### Timeout on Plan Creation

**Solution:** Ollama may be slow. Increase timeout or show loading indicator:

```typescript
const timeoutPromise = new Promise((_, reject) =>
  setTimeout(
    () => reject(new Error("Request timeout after 30s")),
    30000
  )
);
```

### Agent Process Not Spawning

**Solution:** Check that `claude` CLI is in PATH:

```bash
which claude
claude --help
```

### Event Polling Infinite Loop

**Solution:** Ensure you exit loop when `status="stopped"` or `busy=false && events.length === 0`:

```typescript
if (!result.busy && result.events.length === 0) {
  console.log("Agent idle, stopping poll");
  break;
}
```

