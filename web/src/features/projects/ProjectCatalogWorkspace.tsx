import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  createLaunchProfile,
  createProject,
  deleteLaunchProfile,
  fetchProjectCatalog,
  fetchProjectDetail,
  moveProjectSession,
  previewProjectRelocation,
  projectCatalogKeys,
  relocateProject,
  removeProject,
  renameProject,
  updateLaunchProfile,
} from "./api";
import {
  emptyProjectProfileDraft,
  launchResolutionForProfile,
  profileDraft,
  selectedProjectId,
  type ProjectCatalogDetail,
  type ProjectCatalogEntry,
  type ProjectLaunchProfile,
  type ProjectLaunchResolution,
  type ProjectProfileDraft,
  type ProjectRelocationPreview,
  type ProjectSessionSummary,
} from "./model";
import "./projects.css";

export function ProjectCatalogWorkspace() {
  const queryClient = useQueryClient();
  const catalog = useQuery({
    queryKey: projectCatalogKeys.all,
    queryFn: ({ signal }) => fetchProjectCatalog(signal),
  });
  const [requestedProjectId, setRequestedProjectId] = useState<string | null>(null);
  const activeProjectId = selectedProjectId(
    catalog.data?.projects ?? [],
    requestedProjectId,
  );
  const detail = useQuery({
    queryKey: projectCatalogKeys.detail(activeProjectId ?? "none"),
    queryFn: ({ signal }) => fetchProjectDetail(activeProjectId ?? "", signal),
    enabled: activeProjectId !== null,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: projectCatalogKeys.all });
    if (activeProjectId !== null) {
      void queryClient.invalidateQueries({
        queryKey: projectCatalogKeys.detail(activeProjectId),
      });
    }
  };

  if (catalog.isPending) {
    return <div className="project-workspace-state" aria-busy="true">Loading projects…</div>;
  }
  if (catalog.isError || catalog.data === undefined) {
    return <div className="project-workspace-state error">Project Catalog is unavailable.</div>;
  }

  return (
    <section className="project-workspace">
      <header className="project-workspace-header">
        <div>
          <span className="project-eyebrow">Native Agent Gateway</span>
          <h1>Projects</h1>
          <p>Group sessions, keep soft launch defaults, and launch explicitly.</p>
        </div>
        <span className="project-boundary-badge">Metadata only</span>
      </header>
      <div className="project-workspace-grid">
        <aside className="project-catalog-pane">
          <ProjectCreateForm
            onCreated={(project) => {
              setRequestedProjectId(project.catalog_project_id);
              refresh();
            }}
          />
          <div className="project-catalog-list" aria-label="Project Catalog">
            {catalog.data.projects.length === 0 ? (
              <div className="project-empty">Add a project before its first session.</div>
            ) : (
              catalog.data.projects.map((project) => (
                <button
                  className={project.catalog_project_id === activeProjectId ? "selected" : ""}
                  key={project.catalog_project_id}
                  onClick={() => setRequestedProjectId(project.catalog_project_id)}
                  type="button"
                >
                  <strong>{project.display_name}</strong>
                  <span>{project.session_count}{project.session_count_truncated ? "+" : ""} sessions</span>
                  <small>{project.location.canonical_path ?? "Unresolved location"}</small>
                </button>
              ))
            )}
          </div>
        </aside>
        <main className="project-detail-pane">
          {activeProjectId === null ? (
            <div className="project-detail-empty">
              <h2>No project selected</h2>
              <p>Catalog entries may exist before any sessions are created.</p>
            </div>
          ) : detail.isPending ? (
            <div className="project-workspace-state" aria-busy="true">Loading project…</div>
          ) : detail.isError || detail.data === undefined ? (
            <div className="project-workspace-state error">Project detail is unavailable.</div>
          ) : (
            <ProjectDetail
              detail={detail.data}
              key={`${detail.data.project.catalog_project_id}:${detail.data.project.revision}`}
              onRemoved={() => {
                setRequestedProjectId(null);
                refresh();
              }}
              onUpdated={refresh}
              projects={catalog.data.projects}
            />
          )}
        </main>
      </div>
    </section>
  );
}

function ProjectCreateForm({
  onCreated,
}: {
  onCreated: (project: ProjectCatalogEntry) => void;
}) {
  const [path, setPath] = useState("");
  const [displayName, setDisplayName] = useState("");
  const mutation = useMutation({
    mutationFn: () => createProject(path, displayName),
    onSuccess: (project) => {
      setPath("");
      setDisplayName("");
      onCreated(project);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (path.trim() !== "" && displayName.trim() !== "") mutation.mutate();
  };
  return (
    <form className="project-create-form" onSubmit={submit}>
      <label>
        <span>Name</span>
        <input
          maxLength={200}
          onChange={(event) => setDisplayName(event.target.value)}
          placeholder="Payments API"
          required
          value={displayName}
        />
      </label>
      <label>
        <span>Local path</span>
        <input
          maxLength={4096}
          onChange={(event) => setPath(event.target.value)}
          placeholder="/workspace/payments"
          required
          value={path}
        />
      </label>
      <button disabled={mutation.isPending} type="submit">Add project</button>
      <MutationStatus mutation={mutation} />
    </form>
  );
}

function ProjectDetail({
  detail,
  onRemoved,
  onUpdated,
  projects,
}: {
  detail: ProjectCatalogDetail;
  onRemoved: () => void;
  onUpdated: () => void;
  projects: readonly ProjectCatalogEntry[];
}) {
  const project = detail.project;
  const [name, setName] = useState(project.display_name);
  const rename = useMutation({
    mutationFn: () => renameProject(project, name),
    onSuccess: onUpdated,
  });
  const remove = useMutation({
    mutationFn: () => removeProject(project),
    onSuccess: onRemoved,
  });
  return (
    <div className="project-detail">
      <div className="project-detail-heading">
        <div>
          <span className={`project-state ${project.state}`}>{project.state}</span>
          <h2>{project.display_name}</h2>
          <code>{project.catalog_project_id}</code>
        </div>
        <div className="project-stat">
          <strong>{project.session_count}{project.session_count_truncated ? "+" : ""}</strong>
          <span>sessions</span>
        </div>
      </div>
      <section className="project-card">
        <h3>Catalog metadata</h3>
        <form
          className="project-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim() !== "") rename.mutate();
          }}
        >
          <label>
            <span>Display name</span>
            <input maxLength={200} onChange={(event) => setName(event.target.value)} value={name} />
          </label>
          <button disabled={rename.isPending || name.trim() === project.display_name} type="submit">Rename</button>
        </form>
        <ProjectRelocationEditor project={project} onUpdated={onUpdated} />
        <div className="project-destructive-row">
          <span>Removing an entry preserves repository and session data.</span>
          <button className="danger" disabled={remove.isPending} onClick={() => remove.mutate()} type="button">Remove entry</button>
        </div>
        <MutationStatus mutation={rename} />
        <MutationStatus mutation={remove} />
      </section>
      <section className="project-card">
        <div className="project-card-heading">
          <div>
            <h3>Sessions</h3>
            <p>Move existing sessions between catalog groups or explicitly leave them unfiled.</p>
          </div>
          <span>{detail.sessions.length}{detail.sessions_truncated ? "+" : ""}</span>
        </div>
        <div className="project-session-list">
          {detail.sessions.length === 0 ? (
            <div className="project-empty compact">No sessions are grouped here.</div>
          ) : detail.sessions.map((session) => (
            <ProjectSessionMover
              key={`${session.id}:${session.updated_at}`}
              onUpdated={onUpdated}
              projects={projects}
              session={session}
            />
          ))}
        </div>
      </section>
      <section className="project-card">
        <div className="project-card-heading">
          <div>
            <h3>Launch profiles</h3>
            <p>Hints are previewed and never grant authority or inject provider arguments.</p>
          </div>
          <span>{detail.launch_profiles.length}{detail.has_more_profiles ? "+" : ""}</span>
        </div>
        <LaunchProfileCreateForm projectId={project.catalog_project_id} onUpdated={onUpdated} />
        <div className="launch-profile-list">
          {detail.launch_profiles.length === 0 ? (
            <div className="project-empty compact">No launch profiles yet.</div>
          ) : detail.launch_profiles.map((profile) => (
            <LaunchProfileEditor
              key={`${profile.launch_profile_id}:${profile.revision}`}
              onUpdated={onUpdated}
              profile={profile}
              resolution={launchResolutionForProfile(
                profile,
                detail.launch_resolutions,
              )}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function ProjectSessionMover({
  onUpdated,
  projects,
  session,
}: {
  onUpdated: () => void;
  projects: readonly ProjectCatalogEntry[];
  session: ProjectSessionSummary;
}) {
  const [targetProjectId, setTargetProjectId] = useState("");
  const move = useMutation({
    mutationFn: () => moveProjectSession(session, targetProjectId || null),
    onSuccess: onUpdated,
  });
  return (
    <article className="project-session-row">
      <div>
        <strong>{session.title}</strong>
        <code>{session.id}</code>
      </div>
      <label>
        <span>Move to</span>
        <select
          onChange={(event) => setTargetProjectId(event.target.value)}
          value={targetProjectId}
        >
          <option value="">Unfiled</option>
          {projects.map((project) => (
            <option
              disabled={project.catalog_project_id === session.catalog_project_id}
              key={project.catalog_project_id}
              value={project.catalog_project_id}
            >
              {project.display_name}
            </option>
          ))}
        </select>
      </label>
      <button disabled={move.isPending} onClick={() => move.mutate()} type="button">
        Move session
      </button>
      <MutationStatus mutation={move} />
    </article>
  );
}

function ProjectRelocationEditor({
  project,
  onUpdated,
}: {
  project: ProjectCatalogEntry;
  onUpdated: () => void;
}) {
  const [path, setPath] = useState(project.location.path ?? "");
  const [preview, setPreview] = useState<ProjectRelocationPreview | null>(null);
  const previewMutation = useMutation({
    mutationFn: () => previewProjectRelocation(project, path),
    onSuccess: setPreview,
  });
  const applyMutation = useMutation({
    mutationFn: (confirmed: boolean) => {
      if (preview === null) throw new Error("Relocation preview is required.");
      return relocateProject(preview, confirmed);
    },
    onSuccess: () => {
      setPreview(null);
      onUpdated();
    },
  });
  return (
    <div className="project-relocation">
      <label>
        <span>Location</span>
        <input maxLength={4096} onChange={(event) => { setPath(event.target.value); setPreview(null); }} value={path} />
      </label>
      <button disabled={previewMutation.isPending || path.trim() === ""} onClick={() => previewMutation.mutate()} type="button">Preview relocation</button>
      {preview === null ? null : (
        <div className="relocation-preview">
          <span>{preview.old_location.canonical_path}</span>
          <strong aria-hidden="true">→</strong>
          <span>{preview.new_location.canonical_path}</span>
          <button
            disabled={applyMutation.isPending}
            onClick={() => applyMutation.mutate(!preview.identity_matches)}
            type="button"
          >
            {preview.identity_matches ? "Apply relocation" : "Confirm identity change"}
          </button>
        </div>
      )}
      <MutationStatus mutation={previewMutation} />
      <MutationStatus mutation={applyMutation} />
    </div>
  );
}

function LaunchProfileCreateForm({
  projectId,
  onUpdated,
}: {
  projectId: string;
  onUpdated: () => void;
}) {
  const [draft, setDraft] = useState<ProjectProfileDraft>({ ...emptyProjectProfileDraft });
  const mutation = useMutation({
    mutationFn: () => createLaunchProfile(projectId, draft),
    onSuccess: () => {
      setDraft({ ...emptyProjectProfileDraft });
      onUpdated();
    },
  });
  return (
    <ProfileForm
      action="Create profile"
      draft={draft}
      mutation={mutation}
      onChange={setDraft}
      onSubmit={() => mutation.mutate()}
    />
  );
}

function LaunchProfileEditor({
  profile,
  onUpdated,
  resolution,
}: {
  profile: ProjectLaunchProfile;
  onUpdated: () => void;
  resolution: ProjectLaunchResolution | null;
}) {
  const [draft, setDraft] = useState(() => profileDraft(profile));
  const update = useMutation({
    mutationFn: () => updateLaunchProfile(profile, draft),
    onSuccess: onUpdated,
  });
  const remove = useMutation({
    mutationFn: () => deleteLaunchProfile(profile),
    onSuccess: onUpdated,
  });
  return (
    <article className="launch-profile-editor">
      <LaunchResolutionSummary profile={profile} resolution={resolution} />
      <ProfileForm
        action="Save profile"
        draft={draft}
        mutation={update}
        onChange={setDraft}
        onSubmit={() => update.mutate()}
      />
      <button className="text-danger" disabled={remove.isPending} onClick={() => remove.mutate()} type="button">Delete profile</button>
      <MutationStatus mutation={remove} />
    </article>
  );
}

function LaunchResolutionSummary({
  profile,
  resolution,
}: {
  profile: ProjectLaunchProfile;
  resolution: ProjectLaunchResolution | null;
}) {
  if (resolution === null) {
    return <p className="launch-resolution missing">Launch resolution is unavailable.</p>;
  }
  return (
    <div className="launch-resolution">
      <div>
        <strong>Explicit launch preview</strong>
        <code>
          giga project launch {profile.catalog_project_id} --profile {profile.launch_profile_id}
        </code>
      </div>
      <span className={resolution.unsatisfied_hints.length === 0 ? "satisfied" : "unsatisfied"}>
        {resolution.unsatisfied_hints.length === 0
          ? "Configured hints satisfied"
          : `${resolution.unsatisfied_hints.length} unsatisfied hint${resolution.unsatisfied_hints.length === 1 ? "" : "s"}`}
      </span>
      {resolution.unsatisfied_hints.length === 0 ? null : (
        <ul>
          {resolution.unsatisfied_hints.map((hint) => (
            <li key={`${hint.field}:${hint.value}`}>
              <strong>{hint.field}</strong> <code>{hint.value}</code> is unavailable
            </li>
          ))}
        </ul>
      )}
      <small>Authority granted: no · provider arguments: none</small>
    </div>
  );
}

function ProfileForm({
  action,
  draft,
  mutation,
  onChange,
  onSubmit,
}: {
  action: string;
  draft: ProjectProfileDraft;
  mutation: MutationLike;
  onChange: (draft: ProjectProfileDraft) => void;
  onSubmit: () => void;
}) {
  const field = (name: keyof ProjectProfileDraft, value: string) => {
    onChange({ ...draft, [name]: value });
  };
  return (
    <form
      className="launch-profile-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (draft.display_name.trim() !== "") onSubmit();
      }}
    >
      <label className="span-two"><span>Name</span><input maxLength={200} onChange={(event) => field("display_name", event.target.value)} required value={draft.display_name} /></label>
      <label><span>Agent hint</span><input maxLength={200} onChange={(event) => field("agent_hint", event.target.value)} placeholder="codex" value={draft.agent_hint} /></label>
      <label><span>Structured route</span><input maxLength={200} onChange={(event) => field("structured_route_hint", event.target.value)} placeholder="codex.app-server" value={draft.structured_route_hint} /></label>
      <label><span>Model hint</span><input maxLength={200} onChange={(event) => field("model_hint", event.target.value)} value={draft.model_hint} /></label>
      <label><span>Mode hint</span><input maxLength={200} onChange={(event) => field("mode_hint", event.target.value)} value={draft.mode_hint} /></label>
      <label><span>Host hint</span><input maxLength={200} onChange={(event) => field("host_hint", event.target.value)} value={draft.host_hint} /></label>
      <label><span>Workspace policy</span><input maxLength={200} onChange={(event) => field("workspace_policy_hint", event.target.value)} value={draft.workspace_policy_hint} /></label>
      <label>
        <span>Terminal mode</span>
        <select onChange={(event) => field("terminal_mode_hint", event.target.value)} value={draft.terminal_mode_hint}>
          <option value="">No preference</option>
          <option value="auto">Auto</option>
          <option value="direct">Direct</option>
          <option value="managed">Managed</option>
        </select>
      </label>
      <button disabled={mutation.isPending} type="submit">{action}</button>
      <MutationStatus mutation={mutation} />
    </form>
  );
}

type MutationLike = {
  error: Error | null;
  isError: boolean;
  isPending: boolean;
};

function MutationStatus({ mutation }: { mutation: MutationLike }) {
  return mutation.isError ? (
    <p className="project-mutation-error" role="alert">{mutation.error?.message ?? "Request failed."}</p>
  ) : null;
}

export default ProjectCatalogWorkspace;
