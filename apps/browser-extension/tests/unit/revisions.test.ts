import { describe, expect, it } from "vitest";

import { supersedes } from "../../src/background/revisions";

const SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e1";
const NEXT_SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e2";

describe("pushed session state", () => {
  it("takes the first frame and any newer or repeated revision of the same session", () => {
    expect(supersedes(null, { sessionId: SESSION_ID, revision: 1 })).toBe(true);
    expect(
      supersedes({ sessionId: SESSION_ID, revision: 4 }, { sessionId: SESSION_ID, revision: 5 }),
    ).toBe(true);
    expect(
      supersedes({ sessionId: SESSION_ID, revision: 4 }, { sessionId: SESSION_ID, revision: 4 }),
    ).toBe(true);
  });

  it("ignores an older revision of the same session", () => {
    expect(
      supersedes({ sessionId: SESSION_ID, revision: 4 }, { sessionId: SESSION_ID, revision: 3 }),
    ).toBe(false);
  });

  it("starts a later session's revisions afresh", () => {
    expect(
      supersedes(
        { sessionId: SESSION_ID, revision: 12 },
        { sessionId: NEXT_SESSION_ID, revision: 1 },
      ),
    ).toBe(true);
  });
});
