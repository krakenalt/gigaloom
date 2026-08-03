import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AutomationsDestination } from "./AutomationsDestination";
import { InboxDestination } from "./InboxDestination";
import { LibraryDestination } from "./LibraryDestination";
import { MoreDestination } from "./MoreDestination";
import { WorkDestination } from "./WorkDestination";
import { workFirstDestinationIds } from "./destination-contract";

describe("work-first destination modules", () => {
  it("freezes the five destination owners without registering root routes", () => {
    expect(workFirstDestinationIds).toEqual([
      "work",
      "inbox",
      "automations",
      "library",
      "more",
    ]);
  });

  it("renders each destination as a labelled feature-local region", () => {
    const fixtures = [
      <WorkDestination
        composer={<div>Composer</div>}
        context={<div>Context</div>}
        description="Outcome-first work"
        key="work"
        narrative={<div>Narrative</div>}
        title="Work"
      />,
      <InboxDestination
        description="Items that need attention"
        items={<div>Items</div>}
        key="inbox"
        preview={<div>Preview</div>}
        title="Inbox"
      />,
      <AutomationsDestination
        activity={<div>Activity</div>}
        authoring={<div>Authoring</div>}
        description="Reusable work"
        key="automations"
        title="Automations"
      />,
      <LibraryDestination
        catalog={<div>Catalog</div>}
        description="Agents, tools, and evidence"
        detail={<div>Detail</div>}
        key="library"
        title="Library"
      />,
      <MoreDestination
        description="Secondary destinations"
        key="more"
        sections={<div>Settings</div>}
        title="More"
      />,
    ];

    const markup = fixtures.map((fixture) => renderToStaticMarkup(fixture));

    workFirstDestinationIds.forEach((destination, index) => {
      expect(markup[index]).toContain(`data-destination="${destination}"`);
      expect(markup[index]).toContain(
        `aria-labelledby="work-first-${destination}-title"`,
      );
    });
  });
});
