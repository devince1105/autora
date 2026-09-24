// What changed between two versions of an article, paragraph by paragraph (D-046).
//
// An approver of a revision reads the new version against the one on the site. Paragraphs are
// matched whole, by their text: the longest run of paragraphs the two versions share stays as it
// is, and everything else is removed (only in the old one) or added (only in the new one). A
// paragraph edited by one word shows as one removed and one added, next to each other — enough
// to find, and no pretence of knowing which words a writer meant to change.

export type DiffOp = "same" | "removed" | "added";

export interface DiffLine {
  op: DiffOp;
  text: string;
}

export function diffParagraphs(before: readonly string[], after: readonly string[]): DiffLine[] {
  const n = before.length;
  const m = after.length;
  // longest common subsequence, filled from the end so it can be read forwards
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = before[i] === after[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) {
      out.push({ op: "same", text: before[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ op: "removed", text: before[i++] });
    } else {
      out.push({ op: "added", text: after[j++] });
    }
  }
  while (i < n) out.push({ op: "removed", text: before[i++] });
  while (j < m) out.push({ op: "added", text: after[j++] });
  return out;
}

/** Whether a diff changed anything at all. */
export function changed(lines: readonly DiffLine[]): boolean {
  return lines.some((line) => line.op !== "same");
}
