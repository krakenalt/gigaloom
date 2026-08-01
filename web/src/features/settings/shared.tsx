import {
  Component,
  type ReactNode,
  Suspense,
  useEffect,
  useRef,
  useState,
} from "react";

import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

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
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}

export function Boundary({ effect, source }: { effect: string; source: string }) {
  return (
    <div className="settings-boundary">
      <span>{source}</span>
      <span>{effect.replaceAll("_", " ")}</span>
    </div>
  );
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
