import {
  Component,
  type ReactNode,
  Suspense,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { PreferencesContext } from "../../preferences-context";

export interface SettingsSectionProps {
  revision: string;
}

export function DeferredSettingsSection({
  children,
  description,
  id,
  locale,
  title,
}: {
  children: ReactNode;
  description: string;
  id: string;
  locale: LocalePreference;
  title: string;
}) {
  const sectionRef = useRef<HTMLElement>(null);
  const [activated, setActivated] = useState(
    () => globalThis.location?.hash === `#settings-${id}`,
  );

  useEffect(() => {
    if (activated) return;
    const section = sectionRef.current;
    if (section === null || typeof IntersectionObserver !== "function") {
      setActivated(true);
      return;
    }
    const revealHashTarget = () => {
      if (globalThis.location.hash === `#settings-${id}`) setActivated(true);
    };
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setActivated(true);
      },
      { rootMargin: "480px 0px" },
    );
    observer.observe(section);
    globalThis.addEventListener("hashchange", revealHashTarget);
    return () => {
      observer.disconnect();
      globalThis.removeEventListener("hashchange", revealHashTarget);
    };
  }, [activated, id]);

  return (
    <section
      className="settings-section settings-section-deferred"
      data-activated={activated}
      id={`settings-${id}`}
      ref={sectionRef}
    >
      <header>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      {activated ? (
        <SettingsSectionErrorBoundary locale={locale}>
          <Suspense fallback={<SectionPending locale={locale} />}>
            {children}
          </Suspense>
        </SettingsSectionErrorBoundary>
      ) : (
        <div className="settings-section-placeholder" aria-hidden="true" />
      )}
    </section>
  );
}

export function SettingsSection({
  children,
  description,
  id,
  title,
}: {
  children: ReactNode;
  description: string;
  id: string;
  title: string;
}) {
  return (
    <section className="settings-section" id={`settings-${id}`}>
      <header>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      {children}
    </section>
  );
}

export function SectionPending({ locale }: { locale: LocalePreference }) {
  return (
    <div aria-busy="true" className="settings-section-skeleton">
      <span>{message(locale, "loading")}</span>
      <i />
      <i />
    </div>
  );
}

export function SectionError({
  error,
  locale,
}: {
  error?: Error | null;
  locale: LocalePreference;
}) {
  return (
    <div className="settings-section-error" role="alert">
      <strong>{message(locale, "settingsUnavailable")}</strong>
      {error === null || error === undefined ? null : <span>{error.message}</span>}
    </div>
  );
}

export function Fact({
  label,
  mono = false,
  value,
}: {
  label: string;
  mono?: boolean;
  value: string;
}) {
  const locale = useContext(PreferencesContext)?.preferences.locale ?? "en";
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>
        {localizedSettingValue(locale, value)}
      </dd>
    </div>
  );
}

export function Boundary({ effect, source }: { effect: string; source: string }) {
  const locale = useContext(PreferencesContext)?.preferences.locale ?? "en";
  return (
    <div className="settings-boundary">
      <span>{localizedBoundary(locale, source)}</span>
      <span>{localizedBoundary(locale, effect)}</span>
    </div>
  );
}

const settingValues = {
  en: {
    "Continue the pending OS-local bootstrap.":
      "Continue the pending OS-local bootstrap.",
    "Open this loopback UI and recover local access.":
      "Open this loopback UI and recover local access.",
    "Rotate or log out this OS-local browser session.":
      "Rotate or log out this OS-local browser session.",
    "claude auth logout followed by claude auth login":
      "Sign out of Claude Code, then sign in again",
    "codex login status": "Codex CLI sign-in check",
    "claude auth status": "Claude Code sign-in check",
    "retry provider-owned login": "Try signing in through the provider again",
    "return to the provider-owned interactive authentication chooser":
      "Open the Gemini CLI sign-in chooser again",
    reviewed_provider_authentication_evidence_v1:
      "Reviewed provider authentication contract",
    auto: "Automatic",
    available: "Available",
    built_in: "Built in",
    current: "Current folder",
    disabled: "Disabled",
    enabled: "Enabled",
    expired: "Expired",
    false: "No",
    healthy: "Healthy",
    interactive: "Ask when needed",
    local: "Local",
    logged_out: "Signed out",
    new_runs: "New runs",
    not_checked: "Not checked",
    pending: "Sign-in pending",
    project_state: "Project settings",
    ready: "Ready",
    review_every_action: "Review every action",
    revoked: "Revoked",
    true: "Yes",
    trusted: "Trusted",
    unattended: "Run without prompts",
    unavailable: "Unavailable",
    unconfigured: "Not configured",
    unknown: "Status unknown",
    worktree: "Separate Git worktree",
  },
  ru: {
    "Continue the pending OS-local bootstrap.":
      "Завершите начатый локальный вход.",
    "Open this loopback UI and recover local access.":
      "Откройте локальный интерфейс на этом компьютере и восстановите доступ.",
    "Rotate or log out this OS-local browser session.":
      "Можно обновить текущую сессию или выйти из неё.",
    "claude auth logout followed by claude auth login":
      "Выйдите из Claude Code и войдите снова",
    "codex login status": "Проверка входа в Codex CLI",
    "claude auth status": "Проверка входа в Claude Code",
    "retry provider-owned login": "Повторите вход через провайдера",
    "return to the provider-owned interactive authentication chooser":
      "Снова откройте выбор способа входа в Gemini CLI",
    reviewed_provider_authentication_evidence_v1:
      "Проверенные правила входа провайдера",
    auto: "Автоматически",
    available: "Доступно",
    built_in: "Встроено",
    current: "Текущая папка",
    disabled: "Выключено",
    enabled: "Включено",
    expired: "Истекло",
    false: "Нет",
    healthy: "Работает",
    interactive: "Спрашивать при необходимости",
    local: "Локально",
    logged_out: "Вход не выполнен",
    new_runs: "Новые запуски",
    not_checked: "Не проверено",
    pending: "Ожидается завершение входа",
    project_state: "Настройки проекта",
    ready: "Готово",
    review_every_action: "Подтверждать каждое действие",
    revoked: "Отозвано",
    true: "Да",
    trusted: "Доверенный",
    unattended: "Работать без подтверждений",
    unavailable: "Недоступно",
    unconfigured: "Не настроено",
    unknown: "Состояние неизвестно",
    worktree: "Отдельный Git worktree",
  },
} as const;

const boundaryValues = {
  en: {
    backend_versioned: "Saved by GigaLoom",
    browser: "This browser",
    built_in: "Built in",
    current_browser: "Current browser",
    fake_broker: "Demo credential broker",
    fork_or_new_codex_session_required: "New or forked Codex chat",
    fork_or_new_session_required: "New or forked chat",
    harness_settings: "Agent defaults",
    isolated_provider_account_home: "Isolated provider account",
    lease_scoped: "For this lease only",
    live: "Applies now",
    new_runs: "New runs",
    new_session_required: "New chat",
    os_local_private_store: "Private local storage",
    project_state: "Project settings",
    project_config: "Project configuration",
    provider_owned_cli: "Provider CLI",
    restart_required: "Restart required",
    runtime_aggregates: "Local diagnostics",
    user_registry: "Provider registry",
  },
  ru: {
    backend_versioned: "Хранит GigaLoom",
    browser: "Этот браузер",
    built_in: "Встроено",
    current_browser: "Текущий браузер",
    fake_broker: "Учебное хранилище доступов",
    fork_or_new_codex_session_required: "Новый чат Codex или его ответвление",
    fork_or_new_session_required: "Новый чат или его ответвление",
    harness_settings: "Настройки агента",
    isolated_provider_account_home: "Отдельный профиль провайдера",
    lease_scoped: "Только на срок разрешения",
    live: "Применяется сразу",
    new_runs: "Для новых запусков",
    new_session_required: "Для нового чата",
    os_local_private_store: "Защищённое локальное хранилище",
    project_state: "Настройки проекта",
    project_config: "Настройки проекта",
    provider_owned_cli: "CLI провайдера",
    restart_required: "Нужен перезапуск",
    runtime_aggregates: "Локальная диагностика",
    user_registry: "Реестр провайдеров",
  },
} as const;

export function localizedSettingValue(
  locale: LocalePreference,
  value: string,
): string {
  return settingValues[locale][value as keyof typeof settingValues.en] ?? value;
}

function localizedBoundary(locale: LocalePreference, value: string): string {
  return boundaryValues[locale][value as keyof typeof boundaryValues.en]
    ?? value.replaceAll("_", " ");
}

export function ProviderField({
  children,
  error,
  label,
}: {
  children: ReactNode;
  error?: string;
  label: string;
}) {
  return (
    <label>
      {label}
      {children}
      {error === undefined ? null : (
        <span className="settings-field-error">{error}</span>
      )}
    </label>
  );
}

interface SettingsSectionErrorBoundaryProps {
  children: ReactNode;
  locale: LocalePreference;
}

interface SettingsSectionErrorBoundaryState {
  error: Error | null;
}

class SettingsSectionErrorBoundary extends Component<
  SettingsSectionErrorBoundaryProps,
  SettingsSectionErrorBoundaryState
> {
  state: SettingsSectionErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): SettingsSectionErrorBoundaryState {
    return { error };
  }

  render() {
    return this.state.error === null ? this.props.children : (
      <SectionError error={this.state.error} locale={this.props.locale} />
    );
  }
}
