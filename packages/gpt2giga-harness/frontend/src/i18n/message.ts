import type { LocalePreference } from "../preferences";

import { catalogs } from "./catalogs";
import type { MessageKey } from "./keys";

export function message(locale: LocalePreference, key: MessageKey): string {
  return catalogs[locale][key];
}
