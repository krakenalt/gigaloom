import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Fragment, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteCockpit,
  fetchCockpit,
  mutateCockpit,
  patchCockpit,
  type RunPreflightResponse,
  type RunStartResponse,
  type SessionSummary,
  withQuery,
} from "../api";
import { composerAttachments } from "../attachment-model";
import {
  AttachmentGallery,
  formatBytes,
  useAttachmentActions,
} from "../features/workbench/attachment-actions";
import { useComposerController } from "../features/workbench/composer-controller";
import { useReviewedRouteBinding } from "../features/work-first/ReviewedRouteControls";
import {
  CompletionNotices,
  isActiveRunStatus,
  useCompletionNotifications,
} from "../features/workbench/completion-notifications";
import {
  EnvironmentCard,
  useEnvironmentActions,
} from "../features/workbench/environment-actions";
import { useDeferredWorkbenchProjection } from "../features/workbench/lazy-projections";
import { useWorkbenchSessionEntry } from "../features/workbench/session-entry";
import {
  GeneratedFilePreview,
  GeneratedFileCard,
  hasRetainedResponse,
  PlanCard,
  Progression,
  ReasoningDisclosure,
  RetainedToolActivities,
  TokenUsage,
  ToolActivityCard,
} from "../features/workbench/inspectors";
import { MessageMarkdown } from "../message-markdown";
import { projectEnvironment } from "../environment-model";
import {
  latestEditableUserMessageId,
  projectActiveMessageTimeline,
  timelineWhileEditing,
} from "../message-actions";
import {
  MessageActions,
  useMessageActions,
} from "../features/workbench/message-actions";
import {
  isReasoningModel,
  persistRunConfiguration,
  preferredModel,
  type ReasoningEffort,
  type RunConfig,
  useRunConfiguration,
} from "../features/workbench/run-configuration";
import { createWorkbenchRunStreamSelector } from "../features/workbench/stream-projection";
import { useTranscriptVirtualWindow, useWindowedTranscript } from "../features/workbench/transcript-pagination";
import {
  ArchiveSessionIcon,
  DeleteSessionIcon,
  SessionConfirmationDialog,
  type SessionAction,
  useSessionNavigator,
} from "../features/workbench/session-navigation";
import { message, type MessageKey } from "../messages";
import { usePreferences } from "../preferences-context";
import { integrationFlowOptions } from "../remaining-request-graph";
import {
  environmentOptions,
  requestKeys,
  refreshSessionAfterRunStart,
  refreshSessionRevision,
  harnessesOptions,
  modelsOptions,
  sessionAttachmentsOptions,
  sessionEventsOptions,
  sessionIndexOptions,
  sessionOverviewOptions,
  sessionRunsOptions,
  settingsOptions,
  runsCenterOptions,
  workspaceFilesOptions,
} from "../request-graph";
import { observeNativeProcess } from "../native-process-stream";
import { observeSessionUpdates } from "../session-update-stream";
import {
  sessionCreationPayload,
  type SessionCreationIntent,
} from "../session-creation";
import {
  promptWithSkillMentions,
  skillMentionOptions,
  type SkillMention,
} from "../skill-mentions";
import {
  admittedBuiltinToolSelection,
  composerToolCatalog,
  type ComposerToolCategory,
  type ComposerToolOption,
} from "../tool-selection";
import {
  formatTimestamp,
  latestRun,
  runStage,
  sessionGroups,
  shortId,
} from "../surface-model";
import {
  useRunEventStreamSelector,
  useRunEventStreamStore,
} from "../stream-store";
import {
  projectWorkbenchStream,
  workbenchRunActive,
} from "../workbench-model";
import {
  activeAtQuery,
  admittedExecutionTransport,
  consumeAtQuery,
  harnessesForWorkbenchKind,
  legacyModeForProductSelection,
  normalizeProductSelection,
  permissionSimulationHighlights,
  resolveLegacyProductSelection,
  type ProductExecutionSelection,
  type WorkbenchKind,
} from "../workbench-execution";
import { permissionSimulationRows } from "../approval-ux";
const layoutKey = "gpt2giga.web.workbench-layout.v1";
type StartResult =
  | { kind: "preview"; report: RunPreflightResponse["preflight"] }
  | { kind: "run"; run: RunStartResponse["run"] };
const builtinToolLabels: Record<string, string> = {
  code_interpreter: "Code interpreter",
  image_generate: "Image generation",
  model_3d_generate: "3D generation",
  url_content_extraction: "URL content",
  web_search: "Web search",
};
const toolCategoryMessageKeys: Record<ComposerToolCategory, MessageKey> = {
  agent: "toolCategoryAgent",
  gigachat: "toolCategoryGigachat",
  mcp: "toolCategoryMcp",
  plugin: "toolCategoryPlugin",
  skill: "toolCategorySkill",
};
const emptyStringList: readonly string[] = [];
type ProviderHandoffPreview = {
  handoff: {
    command: string[];
    instruction: string;
    status: string;
  };
};
export function WorkbenchSurface() {
  const params = useParams({ strict: false });
  const routeSearch = useSearch({ strict: false });
  const sessionId =
    "sessionId" in params && typeof params.sessionId === "string"
      ? params.sessionId
      : undefined;
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const environmentActions = useEnvironmentActions(sessionId, locale);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const {
    atSelection,
    builtinTools,
    composerCaret,
    composerRef,
    draggingFiles,
    fileInputRef,
    modelMenuOpen,
    plusMenuOpen,
    prompt,
    selectedSkills,
    setAtSelection,
    setBuiltinTools,
    setComposerCaret,
    setDraggingFiles,
    setModelMenuOpen,
    setPlusMenuOpen,
    setPrompt,
    setSelectedSkills,
    setToolPickerOpen,
    setToolSearch,
    toolPickerOpen,
    toolSearch,
  } = useComposerController();
  const {
    advancedConfig,
    advancedOpen,
    legacyModeWarning,
    productSelection,
    reasoningEffort,
    runConfig,
    setAdvancedConfig,
    setAdvancedOpen,
    setLegacyModeWarning,
    setProductSelection,
    setReasoningEffort,
    setRunConfig,
  } = useRunConfiguration();
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [leftWidth, setLeftWidth] = useState(() => loadWidth("left", 264));
  const [rightWidth, setRightWidth] = useState(() => loadWidth("right", 320));
  const [editingMessageId, setEditingMessageId] = useState<string>();
  const attachmentActions = useAttachmentActions({
    composerRef,
    prompt,
    sessionId,
    setAtSelection,
    setComposerCaret,
    setPrompt,
  });
  const [previewReport, setPreviewReport] = useState<RunPreflightResponse["preflight"] | null>(null);
  const reviewedRoute = useReviewedRouteBinding();
  const messageAction = useMessageActions({
    clearPreview: () => setPreviewReport(null),
    composerRef,
    sessionId,
    setComposerCaret,
    setEditingMessageId,
    setPrompt,
  });
  const permissionHighlights = permissionSimulationHighlights(
    previewReport?.permission_simulation,
  );
  const permissionRows = permissionSimulationRows(
    previewReport?.permission_simulation,
  );
  const [startedRuns, setStartedRuns] = useState<Record<string, string>>({});
  const settingsDefaultsApplied = useRef(false);
  const [sessionConfirmation, setSessionConfirmation] = useState<{
    action: SessionAction;
    id: string;
    title: string;
  } | null>(null);

  const index = useQuery(sessionIndexOptions());
  const { filteredSessions, search, setSearch } = useSessionNavigator(
    index.data?.sessions,
    locale,
  );
  const runsCenter = useQuery(runsCenterOptions());
  const completionNotifications = useCompletionNotifications({
    locale,
    runs: runsCenter.data?.runs,
    sessionId,
  });
  const harnesses = useQuery(harnessesOptions());
  const models = useQuery(modelsOptions(runConfig.apiMode));
  const settings = useQuery(settingsOptions());
  const integrationsEnabled = useDeferredWorkbenchProjection(
    sessionId,
    toolPickerOpen || plusMenuOpen || advancedOpen,
  );
  const environmentEnabled = useDeferredWorkbenchProjection(sessionId);
  const attachmentsEnabled = useDeferredWorkbenchProjection(
    sessionId,
    draggingFiles || plusMenuOpen,
  );
  const eventsEnabled = useDeferredWorkbenchProjection(sessionId);
  const integrations = useQuery({
    ...integrationFlowOptions(),
    enabled: integrationsEnabled,
  });
  const overview = useQuery({
    ...sessionOverviewOptions(sessionId ?? "pending"),
    enabled: sessionId !== undefined,
  });
  const environment = useQuery({
    ...environmentOptions(sessionId ?? "pending"),
    enabled: environmentEnabled,
  });
  const messages = useWindowedTranscript(sessionId);
  const runs = useQuery({
    ...sessionRunsOptions(sessionId ?? "pending"),
    enabled: sessionId !== undefined,
  });
  const attachments = useQuery({
    ...sessionAttachmentsOptions(sessionId ?? "pending"),
    enabled: attachmentsEnabled,
  });
  const atQuery = activeAtQuery(prompt, composerCaret);
  const deferredAtQuery = useDeferredValue(atQuery?.query ?? "");
  const workspaceFiles = useQuery({
    ...workspaceFilesOptions(sessionId ?? "pending", deferredAtQuery),
    enabled: sessionId !== undefined && atQuery !== null,
  });
  const availableSkillMentions = skillMentionOptions(
    integrations.data,
    runConfig.harnessId,
    deferredAtQuery,
  ).filter((skill) => !selectedSkills.some((selected) => selected.id === skill.id));
  const events = useQuery({
    ...sessionEventsOptions(sessionId ?? "pending"),
    enabled: eventsEnabled,
  });
  const activeMessages = useMemo(
    () => projectActiveMessageTimeline(messages.data?.messages ?? []),
    [messages.data?.messages],
  );
  const draftAttachments = useMemo(
    () => composerAttachments(
      attachments.data?.attachments ?? [],
      messages.data?.messages ?? [],
    ),
    [attachments.data?.attachments, messages.data?.messages],
  );
  const latestUserMessageId = latestEditableUserMessageId(activeMessages);
  const timelineMessages = useMemo(
    () => timelineWhileEditing(activeMessages, editingMessageId),
    [activeMessages, editingMessageId],
  );
  const transcriptWindow = useTranscriptVirtualWindow(timelineMessages.length, sessionId);
  const visibleMessages = useMemo(
    () => timelineMessages.slice(transcriptWindow.window.start, transcriptWindow.window.end),
    [timelineMessages, transcriptWindow.window.end, transcriptWindow.window.start],
  );

  useEffect(() => {
    setEditingMessageId(undefined);
    setSelectedSkills([]);
  }, [sessionId]);

  useEffect(() => {
    if (settings.data === undefined || settingsDefaultsApplied.current) return;
    settingsDefaultsApplied.current = true;
    const defaults = settings.data.harness_defaults;
    if (sessionId === undefined) {
      setRunConfig({
        apiMode: defaults.default_api_mode,
        harnessId: defaults.default_harness_id,
        mode: defaults.mode,
        model: defaults.default_model ?? "",
      });
      setProductSelection({
        authority: defaults.authority,
        intent: defaults.task_intent,
        kind: defaults.default_harness_id === "direct-chat"
          ? "direct_chat"
          : "coding_agent",
      });
      setLegacyModeWarning(
        settings.data.harness_defaults.compatibility.mode?.warning ?? null,
      );
    }
    setAdvancedConfig((current) => ({
      ...current,
      permissionProfile: defaults.permission_profile,
      workspacePolicy: defaults.workspace_policy,
    }));
  }, [sessionId, settings.data]);
  const retainedLatestRun = latestRun(runs.data?.runs ?? []);
  const selectedRunId =
    sessionId === undefined ? retainedLatestRun?.id : startedRuns[sessionId] ?? retainedLatestRun?.id;
  const locallyStartedRunSelected =
    sessionId !== undefined && startedRuns[sessionId] === selectedRunId;
  const streamStore = useRunEventStreamStore(
    selectedRunId,
    0,
    !locallyStartedRunSelected,
  );
  const streamSelector = useMemo(createWorkbenchRunStreamSelector, []);
  const stream = useRunEventStreamSelector(streamStore, streamSelector);

  useEffect(() => {
    if (sessionId === undefined || typeof globalThis.EventSource !== "function") {
      return;
    }
    return observeSessionUpdates(sessionId, () => {
      void refreshSessionRevision(queryClient, sessionId);
    });
  }, [queryClient, sessionId]);

  useEffect(() => {
    localStorage.setItem(
      layoutKey,
      JSON.stringify({ left: leftWidth, right: rightWidth }),
    );
  }, [leftWidth, rightWidth]);

  useEffect(() => {
    persistRunConfiguration(runConfig, reasoningEffort);
  }, [reasoningEffort, runConfig]);

  useEffect(() => {
    const session = overview.data?.session;
    if (session === undefined) return;
    setRunConfig((current) => ({
      apiMode: session.default_api_mode ?? current.apiMode,
      harnessId: session.default_harness_id ?? current.harnessId,
      mode: session.default_mode ?? current.mode,
      model: session.default_model ?? current.model,
    }));
    setProductSelection((current) => {
      const retained = session.workbench_selection;
      if (retained !== undefined) {
        setLegacyModeWarning(retained.compatibility_warning);
        return {
          authority: retained.authority,
          intent: retained.intent,
          kind: retained.kind,
        };
      }
      const legacy = resolveLegacyProductSelection(
        session.default_mode,
        session.default_harness_id === "direct-chat"
          ? "direct_chat"
          : current.kind,
      );
      setLegacyModeWarning(legacy.warning);
      return legacy.selection;
    });
  }, [
    overview.data?.session.default_api_mode,
    overview.data?.session.default_harness_id,
    overview.data?.session.default_mode,
    overview.data?.session.default_model,
    overview.data?.session.id,
    overview.data?.session.workbench_selection,
  ]);

  useEffect(() => {
    if (models.isPending) return;
    const availableModels = models.data?.models ?? [];
    const selectedModel = models.isSuccess && availableModels.length > 0
      ? preferredModel(availableModels)
      : settings.data?.harness_defaults.default_model ?? "";
    setRunConfig((current) => {
      if (
        current.model &&
        (!models.isSuccess || availableModels.length === 0 || availableModels.includes(current.model))
      ) {
        return current;
      }
      return current.model === selectedModel ? current : { ...current, model: selectedModel };
    });
  }, [
    models.data?.models,
    models.isPending,
    models.isSuccess,
    runConfig.model,
    settings.data?.harness_defaults.default_model,
  ]);

  const createSession = useMutation({
    mutationFn: (intent: SessionCreationIntent) =>
      mutateCockpit<{ session: SessionSummary }>(
        "/api/sessions",
        sessionCreationPayload(intent),
      ),
    onSuccess: ({ session }) => {
      setPrompt("");
      setSelectedSkills([]);
      setBuiltinTools([]);
      void navigate({
        params: { sessionId: session.id },
        to: "/web/work/$sessionId",
      });
      void queryClient.invalidateQueries({ queryKey: requestKeys.sessionIndex() });
    },
  });
  const { entryAgentError, retrySessionCreation } = useWorkbenchSessionEntry({
    createSession: createSession.mutate,
    defaults: settings.data?.harness_defaults,
    harnesses: harnesses.data?.harnesses,
    search: routeSearch,
    sessionId,
  });

  useEffect(() => {
    if (sessionId === undefined || !overview.isSuccess) return;
    const frame = requestAnimationFrame(() => composerRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [overview.isSuccess, sessionId]);

  const startRun = useMutation<StartResult>({
    mutationFn: async () => {
      const session = overview.data?.session;
      if (sessionId === undefined || session === undefined) {
        throw new Error("Session is not selected");
      }
      const payload = {
        api_mode: runConfig.apiMode,
        attachment_ids: draftAttachments.map((attachment) => attachment.id),
        authority: productSelection.authority,
        builtin_tools: admittedBuiltinTools,
        extra: {
          ...(editingMessageId === undefined ? {} : { edit_message_id: editingMessageId }),
          generate_session_title: session.title === "Untitled session",
          session_title_model:
            settings.data?.harness_defaults.default_title_model
            ?? (runConfig.model.trim() || undefined),
          ...(isReasoningModel(runConfig.model)
            ? { agent_adapter_options: { reasoning_effort: reasoningEffort } }
            : {}),
        },
        harness_id: runConfig.harnessId,
        model: runConfig.model.trim() || null,
        permission_profile: advancedConfig.permissionProfile,
        prompt: promptWithSkillMentions(
          prompt,
          selectedSkills,
          runConfig.harnessId,
        ),
        session_id: sessionId,
        task_intent: productSelection.intent,
        workbench_kind: productSelection.kind,
        workspace: session.workspace_bound ? undefined : ".",
        workspace_policy: advancedConfig.workspacePolicy,
      };
      const submissionPayload = reviewedRoute.bindPayload(payload);
      if (advancedConfig.dryRun) {
        const response = await mutateCockpit<RunPreflightResponse>("/api/preflight/run", {
          ...submissionPayload,
          dry_run: true,
        });
        return { kind: "preview", report: response.preflight };
      }
      const response = await mutateCockpit<RunStartResponse>(
        `/api/sessions/${encodeURIComponent(sessionId)}/run/start`,
        submissionPayload,
      );
      return { kind: "run", run: response.run };
    },
    onSuccess: async (result) => {
      if (result.kind === "preview") {
        setPreviewReport(result.report);
        return;
      }
      const { run } = result;
      setPreviewReport(null);
      setStartedRuns((current) => ({ ...current, [run.session_id]: run.id }));
      completionNotifications.recordStartedRun(run.id, run.status);
      await refreshSessionAfterRunStart(queryClient, run.session_id);
      setEditingMessageId(undefined);
      setPrompt("");
      setSelectedSkills([]);
    },
  });
  const openInProvider = useMutation({
    mutationFn: () => fetchCockpit<ProviderHandoffPreview>(withQuery(
      `/api/provider-handoffs/${encodeURIComponent(runConfig.harnessId)}/preview`,
      { action: "open_provider_ui", workspace: "." },
    )),
  });

  const saveRunConfig = useMutation({
    mutationFn: (values: Readonly<Record<string, unknown>>) => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      return patchCockpit<{ session: SessionSummary }>(
        `/api/sessions/${encodeURIComponent(sessionId)}`,
        values,
      );
    },
    onSuccess: ({ session }) => {
      queryClient.setQueryData(
        requestKeys.sessionOverview(session.id),
        (current: typeof overview.data) => current === undefined ? current : { ...current, session },
      );
      void queryClient.invalidateQueries({ queryKey: requestKeys.sessionIndex() });
    },
  });

  const cancelRun = useMutation({
    mutationFn: ({ nativeProcessId, runId }: { nativeProcessId?: string; runId: string }) =>
      nativeProcessId === undefined
        ? mutateCockpit(`/api/runs/${encodeURIComponent(runId)}/cancel`)
        : deleteCockpit(`/api/native/processes/${encodeURIComponent(nativeProcessId)}`),
    onSuccess: async () => {
      if (sessionId !== undefined) {
        await queryClient.invalidateQueries({ queryKey: requestKeys.sessionScope(sessionId) });
      }
      await queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() });
    },
  });

  const changeSession = useMutation({
    mutationFn: ({ action, id }: { action: SessionAction; id: string }) =>
      action === "archive"
        ? patchCockpit(`/api/sessions/${encodeURIComponent(id)}`, { archived: true })
        : deleteCockpit(`/api/sessions/${encodeURIComponent(id)}`),
    onSuccess: async (_, { id }) => {
      setSessionConfirmation(null);
      setStartedRuns((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      if (id === sessionId) {
        await navigate({
          replace: true,
          search: { fromSessionAction: true },
          to: "/web/work",
        });
      }
      queryClient.removeQueries({ queryKey: requestKeys.sessionScope(id) });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: requestKeys.sessionIndex() }),
        queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() }),
      ]);
    },
  });

  const latestRunStateBySession = useMemo(() => {
    const states = new Map<string, { runId: string; status: string }>();
    for (const item of runsCenter.data?.runs ?? []) {
      if (!states.has(item.session_id)) {
        states.set(item.session_id, { runId: item.run_id, status: item.status_group });
      }
    }
    return states;
  }, [runsCenter.data?.runs]);

  const layoutStyle = {
    gridTemplateColumns: `${leftOpen ? `${leftWidth}px 8px` : "44px"} minmax(360px, 1fr) ${rightOpen ? `8px ${rightWidth}px` : "44px"}`,
  };
  const stage = runStage(retainedLatestRun);
  const environmentView = environment.data === undefined
    ? undefined
    : projectEnvironment(environment.data, { failedRefresh: environment.isError });
  const selectedHarness = harnesses.data?.harnesses.find(
    (harness) => harness.spec.id === runConfig.harnessId,
  );
  const selectableHarnesses = harnessesForWorkbenchKind(
    harnesses.data?.harnesses ?? [],
    productSelection.kind,
  );
  const admittedTransport = admittedExecutionTransport(
    selectedHarness,
    productSelection.kind,
  );
  const selectedTransport = selectedHarness?.workbench_transport?.options.find(
    (option) => option.id === admittedTransport,
  );
  const selectedAdmission = selectedHarness?.workbench_admission?.modes.find(
    (mode) => mode.id === productSelection.kind,
  );
  const authorityLimitsChange =
    productSelection.intent === "change"
    && productSelection.authority === "read_only";
  const admissionStatus = selectedAdmission?.status === "blocked"
    ? "blocked"
    : selectedAdmission?.status === "degraded" || authorityLimitsChange
      ? "degraded"
      : "available";
  const admissionReasons = [
    ...(selectedAdmission?.why ?? []),
    ...(authorityLimitsChange
      ? ["change_intent_limited_by_read_only_authority"]
      : []),
  ];
  const providerHandoffActions = [
    ...(selectedHarness?.provider_handoff?.available_actions ?? []),
    ...(selectedHarness?.provider_handoff?.degraded_actions ?? []),
  ];
  const capabilityCopy = productSelection.kind === "coding_agent"
    ? { label: message(locale, "codingAgent"), detail: message(locale, "codingAgentHint") }
    : { label: message(locale, "directChat"), detail: message(locale, "directChatHint") };
  const supportedBuiltinTools = selectedHarness?.spec.supported_builtin_tools ?? emptyStringList;
  const builtinToolsAvailable =
    runConfig.apiMode === "v2" &&
    productSelection.kind === "direct_chat" &&
    supportedBuiltinTools.length > 0;
  const admittedBuiltinTools = useMemo(
    () => admittedBuiltinToolSelection(
      builtinTools,
      supportedBuiltinTools,
      runConfig.apiMode,
      productSelection.kind,
    ),
    [builtinTools, productSelection.kind, runConfig.apiMode, supportedBuiltinTools],
  );
  const toolSkillMentions = useMemo(
    () => skillMentionOptions(
      integrations.data,
      runConfig.harnessId,
      toolSearch,
    ),
    [integrations.data, runConfig.harnessId, toolSearch],
  );
  const toolGroups = useMemo(
    () => composerToolCatalog({
      apiMode: runConfig.apiMode,
      builtinTools: admittedBuiltinTools,
      harnessId: runConfig.harnessId,
      inventory: integrations.data,
      kind: productSelection.kind,
      query: toolSearch,
      selectedSkillIds: new Set(selectedSkills.map((skill) => skill.id)),
      skillMentions: toolSkillMentions,
      supportedBuiltinTools,
    }),
    [
      admittedBuiltinTools,
      integrations.data,
      productSelection.kind,
      runConfig.apiMode,
      runConfig.harnessId,
      selectedSkills,
      supportedBuiltinTools,
      toolSearch,
      toolSkillMentions,
    ],
  );
  const modelSuggestions = models.data?.models ?? [];
  const streamPresentation = useMemo(
    () => projectWorkbenchStream(
      stream.events,
      messages.data?.messages ?? [],
      selectedRunId,
    ),
    [messages.data?.messages, selectedRunId, stream.events],
  );
  const selectedRunHasRetainedResponse = useMemo(
    () => hasRetainedResponse(messages.data?.messages ?? [], selectedRunId),
    [messages.data?.messages, selectedRunId],
  );
  const streamedEventIds = new Set(
    stream.events.map((event) => event.id),
  );
  const retainedGeneratedEvents = (events.data?.events ?? []).filter(
    (event) => event.type === "generated_file" && !streamedEventIds.has(event.id),
  );
  const retainedPlanEvent = (events.data?.events ?? []).filter(
    (event) => event.run_id === selectedRunId && event.type === "plan_updated",
  ).at(-1);
  const retainedToolEvents = (events.data?.events ?? []).filter(
    (event) =>
      event.run_id === selectedRunId &&
      event.type === "tool_call_finished" &&
      !streamedEventIds.has(event.id),
  );
  if (retainedPlanEvent !== undefined && !streamedEventIds.has(retainedPlanEvent.id)) {
    retainedToolEvents.unshift(retainedPlanEvent);
  }
  const locallyStartedRunId = sessionId === undefined ? undefined : startedRuns[sessionId];
  const selectedRunActive = workbenchRunActive(
    retainedLatestRun,
    selectedRunId,
    locallyStartedRunId,
    streamPresentation.terminalEvent,
  );
  const selectedRun = (runs.data?.runs ?? []).find((run) => run.id === selectedRunId);
  const selectedNativeProcessId =
    selectedRun?.native_process_id ?? undefined;

  useEffect(() => {
    const supported = new Set(supportedBuiltinTools);
    setBuiltinTools((current) => (
      builtinToolsAvailable
        ? current.filter((tool) => supported.has(tool))
        : []
    ));
    setProductSelection((current) => normalizeProductSelection(selectedHarness, current));
  }, [
    builtinToolsAvailable,
    runConfig.harnessId,
    selectedHarness,
    supportedBuiltinTools,
  ]);

  useEffect(() => {
    setAtSelection(0);
  }, [deferredAtQuery]);

  useEffect(() => {
    if (
      sessionId === undefined ||
      selectedNativeProcessId === undefined ||
      !selectedRunActive ||
      typeof globalThis.EventSource !== "function"
    ) {
      return;
    }
    let refreshScheduled = false;
    return observeNativeProcess(selectedNativeProcessId, () => {
      if (refreshScheduled) return;
      refreshScheduled = true;
      requestAnimationFrame(() => {
        refreshScheduled = false;
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: requestKeys.sessionScope(sessionId) }),
          queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() }),
        ]);
      });
    });
  }, [queryClient, selectedNativeProcessId, selectedRunActive, sessionId]);

  useEffect(() => {
    if (sessionId !== undefined && streamPresentation.terminalEvent !== null) {
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: requestKeys.sessionScope(sessionId) }),
        queryClient.invalidateQueries({ queryKey: requestKeys.sessionIndex() }),
        queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() }),
      ]);
    }
  }, [queryClient, sessionId, streamPresentation.terminalEvent?.id]);

  const setConfig = <Key extends keyof RunConfig>(
    key: Key,
    value: RunConfig[Key],
    persistKey?: string,
  ) => {
    setRunConfig((current) => ({ ...current, [key]: value }));
    if (persistKey !== undefined) saveRunConfig.mutate({ [persistKey]: value || null });
  };
  const updateProductSelection = (
    patch: Partial<ProductExecutionSelection>,
  ) => {
    const next = { ...productSelection, ...patch };
    setProductSelection(next);
    setLegacyModeWarning(null);
    setRunConfig((current) => ({
      ...current,
      mode: legacyModeForProductSelection(next),
    }));
    saveRunConfig.mutate({ workbench_selection: next });
  };
  const selectWorkbenchKind = (kind: WorkbenchKind) => {
    const compatibleHarnesses = harnessesForWorkbenchKind(
      harnesses.data?.harnesses ?? [],
      kind,
    ).filter(
      (harness) => harness.availability?.status !== "unavailable",
    );
    const preferredHarnessId =
      kind === "coding_agent" ? "codex-cli" : "direct-chat";
    const compatibleHarness =
      compatibleHarnesses?.find(
        (harness) => harness.spec.id === runConfig.harnessId,
      )
      ?? compatibleHarnesses?.find(
        (harness) => harness.spec.id === preferredHarnessId,
      )
      ?? compatibleHarnesses?.[0];
    setProductSelection((current) => ({ ...current, kind }));
    if (
      compatibleHarness !== undefined
      && compatibleHarness.spec.id !== runConfig.harnessId
    ) {
      setConfig(
        "harnessId",
        compatibleHarness.spec.id,
        "default_harness_id",
      );
    }
  };
  const workspaceFileCandidates = workspaceFiles.data?.files ?? [];
  const atCandidates = [
    ...availableSkillMentions.map((skill) => ({ kind: "skill" as const, skill })),
    ...workspaceFileCandidates.map((file) => ({ kind: "file" as const, file })),
  ];
  const chooseWorkspaceFile = (path: string) => {
    if (atQuery === null || attachmentActions.attachWorkspaceFile.isPending) return;
    attachmentActions.attachWorkspaceFile.mutate({ path, token: atQuery });
  };
  const chooseSkill = (skill: SkillMention) => {
    if (atQuery === null) return;
    const nextPrompt = consumeAtQuery(prompt, atQuery);
    setSelectedSkills((current) => [...current, skill]);
    setPrompt(nextPrompt);
    setComposerCaret(nextPrompt.length);
    setAtSelection(0);
    requestAnimationFrame(() => composerRef.current?.focus());
  };
  const chooseAtCandidate = (index: number) => {
    const candidate = atCandidates[index];
    if (candidate?.kind === "skill") chooseSkill(candidate.skill);
    if (candidate?.kind === "file") chooseWorkspaceFile(candidate.file.path);
  };
  const toggleComposerTool = (option: ComposerToolOption) => {
    if (!option.selectable || option.value === null) return;
    const value = option.value;
    if (option.category === "gigachat") {
      setBuiltinTools((current) => (
        current.includes(value)
          ? current.filter((tool) => tool !== value)
          : [...current, value]
      ));
      return;
    }
    const skill = toolSkillMentions.find((candidate) => candidate.id === value);
    if (skill === undefined) return;
    setSelectedSkills((current) => (
      current.some((candidate) => candidate.id === skill.id)
        ? current.filter((candidate) => candidate.id !== skill.id)
        : [...current, skill]
    ));
  };

  return (
    <div className="workbench-layout" style={layoutStyle}>
      {leftOpen ? (
        <aside className="session-navigator" aria-label={message(locale, "allSessions")}>
          <div className="panel-heading">
            <div>
              <span className="section-kicker">{message(locale, "workbench")}</span>
              <h1>{message(locale, "allSessions")}</h1>
            </div>
            <button
              aria-label={message(locale, "collapse")}
              onClick={() => setLeftOpen(false)}
              type="button"
            >
              ‹
            </button>
          </div>
          <button
            className="new-session-button"
            disabled={createSession.isPending || !runConfig.model}
            onClick={() => createSession.mutate({
              config: { ...runConfig, productSelection },
              kind: "configured",
            })}
            type="button"
          >
            <span aria-hidden="true">＋</span>
            {message(locale, "newSession")}
          </button>
          <label className="search-control">
            <span className="sr-only">{message(locale, "searchSessions")}</span>
            <span aria-hidden="true">⌕</span>
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder={message(locale, "searchSessions")}
              type="search"
              value={search}
            />
          </label>
          <div className="list-toolbar">
            <span>{message(locale, "groupByProject")}</span>
            <span>{filteredSessions.length}</span>
          </div>
          <nav className="session-list" aria-label={message(locale, "allSessions")}>
            {index.isPending ? <ListSkeleton rows={6} /> : null}
            {index.isError ? <ReadError locale={locale} /> : null}
            {index.isSuccess && filteredSessions.length === 0 ? (
              <div className="empty-state">{message(locale, "emptySessions")}</div>
            ) : null}
            {sessionGroups(filteredSessions).map((group) => (
              <section className="project-group" key={group.projectId}>
                <h2>{group.projectId === "unbound" ? message(locale, "localSessions") : group.projectId}</h2>
                {group.sessions.map((session) => {
                  const runState = latestRunStateBySession.get(session.id);
                  const running = isActiveRunStatus(runState?.status);
                  const unread = completionNotifications.unreadSessionIds.has(session.id);
                  return (
                    <div
                      className={[
                        "session-row",
                        session.id === sessionId ? "selected" : "",
                        running ? "running" : "",
                        unread ? "unread" : "",
                      ].filter(Boolean).join(" ")}
                      key={session.id}
                    >
                      <Link
                        className="session-row-link"
                        onClick={() => {
                          completionNotifications.markSessionRead(session.id);
                        }}
                        params={{ sessionId: session.id }}
                        to="/web/work/$sessionId"
                      >
                        <strong>{session.title}</strong>
                        <span>
                          {session.default_harness_id ?? "echo"} · {session.default_api_mode ?? "v2"}
                        </span>
                        {running ? (
                          <span aria-label={message(locale, "running")} className="session-status-icon running-spinner" />
                        ) : unread ? (
                          <span aria-label={message(locale, "unreadSession")} className="session-status-icon unread-dot" />
                        ) : (
                          <time>{formatTimestamp(session.updated_at, locale)}</time>
                        )}
                      </Link>
                      <div className="session-row-actions">
                        <button
                          aria-label={`${message(locale, "archiveSession")}: ${session.title}`}
                          disabled={changeSession.isPending}
                          onClick={() => {
                            changeSession.reset();
                            setSessionConfirmation({
                              action: "archive",
                              id: session.id,
                              title: session.title,
                            });
                          }}
                          title={message(locale, "archiveSession")}
                          type="button"
                        >
                          <ArchiveSessionIcon />
                        </button>
                        <button
                          aria-label={`${message(locale, "deleteSession")}: ${session.title}`}
                          className="danger"
                          disabled={changeSession.isPending}
                          onClick={() => {
                            changeSession.reset();
                            setSessionConfirmation({
                              action: "delete",
                              id: session.id,
                              title: session.title,
                            });
                          }}
                          title={message(locale, "deleteSession")}
                          type="button"
                        >
                          <DeleteSessionIcon />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </section>
            ))}
          </nav>
        </aside>
      ) : (
        <button className="panel-restore" onClick={() => setLeftOpen(true)} type="button">
          <span>›</span><span>{message(locale, "restore")}</span>
        </button>
      )}
      {leftOpen ? (
        <ResizeHandle
          label="Resize session navigator"
          onReset={() => setLeftWidth(264)}
          onResize={(delta) => setLeftWidth((value) => clamp(value + delta, 220, 360))}
        />
      ) : null}

      <main className="work-canvas">
        {sessionId === undefined ? (
          <div className="empty-work-canvas">
            {routeSearch.fromSessionAction === true ? (
              <>
                <h1>{message(locale, "noSessionSelected")}</h1>
                <p>{message(locale, "noSessionSelectedDescription")}</p>
              </>
            ) : (
              <>
                <span className="opening-session-spinner" aria-hidden="true" />
                <h1>{message(locale, "openingSession")}</h1>
              </>
            )}
            {routeSearch.fromSessionAction !== true && createSession.isError ? (
              <button
                onClick={retrySessionCreation}
                type="button"
              >
                {message(locale, "retry")}
              </button>
            ) : null}
            {entryAgentError !== null ? (
              <>
                <p>{entryAgentError}</p>
                <Link to="/web/coding-agents">Return to Agent runtimes</Link>
              </>
            ) : null}
          </div>
        ) : overview.isPending ? (
          <ListSkeleton rows={5} />
        ) : overview.isError ? (
          <ReadError locale={locale} />
        ) : (
          <>
            <header className="work-header">
              <div>
                <p className="section-kicker">{message(locale, "session")} · {shortId(sessionId)}</p>
                <h1>{overview.data?.session.title}</h1>
                <span>
                  {runConfig.model || "GigaChat"} · /{runConfig.apiMode} · {runConfig.harnessId}
                </span>
              </div>
              <div className="work-header-actions">
                {selectedRunId === undefined ? null : (
                  <Link params={{ runId: selectedRunId }} to="/web/runs/$runId">
                    {message(locale, "openRun")} ↗
                  </Link>
                )}
                <button
                  disabled={changeSession.isPending}
                  onClick={() => {
                    changeSession.reset();
                    setSessionConfirmation({
                      action: "archive",
                      id: sessionId,
                      title: overview.data?.session.title ?? sessionId,
                    });
                  }}
                  type="button"
                >
                  {message(locale, "archiveSession")}
                </button>
                <button
                  className="danger-button"
                  disabled={changeSession.isPending}
                  onClick={() => {
                    changeSession.reset();
                    setSessionConfirmation({
                      action: "delete",
                      id: sessionId,
                      title: overview.data?.session.title ?? sessionId,
                    });
                  }}
                  type="button"
                >
                  {message(locale, "deleteSession")}
                </button>
              </div>
            </header>
            <EnvironmentCard
              className="mobile-environment"
              commitAction={environmentActions.commitAction}
              pushAction={environmentActions.pushAction}
              pullRequestAction={environmentActions.pullRequestAction}
              environment={environmentView}
              error={environment.isError}
              locale={locale}
              pending={environment.isPending}
            />
            <section
              className="message-region"
              aria-label={message(locale, "sessionMessages")}
              onScroll={transcriptWindow.onScroll}
              ref={transcriptWindow.regionRef}
            >
              {messages.isPending ? <ListSkeleton rows={4} /> : null}
              {messages.isError ? <ReadError locale={locale} /> : null}
              {messages.data !== undefined && timelineMessages.length === 0 ? (
                <div className="empty-state">{message(locale, "emptyMessages")}</div>
              ) : null}
              {messages.hasPreviousPage ? (
                <button
                  className="transcript-page-button"
                  disabled={messages.isFetchingPreviousPage}
                  onClick={() =>
                    void messages.prependPreviousPage(
                      transcriptWindow.regionRef.current,
                    )}
                  type="button"
                >
                  {message(locale, "loadEarlierMessages")}
                </button>
              ) : null}
              <div
                aria-hidden="true"
                className="transcript-spacer"
                style={{ height: transcriptWindow.topSpacer }}
              />
              {visibleMessages.map((item) => (
                <Fragment key={item.id}>
                  {(item.role === "assistant" || item.role === "error") && item.run_id
                    ? (
                        <RetainedToolActivities
                          events={retainedToolEvents.filter((event) => event.run_id === item.run_id)}
                          locale={locale}
                        />
                      )
                    : null}
                  {(item.role === "assistant" || item.role === "error") && item.run_id === selectedRunId ? (
                    <>
                      {!item.reasoning?.text && streamPresentation.reasoningText ? (
                        <ReasoningDisclosure text={streamPresentation.reasoningText} locale={locale} />
                      ) : null}
                      {streamPresentation.plan.length > 0 ? (
                        <PlanCard items={streamPresentation.plan} locale={locale} />
                      ) : null}
                      {streamPresentation.toolActivities.map((activity) => (
                        <ToolActivityCard activity={activity} key={activity.id} locale={locale} />
                      ))}
                    </>
                  ) : null}
                  <article className={`message-entry ${item.role}`}>
                    <header className="message-entry-header">
                      <span className="message-role">{item.role}</span>
                      <span className="message-header-meta">
                        <TokenUsage usage={item.usage} />
                        <time>{formatTimestamp(item.created_at, locale)}</time>
                        {item.role === "assistant" || item.role === "user" ? (
                          <MessageActions
                            canEdit={item.id === latestUserMessageId}
                            locale={locale}
                            messageId={item.id}
                            mutation={messageAction}
                            role={item.role}
                          />
                        ) : null}
                      </span>
                    </header>
                    {item.reasoning?.text ? (
                      <ReasoningDisclosure text={item.reasoning.text} locale={locale} />
                    ) : null}
                    {item.attachments && item.attachments.length > 0 ? (
                      <AttachmentGallery attachments={item.attachments} locale={locale} />
                    ) : null}
                    <MessageMarkdown source={item.content.text} />
                    {item.content.truncated ? <span>{message(locale, "boundedPreview")}</span> : null}
                  </article>
                </Fragment>
              ))}
              <div
                aria-hidden="true"
                className="transcript-spacer"
                style={{ height: transcriptWindow.bottomSpacer }}
              />
              {messages.hasNextPage ? (
                <button
                  className="transcript-page-button"
                  disabled={messages.isFetchingNextPage}
                  onClick={() => void messages.fetchNextPage()}
                  type="button"
                >
                  {message(locale, "loadLaterMessages")}
                </button>
              ) : null}
              {messageAction.isError ? (
                <span className="error-state" role="alert">
                  {message(locale, "messageActionFailed")}
                </span>
              ) : null}
              <span className="sr-only" aria-live="polite">
                {messageAction.isSuccess
                  ? message(
                      locale,
                      messageAction.data.kind === "copy"
                        ? messageAction.variables.role === "user"
                          ? "userMessageCopied"
                          : "assistantMessageCopied"
                        : "userMessageLoaded",
                    )
                  : ""}
              </span>
              {retainedGeneratedEvents.map((event) => (
                <GeneratedFilePreview
                  eventId={event.id}
                  key={event.id}
                  locale={locale}
                  payloadUrl={event.payload_url}
                />
              ))}
              {!selectedRunHasRetainedResponse && streamPresentation.reasoningText ? (
                <ReasoningDisclosure text={streamPresentation.reasoningText} locale={locale} />
              ) : null}
              {!selectedRunHasRetainedResponse && streamPresentation.plan.length > 0 ? (
                <PlanCard items={streamPresentation.plan} locale={locale} />
              ) : null}
              {!selectedRunHasRetainedResponse
                ? streamPresentation.toolActivities.map((activity) => (
                    <ToolActivityCard activity={activity} key={activity.id} locale={locale} />
                  ))
                : null}
              {streamPresentation.assistantText ? (
                <article className="message-entry assistant" key={`live-${selectedRunId}`}>
                  <header className="message-entry-header">
                    <span className="message-role">assistant</span>
                    <TokenUsage usage={streamPresentation.usage} />
                  </header>
                  <MessageMarkdown source={streamPresentation.assistantText} />
                </article>
              ) : null}
              {streamPresentation.generatedFiles.map((event) => (
                <GeneratedFileCard key={event.id} locale={locale} payload={event.payload} />
              ))}
            </section>
            <form
              className={[
                "composer",
                draggingFiles ? "dragging-files" : "",
                modelMenuOpen || plusMenuOpen || toolPickerOpen ? "popover-open" : "",
              ].filter(Boolean).join(" ")}
              onDragEnter={(event) => {
                event.preventDefault();
                setDraggingFiles(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setDraggingFiles(false);
                }
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                setDraggingFiles(false);
                const files = Array.from(event.dataTransfer.files);
                if (files.length > 0) attachmentActions.uploadFiles.mutate({ files, source: "drop" });
              }}
              onSubmit={(event) => {
                event.preventDefault();
                if (prompt.trim() && !startRun.isPending) startRun.mutate();
              }}
            >
              {legacyModeWarning === null ? null : (
                <div className="preview-execution blocked" role="status">
                  <strong>{message(locale, "legacyModeWarningTitle")}</strong>
                  <span>{message(locale, "legacyModeWarning")}</span>
                </div>
              )}
              {previewReport === null ? null : (
                <div
                  className={previewReport.hard_block ? "preview-execution blocked" : "preview-execution"}
                  role="status"
                >
                  <strong>{message(locale, "previewExecutionResult")}</strong>
                  <span>
                    {previewReport.hard_block
                      ? message(locale, "previewExecutionBlocked")
                      : message(locale, "previewExecutionReady")}
                  </span>
                  <small>
                    {message(locale, "previewExecutionEvidence")} · {previewReport.findings.length} {message(locale, "previewFindings")}
                  </small>
                  {permissionHighlights === null ? null : (
                    <>
                      <small className="permission-simulation-summary">
                        {message(locale, "permissionSimulation")} · {permissionHighlights.approvalCount} {message(locale, "approvalRequired")} · {permissionHighlights.unknownCount} {message(locale, "permissionSimulationUnknown")} · {message(locale, "permissionSimulationEvidence")} {permissionHighlights.evidence}
                      </small>
                      <details className="permission-simulation-details">
                        <summary>{message(locale, "whyAllowedOrBlocked")}</summary>
                        <div className="permission-simulation-list">
                          {permissionRows.map((row) => (
                            <div
                              className={`permission-simulation-row ${row.prediction}`}
                              key={`${row.action}-${row.occurrence}-${row.consequence}`}
                            >
                              <strong>{row.action}</strong>
                              <span>{row.prediction} · {row.occurrence}</span>
                              <small>{row.owner} · {row.consequence}</small>
                            </div>
                          ))}
                        </div>
                      </details>
                    </>
                  )}
                </div>
              )}
              {selectedSkills.length > 0 || admittedBuiltinTools.length > 0 ? (
                <div className="attachment-chips" aria-label={message(locale, "selectedTools")}>
                  {admittedBuiltinTools.map((tool) => (
                    <span className="attachment-chip tool-selection-chip" key={tool}>
                      <span aria-hidden="true">⌁</span>
                      <span>{builtinToolLabels[tool] ?? tool}</span>
                      <small>GigaChat</small>
                      <button
                        aria-label={`${message(locale, "removeTool")} ${builtinToolLabels[tool] ?? tool}`}
                        onClick={() => setBuiltinTools((current) => current.filter((item) => item !== tool))}
                        type="button"
                      >×</button>
                    </span>
                  ))}
                  {selectedSkills.map((skill) => (
                    <span className="attachment-chip skill-mention-chip" key={skill.id}>
                      <span aria-hidden="true">✦</span>
                      <span title={`${skill.source} · ${skill.nativeName}`}>{skill.mention}</span>
                      <small>{skill.source}</small>
                      <button
                        aria-label={`${message(locale, "removeTool")} ${skill.mention}`}
                        onClick={() => setSelectedSkills((current) => current.filter((item) => item.id !== skill.id))}
                        type="button"
                      >×</button>
                    </span>
                  ))}
                </div>
              ) : null}
              {draftAttachments.length > 0 ? (
                <AttachmentGallery
                  attachments={draftAttachments}
                  locale={locale}
                  onRemove={(attachmentId) => attachmentActions.removeAttachment.mutate(attachmentId)}
                  removePending={attachmentActions.removeAttachment.isPending}
                />
              ) : null}
              {toolPickerOpen ? (
                <section
                  aria-label={message(locale, "toolPickerTitle")}
                  className="composer-tool-picker"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      setToolPickerOpen(false);
                    }
                  }}
                  role="dialog"
                >
                  <header>
                    <div>
                      <strong>{message(locale, "toolPickerTitle")}</strong>
                      <span>{message(locale, "toolPickerHint")}</span>
                    </div>
                    <button
                      aria-label={message(locale, "close")}
                      onClick={() => setToolPickerOpen(false)}
                      type="button"
                    >×</button>
                  </header>
                  <label className="tool-picker-search">
                    <span className="sr-only">{message(locale, "searchTools")}</span>
                    <input
                      autoFocus
                      onChange={(event) => setToolSearch(event.target.value)}
                      placeholder={message(locale, "searchTools")}
                      type="search"
                      value={toolSearch}
                    />
                  </label>
                  <div className="tool-picker-results">
                    {toolGroups.length === 0 ? (
                      <p className="empty-state">{message(locale, "noToolsFound")}</p>
                    ) : toolGroups.map((group) => (
                      <section className="tool-picker-group" key={group.category}>
                        <h3>{message(locale, toolCategoryMessageKeys[group.category])}</h3>
                        <div>
                          {group.options.map((option) => (
                            <button
                              aria-disabled={!option.selectable}
                              aria-pressed={option.selectable ? option.selected : undefined}
                              className={[
                                "tool-picker-option",
                                option.selected ? "selected" : "",
                                option.selectable ? "" : "unavailable",
                              ].filter(Boolean).join(" ")}
                              key={option.id}
                              onClick={() => toggleComposerTool(option)}
                              type="button"
                            >
                              <span aria-hidden="true">{option.selected ? "✓" : option.selectable ? "+" : "·"}</span>
                              <span>
                                <strong>{option.label}</strong>
                                <small>{option.detail}</small>
                                {option.reason === null ? null : <em>{option.reason}</em>}
                              </span>
                            </button>
                          ))}
                        </div>
                      </section>
                    ))}
                  </div>
                </section>
              ) : null}
              <textarea
                aria-label={message(locale, "composerPlaceholder")}
                aria-controls={atQuery === null ? undefined : "workspace-file-picker"}
                aria-expanded={atQuery !== null}
                disabled={startRun.isPending}
                onChange={(event) => {
                  setPrompt(event.target.value);
                  setComposerCaret(event.target.selectionStart);
                  setPreviewReport(null);
                }}
                onKeyDown={(event) => {
                  if (atQuery !== null && atCandidates.length > 0) {
                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      setAtSelection((current) => (current + 1) % atCandidates.length);
                      return;
                    }
                    if (event.key === "ArrowUp") {
                      event.preventDefault();
                      setAtSelection((current) => (current - 1 + atCandidates.length) % atCandidates.length);
                      return;
                    }
                    if (event.key === "Enter" && !event.metaKey && !event.ctrlKey) {
                      event.preventDefault();
                      chooseAtCandidate(atSelection);
                      return;
                    }
                  }
                  if (event.key === "Escape" && atQuery !== null) {
                    event.preventDefault();
                    setComposerCaret(atQuery.start);
                    return;
                  }
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && prompt.trim()) {
                    event.preventDefault();
                    startRun.mutate();
                  }
                }}
                onPaste={(event) => {
                  const files = Array.from(event.clipboardData.files);
                  if (files.length > 0) attachmentActions.uploadFiles.mutate({ files, source: "paste" });
                }}
                placeholder={message(locale, "composerPlaceholder")}
                ref={composerRef}
                rows={4}
                onSelect={(event) => setComposerCaret(event.currentTarget.selectionStart)}
                value={prompt}
              />
              {atQuery === null ? null : (
                <div className="workspace-file-picker" id="workspace-file-picker" role="listbox">
                  <div>
                    <strong>{locale === "ru" ? "Skills, плагины и файлы" : "Skills, plugins, and files"}</strong>
                    <small>
                      {locale === "ru"
                        ? "OpenAI bundled capabilities доступны здесь через @; для Codex Harness передаст нативный $-вызов."
                        : "OpenAI bundled capabilities are selectable with @; Harness sends Codex the native $ invocation."}
                    </small>
                  </div>
                  {availableSkillMentions.map((skill, index) => (
                    <button
                      aria-selected={index === atSelection}
                      className={index === atSelection ? "selected" : ""}
                      key={skill.id}
                      onClick={() => chooseSkill(skill)}
                      onMouseDown={(event) => event.preventDefault()}
                      role="option"
                      type="button"
                    >
                      <span>{skill.mention}</span>
                      <small>{skill.source} · Skill</small>
                    </button>
                  ))}
                  {workspaceFiles.isPending && workspaceFileCandidates.length === 0 ? (
                    <span className="muted-copy">{message(locale, "loading")}</span>
                  ) : workspaceFiles.isError ? (
                    <span className="error-state" role="alert">{String(workspaceFiles.error)}</span>
                  ) : workspaceFileCandidates.length === 0 && availableSkillMentions.length === 0 ? (
                    <span className="muted-copy">{message(locale, "noWorkspaceFiles")}</span>
                  ) : workspaceFileCandidates.map((file, index) => (
                    <button
                      aria-selected={index + availableSkillMentions.length === atSelection}
                      className={index + availableSkillMentions.length === atSelection ? "selected" : ""}
                      key={file.path}
                      onClick={() => chooseWorkspaceFile(file.path)}
                      onMouseDown={(event) => event.preventDefault()}
                      role="option"
                      type="button"
                    >
                      <span>@{file.path}</span>
                      <small>{file.kind} · {formatBytes(file.size_bytes)}</small>
                    </button>
                  ))}
                </div>
              )}
              {modelMenuOpen && modelSuggestions.length > 0 ? (
                <div
                  className="model-suggestions"
                  onMouseDown={(event) => event.preventDefault()}
                  role="listbox"
                >
                  {modelSuggestions.map((model) => (
                    <button
                      aria-selected={model === runConfig.model}
                      key={model}
                      onClick={() => {
                        setConfig("model", model);
                        setModelMenuOpen(false);
                        saveRunConfig.mutate({ default_model: model });
                      }}
                      role="option"
                      type="button"
                    >{model}</button>
                  ))}
                </div>
              ) : null}
              {advancedOpen ? (
                <section className="advanced-composer-panel" aria-label={message(locale, "advancedSettings")}>
                  <div className="advanced-panel-heading">
                    <div>
                      <strong>{message(locale, "advancedSettings")}</strong>
                      <span>{message(locale, "advancedSettingsHint")}</span>
                    </div>
                    <button aria-label={message(locale, "close")} onClick={() => setAdvancedOpen(false)} type="button">×</button>
                  </div>
                  <div className="advanced-config-grid">
                    <div className="capability-summary">
                      <span>{message(locale, "advancedDiagnostics")}</span>
                      <strong>{message(locale, "whyThisMode")}</strong>
                      <small>{selectedTransport?.detail ?? message(locale, "fastApiAuthority")}</small>
                      <code>{admittedTransport}</code>
                      {admissionReasons.map((reason) => (
                        <small key={reason}>{reason.replaceAll("_", " ")}</small>
                      ))}
                    </div>
                    <div className="capability-summary">
                      <span>{message(locale, "capability")}</span>
                      <strong>{capabilityCopy.label}</strong>
                      <small>{capabilityCopy.detail}</small>
                    </div>
                    <label>
                      <span>{message(locale, "apiMode")}</span>
                      <select
                        disabled={selectedHarness?.spec.supports_api_mode_selection === false}
                        onChange={(event) => setConfig("apiMode", event.target.value, "default_api_mode")}
                        value={runConfig.apiMode}
                      >
                        <option value="v2">/v2</option>
                        <option value="v1">/v1</option>
                      </select>
                    </label>
                    <label>
                      <span>{message(locale, "workspacePolicy")}</span>
                      <select
                        onChange={(event) => setAdvancedConfig((current) => ({ ...current, workspacePolicy: event.target.value }))}
                        value={advancedConfig.workspacePolicy}
                      >
                        <option value="auto">auto</option>
                        <option value="current">current</option>
                        <option value="worktree">worktree</option>
                        <option value="temp_copy">temp copy</option>
                      </select>
                    </label>
                    <label>
                      <span>{message(locale, "permissionProfile")}</span>
                      <select
                        onChange={(event) => setAdvancedConfig((current) => ({ ...current, permissionProfile: event.target.value }))}
                        value={advancedConfig.permissionProfile}
                      >
                        <option value="interactive">interactive</option>
                        <option value="review_every_action">review every action</option>
                      </select>
                    </label>
                  </div>
                  <fieldset className="developer-options">
                    <legend>{message(locale, "developerOptions")}</legend>
                    <label>
                      <input checked={advancedConfig.dryRun} onChange={(event) => setAdvancedConfig((current) => ({ ...current, dryRun: event.target.checked }))} type="checkbox" />
                      <span><strong>{message(locale, "previewExecution")}</strong><small>{message(locale, "previewExecutionHint")}</small></span>
                    </label>
                  </fieldset>
                  <p className="runtime-owned-copy">{message(locale, "streamRuntimeOwned")}</p>
                </section>
              ) : null}
              {reviewedRoute.controls}
              <div className="composer-footer">
                <div className="composer-footer-left">
                  <div className="composer-controls" aria-label={message(locale, "runConfiguration")}>
                    <input
                      className="sr-only"
                      multiple
                      onChange={(event) => {
                        const files = Array.from(event.target.files ?? []);
                        if (files.length > 0) attachmentActions.uploadFiles.mutate({ files, source: "upload" });
                        event.target.value = "";
                      }}
                      ref={fileInputRef}
                      type="file"
                    />
                    <div className="plus-menu-wrapper">
                      <button
                        aria-expanded={plusMenuOpen}
                        aria-label={message(locale, "moreComposerActions")}
                        className="attach-button"
                        disabled={attachmentActions.uploadFiles.isPending}
                        onClick={() => setPlusMenuOpen((open) => !open)}
                        title={message(locale, "moreComposerActions")}
                        type="button"
                      >
                        <span aria-hidden="true">＋</span>
                      </button>
                      {plusMenuOpen ? (
                        <div className="plus-menu" role="menu">
                          <button onClick={() => { setPlusMenuOpen(false); fileInputRef.current?.click(); }} role="menuitem" type="button">
                            <span aria-hidden="true">◇</span>
                            <span><strong>{message(locale, "attachFiles")}</strong><small>{message(locale, "attachFilesHint")}</small></span>
                          </button>
                          <button onClick={() => {
                            setPlusMenuOpen(false);
                            setToolPickerOpen(true);
                            setToolSearch("");
                          }} role="menuitem" type="button">
                            <span aria-hidden="true">✦</span>
                            <span><strong>{message(locale, "toolsAndIntegrations")}</strong><small>{message(locale, "toolPickerMenuHint")}</small></span>
                          </button>
                        </div>
                      ) : null}
                    </div>
                    <label className="compact-control">
                      <span>{message(locale, "taskType")}</span>
                      <select
                        aria-label={message(locale, "taskType")}
                        onChange={(event) => selectWorkbenchKind(
                          event.target.value as WorkbenchKind,
                        )}
                        value={productSelection.kind}
                      >
                        <option value="coding_agent">{message(locale, "codingAgent")}</option>
                        <option value="direct_chat">{message(locale, "directChat")}</option>
                      </select>
                    </label>
                    <label className="compact-control">
                      <span>{message(locale, "harness")}</span>
                      <select
                        aria-label={message(locale, "harness")}
                        onChange={(event) => setConfig("harnessId", event.target.value, "default_harness_id")}
                        value={runConfig.harnessId}
                      >
                        {selectableHarnesses.map((harness) => (
                          <option
                            disabled={harness.availability?.status === "unavailable"}
                            key={harness.spec.id}
                            value={harness.spec.id}
                          >
                            {harness.spec.title || harness.spec.id}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="compact-control model-control">
                      <span>{message(locale, "model")}</span>
                      <div
                        className="model-picker"
                        onBlur={(event) => {
                          if (!event.currentTarget.contains(event.relatedTarget)) {
                            setModelMenuOpen(false);
                            saveRunConfig.mutate({ default_model: runConfig.model.trim() || null });
                          }
                        }}
                      >
                        <button
                          aria-label={message(locale, "model")}
                          aria-expanded={modelMenuOpen}
                          aria-haspopup="listbox"
                          className="model-select-button"
                          disabled={selectedHarness?.spec.supports_model_selection === false}
                          onClick={() => setModelMenuOpen((open) => !open)}
                          type="button"
                        >
                          <span>{runConfig.model || message(locale, "noDefaultModel")}</span>
                          <span aria-hidden="true">⌄</span>
                        </button>
                      </div>
                    </div>
                    {isReasoningModel(runConfig.model) ? (
                      <label className="compact-control reasoning-control">
                        <span>{message(locale, "reasoning")}</span>
                        <select
                          aria-label={message(locale, "reasoning")}
                          onChange={(event) => setReasoningEffort(event.target.value as ReasoningEffort)}
                          value={reasoningEffort}
                        >
                          <option value="low">low</option>
                          <option value="medium">medium</option>
                          <option value="high">high</option>
                        </select>
                      </label>
                    ) : null}
                    <label className="compact-control mode-control">
                      <span>{message(locale, "intent")}</span>
                      <select
                        aria-label={message(locale, "intent")}
                        onChange={(event) => updateProductSelection({
                          intent: event.target.value as ProductExecutionSelection["intent"],
                        })}
                        value={productSelection.intent}
                      >
                        <option value="ask">{message(locale, "ask")}</option>
                        <option value="review">{message(locale, "review")}</option>
                        <option value="change">{message(locale, "change")}</option>
                      </select>
                    </label>
                    <label className="compact-control mode-control">
                      <span>{message(locale, "authority")}</span>
                      <select
                        aria-label={message(locale, "authority")}
                        onChange={(event) => updateProductSelection({
                          authority: event.target.value as ProductExecutionSelection["authority"],
                        })}
                        value={productSelection.authority}
                      >
                        <option value="read_only">{message(locale, "readOnly")}</option>
                        <option value="workspace_write">{message(locale, "workspaceWrite")}</option>
                      </select>
                    </label>
                    <button
                      aria-expanded={advancedOpen}
                      className={advancedConfig.dryRun ? "advanced-button active" : "advanced-button"}
                      onClick={() => setAdvancedOpen((open) => !open)}
                      type="button"
                    >
                      {message(locale, "advanced")}
                    </button>
                  </div>
                  <span className={`stream-indicator ${stream.status}`}>
                    {attachmentActions.uploadFiles.isPending ? message(locale, "uploadingFiles") : stream.status.replaceAll("_", " ")}
                  </span>
                </div>
                {selectedRunActive && selectedRunId !== undefined ? (
                  <button
                    className="danger-button"
                    disabled={cancelRun.isPending}
                    onClick={() => cancelRun.mutate({
                      nativeProcessId: selectedNativeProcessId,
                      runId: selectedRunId,
                    })}
                    type="button"
                  >
                    {message(locale, "cancelRun")}
                  </button>
                ) : (
                  <button className="primary-button" disabled={!prompt.trim() || startRun.isPending || reviewedRoute.pending} type="submit">
                    {message(locale, advancedConfig.dryRun ? "previewExecution" : "runTask")}
                  </button>
                )}
              </div>
              {startRun.isError || attachmentActions.uploadFiles.isError || attachmentActions.attachWorkspaceFile.isError || attachmentActions.removeAttachment.isError || saveRunConfig.isError ? (
                <div className="error-state" role="alert">
                  {String(startRun.error ?? attachmentActions.uploadFiles.error ?? attachmentActions.attachWorkspaceFile.error ?? attachmentActions.removeAttachment.error ?? saveRunConfig.error)}
                </div>
              ) : null}
            </form>
          </>
        )}
      </main>

      {rightOpen ? (
        <>
          <ResizeHandle
            label="Resize run inspector"
            onReset={() => setRightWidth(320)}
            onResize={(delta) => setRightWidth((value) => clamp(value - delta, 280, 420))}
          />
          <aside className="run-readiness" aria-label={message(locale, "readiness")}>
            <div className="panel-heading">
              <div>
                <span className="section-kicker">{message(locale, "readiness")}</span>
                <h2>{message(locale, "startReady")}</h2>
              </div>
              <button aria-label={message(locale, "collapse")} onClick={() => setRightOpen(false)} type="button">×</button>
            </div>
            <div className={selectedTransport?.status === "blocked" || admissionStatus === "blocked" || previewReport?.hard_block ? "readiness-callout blocked" : admissionStatus === "degraded" ? "readiness-callout degraded" : "readiness-callout success"}>
              <strong>{message(locale, selectedTransport?.status === "blocked" || admissionStatus === "blocked" || previewReport?.hard_block ? "blocked" : admissionStatus === "degraded" ? "degraded" : "ready")}</strong>
              <span>{selectedTransport?.detail ?? message(locale, "fastApiAuthority")}</span>
              {selectedTransport?.status === "blocked" && selectedTransport.remediation ? (
                <code>{selectedTransport.remediation}</code>
              ) : null}
            </div>
            <EnvironmentCard
              commitAction={environmentActions.commitAction}
              pushAction={environmentActions.pushAction}
              pullRequestAction={environmentActions.pullRequestAction}
              environment={environmentView}
              error={environment.isError}
              locale={locale}
              pending={environment.isPending}
            />
            <section className="inspector-section">
              <h3>{message(locale, "executionPlan")}</h3>
              <dl className="plan-fields">
                <div><dt>{message(locale, "taskType")}</dt><dd>{capabilityCopy.label}</dd></div>
                <div><dt>{message(locale, "intent")}</dt><dd>{message(locale, productSelection.intent)}</dd></div>
                <div><dt>{message(locale, "authority")}</dt><dd>{message(locale, productSelection.authority === "read_only" ? "readOnly" : "workspaceWrite")}</dd></div>
                <div><dt>{message(locale, "workspacePolicy")}</dt><dd>{message(locale, "workspacePolicyValue")}</dd></div>
                <div><dt>{message(locale, "route")}</dt><dd>{runConfig.model || "GigaChat"} · /{runConfig.apiMode}</dd></div>
                <div><dt>{message(locale, "harness")}</dt><dd>{runConfig.harnessId}</dd></div>
              </dl>
            </section>
            {selectedRun?.provider_session ? (
              <section className="inspector-section provider-session-card">
                <h3>{message(locale, "providerSession")}</h3>
                <dl className="plan-fields">
                  <div><dt>{message(locale, "structuredLink")}</dt><dd>{shortId(selectedRun.provider_session.link_id ?? "-")}</dd></div>
                  <div><dt>{message(locale, "session")}</dt><dd>{shortId(selectedRun.provider_session.external_session_id ?? "-")}</dd></div>
                  <div><dt>{message(locale, "status")}</dt><dd>{selectedRun.provider_session.recovery_state ?? "active"}</dd></div>
                  <div><dt>{message(locale, "connection")}</dt><dd>{selectedRun.provider_session.protocol ?? "structured"}</dd></div>
                </dl>
              </section>
            ) : null}
            <Progression current={stage} locale={locale} />
            <section className="inspector-section next-actions">
              <h3>{message(locale, "nextActions")}</h3>
              {selectedRunId === undefined ? (
                <span className="muted-copy">{message(locale, "runTask")}</span>
              ) : (
                <>
                  <Link params={{ runId: selectedRunId }} to="/web/runs/$runId">{message(locale, "reviewDiff")} <span>›</span></Link>
                  <button onClick={() => openInbox("approvals")} type="button">{message(locale, "requestApproval")} <span>›</span></button>
                  <Link params={{ runId: selectedRunId }} to="/web/runs/$runId">{message(locale, "apply")} <span>›</span></Link>
                  <Link params={{ runId: selectedRunId }} to="/web/runs/$runId">{message(locale, "promote")} <span>›</span></Link>
                </>
              )}
              {providerHandoffActions.includes("open_provider_ui") ? (
                <button disabled={openInProvider.isPending} onClick={() => openInProvider.mutate()} type="button">
                  {message(locale, "openProviderUi")} <span>↗</span>
                </button>
              ) : null}
              {openInProvider.data ? (
                <div className="provider-handoff-instruction" role="status">
                  <span>{openInProvider.data.handoff.instruction}</span>
                  {openInProvider.data.handoff.command.length > 0 ? <code>{openInProvider.data.handoff.command.join(" ")}</code> : null}
                </div>
              ) : null}
              {openInProvider.isError ? <span className="mutation-error">{openInProvider.error.message}</span> : null}
            </section>
          </aside>
        </>
      ) : (
        <button className="panel-restore right" onClick={() => setRightOpen(true)} type="button">
          <span>‹</span><span>{message(locale, "restore")}</span>
        </button>
      )}
      {sessionConfirmation === null ? null : (
        <SessionConfirmationDialog
          action={sessionConfirmation.action}
          error={changeSession.isError}
          locale={locale}
          onCancel={() => {
            if (!changeSession.isPending) setSessionConfirmation(null);
          }}
          onConfirm={() => changeSession.mutate(sessionConfirmation)}
          pending={changeSession.isPending}
          title={sessionConfirmation.title}
        />
      )}
      <CompletionNotices
        locale={locale}
        notices={completionNotifications.notices}
        onOpen={(notice) => {
          completionNotifications.dismiss(notice.id);
          void navigate({
            params: { sessionId: notice.sessionId },
            to: "/web/work/$sessionId",
          });
        }}
      />
    </div>
  );
}

function ResizeHandle({
  label,
  onReset,
  onResize,
}: {
  label: string;
  onReset: () => void;
  onResize: (delta: number) => void;
}) {
  return (
    <div
      aria-label={label}
      className="resize-handle"
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        if (event.key === "ArrowLeft") onResize(-16);
        if (event.key === "ArrowRight") onResize(16);
        if (event.key === "Home") onReset();
      }}
      onPointerDown={(event) => {
        const start = event.clientX;
        let previous = start;
        const move = (moveEvent: PointerEvent) => {
          onResize(moveEvent.clientX - previous);
          previous = moveEvent.clientX;
        };
        const stop = () => {
          globalThis.removeEventListener("pointermove", move);
          globalThis.removeEventListener("pointerup", stop);
        };
        globalThis.addEventListener("pointermove", move);
        globalThis.addEventListener("pointerup", stop);
      }}
      role="separator"
      tabIndex={0}
    />
  );
}

function ListSkeleton({ rows }: { rows: number }) {
  return <>{Array.from({ length: rows }, (_, index) => <div className="skeleton-row" key={index} />)}</>;
}

function ReadError({ locale }: { locale: "en" | "ru" }) {
  return <div className="error-state" role="alert">{message(locale, "boundedDataUnavailable")}</div>;
}

function loadWidth(key: "left" | "right", fallback: number): number {
  try {
    const stored = JSON.parse(localStorage.getItem(layoutKey) ?? "{}") as Record<string, unknown>;
    return typeof stored[key] === "number" ? stored[key] : fallback;
  } catch {
    return fallback;
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function openInbox(kind: "approvals" | "attention") {
  globalThis.dispatchEvent(new CustomEvent("cockpit:open-inbox", { detail: kind }));
}
