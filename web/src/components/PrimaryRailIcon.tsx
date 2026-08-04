import type { SurfaceId } from "../navigation";

interface PrimaryRailIconProps {
  surface: SurfaceId;
}

export function PrimaryRailBrand() {
  return (
    <picture>
      <source
        media="(prefers-color-scheme: dark)"
        srcSet="/web/assets/brand/gigaloom-mark-dark.svg"
      />
      <img
        src="/web/assets/brand/gigaloom-mark.svg"
        alt=""
        width="30"
        height="30"
      />
    </picture>
  );
}

export function PrimaryRailIcon({ surface }: PrimaryRailIconProps) {
  const common = {
    "aria-hidden": true,
    className: "rail-icon",
    focusable: "false",
    viewBox: "0 0 24 24",
  } as const;

  switch (surface) {
    case "work":
      return (
        <svg {...common} data-icon="workbench">
          <circle cx="12" cy="12" r="3.25" />
          <path d="M12 2.75v3M12 18.25v3M2.75 12h3M18.25 12h3M5.46 5.46l2.12 2.12M16.42 16.42l2.12 2.12M18.54 5.46l-2.12 2.12M7.58 16.42l-2.12 2.12" />
        </svg>
      );
    case "inbox":
      return (
        <svg {...common} data-icon="inbox">
          <path d="M4 4.5h16v15H4z" />
          <path className="rail-icon-accent" d="M4 14h4l2 2h4l2-2h4" />
        </svg>
      );
    case "library":
      return (
        <svg {...common} data-icon="library">
          <path d="M4 4h5v16H4zM10.5 4h4v16h-4zM16 5l3.5-1 3 15-3.5 1z" />
        </svg>
      );
    case "more":
      return (
        <svg {...common} data-icon="more">
          <circle cx="5" cy="12" r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="19" cy="12" r="1.5" />
        </svg>
      );
    case "automations":
      return (
        <svg {...common} data-icon="automation">
          <circle cx="6" cy="5" r="2" />
          <circle cx="18" cy="8" r="2" />
          <circle cx="18" cy="18" r="2" />
          <path d="M8 5h2a3 3 0 0 1 3 3v7a3 3 0 0 0 3 3M13 10a3 3 0 0 1 3-2" />
        </svg>
      );
  }
}
