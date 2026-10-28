/** The Case File as an object: a coral folder, a stack of sheets, index dividers, and three of the cast looking out over the top. */
"use client";

import { useRef, useState, type JSX, type KeyboardEvent } from "react";

import { Cameo, ENTRANCE_MS } from "../pieces";
import { usePointerGaze } from "../characters/usePointerGaze";
import { SHOPALYTICS } from "../story/shopalyticsFixture";
import { CASE_FILE_TITLE } from "./CaseFileSummary";
import { CASE_FILE_SHEETS, caseFileSheet, type SheetId } from "./caseFileSheets";

/** Black built it, mint shipped it, peach validated it: the three peer over the folder's edge and watch the pointer. */
const PEEKERS = [
  { colour: "black", size: 108, left: 0, top: 16 },
  { colour: "mint", size: 124, left: 122, top: 0 },
  { colour: "peach", size: 112, left: 258, top: 12 },
] as const;

function Peek(props: Readonly<{ reducedMotion: boolean }>): JSX.Element {
  const anchor = useRef<HTMLDivElement>(null);
  const [gazeX, gazeY] = usePointerGaze(anchor, 360);
  return (
    <div className="cf-peek" ref={anchor} aria-hidden="true">
      {PEEKERS.map((peeker) => (
        <Cameo key={peeker.colour} colour={peeker.colour} state="idle" park={props.reducedMotion ? ENTRANCE_MS : null} size={peeker.size} gazeX={gazeX} gazeY={gazeY} testId={`casefile-agent-${peeker.colour}`} style={{ left: peeker.left, top: peeker.top }} />
      ))}
    </div>
  );
}

export function CaseFileFolder(props: Readonly<{ reducedMotion: boolean }>): JSX.Element {
  const [sheetId, setSheetId] = useState<SheetId>("summary");
  const dividerRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const sheet = caseFileSheet(sheetId);

  function moveSelection(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    let next: number;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowRight":
        next = (index + 1) % CASE_FILE_SHEETS.length;
        break;
      case "ArrowUp":
      case "ArrowLeft":
        next = (index + CASE_FILE_SHEETS.length - 1) % CASE_FILE_SHEETS.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = CASE_FILE_SHEETS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    setSheetId(CASE_FILE_SHEETS[next].id);
    dividerRefs.current[next]?.focus();
  }

  return (
    <div className="cf-folder" data-testid="casefile-folder" data-sheet={sheetId}>
      <Peek reducedMotion={props.reducedMotion} />
      <div className="cf-folder-tab">
        {SHOPALYTICS.client} · {CASE_FILE_TITLE}
      </div>
      <div className="cf-folder-body">
        <div className="cf-sheets">
          <div className="cf-sheet-back cf-sheet-back--far" aria-hidden="true" />
          <div className="cf-sheet-back" aria-hidden="true" />
          <article key={sheet.id} className="cf-sheet" role="tabpanel" id={`casefile-sheet-${sheet.id}`} aria-labelledby={`casefile-divider-${sheet.id}`} data-testid="casefile-sheet">
            <sheet.Body show={setSheetId} />
          </article>
        </div>
      </div>
      <div className="cf-dividers" role="tablist" aria-label="Case File sheets" aria-orientation="vertical">
        {CASE_FILE_SHEETS.map((item, index) => (
          <button
            key={item.id}
            ref={(node) => {
              dividerRefs.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`casefile-divider-${item.id}`}
            className="cf-divider"
            data-testid={`casefile-divider-${item.id}`}
            aria-selected={item.id === sheetId}
            aria-controls={`casefile-sheet-${item.id}`}
            tabIndex={item.id === sheetId ? 0 : -1}
            onClick={() => setSheetId(item.id)}
            onKeyDown={(event) => moveSelection(event, index)}
          >
            {item.tab}
          </button>
        ))}
      </div>
    </div>
  );
}
