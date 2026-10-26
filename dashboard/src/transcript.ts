import type { Utterance } from "./types";

function speakerName(speaker: string): string {
  if (["agent", "assistant"].includes(speaker)) return "agent";
  if (["caller", "user", "recipient"].includes(speaker)) return "recipient";
  return speaker;
}

function gapBetween(previous: Utterance, next: Utterance): number | undefined {
  if (typeof previous.ms === "number" && Number.isFinite(previous.ms) &&
      typeof next.ms === "number" && Number.isFinite(next.ms)) {
    return next.ms - previous.ms;
  }
  const before = Date.parse(previous.at || previous.timestamp || "");
  const after = Date.parse(next.at || next.timestamp || "");
  return Number.isFinite(before) && Number.isFinite(after) ? after - before : undefined;
}

function joinFragments(before: string, after: string): string {
  // Preserve supplied whitespace. Standalone words need a space; punctuation,
  // closing brackets and contraction suffixes attach to the preceding word.
  if (!before || !after || /\s$/.test(before) || /^\s/.test(after) ||
      /^[.,!?;:%)\]}]/u.test(after) || /[([{“‘]$/u.test(before) ||
      /^['’](?:s|re|ve|ll|d|m|t)\b/iu.test(after)) {
    return before + after;
  }
  return before + " " + after;
}

/** Presentation only: source fragments, IDs and exported records stay intact. */
export function groupTranscript(fragments: readonly Utterance[]): Utterance[] {
  const turns: Utterance[] = [];
  let previous: Utterance | undefined;
  for (const fragment of fragments) {
    if (!fragment.text.trim()) continue;
    const speaker = speakerName(fragment.speaker);
    const last = turns.at(-1);
    const gap = previous ? gapBetween(previous, fragment) : undefined;
    const continuous = gap === undefined || (gap >= 0 && gap <= 8000);
    if (last && last.speaker === speaker && speaker !== "system" && continuous) {
      last.text = joinFragments(last.text, fragment.text);
    } else {
      // Keep the turn's initial event ID and timestamp stable while it grows.
      turns.push({ ...fragment, speaker });
    }
    previous = fragment;
  }
  return turns;
}
